"""Tests for the deployment scanner.

The multi-stage Dockerfile cases carry the most weight. A builder stage doing
things that would be alarming in a shipped image is normal, and flagging it
would train people to ignore the scanner.
"""

from pathlib import Path

import pytest

from app.scanners.base import Severity
from app.scanners.deployment import DeploymentScanner

SCANNER = DeploymentScanner()


def _titles(repo: Path) -> set[str]:
    return {f.title for f in SCANNER.scan(repo)}


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


GOOD_DOCKERFILE = """
FROM python:3.14-slim AS builder
RUN pip install build

FROM python:3.14-slim AS runtime
RUN useradd app
COPY --from=builder /app /app
USER app
HEALTHCHECK CMD ["python", "-c", "print(1)"]
CMD ["python", "-m", "app"]
"""


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    """A repository that should produce no findings at all."""
    _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)
    _write(tmp_path, ".github/workflows/ci.yml", "name: ci\n")
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert SCANNER.scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        assert {f.category for f in SCANNER.scan(tmp_path)} == {"deployment"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        """A repository failing everything scores the category zero, not below."""
        _write(tmp_path, "Dockerfile", 'FROM python\nCMD ["python"]\n')

        assert sum(f.score_impact for f in SCANNER.scan(tmp_path)) <= 15

    def test_the_worst_case_with_no_config_also_fits(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md", "# docs\n")

        assert sum(f.score_impact for f in SCANNER.scan(tmp_path)) <= 15


class TestDeploymentConfig:
    def test_flags_a_repository_with_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.py", "print(1)\n")

        assert "No deployment configuration" in _titles(tmp_path)

    def test_is_high_severity(self, tmp_path: Path) -> None:
        finding = next(f for f in SCANNER.scan(tmp_path) if f.title.startswith("No deployment"))

        assert finding.severity is Severity.HIGH

    @pytest.mark.parametrize(
        "name",
        ["Dockerfile", "dockerfile", "Dockerfile.prod", "api.dockerfile"],
    )
    def test_recognises_dockerfile_spellings(self, tmp_path: Path, name: str) -> None:
        _write(tmp_path, name, GOOD_DOCKERFILE)

        assert "No deployment configuration" not in _titles(tmp_path)

    @pytest.mark.parametrize(
        "path",
        ["docker-compose.yml", "compose.yaml", "k8s/deployment.yaml", "charts/app/Chart.yaml"],
    )
    def test_orchestration_counts_even_without_a_dockerfile(
        self, tmp_path: Path, path: str
    ) -> None:
        """The image may legitimately be built elsewhere."""
        _write(tmp_path, path, "kind: Deployment\n")

        assert "No deployment configuration" not in _titles(tmp_path)

    def test_dockerfile_checks_are_skipped_when_there_is_none(self, tmp_path: Path) -> None:
        """Otherwise a repo with no Dockerfile collects four findings all saying
        the same thing."""
        _write(tmp_path, "k8s/deployment.yaml", "kind: Deployment\n")

        assert "Container runs as root" not in _titles(tmp_path)
        assert "Base image is not pinned" not in _titles(tmp_path)


class TestBaseImagePinning:
    @pytest.mark.parametrize("base", ["python", "python:latest", "ghcr.io/org/app:latest"])
    def test_flags_a_floating_tag(self, tmp_path: Path, base: str) -> None:
        _write(tmp_path, "Dockerfile", f'FROM {base}\nUSER app\nCMD ["x"]\n')

        assert "Base image is not pinned" in _titles(tmp_path)

    @pytest.mark.parametrize(
        "base",
        [
            "python:3.14-slim",
            "ghcr.io/org/app:1.2.3",
            "python@sha256:abc123",
            "registry:5000/app:2",
        ],
    )
    def test_accepts_a_pinned_reference(self, tmp_path: Path, base: str) -> None:
        _write(tmp_path, "Dockerfile", f'FROM {base}\nUSER app\nCMD ["x"]\n')

        assert "Base image is not pinned" not in _titles(tmp_path)

    def test_a_stage_reference_is_not_an_unpinned_image(self, tmp_path: Path) -> None:
        """`FROM builder` inherits whatever that stage resolved to — it is
        neither pinned nor unpinned."""
        _write(
            tmp_path,
            "Dockerfile",
            "FROM python:3.14-slim AS builder\nRUN true\n"
            'FROM builder AS final\nUSER app\nCMD ["x"]\n',
        )

        assert "Base image is not pinned" not in _titles(tmp_path)


class TestRunsAsRoot:
    def test_flags_a_runnable_stage_with_no_user(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nCMD ["python"]\n')

        assert "Container runs as root" in _titles(tmp_path)

    def test_ignores_a_builder_stage(self, tmp_path: Path) -> None:
        """A builder runs as root routinely and is thrown away."""
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)

        assert "Container runs as root" not in _titles(tmp_path)

    def test_flags_switching_back_to_root(self, tmp_path: Path) -> None:
        """The last USER wins — installing something as root and never dropping
        back means the process runs as root."""
        _write(
            tmp_path,
            "Dockerfile",
            'FROM python:3.14-slim\nUSER app\nUSER root\nRUN apt-get update\nCMD ["x"]\n',
        )

        assert "Container runs as root" in _titles(tmp_path)

    def test_entrypoint_counts_as_runnable(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nENTRYPOINT ["python"]\n')

        assert "Container runs as root" in _titles(tmp_path)


class TestHealthcheck:
    def test_flags_a_runnable_image_with_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nUSER app\nCMD ["x"]\n')

        assert "No container healthcheck" in _titles(tmp_path)

    def test_accepts_one(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Dockerfile",
            'FROM python:3.14-slim\nUSER app\nHEALTHCHECK CMD true\nCMD ["x"]\n',
        )

        assert "No container healthcheck" not in _titles(tmp_path)

    def test_a_dockerfile_with_no_runnable_stage_is_not_flagged(self, tmp_path: Path) -> None:
        """A base-image-only Dockerfile has nothing to health-check."""
        _write(tmp_path, "Dockerfile", "FROM python:3.14-slim\nRUN pip install build\n")

        assert "No container healthcheck" not in _titles(tmp_path)


class TestCiPipeline:
    def test_flags_a_repository_with_none(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)

        assert "No CI pipeline" in _titles(tmp_path)

    @pytest.mark.parametrize(
        "path",
        [
            ".github/workflows/ci.yml",
            ".gitlab-ci.yml",
            ".circleci/config.yml",
            "Jenkinsfile",
            "azure-pipelines.yml",
        ],
    )
    def test_recognises_the_common_providers(self, tmp_path: Path, path: str) -> None:
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)
        _write(tmp_path, path, "pipeline\n")

        assert "No CI pipeline" not in _titles(tmp_path)

    def test_an_empty_workflows_directory_does_not_count(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)
        (tmp_path / ".github" / "workflows").mkdir(parents=True)

        assert "No CI pipeline" in _titles(tmp_path)


class TestRobustness:
    def test_an_empty_repository_does_not_raise(self, tmp_path: Path) -> None:
        SCANNER.scan(tmp_path)

    def test_a_malformed_dockerfile_does_not_raise(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "this is not a dockerfile\n\x00\x01\n")

        SCANNER.scan(tmp_path)

    def test_comments_are_ignored(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Dockerfile",
            '# USER app\nFROM python:3.14-slim\n# HEALTHCHECK CMD true\nCMD ["x"]\n',
        )

        titles = _titles(tmp_path)
        assert "Container runs as root" in titles
        assert "No container healthcheck" in titles

    def test_vendored_dockerfiles_are_ignored(self, tmp_path: Path) -> None:
        """node_modules routinely ships example Dockerfiles."""
        _write(tmp_path, "node_modules/pkg/Dockerfile", 'FROM node\nCMD ["x"]\n')
        _write(tmp_path, "README.md", "# docs\n")

        assert "No deployment configuration" in _titles(tmp_path)
