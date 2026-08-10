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

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    code_only,
    failed,
    passed,
    skipped,
)

CATEGORY = "deployment"

_CONFIG = CheckSpec("deployment.config", "Deployment configuration")
_PINNING = CheckSpec("deployment.image_pinning", "Pinned base images")
_NON_ROOT = CheckSpec("deployment.non_root", "Container drops privileges")
_HEALTHCHECK = CheckSpec("deployment.healthcheck", "Container healthcheck")
_DOCKERIGNORE = CheckSpec("deployment.dockerignore", "Build context excluded")
_SIGNALS = CheckSpec("deployment.signal_handling", "Container receives stop signals")
_PRIVILEGED = CheckSpec("deployment.privileged", "Host isolation preserved")
_CI = CheckSpec("deployment.ci", "CI pipeline")

# The image checks read Dockerfile stages, so without one there is nothing to
# read — a repository whose image is built elsewhere is not failing them.
_NO_DOCKERFILE = "no Dockerfile was found to inspect"

# Impacts. Both paths through the checks below total the category weight of 15:
# a repository with no deployment config at all loses 12 + 3, and one with a
# Dockerfile and orchestration can lose 3 + 4 + 1 + 3 + 1 + 2 + 1.
#
# Rebalanced when the signal-handling and privileged checks were added. The
# weight has to come from somewhere, and it came from the two findings about
# what ends up in the image rather than from the two about reproducibility and
# privilege — a wedged container and a fat layer are both recoverable, and
# neither is somebody owning the host.
_NO_DEPLOYMENT_CONFIG = 12
_UNPINNED_BASE_IMAGE = 3
_RUNS_AS_ROOT = 4
_NO_HEALTHCHECK = 1
_NO_CI_PIPELINE = 3
_NO_DOCKERIGNORE = 1
_PRIVILEGED_CONTAINER = 2
_SHELL_FORM_ENTRYPOINT = 1

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

# Only these get read for the privilege check. A directory can be recognised as
# orchestration because of a README sitting in it; a manifest is YAML.
_MANIFEST_SUFFIXES = frozenset({".yml", ".yaml"})

# A ceiling on how many manifests are read. A Helm chart or a Kustomize tree can
# hold hundreds of templates, and the question here is answered by the first
# offending line — reading the whole tree would cost more than the answer is
# worth. The same reasoning as MAX_READ_BYTES one level up.
_MAX_MANIFESTS = 50

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


def _compose_unpinned_images(content: str) -> list[str]:
    """Floating `image:` references in a Compose file.

    Minimal structural parse rather than a bare regex over lines, because one
    distinction is load-bearing: a service with a `build:` key uses `image:`
    as the *name for what it builds*, not as something pulled from a registry
    — flagging `image: myapp:latest` on a built service would hit nearly
    every real Compose file. Interpolated values (`${TAG}`) are skipped too:
    the environment decides those, which is the correct pattern.
    """
    services: list[dict] = []
    current: dict | None = None
    in_services = False
    service_indent: int | None = None

    for raw in content.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())

        if indent == 0:
            in_services = stripped == "services:"
            current, service_indent = None, None
            continue
        if not in_services:
            continue

        is_key_only = stripped.endswith(":") and ":" not in stripped[:-1]
        if is_key_only and (service_indent is None or indent == service_indent):
            service_indent = service_indent or indent
            if indent == service_indent:
                current = {"image": None, "build": False}
                services.append(current)
                continue

        if current is None or service_indent is None or indent <= service_indent:
            continue
        if stripped.startswith("image:"):
            current["image"] = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped == "build:" or stripped.startswith("build:"):
            current["build"] = True

    return [
        service["image"]
        for service in services
        if service["image"]
        and not service["build"]
        and "${" not in service["image"]
        and not _is_pinned(service["image"], set())
    ]


# A COPY/ADD that brings in the entire build context. `--from=` copies are
# excluded — those read from an earlier stage, not from the directory.
_BROAD_COPY_SOURCES = {".", "./"}


def _copies_full_context(stage: _Stage) -> bool:
    for keyword in ("copy", "add"):
        for line in stage.instructions.get(keyword, []):
            tokens = line.split()
            if any(token.lower().startswith("--from=") for token in tokens):
                continue
            arguments = [token for token in tokens[1:] if not token.startswith("--")]
            if len(arguments) >= 2 and any(
                source in _BROAD_COPY_SOURCES for source in arguments[:-1]
            ):
                return True
    return False


