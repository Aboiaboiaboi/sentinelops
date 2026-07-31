"""Reliability checks.

What happens to this service when something it depends on misbehaves — a slow
upstream, a transient failure, an orchestrator asking it to shut down.

These are text patterns over source files, not real static analysis, so every
check is written to fail *quiet* rather than loud. A missed problem is a shame;
a confident false positive teaches people to ignore the whole category.
"""

import re
from pathlib import Path

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    failed,
    passed,
    skipped,
)

CATEGORY = "reliability"

_HEALTH = CheckSpec("reliability.health", "Health endpoint")
_TIMEOUTS = CheckSpec("reliability.timeouts", "Timeouts on outbound calls")
_SWALLOWED = CheckSpec("reliability.swallowed_errors", "Errors are not discarded")
_RETRIES = CheckSpec("reliability.retries", "Retry handling")

# Impacts, summing to the category weight of 20.
_NO_HEALTH_ENDPOINT = 6
_CALLS_WITHOUT_TIMEOUTS = 6
_SWALLOWED_ERRORS = 4
_NO_RETRY_HANDLING = 4

# Route paths and probe keys that mean "something can ask if I am alive".
_HEALTH_MARKERS = (
    "/health",
    "/healthz",
    "/healthcheck",
    "/readyz",
    "/livez",
    "/ready",
    "/ping",
    "/status",
    "livenessprobe",
    "readinessprobe",
    "healthcheck:",
)

# Outbound network calls, per ecosystem. Matching the call site rather than the
# import, because importing a client says nothing about whether it is used
# carelessly.
_OUTBOUND_CALL = re.compile(
    r"""
    \b(?:
        requests\.(?:get|post|put|patch|delete|head|request)
      | httpx\.(?:get|post|put|patch|delete|head|request)
      | urlopen
      | axios\.(?:get|post|put|patch|delete|head|request)
      | fetch
      | http\.(?:Get|Post|Head|PostForm)
      | HttpClient
    )\s*\(
    """,
    re.VERBOSE,
)

# Evidence that a file thinks about how long it is willing to wait. Deliberately
# generous: a session configured once at the top of a module covers every call
# below it, and flagging those would be wrong.
_TIMEOUT_EVIDENCE = re.compile(
    r"\b(?:timeout|timeouts|deadline|AbortSignal|AbortController|signal\s*[:=]|"
    r"WithTimeout|setTimeout|read_timeout|connect_timeout)\b",
    re.IGNORECASE,
)

# Libraries whose whole purpose is retrying with backoff.
_RETRY_EVIDENCE = re.compile(
    r"\b(?:tenacity|backoff|retrying|urllib3\.util\.retry|Retry\(|p-retry|axios-retry|"
    r"async-retry|resilience4j|polly|retry_on|max_retries|retries\s*[:=]|@retry)\b",
    re.IGNORECASE,
)

# `except:` with nothing after it, and `except Whatever:` whose body is a bare
# pass. Both mean a failure happened and nobody will ever know.
_BARE_EXCEPT = re.compile(r"^\s*except\s*:", re.MULTILINE)
_EXCEPT_PASS = re.compile(r"^\s*except\b[^\n]*:\s*\n\s*pass\s*$", re.MULTILINE)
# JavaScript's equivalent: catch with an empty body.
_EMPTY_CATCH = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}")


