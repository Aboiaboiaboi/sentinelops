"""Tests for the observability scanner.

Two things carry the weight here: that evidence is picked up from *either* a
manifest or the source, and that a library is not asked for telemetry it has no
business owning.
"""

from pathlib import Path

import pytest

from app.scanners.base import RepositoryIndex, Severity
from app.scanners.observability import ObservabilityScanner

SCANNER = ObservabilityScanner()


def _scan(repo: Path, framework: str | None = "FastAPI"):
    return SCANNER.scan(RepositoryIndex.build(repo, framework=framework))


def _titles(repo: Path, framework: str | None = "FastAPI") -> set[str]:
    return {f.title for f in _scan(repo, framework)}


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    """Structured logging plus telemetry — nothing to report."""
    _write(
        tmp_path,
        "pyproject.toml",
        '[project]\ndependencies = ["structlog", "sentry-sdk", "prometheus-client"]\n',
    )
    _write(tmp_path, "app/main.py", "import logging\nlogger = logging.getLogger(__name__)\n")
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert _scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "x = 1\n")

        assert {f.category for f in _scan(tmp_path)} == {"observability"}

    def test_worst_case_equals_the_category_weight(self, tmp_path: Path) -> None:
        """Nothing logs and nothing is measured: the full 10."""
        _write(tmp_path, "app.py", "x = 1\n")

        assert sum(f.score_impact for f in _scan(tmp_path)) == 10

    def test_impacts_never_exceed_the_weight(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "print('hello')\n")

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 10


class TestLogging:
    def test_flags_a_project_that_logs_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "def main(): print('hi')\n")

        assert "No logging" in _titles(tmp_path)

    def test_is_the_most_severe_check(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "x = 1\n")

        finding = next(f for f in _scan(tmp_path) if f.title == "No logging")
        assert finding.severity is Severity.HIGH

    @pytest.mark.parametrize(
        "evidence",
        [
            "import logging",
            "logger.info('x')",
            "const log = require('winston')",
            'import "log/slog"',
        ],
    )
    def test_recognises_loggers_across_ecosystems(self, tmp_path: Path, evidence: str) -> None:
        _write(tmp_path, "app.py", evidence)

        assert "No logging" not in _titles(tmp_path)

    def test_a_mention_in_prose_is_not_evidence(self, tmp_path: Path) -> None:
        """A docstring saying a tool was deliberately *not* adopted used to read
        as proof it had been. This scanner reported itself clean because of it.
        """
        _write(
            tmp_path,
            "app.py",
            '"""We considered winston and logrus but adopted neither yet."""\ndef main(): ...\n',
        )

        assert "No logging" in _titles(tmp_path)

    def test_a_dependency_counts_as_evidence(self, tmp_path: Path) -> None:
        """A logger can be imported in one file out of five hundred, but it is
        always in the manifest."""
        _write(tmp_path, "package.json", '{"dependencies": {"pino": "^9"}}')
        _write(tmp_path, "app.js", "doWork();\n")

        assert "No logging" not in _titles(tmp_path)

    def test_applies_to_libraries_too(self, tmp_path: Path) -> None:
        """Unlike the other two checks — a library that logs nothing is still
        harder to operate."""
        _write(tmp_path, "lib.py", "def helper(): ...\n")

        assert "No logging" in _titles(tmp_path, framework="Python")


class TestStructuredLogging:
    def test_flags_plain_text_logging_in_a_service(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "import logging\nlogging.getLogger(__name__)\n")

        assert "Logs are not structured" in _titles(tmp_path, "FastAPI")

    @pytest.mark.parametrize(
        "evidence",
        [
            "import structlog",
            "handler.setFormatter(JsonFormatter())",
            "from pythonjsonlogger import jsonlogger",
        ],
    )
    def test_accepts_structured_output(self, tmp_path: Path, evidence: str) -> None:
        """An import or a call site — a comment naming the library is not use."""
        _write(tmp_path, "app.py", f"import logging\n{evidence}\n")

        assert "Logs are not structured" not in _titles(tmp_path)

    def test_a_declared_dependency_counts(self, tmp_path: Path) -> None:
        _write(tmp_path, "pyproject.toml", '[project]\ndependencies = ["python-json-logger"]\n')
        _write(tmp_path, "app.py", "import logging\n")

        assert "Logs are not structured" not in _titles(tmp_path)

    def test_is_not_reported_when_there_is_no_logging_at_all(self, tmp_path: Path) -> None:
        """Two findings restating the same absence is noise — "unstructured" is
        not a meaningful complaint about a project that logs nothing."""
        _write(tmp_path, "app.py", "x = 1\n")

        titles = _titles(tmp_path)
        assert "No logging" in titles
        assert "Logs are not structured" not in titles

    @pytest.mark.parametrize("framework", [None, "Python", "Rust"])
    def test_is_not_asked_of_a_library(self, tmp_path: Path, framework: str | None) -> None:
        """A library's log format is the host application's business."""
        _write(tmp_path, "lib.py", "import logging\nlogger = logging.getLogger(__name__)\n")

        assert "Logs are not structured" not in _titles(tmp_path, framework)


class TestTelemetry:
    def test_flags_a_service_with_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "import logging\nimport structlog\n")

        assert "No metrics or error tracking" in _titles(tmp_path, "FastAPI")

    @pytest.mark.parametrize(
        "evidence", ["prometheus_client", "opentelemetry", "sentry_sdk", "datadog", "newrelic"]
    )
    def test_accepts_any_of_the_common_tools(self, tmp_path: Path, evidence: str) -> None:
        _write(tmp_path, "app.py", f"import logging\nimport structlog\nimport {evidence}\n")

        assert "No metrics or error tracking" not in _titles(tmp_path)

    @pytest.mark.parametrize("framework", [None, "Python", "Go"])
    def test_is_not_asked_of_a_library(self, tmp_path: Path, framework: str | None) -> None:
        """Telemetry belongs to whatever embeds the library, not the library."""
        _write(tmp_path, "lib.py", "import logging\n")

        assert "No metrics or error tracking" not in _titles(tmp_path, framework)


class TestRobustness:
    def test_test_files_are_not_evidence(self, tmp_path: Path) -> None:
        """A test suite's logging setup says nothing about the service's."""
        _write(tmp_path, "app/main.py", "def main(): ...\n")
        _write(
            tmp_path, "tests/test_main.py", "import logging\nimport structlog\nimport sentry_sdk\n"
        )

        assert "No logging" in _titles(tmp_path)

    def test_vendored_code_is_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/main.py", "def main(): ...\n")
        _write(tmp_path, "node_modules/pkg/index.js", "const pino = require('pino')\n")

        assert "No logging" in _titles(tmp_path)

    def test_an_empty_repository_does_not_raise(self, tmp_path: Path) -> None:
        _scan(tmp_path)

    def test_a_binary_file_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "blob.py").write_bytes(b"\xff\xfe\x00\x01")

        _scan(tmp_path)
