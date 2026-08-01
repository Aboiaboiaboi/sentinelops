"""Dependency vulnerability scanning with Trivy.

Answers `security.dependency_vulnerabilities`: are any of the versions this
project pins known to be vulnerable? Regex could never ask this — it needs a
database of advisories, which is why milestone 3 exists to warm one.

**Exit codes, checked rather than assumed** (the mistake the Gitleaks wrapper
made and paid for):

- vulnerabilities found -> **0**. Trivy only signals findings through
  `--exit-code`, which is deliberately not passed.
- unreadable target -> 1
- no vulnerability database -> 1

So zero is the only success, and a missing database can never be mistaken for a
clean repository — it errors, loudly.

**No `Results` key at all** is Trivy's way of saying it found nothing it knows
how to read. That is a *skip*, not a pass: a repository with no lockfile has not
demonstrated that its dependencies are sound, and claiming otherwise would hand
out marks for work nobody did.
"""

import json
import logging
from collections.abc import Iterable
from typing import Any

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
    skipped,
)
from app.utils.sandbox import (
    CACHE_MOUNT,
    REPO_PLACEHOLDER,
    SandboxResult,
    SandboxSpec,
    SandboxUnavailable,
    get_sandbox,
)

logger = logging.getLogger(__name__)

IMAGE = "aquasec/trivy:0.72.0"

# Slower than Gitleaks — it parses every manifest and joins against a 1.1 GB
# database — but far quicker than Semgrep. Capped further by the operator's
# SANDBOX_TIMEOUT_SECONDS.
TIMEOUT_SECONDS = 240

BUDGET = 5

# What the worst vulnerability alone costs. Severity leads because kind matters
# more than count here: forty advisories about a logging library are not worse
# than one remote-code-execution hole, and letting volume drive the number would
# make the score a measure of how many dependencies a project has.
_SEVERITY_COST = {
    "CRITICAL": 5,
    "HIGH": 3,
    "MEDIUM": 2,
    "LOW": 1,
    "UNKNOWN": 1,
}

# Volume is not ignored either, it just cannot dominate: a project carrying this
# many known-vulnerable versions has a maintenance problem beyond whichever one
# happens to be worst.
_MANY = 10
_MANY_SURCHARGE = 1

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]

_FINDING_SEVERITY = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "UNKNOWN": Severity.LOW,
}


def _spec() -> SandboxSpec:
    return SandboxSpec(
        image=IMAGE,
        command=(
            "fs",
            # Vulnerabilities only. Trivy also scans for secrets and
            # misconfiguration, which are other checks' questions — running them
            # here would double-report the same problem under two headings.
            "--scanners",
            "vuln",
            "--format",
            "json",
            "--quiet",
            # The container has no network, so both of these are statements of
            # fact rather than preferences. Without them Trivy tries to reach
            # out, fails, and takes the whole run down with it.
            "--skip-db-update",
            "--offline-scan",
            "--cache-dir",
            CACHE_MOUNT,
            REPO_PLACEHOLDER,
        ),
        timeout_seconds=TIMEOUT_SECONDS,
        # Without the warmed database there is nothing to compare against. The
        # runner refuses the run outright rather than letting Trivy report a
        # repository as clean because it had nothing to check it with.
        needs_cache=True,
    )


def scan_dependencies(check: CheckSpec, repo: RepositoryIndex) -> CheckResult:
    """Run Trivy over the checkout and turn its report into one outcome."""
    try:
        result = get_sandbox().run(_spec(), repo_path=repo.path)
    except SandboxUnavailable as exc:
        return errored(check, f"the dependency scanner could not be run: {exc}")

    if result.timed_out:
        return errored(check, f"the dependency scanner did not finish within {TIMEOUT_SECONDS}s")

    if result.exit_code != 0:
        logger.warning(
            "trivy failed",
            extra={"exit_code": result.exit_code, "stderr": result.stderr[:2000]},
        )
        return errored(check, "the dependency scanner exited unexpectedly")

    if result.truncated:
        return errored(check, "the dependency scanner produced more output than could be read")

    try:
        report = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        logger.warning("trivy produced unparseable output")
        return errored(check, "the dependency scanner produced a report that could not be read")

    if not isinstance(report, dict):
        return errored(check, "the dependency scanner produced a report that could not be read")

    results = report.get("Results")
    if not results:
        # Nothing Trivy recognises as a dependency manifest. Genuinely "the
        # question does not apply", which is exactly what skipped means.
        return skipped(
            check, "no dependency manifest or lockfile was found that the scanner understands"
        )

    vulnerabilities = _relevant_vulnerabilities(results, repo, result)
    logger.info(
        "trivy reported",
        extra={"raw": _count_all(results), "kept": len(vulnerabilities)},
    )

    if not vulnerabilities:
        return passed(check)

    return failed(check, _finding(vulnerabilities))


