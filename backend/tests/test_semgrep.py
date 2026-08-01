"""Tests for the Semgrep wrapper.

Report shapes are taken from real Semgrep 1.171 output, including the two
details that only show up when you run it for real: `check_id` carries a
`cache.semgrep.` prefix because the config is a file path, and `paths.scanned`
is what distinguishes "nothing to look at" from "looked, found nothing".

Exit codes were measured first: findings and no findings both exit 0, a missing
ruleset exits 7, an unreadable target exits 2. Zero is the only success.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from app.scanners.base import CheckOutcome, CheckSpec, RepositoryIndex
from app.scanners.security.tools import semgrep
from app.utils.sandbox import SandboxResult, SandboxSpec, SandboxUnavailable, set_sandbox

CHECK = CheckSpec("security.code_patterns", "No dangerous code patterns")

REPO_MOUNT = "/repo"

RULE = "cache.semgrep.python.lang.security.audit.subprocess-shell-true.subprocess-shell-true"


def _match(path: str = "app.py", severity: str = "ERROR", rule: str = RULE) -> dict[str, Any]:
    return {
        "check_id": rule,
        "path": f"{REPO_MOUNT}/{path}",
        "start": {"line": 12, "col": 38},
        "end": {"line": 12, "col": 42},
        "extra": {
            "message": "Found 'subprocess' function 'run' with 'shell=True'.",
            "severity": severity,
            "metadata": {"category": "security", "impact": "LOW"},
        },
    }


def _report(*matches: dict[str, Any], scanned: tuple[str, ...] = ("app.py",)) -> str:
    return json.dumps(
        {
            "version": "1.171.0",
            "results": list(matches),
            "errors": [],
            "paths": {"scanned": [f"{REPO_MOUNT}/{name}" for name in scanned]},
        }
    )


NOTHING_SCANNED = json.dumps(
    {"version": "1.171.0", "results": [], "errors": [], "paths": {"scanned": []}}
)


class FakeSandbox:
    def __init__(self, **result: Any) -> None:
        self.spec: SandboxSpec | None = None
        self._result = {
            "exit_code": 0,
            "stdout": _report(),
            "stderr": "",
            "timed_out": False,
            "repo_mount": REPO_MOUNT,
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
    """One production file, so a match against it is kept."""
    (tmp_path / "app.py").write_text("import subprocess\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("import subprocess\n", encoding="utf-8")
    return RepositoryIndex.build(tmp_path, framework="FastAPI")


def _run(repo: RepositoryIndex, **result: Any):
    sandbox = FakeSandbox(**result)
    set_sandbox(sandbox)
    return semgrep.scan_code_patterns(CHECK, repo), sandbox


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def test_the_ruleset_comes_from_the_cache_not_the_registry(repo: RepositoryIndex) -> None:
    """Semgrep caches nothing and re-fetches registry rules every run, which a
    container with no network cannot do."""
    command = _run(repo)[1].spec.command

    assert command[command.index("--config") + 1] == semgrep.RULES_PATH
    assert "p/security-audit" not in command


def test_home_is_set_or_semgrep_dies_before_scanning(repo: RepositoryIndex) -> None:
    """It writes a settings file at startup; with no HOME that resolves to
    /.semgrep on the read-only root and raises OSError."""
    _, sandbox = _run(repo)

    assert ("HOME", "/tmp") in sandbox.spec.environment


def test_it_makes_no_network_calls(repo: RepositoryIndex) -> None:
    command = _run(repo)[1].spec.command

    assert "--metrics=off" in command
    assert "--disable-version-check" in command


def test_it_demands_the_warmed_cache(repo: RepositoryIndex) -> None:
    _, sandbox = _run(repo)

    assert sandbox.spec.needs_cache is True


def test_the_image_is_pinned(repo: RepositoryIndex) -> None:
    assert ":" in semgrep.IMAGE
    assert not semgrep.IMAGE.endswith(":latest")


def test_it_is_given_the_longest_timeout_of_the_three_tools(repo: RepositoryIndex) -> None:
    """It parses every file and runs 225 rules over the AST."""
    from app.scanners.security.tools import gitleaks, trivy

    assert semgrep.TIMEOUT_SECONDS > trivy.TIMEOUT_SECONDS > gitleaks.TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_a_repository_with_no_source_is_skipped_without_starting_a_container(
    tmp_path: Path,
) -> None:
    """Caught by running it: Semgrep counts a README as a scanned path, so its
    own report cannot tell "there is no code here" from "the code is fine". A
    documentation-only repository was passing this check."""
    (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")
    docs_only = RepositoryIndex.build(tmp_path, framework=None)

    result, sandbox = _run(docs_only)

    assert result.outcome is CheckOutcome.SKIPPED
    assert result.reason == semgrep.NO_SOURCE
    assert sandbox.spec is None, "no container should have been started"


def test_source_in_an_uncovered_language_is_skipped(repo: RepositoryIndex) -> None:
    """There is code, Semgrep just has no rules that read it."""
    result, _ = _run(repo, stdout=NOTHING_SCANNED)

    assert result.outcome is CheckOutcome.SKIPPED
    assert result.reason == semgrep.NO_SUPPORTED_SOURCE


def test_scanned_with_no_matches_is_a_pass(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report())

    assert result.outcome is CheckOutcome.PASSED


def test_an_error_severity_match_fails_the_check(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_match()))

    assert result.outcome is CheckOutcome.FAILED
    assert result.finding is not None


def test_the_finding_names_the_file_and_the_rule_in_words(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_match()))

    assert "app.py" in result.finding.description
    assert "subprocess shell true" in result.finding.description


def test_the_cache_prefix_never_reaches_a_reader(repo: RepositoryIndex) -> None:
    """`--config <file>` makes Semgrep derive rule ids from the config's path,
    so every id arrives prefixed with our own cache layout."""
    result, _ = _run(repo, stdout=_report(_match()))

    assert "cache.semgrep" not in result.finding.description


# ---------------------------------------------------------------------------
# Noise control — the reason this check exists at ERROR only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["WARNING", "INFO"])
def test_advisory_severities_are_not_reported(repo: RepositoryIndex, severity: str) -> None:
    """p/security-audit at full volume is mostly "consider whether" advice, and
    noise is how a scanner earns a reputation for crying wolf."""
    result, _ = _run(repo, stdout=_report(_match(severity=severity)))

    assert result.outcome is CheckOutcome.PASSED


def test_a_match_in_a_test_file_is_a_fixture(repo: RepositoryIndex) -> None:
    """A deliberate shell=True inside the test suite is the test suite doing
    its job."""
    result, _ = _run(repo, stdout=_report(_match(path="tests/test_app.py")))

    assert result.outcome is CheckOutcome.PASSED


def test_a_match_in_a_file_that_is_not_source_is_ignored(repo: RepositoryIndex) -> None:
    """Filtered against the index's production files, which also excludes
    machine-generated code — 'split this generated client' is not advice."""
    result, _ = _run(repo, stdout=_report(_match(path="vendor/generated_client.py")))

    assert result.outcome is CheckOutcome.PASSED


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def test_a_single_pattern_costs_less_than_the_whole_budget(repo: RepositoryIndex) -> None:
    result, _ = _run(repo, stdout=_report(_match()))

    assert 0 < result.finding.score_impact < semgrep.BUDGET


def test_many_patterns_reach_the_budget_but_never_exceed_it(repo: RepositoryIndex) -> None:
    assert semgrep.score_impact([("rule", "a.py")] * 50) == semgrep.BUDGET


def test_one_finding_however_many_matches(repo: RepositoryIndex) -> None:
    matches = [_match(path="app.py") for _ in range(12)]

    result, _ = _run(repo, stdout=_report(*matches))

    assert "12 matches" in result.finding.description


# ---------------------------------------------------------------------------
# Every way it can fail
# ---------------------------------------------------------------------------


def test_no_sandbox_is_errored(repo: RepositoryIndex) -> None:
    set_sandbox(RefusingSandbox())

    result = semgrep.scan_code_patterns(CHECK, repo)

    assert result.outcome is CheckOutcome.ERRORED
    assert result.finding is None


@pytest.mark.parametrize(("exit_code", "why"), [(7, "missing ruleset"), (2, "unreadable target")])
def test_a_failed_run_is_errored_not_clean(repo: RepositoryIndex, exit_code: int, why: str) -> None:
    result, _ = _run(repo, exit_code=exit_code, stdout="")

    assert result.outcome is CheckOutcome.ERRORED, why


def test_a_timeout_is_errored(repo: RepositoryIndex) -> None:
    """Semgrep is the slowest of the three, so this is the likeliest to happen —
    and a slow scan must not read as a clean one."""
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
    result, _ = _run(repo, exit_code=2, stderr="/data/repos/scan-abc/repo/secret.py")

    assert "scan-abc" not in (result.reason or "")
