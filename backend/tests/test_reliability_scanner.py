"""Tests for the reliability scanner.

These checks are text patterns, not real analysis, so the false-positive cases
matter more than the detections. A scanner that shouts at a healthy repository
gets ignored, and then it catches nothing at all.
"""

from pathlib import Path

import pytest

from app.scanners.base import RepositoryIndex, Severity
from app.scanners.reliability import ReliabilityScanner

SCANNER = ReliabilityScanner()


def _scan(repo: Path, framework: str | None = "FastAPI"):
    return SCANNER.scan(RepositoryIndex.build(repo, framework=framework))


def _titles(repo: Path, framework: str | None = "FastAPI") -> set[str]:
    return {f.title for f in _scan(repo, framework)}


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


HEALTHY = """
import httpx
from tenacity import retry

@app.get("/health")
def health():
    return {"status": "ok"}

@retry
def fetch_user(user_id):
    try:
        return httpx.get(f"/users/{user_id}", timeout=5).json()
    except httpx.HTTPError as exc:
        logger.warning("lookup failed", exc_info=exc)
        raise
"""


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    _write(tmp_path, "app/main.py", HEALTHY)
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert _scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "import requests\nrequests.get(url)\nexcept:\n")

        assert {f.category for f in _scan(tmp_path)} == {"reliability"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app.py",
            "import requests\n"
            "def f():\n"
            "    try:\n"
            "        requests.get(url)\n"
            "    except:\n"
            "        pass\n",
        )

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 20

    def test_an_empty_repository_produces_nothing_but_health(self, tmp_path: Path) -> None:
        """Nothing to analyse means nothing to report, beyond the service-level
        question of whether it can say it is alive."""
        assert _titles(tmp_path) <= {"No health endpoint"}