class ReliabilityScanner:
    category = CATEGORY
    CHECKS = (_HEALTH, _TIMEOUTS, _SWALLOWED, _RETRIES)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        # Read each source file once and answer every question from it, rather
        # than four passes over the same text.
        untimed: list[str] = []
        swallowed: list[str] = []
        makes_calls = False
        has_retries = False
        has_health = False

        # Production code only. A test that deliberately swallows an
        # exception, or calls out without a timeout, is not a production
        # reliability problem — and fixture strings containing `except: pass`
        # would otherwise be reported as real code.
        for path in repo.production_files:
            content = repo.read(path)
            if not content:
                continue
            lowered = content.lower()

            if not has_health and any(marker in lowered for marker in _HEALTH_MARKERS):
                has_health = True
            if not has_retries and _RETRY_EVIDENCE.search(content):
                has_retries = True

            if _OUTBOUND_CALL.search(content):
                makes_calls = True
                if not _TIMEOUT_EVIDENCE.search(content):
                    untimed.append(repo.relative(path))

            if (
                _BARE_EXCEPT.search(content)
                or _EXCEPT_PASS.search(content)
                or _EMPTY_CATCH.search(content)
            ):
                swallowed.append(repo.relative(path))

        # Probes live in deployment manifests as often as in code.
        if not has_health:
            has_health = self._probes_in_manifests(repo)

        return [
            self._check_health(repo, has_health),
            self._check_timeouts(makes_calls, untimed),
            self._check_swallowed(swallowed),
            self._check_retries(makes_calls, has_retries),
        ]

    def _probes_in_manifests(self, repo: RepositoryIndex) -> bool:
        for path in repo.files:
            if path.suffix.lower() not in {".yml", ".yaml"} and not _is_dockerfile(path):
                continue
            lowered = repo.read(path).lower()
            if any(marker in lowered for marker in _HEALTH_MARKERS):
                return True
        return False

    def _check_health(self, repo: RepositoryIndex, has_health: bool) -> CheckResult:
        # Only asked of things that serve traffic. A library answering health
        # checks would be the odd one out — and saying "not applicable" is not
        # the same as saying "fine", which is what an empty list used to mean.
        if not repo.is_service:
            return skipped(_HEALTH, "only asked of something that serves traffic")
        if has_health:
            return passed(_HEALTH)
        return failed(
            _HEALTH,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="No health endpoint",
                description=(
                    f"This looks like a {repo.framework} service, but no health or readiness "
                    "endpoint was found. Nothing can distinguish a process that is running from "
                    "one that is actually serving, so a wedged instance keeps receiving traffic "
                    "until somebody notices."
                ),
                recommendation=(
                    "Add a lightweight endpoint such as /health that returns quickly, and point "
                    "your orchestrator's readiness probe at it."
                ),
                score_impact=_NO_HEALTH_ENDPOINT,
            ),
        )

    def _check_timeouts(self, makes_calls: bool, untimed: list[str]) -> CheckResult:
        if not makes_calls:
            return skipped(_TIMEOUTS, "no outbound calls were found to time out")
        if not untimed:
            return passed(_TIMEOUTS)
        others = f" and {len(untimed) - 1} other files" if len(untimed) > 1 else ""
        return failed(
            _TIMEOUTS,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="Network calls without timeouts",
                description=(
                    f"{untimed[0]}{others} makes outbound requests with no timeout set. Most HTTP "
                    "clients wait indefinitely by default, so one slow dependency holds a worker "
                    "open until the pool is exhausted and the service stops responding — to "
                    "everything, not just that dependency."
                ),
                recommendation=(
                    "Set an explicit timeout on every outbound call, or configure one once on a "
                    "shared client and use it everywhere."
                ),
                score_impact=_CALLS_WITHOUT_TIMEOUTS,
            ),
        )

    def _check_retries(self, makes_calls: bool, has_retries: bool) -> CheckResult:
        # A project that talks to nothing has nothing to retry.
        if not makes_calls:
            return skipped(_RETRIES, "no outbound calls were found to retry")
        if has_retries:
            return passed(_RETRIES)
        return failed(
            _RETRIES,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="No retry handling for outbound calls",
                description=(
                    "Outbound requests were found with no sign of retry or backoff handling. A "
                    "single dropped connection then becomes a failed request to your own users, "
                    "even when retrying it immediately would have worked."
                ),
                recommendation=(
                    "Retry idempotent calls with exponential backoff and a cap. Do not retry "
                    "blindly — a retry storm against a struggling dependency makes it worse."
                ),
                score_impact=_NO_RETRY_HANDLING,
            ),
        )

    def _check_swallowed(self, swallowed: list[str]) -> CheckResult:
        if not swallowed:
            return passed(_SWALLOWED)
        others = f" and {len(swallowed) - 1} other files" if len(swallowed) > 1 else ""
        return failed(
            _SWALLOWED,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="Errors are silently discarded",
                description=(
                    f"{swallowed[0]}{others} catches exceptions and does nothing with them. The "
                    "failure still happened, but nothing is logged and nothing is raised — so the "
                    "first sign of trouble is wrong data or a confusing symptom somewhere else."
                ),
                recommendation=(
                    "Log the exception with context, or let it propagate. Catch a specific "
                    "exception type when you genuinely intend to continue."
                ),
                score_impact=_SWALLOWED_ERRORS,
            ),
        )


def _is_dockerfile(path: Path) -> bool:
    name = path.name.lower()
    return name == "dockerfile" or name.startswith("dockerfile.")
