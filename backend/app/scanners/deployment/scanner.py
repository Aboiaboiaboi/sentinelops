"""Deployment checks.

How does this thing get built and shipped, and does the artefact it produces
behave itself once it is running.

Dockerfiles are parsed by stage rather than line by line. A multi-stage build
routinely does things in a builder that would be alarming in the image that
ships — installing compilers, running as root — and flagging those would be
noise. What matters is the stages that actually run something.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.scanners.base import RepositoryIndex, ScanFinding, Severity

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

# CI providers that keep their config in a directory. Matched on the path
# prefix so that an *empty* .github/workflows/ does not count — a directory with
# no workflow in it runs nothing.
_CI_DIRECTORY_PREFIXES = (".github/workflows/", ".circleci/")

# Providers that use a single file at the repository root.
_CI_ROOT_FILES = (
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
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


def _is_dockerfile(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in _DOCKERFILE_NAMES or name.startswith("dockerfile.") or name.endswith(".dockerfile")
    )


def _is_orchestration(path: Path, root: Path) -> bool:
    """Compose, a Kubernetes layout, or a Helm chart.

    A repository can legitimately have no Dockerfile — the image may be built
    elsewhere — while still describing how it is deployed.
    """
    name = path.name.lower()
    if name in _COMPOSE_NAMES or name == "chart.yaml":
        return True
    relative = path.relative_to(root)
    return any(part.lower() in _ORCHESTRATION_DIRECTORIES for part in relative.parts[:-1])


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

    def scan(self, repo: RepositoryIndex) -> list[ScanFinding]:
        # Both questions answered in one pass over the already-built index.
        # This used to be two separate walks of the tree, which on top of the
        # other scanners' walks meant traversing the same repository roughly a
        # dozen times per scan.
        dockerfiles: list[Path] = []
        has_orchestration = False
        for path in repo.files:
            if _is_dockerfile(path):
                dockerfiles.append(path)
            elif not has_orchestration and _is_orchestration(path, repo.path):
                has_orchestration = True

        findings: list[ScanFinding] = []
        if not dockerfiles and not has_orchestration:
            findings.append(self._no_deployment_config())
        elif dockerfiles:
            findings.extend(self._check_dockerfiles(dockerfiles, repo))

        findings.extend(self._check_ci(repo))
        return findings

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

    def _check_dockerfiles(
        self, dockerfiles: list[Path], repo: RepositoryIndex
    ) -> list[ScanFinding]:
        unpinned: list[str] = []
        root_stages: list[str] = []
        healthchecked = False
        runnable_seen = False

        for path in dockerfiles:
            relative = repo.relative(path)
            stages = _parse_stages(repo.read(path))
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

    def _check_ci(self, repo: RepositoryIndex) -> list[ScanFinding]:
        # A CI directory only counts if it has something in it — an empty
        # .github/workflows/ runs nothing.
        if any(
            repo.relative(path).lower().startswith(_CI_DIRECTORY_PREFIXES) for path in repo.files
        ):
            return []
        if repo.has_root_entry(*_CI_ROOT_FILES):
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
