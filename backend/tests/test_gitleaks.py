"""Tests for the Gitleaks wrapper.

Driven by a fake sandbox returning captured Gitleaks output, so the whole
translation — report to outcome — is tested without a Docker daemon. The one
thing a fake cannot verify is that the command is the one Gitleaks actually
accepts, so that is asserted against the real CLI's grammar here (`dir`, not the
removed `detect`) and exercised for real in test_sandbox.py's integration test.

Every failure mode maps to ERRORED rather than PASSED. That is the whole point:
a secret scanner that could not run has not established that a repository is
clean, and saying otherwise is worse than saying nothing.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.scanners.base import CheckOutcome, CheckSpec, RepositoryIndex
from app.scanners.security.tools import gitleaks
from app.utils.sandbox import SandboxResult, SandboxSpec, SandboxUnavailable, set_sandbox

CHECK = CheckSpec("security.hardcoded_secrets", "No secrets hardcoded in source")

REPO_MOUNT = "/repo"


def _leak(rule: str, file: str, line: int = 1) -> dict[str, Any]:
    """One entry in Gitleaks' JSON report, shaped like the real thing.

    `Secret` and `Match` are REDACTED because the wrapper runs it with --redact:
    the value never reaches us, which is what keeps it out of the database.
    """
    return {
        "RuleID": rule,
        "Description": "Identified a secret.",
        "StartLine": line,
        "File": f"{REPO_MOUNT}/{file}",
        "Match": "REDACTED",
        "Secret": "REDACTED",
        "Entropy": 4.2,
        "Fingerprint": f"{REPO_MOUNT}/{file}:{rule}:{line}",
    }


class FakeSandbox:
    """Returns a canned result, and remembers the spec it was given."""

    def __init__(self, **result: Any) -> None:
        self.spec: SandboxSpec | None = None
        self.repo_path: Path | None = None
        self._result = {
            "exit_code": 0,
            "stdout": "[]",
            "stderr": "",
            "timed_out": False,
            "repo_mount": REPO_MOUNT,
        } | result

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        self.spec = spec
        self.repo_path = repo_path
        return SandboxResult(**self._result)


class RefusingSandbox:
    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        raise SandboxUnavailable("no sandbox is configured")


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
    return gitleaks.scan_for_secrets(CHECK, repo), sandbox


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_it_runs_dir_not_the_removed_detect_command(repo: RepositoryIndex) -> None:
    """`detect` was removed in v8.19. On v8.30 it is a usage error, which this
    wrapper would report as an errored check on every scan, forever."""
    _, sandbox = _run(repo)

    assert sandbox.spec is not None
    assert sandbox.spec.command[0] == "dir"
    assert "detect" not in sandbox.spec.command


def test_secrets_are_redacted_before_they_reach_us(repo: RepositoryIndex) -> None:
    """The finding is that a credential is committed. The credential itself has
    no business in our logs or our database."""
    _, sandbox = _run(repo)

    assert "--redact" in sandbox.spec.command


def test_the_report_comes_back_on_stdout(repo: RepositoryIndex) -> None:
    """The container's filesystem is read-only, so there is nowhere to write a
    report file to."""
    command = _run(repo)[1].spec.command

    assert command[command.index("--report-path") + 1] == "-"


def test_the_image_is_pinned(repo: RepositoryIndex) -> None:
    assert ":" in gitleaks.IMAGE
    assert not gitleaks.IMAGE.endswith(":latest")


def test_it_needs_no_warmed_cache(repo: RepositoryIndex) -> None:
    """Gitleaks carries its own rules, so it must keep working while the
    vulnerability database is still downloading."""
    _, sandbox = _run(repo)

    assert sandbox.spec.needs_cache is False


def test_it_scans_the_checkout_it_was_given(repo: RepositoryIndex) -> None:
    _, sandbox = _run(repo)

    assert sandbox.repo_path == repo.path


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_an_empty_report_is_a_pass(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout="[]", exit_code=0)

    assert result.outcome is CheckOutcome.PASSED
    assert result.finding is None


def test_finding_leaks_is_not_signalled_as_an_error(repo: RepositoryIndex) -> None:
    """--exit-code 0 makes a leak-finding run exit zero, so a non-zero exit can
    mean exactly one thing: the tool failed."""
    command = _run(repo)[1].spec.command

    assert command[command.index("--exit-code") + 1] == "0"


def test_leaks_are_reported_from_a_zero_exit(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=json.dumps([_leak("aws-access-token", "conf.py")]), exit_code=0)

    assert result.outcome is CheckOutcome.FAILED
    assert result.finding is not None


def test_a_failed_run_is_never_read_as_a_clean_repository(repo: RepositoryIndex) -> None:
    """The bug this test exists for, found by scanning a real repository.

    The clone sat in a 0700 directory that the sandbox user could not enter.
    Gitleaks exited 1 with an empty report — the same exit code its default
    configuration uses for "leaks found" — and the wrapper read the empty report
    as a repository with no secrets. A tool that saw nothing said everything was
    fine.
    """
    result, _ = _run(repo, exit_code=1, stdout="", stderr="FTL stat /data/...: permission denied")

    assert result.outcome is CheckOutcome.ERRORED
    assert result.outcome is not CheckOutcome.PASSED


def test_a_leak_costs_the_whole_check_budget(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=json.dumps([_leak("aws-access-token", "conf.py")]))

    assert result.finding.score_impact == gitleaks.BUDGET


def test_the_finding_names_the_file_and_the_kind(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=json.dumps([_leak("aws-access-token", "src/conf.py")]))

    assert "src/conf.py" in result.finding.description
    # Rule ids are kebab-case machine names; a person reads words.
    assert "aws access token" in result.finding.description


def test_many_leaks_produce_one_finding(repo: RepositoryIndex) -> None:
    """A repository with 200 leaked keys must not produce 200 findings."""
    report = [_leak("generic-api-key", f"file{index}.py", index) for index in range(200)]

    result, _ = _run(repo, stdout=json.dumps(report))

    assert result.outcome is CheckOutcome.FAILED
    assert "200 places" in result.finding.description


def test_the_headline_is_the_most_prevalent_kind(repo: RepositoryIndex) -> None:
    """Not whatever Gitleaks happened to print first."""
    report = [_leak("slack-token", "a.py")] + [
        _leak("generic-api-key", f"b{index}.py", index) for index in range(5)
    ]

    result, _ = _run(repo, stdout=json.dumps(report))

    assert "generic api key" in result.finding.description.split("Detected:")[0]


def test_only_three_kinds_are_named(repo: RepositoryIndex) -> None:
    report = [_leak(f"rule-{index}", f"f{index}.py") for index in range(6)]

    result, _ = _run(repo, stdout=json.dumps(report))

    assert "3 other kinds" in result.finding.description


def test_the_recommendation_leads_with_rotation(repo: RepositoryIndex) -> None:
    """Removing a committed credential without rotating it fixes nothing — the
    old value is still in history."""
    result, _ = _run(repo, stdout=json.dumps([_leak("aws-access-token", "conf.py")]))

    assert result.finding.recommendation.lower().startswith("rotate")


# ---------------------------------------------------------------------------
# False positives
# ---------------------------------------------------------------------------


def test_fixtures_in_test_files_are_not_leaks(repo: RepositoryIndex) -> None:
    """Measured, not assumed: against a fresh clone of SentinelOps, Gitleaks
    reports seven leaks and all seven are fixtures in the security scanner's own
    test file. A tool that flags a project's test fixtures gets muted."""
    report = [
        _leak("private-key", "backend/tests/test_security_scanner.py"),
        _leak("gcp-api-key", "backend/tests/test_security_scanner.py", 220),
    ]

    result, _ = _run(repo, stdout=json.dumps(report))

    assert result.outcome is CheckOutcome.PASSED


