"""Tests for check-level results.

The contract this file defends is the one the whole design exists for: a
scanner must account for **every** check it declares, on every repository, and
must never call something "passed" that it did not actually look at.

That guarantee is why check results are objects rather than an inference. The
alternative considered was declaring checks and subtracting failures and skips
to get passes — cheaper, but forgetting one line would silently report a check
as passed. Here, a check that is not returned fails the parametrised test below
instead.
"""

from pathlib import Path

import pytest

from app.scanners.base import (
    CheckOutcome,
    RepositoryIndex,
    findings_of,
)
from app.scanners.registry import SCANNERS


def _write(root: Path, name: str, content: str = "x") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def service_repo(tmp_path: Path) -> Path:
    """A well-formed service, so most checks have something real to pass on."""
    _write(tmp_path, "README.md", "# service\n")
    # Telemetry included deliberately: without it the observability scanner is
    # right to report a finding, and a fixture called "healthy" that fails a
    # check tests the fixture rather than the code.
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\ndependencies = ["fastapi", "structlog", "sentry-sdk", "prometheus-client"]\n',
    )
    _write(tmp_path, "uv.lock", "")
    _write(tmp_path, ".gitignore", ".env\n")
    _write(tmp_path, "app/main.py", "import logging\n@app.get('/health')\ndef health(): ...\n")
    _write(tmp_path, "tests/test_main.py", "def test_health(): ...\n")
    _write(
        tmp_path, "Dockerfile", 'FROM python:3.14-slim\nUSER app\nHEALTHCHECK CMD true\nCMD ["x"]\n'
    )
    _write(tmp_path, ".github/workflows/ci.yml", "name: ci\n")
    return tmp_path


@pytest.fixture
def library_repo(tmp_path: Path) -> Path:
    """No framework, so every service-only check must skip rather than pass."""
    _write(tmp_path, "README.md", "# lib\n")
    _write(tmp_path, "lib.py", "def helper(): ...\n")
    return tmp_path


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    """Documentation only — nothing for a source check to examine."""
    _write(tmp_path, "README.md", "# docs\n")
    return tmp_path


ALL_SCANNERS = pytest.mark.parametrize(
    "scanner", SCANNERS.values(), ids=[name for name in SCANNERS]
)


class TestEveryCheckIsAccountedFor:
    """The guarantee that makes 'passed' trustworthy."""

    @ALL_SCANNERS
    @pytest.mark.parametrize("repo_name", ["service_repo", "library_repo", "bare_repo"])
    @pytest.mark.parametrize("framework", ["FastAPI", None])
    def test_every_declared_check_reports_exactly_once(
        self, scanner, repo_name: str, framework: str | None, request: pytest.FixtureRequest
    ) -> None:
        repo = request.getfixturevalue(repo_name)
        results = scanner.scan(RepositoryIndex.build(repo, framework=framework))

        reported = [result.id for result in results]
        assert sorted(reported) == sorted(check.id for check in scanner.CHECKS)
        assert len(reported) == len(set(reported)), "a check reported twice"

    @ALL_SCANNERS
    def test_check_ids_are_namespaced_by_category(self, scanner) -> None:
        """Ids are stored, so they have to be unique across scanners and stay
        recognisable in a mixed list."""
        for check in scanner.CHECKS:
            assert check.id.startswith(f"{scanner.category}."), check.id

    @ALL_SCANNERS
    def test_every_check_has_a_title(self, scanner) -> None:
        for check in scanner.CHECKS:
            assert check.title.strip()