def _count_all(results: Iterable[Any]) -> int:
    return sum(
        len(entry.get("Vulnerabilities") or []) for entry in results if isinstance(entry, dict)
    )


def _relevant_vulnerabilities(
    results: Iterable[Any], repo: RepositoryIndex, sandbox_result: SandboxResult
) -> list[dict[str, Any]]:
    """Every distinct vulnerability worth reporting.

    Deduplicated by (advisory, package, version): the same CVE is reported once
    per manifest that pins the package, and a monorepo pinning the same library
    in four places has one problem, not four.

    Lockfiles under a test directory are excluded, the same rule the other
    security checks apply. A vulnerable version used only by the test suite is
    not shipped to anybody.
    """
    seen: set[tuple[str, str, str]] = set()
    kept: list[dict[str, Any]] = []

    for entry in results:
        if not isinstance(entry, dict):
            continue
        target = _repository_path(str(entry.get("Target") or ""), sandbox_result.repo_mount)
        if target and is_test_file(repo.path / target, repo.path):
            continue

        for vulnerability in entry.get("Vulnerabilities") or []:
            if not isinstance(vulnerability, dict):
                continue
            identity = (
                str(vulnerability.get("VulnerabilityID") or ""),
                str(vulnerability.get("PkgName") or ""),
                str(vulnerability.get("InstalledVersion") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            kept.append(vulnerability)

    return kept


def _repository_path(target: str, repo_mount: str) -> str:
    """Trivy reports targets relative to the scanned root, but not always —
    a container path shows up for some artefact types, so strip it if present."""
    path = target.replace("\\", "/")
    if repo_mount and path.startswith(repo_mount):
        path = path[len(repo_mount) :]
    return path.lstrip("/")


def _severity_of(vulnerability: dict[str, Any]) -> str:
    severity = str(vulnerability.get("Severity") or "UNKNOWN").upper()
    return severity if severity in _SEVERITY_COST else "UNKNOWN"


def _worst(vulnerabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return min(vulnerabilities, key=lambda v: _SEVERITY_ORDER.index(_severity_of(v)))


def score_impact(vulnerabilities: list[dict[str, Any]]) -> int:
    """What this check deducts. Never more than its own budget.

    Exposed so the test suite can assert the cap directly: a repository with
    forty CVEs must not eat the whole security category, which is worth far more
    than this one question.
    """
    worst = _SEVERITY_COST[_severity_of(_worst(vulnerabilities))]
    surcharge = _MANY_SURCHARGE if len(vulnerabilities) >= _MANY else 0
    return min(BUDGET, worst + surcharge)


def _finding(vulnerabilities: list[dict[str, Any]]) -> ScanFinding:
    worst = _worst(vulnerabilities)
    severity = _severity_of(worst)

    identifier = str(worst.get("VulnerabilityID") or "an advisory")
    package = str(worst.get("PkgName") or "a dependency")
    installed = str(worst.get("InstalledVersion") or "")
    fixed = str(worst.get("FixedVersion") or "")
    title = str(worst.get("Title") or "").strip()

    pinned = f"{package} {installed}".strip()
    others = ""
    if len(vulnerabilities) > 1:
        packages = {str(v.get("PkgName") or "") for v in vulnerabilities}
        others = (
            f" It is the worst of {len(vulnerabilities)} known vulnerabilities "
            f"across {len(packages)} package{'s' if len(packages) > 1 else ''}."
        )

    # The advisory's own summary, when it has one, because "CVE-2024-47081" tells
    # a reader nothing on its own. Truncated: these come from a third-party
    # database and are not length-bounded.
    summary = f" {title[:200]}." if title else ""

    if fixed:
        recommendation = (
            f"Upgrade {package} to {fixed} or later, then re-scan. If the upgrade is not "
            "straightforward, record why and track it — an unpatched dependency with a published "
            "fix is the most likely way this project gets compromised, because the advisory tells "
            "an attacker exactly what to try."
        )
    else:
        recommendation = (
            f"No fixed version of {package} is published yet. Check whether the vulnerable code "
            "path is one this project actually uses, and if it is, look for a workaround the "
            "advisory suggests or an alternative library."
        )

    return ScanFinding(
        category="security",
        severity=_FINDING_SEVERITY[severity],
        title="Dependencies have known vulnerabilities",
        description=(
            f"{pinned} is affected by {identifier} ({severity.lower()}).{summary}{others} "
            "These are published, catalogued weaknesses in versions this project pins — which "
            "means they are as available to an attacker as they are to you."
        ),
        recommendation=recommendation,
        score_impact=score_impact(vulnerabilities),
    )
