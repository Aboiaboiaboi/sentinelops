"""Tests for the deployment scanner.

The multi-stage Dockerfile cases carry the most weight. A builder stage doing
things that would be alarming in a shipped image is normal, and flagging it
would train people to ignore the scanner.
"""

from pathlib import Path

import pytest

from app.scanners.base import RepositoryIndex, Severity, findings_of
from app.scanners.deployment import DeploymentScanner

SCANNER = DeploymentScanner()


def _scan(repo: Path):
    """The findings from a scan; check outcomes are covered in
    test_check_results.py."""
    return findings_of(SCANNER.scan(RepositoryIndex.build(repo)))


def _titles(repo: Path) -> set[str]:
    return {f.title for f in _scan(repo)}


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
        assert _scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        assert {f.category for f in _scan(tmp_path)} == {"deployment"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        """A repository failing everything scores the category zero, not below."""
        _write(tmp_path, "Dockerfile", 'FROM python\nCMD ["python"]\n')

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 15

    def test_the_worst_case_with_no_config_also_fits(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md", "# docs\n")

        assert sum(f.score_impact for f in _scan(tmp_path)) <= 15


class TestDeploymentConfig:
    def test_flags_a_repository_with_nothing(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.py", "print(1)\n")

        assert "No deployment configuration" in _titles(tmp_path)

    def test_is_high_severity(self, tmp_path: Path) -> None:
        finding = next(f for f in _scan(tmp_path) if f.title.startswith("No deployment"))

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


class TestSignalHandling:
    def test_flags_a_shell_form_cmd(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "FROM python:3.14-slim\nUSER app\nCMD python -m app\n")

        assert "Container does not receive stop signals" in _titles(tmp_path)

    def test_accepts_the_json_array_form(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nUSER app\nCMD ["python", "-m"]\n')

        assert "Container does not receive stop signals" not in _titles(tmp_path)

    def test_flags_a_shell_form_entrypoint(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "FROM python:3.14-slim\nUSER app\nENTRYPOINT ./run.sh\n")

        assert "Container does not receive stop signals" in _titles(tmp_path)

    def test_shell_form_cmd_under_an_exec_entrypoint_is_arguments_not_a_process(
        self, tmp_path: Path
    ) -> None:
        """CMD supplies arguments to an exec-form ENTRYPOINT. The process is
        still PID 1, and flagging this would hit a correct, common pattern."""
        _write(
            tmp_path,
            "Dockerfile",
            "FROM python:3.14-slim\nUSER app\n"
            'ENTRYPOINT ["python", "-m", "app"]\nCMD --port 8000\n',
        )

        assert "Container does not receive stop signals" not in _titles(tmp_path)

    @pytest.mark.parametrize(
        "command",
        ["exec python -m app", "tini -- python -m app", "/sbin/tini -- app", "dumb-init python"],
    )
    def test_accepts_wrappers_that_forward_signals(self, tmp_path: Path, command: str) -> None:
        _write(tmp_path, "Dockerfile", f"FROM python:3.14-slim\nUSER app\nCMD {command}\n")

        assert "Container does not receive stop signals" not in _titles(tmp_path)

    def test_a_builder_stage_is_not_a_shipped_process(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)

        assert "Container does not receive stop signals" not in _titles(tmp_path)


class TestPrivilegedContainer:
    @pytest.mark.parametrize(
        "line",
        [
            "    privileged: true",
            '    privileged: "true"',
            "      - /var/run/docker.sock:/var/run/docker.sock",
            "    network_mode: host",
        ],
    )
    def test_flags_a_compose_service_with_host_access(self, tmp_path: Path, line: str) -> None:
        _write(tmp_path, "docker-compose.yml", f"services:\n  app:\n    image: app:1\n{line}\n")

        assert "Container granted host-level access" in _titles(tmp_path)

    @pytest.mark.parametrize("line", ["      privileged: true", "  hostNetwork: true"])
    def test_flags_a_kubernetes_manifest(self, tmp_path: Path, line: str) -> None:
        _write(tmp_path, "k8s/deployment.yaml", f"kind: Deployment\nspec:\n{line}\n")

        assert "Container granted host-level access" in _titles(tmp_path)

    def test_flags_sys_admin(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  app:\n    image: app:1\n    cap_add:\n      - SYS_ADMIN\n",
        )

        assert "Container granted host-level access" in _titles(tmp_path)

    @pytest.mark.parametrize("capability", ["NET_ADMIN", "NET_BIND_SERVICE", "SYS_PTRACE"])
    def test_accepts_a_narrow_capability(self, tmp_path: Path, capability: str) -> None:
        """The capability model used correctly. Flagging these would penalise
        the careful alternative to the blunt instrument this check looks for."""
        _write(
            tmp_path,
            "docker-compose.yml",
            f"services:\n  app:\n    image: app:1\n    cap_add:\n      - {capability}\n",
        )

        assert "Container granted host-level access" not in _titles(tmp_path)

    def test_a_comment_describing_the_risk_is_not_the_risk(self, tmp_path: Path) -> None:
        """A file that documents why it does something dangerous contains the
        dangerous string in prose. Reporting the warning as the offence would
        punish the projects that explained themselves."""
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  app:\n    image: app:1\n"
            "    # Never mount /var/run/docker.sock here, and never set privileged: true\n",
        )

        assert "Container granted host-level access" not in _titles(tmp_path)

    def test_an_ordinary_compose_file_passes(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  app:\n    image: app:1\n    ports:\n      - '8000:8000'\n",
        )

        assert "Container granted host-level access" not in _titles(tmp_path)

    def test_is_high_severity(self, tmp_path: Path) -> None:
        _write(tmp_path, "docker-compose.yml", "services:\n  app:\n    privileged: true\n")

        finding = next(f for f in _scan(tmp_path) if f.title.startswith("Container granted"))
        assert finding.severity is Severity.HIGH

    def test_a_dockerfile_alone_is_not_asked_the_question(self, tmp_path: Path) -> None:
        """No orchestration means nothing declares how the container is run, so
        the check skips rather than crediting a repository for a file it has
        not got."""
        _write(tmp_path, "Dockerfile", GOOD_DOCKERFILE)

        assert "Container granted host-level access" not in _titles(tmp_path)


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
        _scan(tmp_path)

    def test_a_malformed_dockerfile_does_not_raise(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", "this is not a dockerfile\n\x00\x01\n")

        _scan(tmp_path)

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


class TestComposeImagePinning:
    """The same reproducibility question asked of Compose. `image: postgres`
    pulls a different database on different days, exactly like a floating FROM.
    """

    def test_flags_a_floating_service_image(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  db:\n    image: postgres\n",
        )

        assert "Base image is not pinned" in _titles(tmp_path)

    def test_accepts_a_pinned_service_image(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  db:\n    image: postgres:17-alpine\n",
        )

        assert "Base image is not pinned" not in _titles(tmp_path)

    def test_a_built_service_names_its_output_rather_than_pulling(self, tmp_path: Path) -> None:
        """With a `build:` key, `image:` is the name for what is built locally,
        not something pulled from a registry. Flagging it would hit nearly every
        real Compose file."""
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  api:\n    build: .\n    image: myapp:latest\n",
        )

        assert "Base image is not pinned" not in _titles(tmp_path)

    def test_an_interpolated_tag_is_the_environments_decision(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "docker-compose.yml",
            "services:\n  db:\n    image: postgres:${PG_VERSION}\n",
        )

        assert "Base image is not pinned" not in _titles(tmp_path)


class TestDockerignore:
    def test_flags_a_full_context_copy_with_no_dockerignore(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nCOPY . /app\nUSER app\nCMD ["x"]\n')

        assert "Full build context copied with no .dockerignore" in _titles(tmp_path)

    def test_a_dockerignore_clears_it(self, tmp_path: Path) -> None:
        _write(tmp_path, "Dockerfile", 'FROM python:3.14-slim\nCOPY . /app\nUSER app\nCMD ["x"]\n')
        _write(tmp_path, ".dockerignore", ".git\n.env*\n")

        assert "Full build context copied with no .dockerignore" not in _titles(tmp_path)

    def test_copying_named_paths_is_not_a_full_context_copy(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "Dockerfile",
            'FROM python:3.14-slim\nCOPY app/ /app\nCOPY pyproject.toml /\nUSER app\nCMD ["x"]\n',
        )

        assert "Full build context copied with no .dockerignore" not in _titles(tmp_path)

    def test_a_stage_to_stage_copy_reads_no_build_context(self, tmp_path: Path) -> None:
        """`COPY --from=builder . /app` copies from an earlier stage, not from
        the directory, so .dockerignore is irrelevant to it."""
        _write(
            tmp_path,
            "Dockerfile",
            "FROM python:3.14-slim AS builder\nRUN true\n"
            'FROM python:3.14-slim\nCOPY --from=builder . /app\nUSER app\nCMD ["x"]\n',
        )

        assert "Full build context copied with no .dockerignore" not in _titles(tmp_path)
