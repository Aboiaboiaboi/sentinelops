"""arq task definitions.

Deliberately thin. Each task opens a database session and hands it to a function
that does the work, which is the same shape `services/` already uses — so the
logic is reachable from a test without arq running at all.

That split is not stylistic. The test fixtures bind every session to one
connection that is rolled back afterwards; a task that opened its own session
would escape that isolation and write to the development database instead.
"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import SessionLocal
from app.models import SCAN_CATEGORIES, CategoryStatus, ScanErrorCategory
from app.scanners import registry
from app.scanners.base import CONCLUSIVE_OUTCOMES, RepositoryIndex, ScanFinding, findings_of
from app.scanners.framework import detect_framework
from app.services import github_service, project_service, scan_service, scoring_service
from app.services.github_app_service import (
    GitHubApiError,
    GitHubAppNotConfigured,
    GitHubAppNotInstalled,
    InstallationTokenError,
    get_github_app_auth,
    git_credential_header,
)
from app.workers.repo import (
    CloneError,
    CloneTimedOut,
    CloneTooLarge,
    cloned_repository,
    read_head_commit,
)

logger = logging.getLogger(__name__)


def _github_full_name(repository_url: str) -> str | None:
    """The `owner/repo` a github.com URL points at, or None for anything else.

    Only github.com URLs can be authenticated with our App, so everything else
    short-circuits to an anonymous clone without touching the GitHub API.
    """
    parsed = urlparse(repository_url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


async def resolve_credential(
    db: AsyncSession, *, repository_url: str, user_id: uuid.UUID
) -> str | None:
    """Credentials for this clone, or None to clone anonymously.

    Deterministic rather than try-and-retry: for a github.com repository, each
    of the owner's installations is asked whether its grant covers the repo,
    and the first that does supplies a token. The token becomes a header via
    `git_credential_header` — never part of the URL.

    Every failure path here degrades to anonymous. A public repository must
    keep scanning when the App is unconfigured, the user has no installations,
    or GitHub is having a bad day — worst case, a *private* clone then fails
    exactly as it would have without this function.
    """
    full_name = _github_full_name(repository_url)
    if full_name is None:
        return None

    installation_ids = await github_service.installation_ids_for_user(db, user_id=user_id)
    if not installation_ids:
        return None

    try:
        auth = get_github_app_auth()
    except GitHubAppNotConfigured:
        return None

    for installation_id in installation_ids:
        try:
            if await auth.can_access(installation_id, full_name):
                logger.info(
                    "cloning with github app credentials",
                    extra={"installation_id": installation_id, "repository": full_name},
                )
                return git_credential_header(await auth.installation_token(installation_id))
        except GitHubAppNotInstalled:
            # Stale row — the user uninstalled the App there. Another
            # installation may still cover the repository.
            continue
        except (GitHubApiError, InstallationTokenError) as exc:
            logger.warning(
                "github credential resolution failed, cloning anonymously",
                extra={"installation_id": installation_id, "reason": str(exc)},
            )
            return None
    return None


# Substrings git uses for each cause, checked against stderr in order.
#
# Read to classify, never stored. git's stderr can echo the clone URL, and for
# a private repository that URL carries an installation token — so what is
# persisted is the fixed detail below, chosen by the match, and never the text
# that produced it.
#
# "not found" covers both a missing repository and a private one we lack access
# to: GitHub deliberately answers identically for the two, so that they cannot
# be used to probe which private repositories exist. The hint for that category
# says so rather than guessing.
_CLONE_FAILURE_SIGNATURES: tuple[tuple[str, ScanErrorCategory], ...] = (
    # Credentials were supplied and *refused*. Only these are authentication
    # failures, and the distinction is not pedantic: it decides whether the
    # advice is "connect GitHub" or "check the URL".
    ("authentication failed", ScanErrorCategory.AUTHENTICATION),
    ("permission denied", ScanErrorCategory.AUTHENTICATION),
    ("403", ScanErrorCategory.AUTHENTICATION),
    # git wanted credentials and had none. Found by scanning a repository that
    # genuinely did not exist and watching it come back as "authentication":
    # GitHub answers 404 identically for a missing repository and a private one
    # you cannot see — deliberately, so the API cannot be used to probe which
    # private repositories exist — and git responds by asking for a username.
    # So this says nothing about credentials being wrong, and the honest
    # category is the one whose wording already covers both cases.
    ("could not read username", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("could not read password", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("terminal prompts disabled", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("repository not found", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("not found", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("does not appear to be a git repository", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("repository does not exist", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("the repository exists", ScanErrorCategory.REPOSITORY_NOT_FOUND),
    ("could not resolve host", ScanErrorCategory.NETWORK_UNREACHABLE),
    ("failed to connect", ScanErrorCategory.NETWORK_UNREACHABLE),
    ("connection timed out", ScanErrorCategory.NETWORK_UNREACHABLE),
    ("unable to access", ScanErrorCategory.NETWORK_UNREACHABLE),
)

# The detail persisted for each category. Fixed text, so nothing a repository
# or a git remote can influence ever reaches the database.
_ERROR_DETAILS: dict[ScanErrorCategory, str] = {
    ScanErrorCategory.REPOSITORY_NOT_FOUND: (
        "No repository was found at that URL, or it is private and this account has not "
        "been granted access to it."
    ),
    ScanErrorCategory.AUTHENTICATION: (
        "The repository refused access with the credentials available for this account."
    ),
    ScanErrorCategory.NETWORK_UNREACHABLE: "The repository host could not be reached.",
    ScanErrorCategory.CLONE_FAILED: "The repository could not be cloned.",
    ScanErrorCategory.INTERNAL: "The scan stopped because of an unexpected error.",
}


def classify_clone_failure(error: CloneError) -> tuple[ScanErrorCategory, str]:
    """Map a clone failure to a stored category and a safe detail.

    `CloneTooLarge` and `CloneTimedOut` carry messages this codebase wrote, so
    their text is safe to keep and is more useful than a fixed sentence — it
    names the limit that was hit. `CloneFailed` carries git's, which is not.
    """
    if isinstance(error, CloneTooLarge):
        return ScanErrorCategory.REPOSITORY_TOO_LARGE, str(error)
    if isinstance(error, CloneTimedOut):
        return ScanErrorCategory.TIMEOUT, str(error)

    haystack = str(error).lower()
    for signature, category in _CLONE_FAILURE_SIGNATURES:
        if signature in haystack:
            return category, _ERROR_DETAILS[category]
    return ScanErrorCategory.CLONE_FAILED, _ERROR_DETAILS[ScanErrorCategory.CLONE_FAILED]


async def run_scan(ctx: dict[str, Any], scan_id: str) -> None:
    """arq entrypoint. Opens a session and delegates.

    Session management plus one thing that has to happen at this exact
    boundary: recording the job's outcome for app/observability.py's
    /metrics to read. It belongs here, not in execute_scan, because arq
    itself can raise around execute_scan (job_timeout) without execute_scan
    ever getting a chance to record anything — the outcome must be captured
    at the actual entrypoint.

    This is the arq job's outcome (did the worker process crash), not the
    scan's — execute_scan catches CloneError and records a normal *scan*
    failure via scan_service.fail_scan without raising, so a badly-typed
    repository URL counts as a "success" job here. The dashboard this feeds
    (deploy/compose/observability/grafana/dashboards/scans-and-queue.json)
    is therefore reading "did the worker stay up", not "did users' scans
    pass" — a real distinction worth keeping in mind reading the graph, not
    a bug in either place.
    """
    async with SessionLocal() as db:
        try:
            await execute_scan(db, scan_id=uuid.UUID(scan_id))
        except Exception:
            await _record_job_outcome(ctx, "failure")
            raise
        else:
            await _record_job_outcome(ctx, "success")


async def _record_job_outcome(ctx: dict[str, Any], outcome: str) -> None:
    """INCR a plain Redis counter app/observability.py mirrors at scrape
    time. Not an in-process prometheus_client Counter: the worker and the
    API are separate processes, so a Counter here would never be visible to
    the API's /metrics — Redis is the coordination point, the same role it
    already plays for the queue itself (app/utils/queue.py).

    ctx["redis"] is the pool arq's own Worker puts there before calling any
    job function — no separate connection needed.
    """
    await ctx["redis"].incr(f"sentinelops:jobs:{outcome}")


async def execute_scan(db: AsyncSession, *, scan_id: uuid.UUID) -> None:
    """Drive one scan from pending to a terminal state."""
    if not await scan_service.claim_scan(db, scan_id=scan_id):
        # Already running, already finished, or gone. arq redelivers on retry
        # and a duplicate must not restart work that is underway.
        logger.info("scan not claimable, skipping", extra={"scan_id": str(scan_id)})
        return

    target = await scan_service.get_scan_target(db, scan_id=scan_id)
    if target is None:
        logger.warning("scan has no project", extra={"scan_id": str(scan_id)})
        await scan_service.fail_scan(
            db,
            scan_id=scan_id,
            category=ScanErrorCategory.INTERNAL.value,
            detail=_ERROR_DETAILS[ScanErrorCategory.INTERNAL],
        )
        return

    logger.info(
        "scan started",
        extra={"scan_id": str(scan_id), "repository_url": target.repository_url},
    )

    credential_header = await resolve_credential(
        db, repository_url=target.repository_url, user_id=target.user_id
    )

    try:
        async with cloned_repository(
            target.repository_url, credential_header=credential_header
        ) as repo_path:
            # Recorded before the scanners run, so a scan that fails partway
            # still says which commit it was looking at — precisely when
            # somebody wants to know.
            await _record_commit_context(db, scan_id, repo_path)

            # Detected first so the scanners can see it. Whether a missing
            # health endpoint is a problem depends on whether this is a service
            # at all, and only the framework answers that.
            framework = await _detect_and_record_framework(db, target.project_id, repo_path)
            findings, category_status, checks = await _run_scanners(
                db, scan_id, repo_path, framework=framework
            )
    except CloneError as exc:
        # Not re-raised. A repository that cannot be cloned will not clone on a
        # retry either — the URL is wrong, private, or the repo is too large —
        # so retrying only burns a worker slot to reach the same conclusion.
        category, detail = classify_clone_failure(exc)
        # str(exc) goes to the log and no further: it is git's stderr, which can
        # echo a URL carrying an installation token. `detail` is fixed text.
        logger.warning(
            "scan failed to clone repository",
            extra={"scan_id": str(scan_id), "category": category.value, "reason": str(exc)},
        )
        await scan_service.fail_scan(db, scan_id=scan_id, category=category.value, detail=detail)
        return
    except Exception:
        # Anything else is unexpected and may well be transient, so the scan is
        # marked failed and the exception is re-raised for arq to retry. The
        # retry finds the scan unclaimable and stops, which is the intended
        # outcome — the failure is recorded rather than silently swallowed.
        #
        # The exception text is deliberately not stored: an internal error can
        # carry anything, including a connection string.
        logger.exception("scan failed", extra={"scan_id": str(scan_id)})
        await scan_service.fail_scan(
            db,
            scan_id=scan_id,
            category=ScanErrorCategory.INTERNAL.value,
            detail=_ERROR_DETAILS[ScanErrorCategory.INTERNAL],
        )
        raise

    category_scores = scoring_service.score_by_category(findings, category_status)
    score = sum(category_scores.values())
    await scan_service.complete_scan(
        db,
        scan_id=scan_id,
        score=score,
        scoring_version=scoring_service.SCORING_VERSION,
        category_scores=category_scores,
        check_results=checks,
    )
    logger.info(
        "scan completed",
        extra={
            "scan_id": str(scan_id),
            "score": score,
            "findings": len(findings),
            "reported": sum(
                1 for s in category_status.values() if s == CategoryStatus.COMPLETED.value
            ),
        },
    )


async def _record_commit_context(db: AsyncSession, scan_id: uuid.UUID, repo_path: Path) -> None:
    """Store the HEAD commit, if the checkout has one.

    Off the event loop because it shells out to git. Silence is a valid answer:
    an empty repository has no HEAD, and commit context is an extra on top of a
    scan rather than a precondition for one.
    """
    commit = await asyncio.to_thread(read_head_commit, repo_path)
    if commit is None:
        return

    await scan_service.record_commit_context(
        db,
        scan_id=scan_id,
        sha=commit.sha,
        message=commit.message,
        author=commit.author,
        committed_at=commit.committed_at,
    )
    logger.info(
        "commit context recorded",
        extra={"scan_id": str(scan_id), "commit": commit.sha[:8]},
    )


async def _detect_and_record_framework(
    db: AsyncSession, project_id: uuid.UUID, repo_path: Path
) -> str | None:
    """Fill in Project.framework, which is null until a scan runs.

    Failure here is not fatal: not knowing the stack costs some context in later
    scanners, but it is no reason to throw away a whole scan.
    """
    try:
        framework = await asyncio.to_thread(detect_framework, repo_path)
    except Exception:
        logger.exception("framework detection failed", extra={"project_id": str(project_id)})
        return None

    if framework is None:
        return None
    await project_service.set_framework(db, project_id=project_id, framework=framework)
    logger.info("framework detected", extra={"project_id": str(project_id), "framework": framework})
    return framework


async def _run_scanners(
    db: AsyncSession, scan_id: uuid.UUID, repo_path: Path, *, framework: str | None
) -> tuple[list[ScanFinding], dict[str, str], list[dict[str, Any]]]:
    """Run every category, recording each result as it lands.

    Results are written per category rather than in one batch at the end, so a
    client polling mid-scan watches the categories light up instead of seeing
    nothing until everything finishes.

    One category failing never stops the others. That is the partial-failure
    behaviour the whole design is built around: the scan completes with whatever
    reported, and the categories that did not cost their weight.
    """
    # One walk of the tree, shared by every scanner. Built off the event loop
    # like the scanners themselves — it is filesystem work, and on a large
    # repository it is not instant.
    index = await asyncio.to_thread(RepositoryIndex.build, repo_path, framework=framework)
    logger.info(
        "repository indexed",
        extra={
            "scan_id": str(scan_id),
            "files": len(index.files),
            "source_files": len(index.source_files),
        },
    )

    findings: list[ScanFinding] = []
    category_status: dict[str, str] = {}
    checks: list[dict[str, Any]] = []

    async def record(category: str, status: CategoryStatus) -> None:
        """Write the result and remember it.

        Both have to happen together: the database row is what the client polls,
        and the in-memory copy is what the score is computed from. Two call
        sites updating one and forgetting the other is exactly how a scan ends
        up showing categories the score does not account for.
        """
        category_status[category] = status.value
        await scan_service.record_category_result(
            db, scan_id=scan_id, category=category, status=status
        )

    # A repository with no source code is not an application, and a category
    # that assessed nothing must not report as if it assessed something. Every
    # scanner is individually built to stay silent rather than guess — the
    # right behaviour per check, but summed up it meant an *empty* repository
    # scored 77/100: silence priced as excellence. Found when a README-only
    # test repo outscored a real Flask project by four points.
    if not index.source_files:
        logger.info(
            "repository has no source code, nothing to assess",
            extra={"scan_id": str(scan_id)},
        )
        for category in SCAN_CATEGORIES:
            await record(category, CategoryStatus.FAILED)
        return findings, category_status, checks

    for category in SCAN_CATEGORIES:
        scanner = registry.get_scanner(category)
        if scanner is None:
            await record(category, CategoryStatus.FAILED)
            continue

        try:
            # Off the event loop. Scanners are file and subprocess work, and one
            # running inside a coroutine would stall every other job this worker
            # is handling.
            results = await asyncio.to_thread(scanner.scan, index)
        except Exception:
            logger.exception(
                "scanner failed",
                extra={"scan_id": str(scan_id), "category": category},
            )
            await record(category, CategoryStatus.FAILED)
            continue

        # Scoring reads exactly the findings it always did — this one call is
        # what keeps check results purely additive.
        produced = findings_of(results)
        await scan_service.record_findings(db, scan_id=scan_id, findings=produced)
        findings.extend(produced)
        checks.extend(
            {
                "id": result.id,
                "category": category,
                "title": result.title,
                "outcome": result.outcome.value,
                "reason": result.reason,
            }
            for result in results
        )

        # A category where no check reached a verdict assessed nothing, and must
        # not be paid as though it had. Scalability on a library is the plain
        # case: all three checks ask what a second instance would do, none of
        # them apply, and it was collecting the full 10 for work nobody did.
        # A category whose tools all failed to run is the same situation
        # arriving by a different route.
        #
        # This is the same rule already applied to a category with no scanner
        # and to a repository with no source — silence is not excellence — and
        # it only bites when *nothing* concluded. One passing check is enough to
        # have earned the category, because something really was verified.
        #
        # Check results are still stored above, so "what was checked" can say
        # which questions did not apply, or which could not be answered, rather
        # than leaving a bare zero.
        if results and not any(result.outcome in CONCLUSIVE_OUTCOMES for result in results):
            logger.info(
                "category reached no verdict, nothing assessed",
                extra={"scan_id": str(scan_id), "category": category},
            )
            await record(category, CategoryStatus.FAILED)
            continue

        await record(category, CategoryStatus.COMPLETED)
        logger.info(
            "category scanned",
            extra={
                "scan_id": str(scan_id),
                "category": category,
                "findings": len(produced),
                "checks": len(results),
            },
        )

    return findings, category_status, checks