# Shell-form CMD/ENTRYPOINT wrappers that do forward signals, so the process
# below them still gets SIGTERM. `exec` replaces the shell entirely; the rest are
# init processes written for exactly this problem.
_SIGNAL_SAFE_ENTRYPOINTS = frozenset(
    {"exec", "tini", "dumb-init", "supervisord", "s6-svscan", "runit", "catatonit"}
)


def _shell_form_entry(stage: _Stage) -> str | None:
    """The stage's effective entry instruction, if it is shell form.

    ENTRYPOINT wins over CMD: when both are present, CMD supplies arguments to
    it rather than starting anything, so a shell-form CMD beneath an exec-form
    ENTRYPOINT is correct and must not be flagged.

    Shell form means the process runs as a child of `/bin/sh -c`, which does not
    forward SIGTERM to it. Returns the offending instruction for the finding to
    quote, or None.
    """
    lines = stage.instructions.get("entrypoint") or stage.instructions.get("cmd")
    if not lines:
        return None

    # The last one wins, the same rule as USER.
    instruction = lines[-1]
    _, _, argument = instruction.partition(" ")
    argument = argument.strip()
    if not argument or argument.startswith("["):
        return None

    first_word = argument.split(None, 1)[0].strip("\"'")
    # Basename, so /sbin/tini and /usr/bin/dumb-init are recognised too.
    if first_word.rsplit("/", 1)[-1] in _SIGNAL_SAFE_ENTRYPOINTS:
        return None
    return instruction


# The four ways a committed deployment file hands a container authority over the
# machine it runs on. Written to match both Compose and Kubernetes, because
# `privileged: true` is spelled identically in each and matching text is honest
# about what this check is — a line scan, not a YAML parser.
_HOST_ACCESS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^\s*-?\s*privileged\s*:\s*[\"']?(?:true|yes)[\"']?\s*$", re.IGNORECASE),
        "runs privileged, which grants every capability and the host's devices",
    ),
    (
        re.compile(r"/var/run/docker\.sock"),
        "mounts the Docker socket, which is root on the host machine",
    ),
    (
        re.compile(r"^\s*-?\s*network_mode\s*:\s*[\"']?host[\"']?\s*$", re.IGNORECASE),
        "shares the host's network namespace",
    ),
    (
        re.compile(r"^\s*-?\s*hostNetwork\s*:\s*[\"']?true[\"']?\s*$", re.IGNORECASE),
        "shares the host's network namespace",
    ),
    (
        re.compile(r"^\s*-\s*[\"']?SYS_ADMIN[\"']?\s*$", re.IGNORECASE),
        "adds SYS_ADMIN, the capability that makes container escape routine",
    ),
)