def test_a_leak_outside_tests_still_counts(repo: RepositoryIndex) -> None:
    report = [
        _leak("private-key", "backend/tests/test_security_scanner.py"),
        _leak("aws-access-token", "backend/app/config.py"),
    ]

    result, _ = _run(repo, stdout=json.dumps(report))

    assert result.outcome is CheckOutcome.FAILED
    assert "backend/app/config.py" in result.finding.description


def test_paths_are_reported_relative_to_the_repository(repo: RepositoryIndex) -> None:
    """Under a named volume the container sees the clone's real path, not
    /repo. Neither belongs in a finding somebody reads."""
    report = [
        {
            **_leak("aws-access-token", "conf.py"),
            "File": "/data/repos/scan-abc/repo/conf.py",
        }
    ]

    result, _ = _run(repo, stdout=json.dumps(report), repo_mount="/data/repos/scan-abc/repo")

    assert "conf.py" in result.finding.description
    assert "/data/repos" not in result.finding.description


# ---------------------------------------------------------------------------
# Every way it can fail
# ---------------------------------------------------------------------------


def test_no_sandbox_is_errored_not_passed(repo: RepositoryIndex) -> None:
    set_sandbox(RefusingSandbox())

    result = gitleaks.scan_for_secrets(CHECK, repo)

    assert result.outcome is CheckOutcome.ERRORED
    assert result.finding is None
    assert result.reason


def test_a_timeout_is_errored(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, timed_out=True, exit_code=-1)

    assert result.outcome is CheckOutcome.ERRORED


def test_an_unexpected_exit_code_is_errored(repo: RepositoryIndex) -> None:
    """Exit 2 is a usage error — what running the removed `detect` command
    produces. It must never be read as 'no leaks'."""
    result, _ = _run(repo, exit_code=2, stdout="", stderr="unknown command")

    assert result.outcome is CheckOutcome.ERRORED


def test_unparseable_output_is_errored(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout="not json at all", exit_code=0)

    assert result.outcome is CheckOutcome.ERRORED


def test_truncated_output_is_errored(repo: RepositoryIndex) -> None:
    """Half a JSON document parses as nothing, and the half that is missing is
    exactly the part that might have held a leak."""
    from app.utils.sandbox import MAX_OUTPUT_BYTES

    result, _ = _run(repo, stdout="x" * MAX_OUTPUT_BYTES)

    assert result.outcome is CheckOutcome.ERRORED


def test_an_errored_check_never_carries_a_finding(repo: RepositoryIndex) -> None:
    """A check that did not finish has not established that anything is wrong,
    and deducting points would charge the repository for our outage."""
    result, _ = _run(repo, exit_code=2)

    assert result.finding is None


def test_the_tool_s_own_stderr_is_never_stored(repo: RepositoryIndex) -> None:
    """It is text produced by a program reading an arbitrary repository. It goes
    to the log and no further; what is stored is fixed text we chose."""
    result, _ = _run(repo, exit_code=2, stderr="secret-looking-value-from-the-repo")

    assert "secret-looking-value-from-the-repo" not in (result.reason or "")
