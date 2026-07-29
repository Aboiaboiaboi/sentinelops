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

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import SessionLocal
from app.models import SCAN_CATEGORIES, CategoryStatus
from app.scanners import registry
from app.scanners.base import RepositoryIndex, ScanFinding
from app.scanners.framework import detect_framework
from app.services import project_service, scan_service, scoring_service
from app.workers.repo import CloneError, cloned_repository

logger = logging.getLogger(__name__)


async def run_scan(ctx: dict[str, Any], scan_id: str) -> None:
    """arq entrypoint. Opens a session and delegates.

    Nothing but session management belongs here — see `execute_scan`.
    """
    async with SessionLocal() as db:
        await execute_scan(db, scan_id=uuid.UUID(scan_id))


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
        await scan_service.fail_scan(db, scan_id=scan_id)
        return

    logger.info(
        "scan started",
        extra={"scan_id": str(scan_id), "repository_url": target.repository_url},
    )

    try:
        async with cloned_repository(target.repository_url) as repo_path:
            await _detect_and_record_framework(db, target.project_id, repo_path)
            findings, category_status = await _run_scanners(db, scan_id, repo_path)
    except CloneError as exc:
        # Not re-raised. A repository that cannot be cloned will not clone on a
        # retry either — the URL is wrong, private, or the repo is too large —
        # so retrying only burns a worker slot to reach the same conclusion.
        logger.warning(
            "scan failed to clone repository",
            extra={"scan_id": str(scan_id), "reason": str(exc)},
        )
        await scan_service.fail_scan(db, scan_id=scan_id)
        return
    except Exception:
        # Anything else is unexpected and may well be transient, so the scan is
        # marked failed and the exception is re-raised for arq to retry. The
        # retry finds the scan unclaimable and stops, which is the intended
        # outcome — the failure is recorded rather than silently swallowed.
        logger.exception("scan failed", extra={"scan_id": str(scan_id)})
        await scan_service.fail_scan(db, scan_id=scan_id)
        raise

    category_scores = scoring_service.score_by_category(findings, category_status)
    score = sum(category_scores.values())
    await scan_service.complete_scan(
        db,
        scan_id=scan_id,
        score=score,
        scoring_version=scoring_service.SCORING_VERSION,
        category_scores=category_scores,
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


async def _detect_and_record_framework(
    db: AsyncSession, project_id: uuid.UUID, repo_path: Path
) -> None:
    """Fill in Project.framework, which is null until a scan runs.

    Failure here is not fatal: not knowing the stack costs some context in later
    scanners, but it is no reason to throw away a whole scan.
    """
    try:
        framework = await asyncio.to_thread(detect_framework, repo_path)
    except Exception:
        logger.exception("framework detection failed", extra={"project_id": str(project_id)})
        return

    if framework is None:
        return
    await project_service.set_framework(db, project_id=project_id, framework=framework)
    logger.info("framework detected", extra={"project_id": str(project_id), "framework": framework})


async def _run_scanners(
    db: AsyncSession, scan_id: uuid.UUID, repo_path: Path
) -> tuple[list[ScanFinding], dict[str, str]]:
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
    index = await asyncio.to_thread(RepositoryIndex.build, repo_path)
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

    for category in SCAN_CATEGORIES:
        scanner = registry.get_scanner(category)
        if scanner is None:
            await record(category, CategoryStatus.FAILED)
            continue

        try:
            # Off the event loop. Scanners are file and subprocess work, and one
            # running inside a coroutine would stall every other job this worker
            # is handling.
            produced = await asyncio.to_thread(scanner.scan, index)
        except Exception:
            logger.exception(
                "scanner failed",
                extra={"scan_id": str(scan_id), "category": category},
            )
            await record(category, CategoryStatus.FAILED)
            continue

        await scan_service.record_findings(db, scan_id=scan_id, findings=produced)
        findings.extend(produced)
        await record(category, CategoryStatus.COMPLETED)
        logger.info(
            "category scanned",
            extra={"scan_id": str(scan_id), "category": category, "findings": len(produced)},
        )

    return findings, category_status