class TestHealthEndpoint:
    def test_flags_a_service_without_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/main.py", "@app.get('/users')\ndef users(): ...\n")

        assert "No health endpoint" in _titles(tmp_path, "FastAPI")

    def test_is_high_severity(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/main.py", "@app.get('/users')\ndef users(): ...\n")

        finding = next(f for f in _scan(tmp_path) if f.title == "No health endpoint")
        assert finding.severity is Severity.HIGH

    @pytest.mark.parametrize("route", ["/health", "/healthz", "/readyz", "/livez", "/ping"])
    def test_accepts_the_usual_route_names(self, tmp_path: Path, route: str) -> None:
        _write(tmp_path, "app/main.py", f"@app.get('{route}')\ndef probe(): ...\n")

        assert "No health endpoint" not in _titles(tmp_path)

    def test_accepts_a_kubernetes_probe(self, tmp_path: Path) -> None:
        """A probe declared in a manifest is just as good as one in code."""
        _write(tmp_path, "app/main.py", "@app.get('/users')\ndef users(): ...\n")
        _write(tmp_path, "k8s/deploy.yaml", "spec:\n  livenessProbe:\n    httpGet: {}\n")

        assert "No health endpoint" not in _titles(tmp_path)

    @pytest.mark.parametrize("framework", [None, "Python", "Rust", "Go"])
    def test_is_not_asked_of_a_library_or_cli(self, tmp_path: Path, framework: str | None) -> None:
        """A CLI tool with no health endpoint is correct, not broken."""
        _write(tmp_path, "cli.py", "def main(): ...\n")

        assert "No health endpoint" not in _titles(tmp_path, framework)


class TestTimeouts:
    @pytest.mark.parametrize(
        "call",
        [
            "requests.get(url)",
            "httpx.post(url, json=body)",
            "axios.get(url)",
            "fetch(url)",
            "http.Get(url)",
        ],
    )
    def test_flags_calls_with_no_timeout(self, tmp_path: Path, call: str) -> None:
        _write(tmp_path, "client.py", f"/health\n{call}\n")

        assert "Network calls without timeouts" in _titles(tmp_path)

    @pytest.mark.parametrize(
        "call",
        [
            "requests.get(url, timeout=5)",
            "httpx.get(url, timeout=httpx.Timeout(5))",
            "fetch(url, { signal: AbortSignal.timeout(5000) })",
        ],
    )
    def test_accepts_an_explicit_timeout(self, tmp_path: Path, call: str) -> None:
        _write(tmp_path, "client.py", f"/health\n{call}\n")

        assert "Network calls without timeouts" not in _titles(tmp_path)

    def test_a_timeout_configured_elsewhere_in_the_file_counts(self, tmp_path: Path) -> None:
        """A client configured once at the top covers every call below it —
        flagging those would be wrong."""
        _write(
            tmp_path,
            "client.py",
            "/health\nsession = httpx.Client(timeout=5)\n"
            "def a(): return session.get('/a')\n"
            "def b(): return httpx.get('/b')\n",
        )

        assert "Network calls without timeouts" not in _titles(tmp_path)

    def test_a_repository_making_no_calls_is_not_flagged(self, tmp_path: Path) -> None:
        _write(tmp_path, "math.py", "/health\ndef add(a, b): return a + b\n")

        assert "Network calls without timeouts" not in _titles(tmp_path)


class TestRetries:
    def test_flags_outbound_calls_with_no_retry_handling(self, tmp_path: Path) -> None:
        _write(tmp_path, "client.py", "/health\nrequests.get(url, timeout=5)\n")

        assert "No retry handling for outbound calls" in _titles(tmp_path)

    @pytest.mark.parametrize(
        "evidence", ["from tenacity import retry", "import backoff", "max_retries=3"]
    )
    def test_accepts_retry_evidence(self, tmp_path: Path, evidence: str) -> None:
        _write(tmp_path, "client.py", f"/health\n{evidence}\nrequests.get(url, timeout=5)\n")

        assert "No retry handling for outbound calls" not in _titles(tmp_path)

    def test_is_not_asked_of_a_project_that_talks_to_nothing(self, tmp_path: Path) -> None:
        """A project with no outbound calls has nothing to retry."""
        _write(tmp_path, "math.py", "/health\ndef add(a, b): return a + b\n")

        assert "No retry handling for outbound calls" not in _titles(tmp_path)


class TestSwallowedErrors:
    def test_flags_a_bare_except(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "/health\ntry:\n    work()\nexcept:\n    pass\n")

        assert "Errors are silently discarded" in _titles(tmp_path)

    def test_flags_except_pass(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "/health\ntry:\n    work()\nexcept ValueError:\n    pass\n")

        assert "Errors are silently discarded" in _titles(tmp_path)

    def test_flags_an_empty_javascript_catch(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.js", "// /health\ntry { work(); } catch (e) {}\n")

        assert "Errors are silently discarded" in _titles(tmp_path)

    def test_accepts_a_handled_exception(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "app.py",
            "/health\n"
            "try:\n"
            "    work()\n"
            "except ValueError as exc:\n"
            "    logger.warning('failed', exc_info=exc)\n",
        )

        assert "Errors are silently discarded" not in _titles(tmp_path)

    def test_accepts_a_deliberate_reraise(self, tmp_path: Path) -> None:
        _write(tmp_path, "app.py", "/health\ntry:\n    work()\nexcept ValueError:\n    raise\n")

        assert "Errors are silently discarded" not in _titles(tmp_path)


class TestRobustness:
    def test_vendored_code_is_ignored(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/main.py", HEALTHY)
        _write(tmp_path, "node_modules/pkg/index.js", "fetch(url)\ntry{}catch(e){}\n")

        assert _scan(tmp_path) == []

    def test_a_binary_file_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "blob.py").write_bytes(b"\xff\xfe\x00\x01")

        _scan(tmp_path)

    def test_reports_once_per_problem_not_once_per_file(self, tmp_path: Path) -> None:
        """Twenty findings saying the same thing is not twenty times as useful."""
        for index in range(5):
            _write(tmp_path, f"client{index}.py", "/health\nrequests.get(url)\n")

        matching = [f for f in _scan(tmp_path) if f.title == "Network calls without timeouts"]
        assert len(matching) == 1
        assert "4 other files" in matching[0].description


class TestProductionCodeOnly:
    """Reliability is a property of what ships, not of the test suite."""

    def test_ignores_swallowed_errors_in_tests(self, tmp_path: Path) -> None:
        """A test that deliberately swallows an exception is fine — and a test
        file containing `except: pass` inside a fixture string is not code at
        all. This scanner reported itself before the exclusion existed."""
        _write(tmp_path, "app/main.py", HEALTHY)
        _write(tmp_path, "tests/test_thing.py", "try:\n    work()\nexcept:\n    pass\n")

        assert "Errors are silently discarded" not in _titles(tmp_path)

    def test_ignores_untimed_calls_in_tests(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/main.py", HEALTHY)
        _write(tmp_path, "tests/test_client.py", "requests.get(url)\n")

        assert "Network calls without timeouts" not in _titles(tmp_path)

    def test_still_reports_problems_in_production_code(self, tmp_path: Path) -> None:
        """The exclusion must not become a blanket amnesty."""
        _write(tmp_path, "app/main.py", "/health\ntry:\n    work()\nexcept:\n    pass\n")

        assert "Errors are silently discarded" in _titles(tmp_path)
