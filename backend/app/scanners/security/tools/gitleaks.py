"""Secret scanning with Gitleaks.

Replaces the regex implementation of `security.hardcoded_secrets`. The check id
is deliberately unchanged: ids are stored on every scan, and renaming one
orphans the history of everything that reported it. The question is the same —
"is a credential committed in this code?" — and only the answer got better.

**The command is `gitleaks dir`, not `gitleaks detect`.** `detect` was removed in
v8.19; on v8.30 it exits non-zero with a usage error, which this wrapper would
faithfully report as an errored check forever.

**Known limitation, and it is a real one.** Clones are `--depth 1`, so this sees
the working tree only. Gitleaks' highest-value trick is finding a secret that was
committed and later deleted — still in history, still leaked, invisible here. An
opt-in deeper clone is the follow-up.
"""

import json
import logging
from collections.abc import Iterable

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    errored,
    failed,
    is_test_file,
    passed,
)
from app.utils.sandbox import (
    REPO_PLACEHOLDER,
    SandboxResult,
    SandboxSpec,
    SandboxUnavailable,
    get_sandbox,
)

logger = logging.getLogger(__name__)

IMAGE = "ghcr.io/gitleaks/gitleaks:v8.30.1"

# Gitleaks is the fastest of the three tools — it reads files and matches
# patterns. Generous enough for a large repository, and a ceiling the operator
# can lower further through SANDBOX_TIMEOUT_SECONDS.
TIMEOUT_SECONDS = 180

# With --exit-code 0 below, a clean run and a run that found leaks both exit 0,
# and anything non-zero is the tool itself failing.
#
# Gitleaks' default is to exit 1 when it finds leaks — but it also exits 1 when
# it cannot read the directory at all, and the two are indistinguishable. That
# is not hypothetical: it happened here. The clone sat in a 0700 directory the
# sandbox user could not enter, gitleaks exited 1 with an empty report, and the
# wrapper read "exit 1, no leaks in the report" as a repository with no secrets.
# A tool that saw nothing reported everything as fine, which is the one outcome
# this whole design exists to prevent.
_SUCCESS_EXIT_CODES = frozenset({0})

# The points this check may deduct in total, however many rules matched. A
# repository with forty leaked keys has already lost everything this check can
# take; the rest is detail, not further deduction.
BUDGET = 5

# The plan called for one finding per Gitleaks rule. That shape is not available
# and should not be forced: a CheckResult carries exactly one finding, because a
# check is one question with one answer. So the rules are summarised inside a
# single finding, which is also how every other check in this category reports —
# one finding per *problem*, never one per occurrence.
_MAX_KINDS_NAMED = 3


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image=IMAGE,
        command=(
            "dir",
            "--no-banner",
            # Every secret value replaced with REDACTED before it reaches our
            # stdout. The point of this scan is to report *that* a credential is
            # committed, and the credential itself has no business in a log line
            # or a database row — the same rule that redacts git's stderr.
            "--redact",
            # Finding leaks is not an error, so do not signal one. This is what
            # makes a non-zero exit mean "the tool failed" and nothing else —
            # see _SUCCESS_EXIT_CODES.
            "--exit-code",
            "0",
            "--report-format",
            "json",
            # "-" is stdout. Not /dev/stdout: the container's root filesystem is
            # read-only, and this avoids depending on how that path resolves.
            "--report-path",
            "-",
            REPO_PLACEHOLDER,
        ),
        timeout_seconds=TIMEOUT_SECONDS,
    )


def scan_for_secrets(check: CheckSpec, repo: RepositoryIndex) -> CheckResult:
    """Run Gitleaks over the checkout and turn its report into one outcome."""
    try:
        result = get_sandbox().run(_spec(), repo_path=repo.path)
    except SandboxUnavailable as exc:
        return errored(check, f"the secret scanner could not be run: {exc}")

    if result.timed_out:
        return errored(check, f"the secret scanner did not finish within {TIMEOUT_SECONDS}s")

    if result.exit_code not in _SUCCESS_EXIT_CODES:
        # The tool's own stderr goes to the log and no further. It is text
        # produced by a program reading an arbitrary repository, and a stored
        # message must be fixed text we chose — the rule that governs clone
        # failures too.
        logger.warning(
            "gitleaks failed",
            extra={"exit_code": result.exit_code, "stderr": result.stderr[:2000]},
        )
        return errored(check, "the secret scanner exited unexpectedly")

    if result.truncated:
        return errored(check, "the secret scanner produced more output than could be read")

    try:
        report = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        logger.warning("gitleaks produced unparseable output")
        return errored(check, "the secret scanner produced a report that could not be read")

    if not isinstance(report, list):
        return errored(check, "the secret scanner produced a report that could not be read")

    leaks = _relevant_leaks(report, repo, result)
    # Both numbers, deliberately. "Gitleaks found 22 and we report 0" is a
    # sentence worth being able to read in a log — the filtering below is the
    # only thing between a tool's output and a user's score, and a silent
    # discrepancy there is the hardest kind of bug to notice.
    logger.info("gitleaks reported", extra={"raw": len(report), "kept": len(leaks)})

    if not leaks:
        return passed(check)

    return failed(check, _finding(leaks))


