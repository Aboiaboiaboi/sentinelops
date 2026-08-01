"""Tests for the Trivy wrapper.

The report shapes here are taken from real Trivy 0.72 output, including the two
that decide the outcome and are easy to get backwards:

- a repository with no manifest has **no `Results` key at all** — a skip
- a manifest whose packages are all current has `Results` with no
  `Vulnerabilities` — a pass

Every failure mode maps to ERRORED. Trivy exits 1 when its database is missing,
and a database it could not read must never be reported as a repository with
nothing wrong with it.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.scanners.base import CheckOutcome, CheckSpec, RepositoryIndex, Severity
from app.scanners.security.tools import trivy
from app.utils.sandbox import SandboxResult, SandboxSpec, SandboxUnavailable, set_sandbox

CHECK = CheckSpec("security.dependency_vulnerabilities", "No known-vulnerable dependencies")


def _vulnerability(
    identifier: str = "CVE-2024-47081",
    package: str = "requests",
    severity: str = "MEDIUM",
    installed: str = "2.32.3",
    fixed: str = "2.32.4",
    title: str = "requests: credentials leak via malicious URLs",
) -> dict[str, Any]:
    return {
        "VulnerabilityID": identifier,
        "PkgName": package,
        "InstalledVersion": installed,
        "FixedVersion": fixed,
        "Severity": severity,
        "Title": title,
        "PrimaryURL": f"https://avd.aquasec.com/nvd/{identifier.lower()}",
        "Status": "fixed",
    }


def _report(*vulnerabilities: dict[str, Any], target: str = "requirements.txt") -> str:
    return json.dumps(
        {
            "SchemaVersion": 2,
            "ArtifactName": "/repo",
            "ArtifactType": "filesystem",
            "Results": [
                {
                    "Target": target,
                    "Class": "lang-pkgs",
                    "Type": "pip",
                    "Vulnerabilities": list(vulnerabilities),
                }
            ],
        }
    )


NO_MANIFEST = json.dumps(
    {"SchemaVersion": 2, "ArtifactName": "/repo", "ArtifactType": "filesystem"}
)

CLEAN_MANIFEST = json.dumps(
    {
        "SchemaVersion": 2,
        "ArtifactName": "/repo",
        "ArtifactType": "filesystem",
        "Results": [{"Target": "requirements.txt", "Class": "lang-pkgs", "Type": "pip"}],
    }
)


class FakeSandbox:
    def __init__(self, **result: Any) -> None:
        self.spec: SandboxSpec | None = None
        self._result = {
            "exit_code": 0,
            "stdout": NO_MANIFEST,
            "stderr": "",
            "timed_out": False,
            "repo_mount": "/repo",
        } | result

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        self.spec = spec
        return SandboxResult(**self._result)


class RefusingSandbox:
    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        raise SandboxUnavailable("no cache volume is configured")


@pytest.fixture(autouse=True)
def restore_sandbox():
    from app.utils.sandbox import get_sandbox

    original = get_sandbox()
    yield
    set_sandbox(original)


@pytest.fixture
def repo(tmp_path: Path) -> RepositoryIndex:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    return RepositoryIndex.build(tmp_path, framework="FastAPI")


def _run(repo: RepositoryIndex, **result: Any):
    sandbox = FakeSandbox(**result)
    set_sandbox(sandbox)
    return trivy.scan_dependencies(CHECK, repo), sandbox


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_it_demands_the_warmed_cache(repo: RepositoryIndex) -> None:
    """Without the database there is nothing to compare against, and the runner
    refuses rather than letting Trivy report a repository as clean."""
    _, sandbox = _run(repo)

    assert sandbox.spec.needs_cache is True


def test_it_never_tries_to_reach_the_network(repo: RepositoryIndex) -> None:
    """The container has --network=none, so an attempted update is not a slow
    path, it is a failed run."""
    command = _run(repo)[1].spec.command

    assert "--skip-db-update" in command
    assert "--offline-scan" in command


def test_it_asks_only_about_vulnerabilities(repo: RepositoryIndex) -> None:
    """Trivy also finds secrets and misconfiguration — other checks' questions.
    Running them here would report the same problem twice under two headings."""
    command = _run(repo)[1].spec.command

    assert command[command.index("--scanners") + 1] == "vuln"


def test_the_image_is_pinned(repo: RepositoryIndex) -> None:
    assert ":" in trivy.IMAGE
    assert not trivy.IMAGE.endswith(":latest")


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_no_manifest_is_skipped_not_passed(repo: RepositoryIndex) -> None:
    """A repository with no lockfile has not demonstrated that its dependencies
    are sound. Passing it would hand out marks for work nobody did."""
    result, _ = _run(repo, stdout=NO_MANIFEST)

    assert result.outcome is CheckOutcome.SKIPPED
    assert "manifest" in (result.reason or "")


def test_a_manifest_with_nothing_wrong_is_a_pass(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=CLEAN_MANIFEST)

    assert result.outcome is CheckOutcome.PASSED


def test_a_vulnerability_fails_the_check(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_vulnerability()))

    assert result.outcome is CheckOutcome.FAILED
    assert result.finding is not None


def test_the_finding_names_the_advisory_the_package_and_the_version(
    repo: RepositoryIndex,
) -> None:
    result, _ = _run(repo, stdout=_report(_vulnerability()))

    assert "CVE-2024-47081" in result.finding.description
    assert "requests 2.32.3" in result.finding.description


def test_the_recommendation_names_the_version_to_upgrade_to(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_vulnerability(fixed="2.32.4")))

    assert "2.32.4" in result.finding.recommendation


def test_an_unfixed_vulnerability_does_not_advise_an_upgrade(repo: RepositoryIndex) -> None:
    """Telling somebody to upgrade to a version that does not exist wastes the
    time of the one person who tried to act on the report."""
    result, _ = _run(repo, stdout=_report(_vulnerability(fixed="")))

    assert "No fixed version" in result.finding.recommendation


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("CRITICAL", 5), ("HIGH", 3), ("MEDIUM", 2), ("LOW", 1), ("UNKNOWN", 1)],
)
def test_the_worst_severity_drives_the_deduction(
    repo: RepositoryIndex, severity: str, expected: int
) -> None:
    result, _ = _run(repo, stdout=_report(_vulnerability(severity=severity)))

    assert result.finding.score_impact == expected


def test_many_vulnerabilities_add_a_surcharge_but_cannot_dominate(
    repo: RepositoryIndex,
) -> None:
    """A project carrying this many known-vulnerable versions has a maintenance
    problem beyond whichever one happens to be worst — but count must not become
    the measure, or the score just tracks how many dependencies a project has."""
    few = [_vulnerability(f"CVE-2024-{n:04d}", severity="LOW") for n in range(2)]
    many = [_vulnerability(f"CVE-2024-{n:04d}", severity="LOW") for n in range(12)]

    assert trivy.score_impact(few) == 1
    assert trivy.score_impact(many) == 2


def test_the_deduction_never_exceeds_the_check_budget(repo: RepositoryIndex) -> None:
    """Forty CVEs must not eat the security category, which is worth five times
    this one question."""
    report = [_vulnerability(f"CVE-2024-{n:04d}", severity="CRITICAL") for n in range(40)]

    assert trivy.score_impact(report) == trivy.BUDGET


def test_the_finding_severity_follows_the_worst_vulnerability(repo: RepositoryIndex) -> None:
    result, _ = _run(
        repo,
        stdout=_report(_vulnerability(severity="LOW"), _vulnerability("CVE-2", severity="HIGH")),
    )

    assert result.finding.severity is Severity.HIGH


def test_one_finding_however_many_vulnerabilities(repo: RepositoryIndex) -> None:
    report = [_vulnerability(f"CVE-2024-{n:04d}") for n in range(30)]

    result, _ = _run(repo, stdout=_report(*report))

    assert "30 known vulnerabilities" in result.finding.description


# ---------------------------------------------------------------------------
# Noise control
# ---------------------------------------------------------------------------


def test_the_same_advisory_in_two_manifests_is_one_problem(repo: RepositoryIndex) -> None:
    """A monorepo pinning the same library in four places has one problem."""
    duplicated = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {"Target": "a/requirements.txt", "Vulnerabilities": [_vulnerability()]},
                {"Target": "b/requirements.txt", "Vulnerabilities": [_vulnerability()]},
            ],
        }
    )

    result, _ = _run(repo, stdout=duplicated)

    # One vulnerability, so no "worst of N" clause at all.
    assert "known vulnerabilities" not in result.finding.description


def test_a_lockfile_under_tests_is_not_shipped_to_anybody(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_vulnerability(), target="tests/requirements.txt"))

    assert result.outcome is CheckOutcome.PASSED


# ---------------------------------------------------------------------------
# Every way it can fail
# ---------------------------------------------------------------------------


def test_no_sandbox_is_errored(repo: RepositoryIndex) -> None:
    set_sandbox(RefusingSandbox())

    result = trivy.scan_dependencies(CHECK, repo)

    assert result.outcome is CheckOutcome.ERRORED
    assert result.finding is None


def test_a_missing_database_is_errored_not_clean(repo: RepositoryIndex) -> None:
    """Trivy exits 1 when it has no vulnerability database. Reading that as a
    repository with no vulnerabilities is the failure this whole phase is built
    to avoid — and the exact shape of the bug Gitleaks shipped with."""
    result, _ = _run(repo, exit_code=1, stdout="", stderr="FATAL DB error: database not found")

    assert result.outcome is CheckOutcome.ERRORED


def test_a_timeout_is_errored(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, timed_out=True, exit_code=-1)

    assert result.outcome is CheckOutcome.ERRORED


def test_unparseable_output_is_errored(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout="not json")

    assert result.outcome is CheckOutcome.ERRORED


def test_truncated_output_is_errored(repo: RepositoryIndex) -> None:
    from app.utils.sandbox import MAX_OUTPUT_BYTES

    result, _ = _run(repo, stdout="x" * MAX_OUTPUT_BYTES)

    assert result.outcome is CheckOutcome.ERRORED


def test_the_tool_s_own_stderr_is_never_stored(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, exit_code=1, stderr="path /data/repos/scan-secret-looking/x")

    assert "scan-secret-looking" not in (result.reason or "")