def _host_access(content: str) -> str | None:
    """The first host-level grant in a deployment file, described, or None.

    Comments are stripped before matching, and that is not a detail: a file
    explaining *why* it mounts the Docker socket contains the string
    `/var/run/docker.sock` in prose, and reporting the warning as the offence
    would punish the projects that documented themselves.

    Narrow capabilities — NET_ADMIN, NET_BIND_SERVICE, SYS_PTRACE — are
    deliberately not matched. Those are the capability model being used
    correctly, and flagging them would penalise the careful alternative to the
    blunt instrument this check is actually looking for.
    """
    for line in code_only(content).splitlines():
        for pattern, description in _HOST_ACCESS_PATTERNS:
            if pattern.search(line):
                return description
    return None


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
    CHECKS = (_CONFIG, _PINNING, _NON_ROOT, _HEALTHCHECK, _DOCKERIGNORE, _SIGNALS, _PRIVILEGED, _CI)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        # Both questions answered in one pass over the already-built index.
        # This used to be two separate walks of the tree, which on top of the
        # other scanners' walks meant traversing the same repository roughly a
        # dozen times per scan.
        dockerfiles: list[Path] = []
        compose_files: list[Path] = []
        manifest_files: list[Path] = []
        has_orchestration = False
        for path in repo.files:
            if _is_dockerfile(path):
                dockerfiles.append(path)
                continue
            if path.name.lower() in _COMPOSE_NAMES:
                compose_files.append(path)
                has_orchestration = True
                continue
            # No longer short-circuited once orchestration is found: the
            # privilege check needs the manifests themselves, not just the fact
            # that some exist. The test is string work on an already-walked
            # path, so it costs no additional I/O.
            if _is_orchestration(path, repo.path):
                has_orchestration = True
                if (
                    path.suffix.lower() in _MANIFEST_SUFFIXES
                    and len(manifest_files) < _MAX_MANIFESTS
                ):
                    manifest_files.append(path)

        results: list[CheckResult] = []
        if not dockerfiles and not has_orchestration:
            results.append(failed(_CONFIG, self._no_deployment_config()))
            # Nothing describes the deployment, so there is nothing to inspect
            # for pinning, privileges or build context. Reporting those as
            # passed would credit a repository for a file it does not have.
            no_config = "no deployment configuration was found to inspect"
            results.extend(
                skipped(check, no_config)
                for check in (_PINNING, _NON_ROOT, _HEALTHCHECK, _DOCKERIGNORE, _SIGNALS)
            )
        else:
            results.append(passed(_CONFIG))
            results.extend(self._check_images(dockerfiles, compose_files, repo))

        # Outside the branch: it reads orchestration rather than the image, and
        # it answers for itself when there is none.
        results.append(self._check_privileged(compose_files, manifest_files, repo))
        results.append(self._check_ci(repo))
        return results

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

    def _check_images(
        self, dockerfiles: list[Path], compose_files: list[Path], repo: RepositoryIndex
    ) -> list[CheckResult]:
        unpinned: list[str] = []
        root_stages: list[str] = []
        healthchecked = False
        runnable_seen = False
        broad_copy: str | None = None
        shell_entry: tuple[str, str] | None = None

        for path in dockerfiles:
            relative = repo.relative(path)
            stages = _parse_stages(repo.read(path))
            names = {s.name.lower() for s in stages if s.name}

            for stage in stages:
                if not _is_pinned(stage.base, names):
                    unpinned.append(f"{relative} ({stage.base})")
                if broad_copy is None and _copies_full_context(stage):
                    broad_copy = relative
                if not stage.is_runnable:
                    continue
                runnable_seen = True
                if _runs_as_root(stage):
                    root_stages.append(f"{relative} ({stage.name or stage.base})")
                if "healthcheck" in stage.instructions:
                    healthchecked = True
                if shell_entry is None:
                    instruction = _shell_form_entry(stage)
                    if instruction is not None:
                        shell_entry = (relative, instruction)

        # The same pinning question asked of Compose: `image: postgres` pulls
        # a different database on different days, exactly like a floating FROM.
        for path in compose_files:
            relative = repo.relative(path)
            for image in _compose_unpinned_images(repo.read(path)):
                unpinned.append(f"{relative} ({image})")

        results: list[CheckResult] = []

        if not dockerfiles:
            results.append(skipped(_DOCKERIGNORE, _NO_DOCKERFILE))
        elif repo.has_root_entry(".dockerignore"):
            results.append(passed(_DOCKERIGNORE))
        elif broad_copy is None:
            results.append(
                skipped(_DOCKERIGNORE, "nothing copies the whole build context into the image")
            )
        else:
            results.append(
                failed(
                    _DOCKERIGNORE,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.MEDIUM,
                        title="Full build context copied with no .dockerignore",
                        description=(
                            f"{broad_copy} copies the entire directory into the image and there "
                            "is no .dockerignore. Whatever is lying around at build time ships in "
                            "the layers — local env files, the .git history, dependency caches — "
                            "and stays retrievable from the image even if a later layer deletes it."
                        ),
                        recommendation=(
                            "Add a .dockerignore excluding at least .git, .env* and dependency "
                            "directories — or copy only the paths the image actually needs."
                        ),
                        score_impact=_NO_DOCKERIGNORE,
                    ),
                )
            )

        if not unpinned:
            results.append(passed(_PINNING))
        else:
            results.append(
                failed(
                    _PINNING,
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
                            "Pin to a specific version, or to a digest with @sha256: for a build "
                            "that is reproducible byte for byte."
                        ),
                        score_impact=_UNPINNED_BASE_IMAGE,
                    ),
                )
            )

        # Privileges and health are properties of the stage that actually ships.
        # A Dockerfile with no runnable stage — a base image others build on —
        # has no process to run unprivileged or to health-check.
        if not dockerfiles:
            results.append(skipped(_NON_ROOT, _NO_DOCKERFILE))
            results.append(skipped(_HEALTHCHECK, _NO_DOCKERFILE))
            results.append(skipped(_SIGNALS, _NO_DOCKERFILE))
            return results

        if not runnable_seen:
            no_process = "no stage in the Dockerfile starts a process"
            results.append(skipped(_NON_ROOT, no_process))
            results.append(skipped(_HEALTHCHECK, no_process))
            results.append(skipped(_SIGNALS, no_process))
            return results

        if not root_stages:
            results.append(passed(_NON_ROOT))
        else:
            results.append(
                failed(
                    _NON_ROOT,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.HIGH,
                        title="Container runs as root",
                        description=(
                            f"{root_stages[0]} defines a process but never drops privileges. If "
                            "the application is compromised, the attacker starts as root inside "
                            "the container — and with some host configurations that maps to root "
                            "outside it."
                        ),
                        recommendation=(
                            "Create an unprivileged user in the image and add a USER instruction "
                            "before CMD. Keep application code owned by root so the running "
                            "process cannot rewrite it."
                        ),
                        score_impact=_RUNS_AS_ROOT,
                    ),
                )
            )

        if healthchecked:
            results.append(passed(_HEALTHCHECK))
        else:
            results.append(
                failed(
                    _HEALTHCHECK,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.LOW,
                        title="No container healthcheck",
                        description=(
                            "No HEALTHCHECK instruction was found. An orchestrator can only tell "
                            "that the process started, not that it is serving — so a container "
                            "that is up but wedged keeps receiving traffic."
                        ),
                        recommendation=(
                            "Add a HEALTHCHECK that exercises the path that matters, or configure "
                            "a readiness probe if you deploy to Kubernetes."
                        ),
                        score_impact=_NO_HEALTHCHECK,
                    ),
                )
            )

        if shell_entry is None:
            results.append(passed(_SIGNALS))
        else:
            where, instruction = shell_entry
            results.append(
                failed(
                    _SIGNALS,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.MEDIUM,
                        title="Container does not receive stop signals",
                        description=(
                            f"{where} starts its process with `{instruction}` — shell form, so the "
                            "application runs as a child of /bin/sh, which does not forward "
                            "SIGTERM. On every deploy and every scale-down the orchestrator asks "
                            "the container to stop, nothing hears it, and the container is killed "
                            "once the grace period expires — dropping whatever was in flight."
                        ),
                        recommendation=(
                            "Use the JSON array form, so the process is PID 1 and receives the "
                            'signal directly: CMD ["python", "-m", "app"]. If a shell really is '
                            "needed, prefix the command with exec, or use an init such as tini."
                        ),
                        score_impact=_SHELL_FORM_ENTRYPOINT,
                    ),
                )
            )
        return results

    def _check_privileged(
        self, compose_files: list[Path], manifest_files: list[Path], repo: RepositoryIndex
    ) -> CheckResult:
        """Whether any committed deployment file removes the container boundary.

        Asked of Compose and Kubernetes together. The grants are spelled the
        same way in both, and a repository that deploys to Kubernetes is
        precisely the one this check exists for.
        """
        candidates = compose_files + manifest_files
        if not candidates:
            return skipped(
                _PRIVILEGED, "no Compose file or orchestration manifest was found to inspect"
            )

        for path in candidates:
            description = _host_access(repo.read(path))
            if description is None:
                continue
            relative = repo.relative(path)
            return failed(
                _PRIVILEGED,
                ScanFinding(
                    category=CATEGORY,
                    severity=Severity.HIGH,
                    title="Container granted host-level access",
                    description=(
                        f"{relative} defines a container that {description}. The isolation "
                        "between the container and the machine it runs on is removed, so a "
                        "compromise of the application is a compromise of the host rather than "
                        "of a sandbox. A development-only Compose file is the common and "
                        "legitimate case for this — the thing worth checking is that the line "
                        "has not been copied into whatever actually gets deployed."
                    ),
                    recommendation=(
                        "Remove the grant from anything that reaches a deployed environment. "
                        "Where the capability is genuinely needed, add only the specific one "
                        "(cap_add: NET_ADMIN) rather than privileged, and reach a container "
                        "runtime through its platform API instead of by mounting its socket."
                    ),
                    score_impact=_PRIVILEGED_CONTAINER,
                ),
            )

        return passed(_PRIVILEGED)

    def _check_ci(self, repo: RepositoryIndex) -> CheckResult:
        # A CI directory only counts if it has something in it — an empty
        # .github/workflows/ runs nothing.
        if any(
            repo.relative(path).lower().startswith(_CI_DIRECTORY_PREFIXES) for path in repo.files
        ):
            return passed(_CI)
        if repo.has_root_entry(*_CI_ROOT_FILES):
            return passed(_CI)

        return failed(
            _CI,
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
            ),
        )
