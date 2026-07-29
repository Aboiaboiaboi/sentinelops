"""Deployment checks.

How does this thing get built and shipped, and does the artefact it produces
behave itself once it is running.

Dockerfiles are parsed by stage rather than line by line. A multi-stage build
routinely does things in a builder that would be alarming in the image that
ships — installing compilers, running as root — and flagging those would be
noise. What matters is the stages that actually run something.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.scanners.base import ScanFinding, Severity, iter_files, read_text

CATEGORY = "deployment"

# Impacts. Both paths through the checks below total the category weight of 15:
# a repository with no deployment config at all loses 11 + 4, and one with a
# Dockerfile can lose 4 + 4 + 3 + 4.
_NO_DEPLOYMENT_CONFIG = 11
_UNPINNED_BASE_IMAGE = 4
_RUNS_AS_ROOT = 4
_NO_HEALTHCHECK = 3
_NO_CI_PIPELINE = 4

_DOCKERFILE_NAMES = ("dockerfile",)
_COMPOSE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

# Where orchestration manifests conventionally live. Checked by directory rather
# than by parsing every YAML in the repository, which on a large one would mean
# reading thousands of files to answer a yes/no question.
_ORCHESTRATION_DIRECTORIES = frozenset(
    {"k8s", "kubernetes", "manifests", "deploy", "deployment", "helm", "charts"}
)

_CI_PATHS = (
    ".github/workflows",
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "jenkinsfile",
    "azure-pipelines.yml",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".travis.yml",
    ".woodpecker.yml",
)

# `FROM image[:tag] [AS name]`, with the tag and stage name optional.
_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.IGNORECASE)
_INSTRUCTION = re.compile(r"^\s*(?P<keyword>[A-Za-z]+)\s", re.IGNORECASE)


@dataclass
class _Stage:
    """One FROM block of a Dockerfile."""

    base: str
    name: str | None
    instructions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_runnable(self) -> bool:
        """Whether this stage defines a process, and so could be the shipped image.

        A builder stage has no CMD or ENTRYPOINT and is discarded, so its
        privileges and health are irrelevant.
        """
        return "cmd" in self.instructions or "entrypoint" in self.instructions


def _parse_stages(content: str) -> list[_Stage]:
    stages: list[_Stage] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        from_match = _FROM.match(line)
        if from_match:
            stages.append(_Stage(base=from_match.group("image"), name=from_match.group("stage")))
            continue

        if not stages:
            continue
        instruction = _INSTRUCTION.match(line)
        if instruction:
            keyword = instruction.group("keyword").lower()
            stages[-1].instructions.setdefault(keyword, []).append(line)
    return stages


def _is_pinned(image: str, known_stages: set[str]) -> bool:
    """Whether a FROM refers to something reproducible.

    `FROM builder` is a reference to an earlier stage in the same file, not a
    base image, so it is neither pinned nor unpinned — it inherits whatever that
    stage resolved to.
    """
    if image.lower() in known_stages:
        return True
    if "@sha256:" in image:
        return True
    # Strip a registry host, which may itself contain a port and a colon.
    last_segment = image.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        return False
    tag = last_segment.rsplit(":", 1)[1]
    return tag.lower() not in {"latest", ""}


def _runs_as_root(stage: _Stage) -> bool:
    users = stage.instructions.get("user", [])
    if not users:
        return True
    # The last USER wins. A stage that switches to root to install something and
    # never switches back is running as root.
    final = users[-1].split(None, 1)[1].strip().strip('"').lower()
    return final in {"root", "0", "root:root", "0:0"}


class DeploymentScanner:
    category = CATEGORY

    def scan(self, repo_path: Path) -> list[ScanFinding]:
        dockerfiles = list(self._find_dockerfiles(repo_path))
        has_orchestration = self._has_orchestration(repo_path)

        findings: list[ScanFinding] = []
        if not dockerfiles and not has_orchestration:
            findings.append(self._no_deployment_config())
        elif dockerfiles:
            findings.extend(self._check_dockerfiles(dockerfiles, repo_path))

        findings.extend(self._check_ci(repo_path))
        return findings

    def _find_dockerfiles(self, repo_path: Path) -> Iterator[Path]:
        for path in iter_files(repo_path):
            name = path.name.lower()
            if name in _DOCKERFILE_NAMES or name.startswith("dockerfile."):
                yield path
            elif name.endswith(".dockerfile"):
                yield path

    def _has_orchestration(self, repo_path: Path) -> bool:
        """Compose or a Kubernetes/Helm layout.

        A repository can legitimately have no Dockerfile — the image may be
        built elsewhere — while still describing how it is deployed.
        """
        for path in iter_files(repo_path):
            if path.name.lower() in _COMPOSE_NAMES:
                return True
            relative = path.relative_to(repo_path)
            if any(part.lower() in _ORCHESTRATION_DIRECTORIES for part in relative.parts[:-1]):
                return True
            if path.name.lower() == "chart.yaml":
                return True
        return False

    def _no_deployment_config(self) -> ScanFinding:
        return ScanFinding(
            category=CATEGORY,
            severity=Severity.HIGH,
            title="No deployment configuration",
            description=(
                "No Dockerfile, Compose file, or Kubernetes manifest was found. Nothing in the "
                "repository describes how the service is packaged or run, so how it reaches an "
                "environment lives only in somebody's shell history."
            ),
            recommendation=(
                "Add a Dockerfile describing how the service is built and started, so the same "
                "artefact runs locally and in every environment."
            ),
            score_impact=_NO_DEPLOYMENT_CONFIG,
        )

    def _check_dockerfiles(self, dockerfiles: list[Path], repo_path: Path) -> list[ScanFinding]:
        unpinned: list[str] = []
        root_stages: list[str] = []
        healthchecked = False
        runnable_seen = False

        for path in dockerfiles:
            relative = path.relative_to(repo_path).as_posix()
            stages = _parse_stages(read_text(path))
            names = {s.name.lower() for s in stages if s.name}

            for stage in stages:
                if not _is_pinned(stage.base, names):
                    unpinned.append(f"{relative} ({stage.base})")
                if not stage.is_runnable:
                    continue
                runnable_seen = True
                if _runs_as_root(stage):
                    root_stages.append(f"{relative} ({stage.name or stage.base})")
                if "healthcheck" in stage.instructions:
                    healthchecked = True

        findings: list[ScanFinding] = []
        if unpinned:
            findings.append(
                ScanFinding(
                    category=CATEGORY,
                    severity=Severity.MEDIUM,
                    title="Base image is not pinned",
                    description=(
                        f"{unpinned[0]} uses a floating tag. The same Dockerfile will produce "
                        "different images on different days, so a build that passed CI is not "
                        "necessarily the one that ships."
                    ),
                    recommendation=(
                        "Pin to a specific version, or to a digest with @sha256: for a build that "
                        "is reproducible byte for byte."
                    ),
                    score_impact=_UNPINNED_BASE_IMAGE,
                )
            )

        if root_stages:
            findings.append(
                ScanFinding(
                    category=CATEGORY,
                    severity=Severity.HIGH,
                    title="Container runs as root",
                    description=(
                        f"{root_stages[0]} defines a process but never drops privileges. If the "
                        "application is compromised, the attacker starts as root inside the "
                        "container — and with some host configurations that maps to root outside "
                        "it."
                    ),
                    recommendation=(
                        "Create an unprivileged user in the image and add a USER instruction "
                        "before CMD. Keep application code owned by root so the running process "
                        "cannot rewrite it."
                    ),
                    score_impact=_RUNS_AS_ROOT,
                )
            )

        if runnable_seen and not healthchecked:
            findings.append(
                ScanFinding(
                    category=CATEGORY,
                    severity=Severity.LOW,
                    title="No container healthcheck",
                    description=(
                        "No HEALTHCHECK instruction was found. An orchestrator can only tell that "
                        "the process started, not that it is serving — so a container that is up "
                        "but wedged keeps receiving traffic."
                    ),
                    recommendation=(
                        "Add a HEALTHCHECK that exercises the path that matters, or configure a "
                        "readiness probe if you deploy to Kubernetes."
                    ),
                    score_impact=_NO_HEALTHCHECK,
                )
            )
        return findings

    def _check_ci(self, repo_path: Path) -> list[ScanFinding]:
        for candidate in _CI_PATHS:
            path = repo_path / candidate
            if path.is_dir() and any(path.iterdir()):
                return []
            if path.is_file():
                return []
        # Case-insensitive fallback, since Jenkinsfile is capitalised in practice.
        for entry in repo_path.iterdir():
            if entry.name.lower() in _CI_PATHS:
                return []

        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="No CI pipeline",
                description=(
                    "No continuous integration configuration was found. Nothing builds or tests "
                    "the project automatically, so whether the main branch works depends on "
                    "somebody having checked by hand."
                ),
                recommendation=(
                    "Add a pipeline that installs dependencies, runs the test suite, and builds "
                    "the image on every push."
                ),
                score_impact=_NO_CI_PIPELINE,
            )
        ]