class TestOutcomeInvariants:
    @ALL_SCANNERS
    @pytest.mark.parametrize("repo_name", ["service_repo", "library_repo", "bare_repo"])
    def test_a_failed_check_carries_its_finding(
        self, scanner, repo_name: str, request: pytest.FixtureRequest
    ) -> None:
        repo = request.getfixturevalue(repo_name)
        results = scanner.scan(RepositoryIndex.build(repo, framework="FastAPI"))

        for result in results:
            if result.outcome is CheckOutcome.FAILED:
                assert result.finding is not None, result.id
                assert result.finding.category == scanner.category

    @ALL_SCANNERS
    @pytest.mark.parametrize("repo_name", ["service_repo", "library_repo", "bare_repo"])
    def test_a_skipped_check_explains_why(
        self, scanner, repo_name: str, request: pytest.FixtureRequest
    ) -> None:
        """A skip with no reason is the same dead end as the silence this
        replaced."""
        repo = request.getfixturevalue(repo_name)
        results = scanner.scan(RepositoryIndex.build(repo, framework="FastAPI"))

        for result in results:
            if result.outcome is CheckOutcome.SKIPPED:
                assert result.reason, result.id

    @ALL_SCANNERS
    @pytest.mark.parametrize("repo_name", ["service_repo", "library_repo", "bare_repo"])
    def test_a_passed_check_carries_no_finding(
        self, scanner, repo_name: str, request: pytest.FixtureRequest
    ) -> None:
        repo = request.getfixturevalue(repo_name)
        results = scanner.scan(RepositoryIndex.build(repo, framework="FastAPI"))

        for result in results:
            if result.outcome is CheckOutcome.PASSED:
                assert result.finding is None, result.id
                assert result.reason is None, result.id


class TestPassedIsDistinctFromSkipped:
    """The distinction the type exists for, on the case that motivated it."""

    def test_a_library_skips_the_health_check_rather_than_passing_it(
        self, library_repo: Path
    ) -> None:
        scanner = SCANNERS["reliability"]

        results = scanner.scan(RepositoryIndex.build(library_repo, framework=None))
        health = next(r for r in results if r.id == "reliability.health")

        assert health.outcome is CheckOutcome.SKIPPED
        assert "serves traffic" in health.reason

    def test_a_service_with_a_health_endpoint_passes_it(self, service_repo: Path) -> None:
        scanner = SCANNERS["reliability"]

        results = scanner.scan(RepositoryIndex.build(service_repo, framework="FastAPI"))
        health = next(r for r in results if r.id == "reliability.health")

        assert health.outcome is CheckOutcome.PASSED

    def test_both_produce_no_finding_which_is_why_the_outcome_matters(
        self, library_repo: Path, service_repo: Path
    ) -> None:
        """Before check results these two were indistinguishable — both were an
        empty list, and a category scoring full marks could not say whether it
        had verified anything at all."""
        scanner = SCANNERS["reliability"]

        library = scanner.scan(RepositoryIndex.build(library_repo, framework=None))
        service = scanner.scan(RepositoryIndex.build(service_repo, framework="FastAPI"))

        assert not [f for f in findings_of(library) if "health" in f.title.lower()]
        assert not [f for f in findings_of(service) if "health" in f.title.lower()]
        assert (
            next(r for r in library if r.id == "reliability.health").outcome
            is not next(r for r in service if r.id == "reliability.health").outcome
        )


class TestScoringIsUnchanged:
    @ALL_SCANNERS
    @pytest.mark.parametrize("repo_name", ["service_repo", "library_repo", "bare_repo"])
    def test_findings_come_only_from_failed_checks(
        self, scanner, repo_name: str, request: pytest.FixtureRequest
    ) -> None:
        """Scoring reads findings_of() and nothing else, so the arithmetic is
        identical to before check results existed."""
        repo = request.getfixturevalue(repo_name)
        results = scanner.scan(RepositoryIndex.build(repo, framework="FastAPI"))

        failed = [r for r in results if r.outcome is CheckOutcome.FAILED]
        assert len(findings_of(results)) == len(failed)

    @ALL_SCANNERS
    def test_a_healthy_service_produces_no_findings(self, scanner, service_repo: Path) -> None:
        index = RepositoryIndex.build(service_repo, framework="FastAPI")

        assert findings_of(scanner.scan(index)) == []