def _relevant_leaks(
    report: Iterable[object], repo: RepositoryIndex, result: SandboxResult
) -> list[tuple[str, str]]:
    """(rule id, repository-relative path) for every leak worth reporting.

    Test files are excluded, which is a deliberate trade-off rather than an
    oversight: run against a fresh clone of SentinelOps, Gitleaks reports seven
    leaks and all seven are fixtures in the security scanner's own test file —
    format-valid keys that exist precisely to be detected. A tool that flags a
    project's test fixtures gets muted, and a muted tool catches nothing. The
    cost is that a genuine credential committed inside a test is not reported
    here; `security.credential_files` applies the same rule for the same reason.
    """
    leaks: list[tuple[str, str]] = []
    for entry in report:
        if not isinstance(entry, dict):
            continue
        rule = str(entry.get("RuleID") or "unknown")
        relative = _relative_path(str(entry.get("File") or ""), result.repo_mount)
        if not relative:
            continue
        if is_test_file(repo.path / relative, repo.path):
            continue
        leaks.append((rule, relative))
    return leaks


def _relative_path(reported: str, repo_mount: str) -> str:
    """Turn the path the container saw into one that means something here.

    Gitleaks reports absolute paths from inside its own filesystem, which is
    either /repo or the clone's real path depending on how the sandbox mounted
    it. The runner reports which, so this wrapper does not have to know.
    """
    path = reported.replace("\\", "/")
    if repo_mount and path.startswith(repo_mount):
        path = path[len(repo_mount) :]
    return path.lstrip("/")


def _describe_rule(rule: str) -> str:
    """A rule id as something readable. `aws-access-token` -> `aws access token`."""
    return rule.replace("-", " ").replace("_", " ")


def _finding(leaks: list[tuple[str, str]]) -> ScanFinding:
    """One finding for the check, naming what was found and where.

    Grouped by rule and ordered by how much of the report each rule accounts
    for, so the headline is the most prevalent kind of leak rather than whatever
    Gitleaks happened to print first.
    """
    by_rule: dict[str, list[str]] = {}
    for rule, path in leaks:
        by_rule.setdefault(rule, []).append(path)

    ranked = sorted(by_rule.items(), key=lambda item: (-len(item[1]), item[0]))
    headline_rule, headline_paths = ranked[0]

    kinds = ", ".join(_describe_rule(rule) for rule, _ in ranked[:_MAX_KINDS_NAMED])
    if len(ranked) > _MAX_KINDS_NAMED:
        kinds += f", and {len(ranked) - _MAX_KINDS_NAMED} other kinds"

    occurrences = (
        "1 place" if len(leaks) == 1 else f"{len(leaks)} places across {len(by_rule)} kinds"
    )

    return ScanFinding(
        category="security",
        severity=Severity.CRITICAL,
        title="Credentials are committed in the code",
        description=(
            f"Gitleaks found what it recognises as live credential formats in {occurrences}, "
            f"starting with {headline_paths[0]} ({_describe_rule(headline_rule)}). "
            f"Detected: {kinds}. A committed credential is readable by everyone with repository "
            "access, survives in git history after the file is deleted, and ships inside every "
            "build artefact made from this code. The values themselves are redacted from this "
            "report — SentinelOps never stores them."
        ),
        recommendation=(
            "Rotate every credential found, before anything else: removing one without rotating "
            "it fixes nothing, because the old value is still in history. Then load them from the "
            "environment or a secrets manager, and add a pre-commit secret scanner so the next "
            "one is caught before it lands."
        ),
        score_impact=BUDGET,
    )
