"""Parsing helpers for Dockerfiles and orchestration manifests.

Split out of scanner.py, which grew past the 600-line threshold this project's
own architecture.file_size check applies to everyone else. Everything here is
pure parsing — no CheckResult, no ScanFinding, no scoring — so it reads and
tests the same way regardless of which check ends up using it.

Dockerfiles are parsed by stage rather than line by line. A multi-stage build
routinely does things in a builder that would be alarming in the image that
ships — installing compilers, running as root — and flagging those would be
noise. What matters is the stages that actually run something.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.scanners.base import code_only

_DOCKERFILE_NAMES = ("dockerfile",)
COMPOSE_NAMES = (
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

# `FROM image[:tag] [AS name]`, with the tag and stage name optional.
_FROM = re.compile(r"^\s*FROM\s+(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.IGNORECASE)
_INSTRUCTION = re.compile(r"^\s*(?P<keyword>[A-Za-z]+)\s", re.IGNORECASE)


@dataclass
class Stage:
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


def parse_stages(content: str) -> list[Stage]:
    stages: list[Stage] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        from_match = _FROM.match(line)
        if from_match:
            stages.append(Stage(base=from_match.group("image"), name=from_match.group("stage")))
            continue

        if not stages:
            continue
        instruction = _INSTRUCTION.match(line)
        if instruction:
            keyword = instruction.group("keyword").lower()
            stages[-1].instructions.setdefault(keyword, []).append(line)
    return stages


def is_pinned(image: str, known_stages: set[str]) -> bool:
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


def is_dockerfile(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in _DOCKERFILE_NAMES or name.startswith("dockerfile.") or name.endswith(".dockerfile")
    )


def is_orchestration(path: Path, root: Path) -> bool:
    """Compose, a Kubernetes layout, or a Helm chart.

    A repository can legitimately have no Dockerfile — the image may be built
    elsewhere — while still describing how it is deployed.
    """
    name = path.name.lower()
    if name in COMPOSE_NAMES or name == "chart.yaml":
        return True
    relative = path.relative_to(root)
    return any(part.lower() in _ORCHESTRATION_DIRECTORIES for part in relative.parts[:-1])


def compose_unpinned_images(content: str) -> list[str]:
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
        and not is_pinned(service["image"], set())
    ]


# A COPY/ADD that brings in the entire build context. `--from=` copies are
# excluded — those read from an earlier stage, not from the directory.
_BROAD_COPY_SOURCES = {".", "./"}


def copies_full_context(stage: Stage) -> bool:
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


def shell_form_entry(stage: Stage) -> str | None:
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


def host_access(content: str) -> str | None:
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


def runs_as_root(stage: Stage) -> bool:
    users = stage.instructions.get("user", [])
    if not users:
        return True
    # The last USER wins. A stage that switches to root to install something and
    # never switches back is running as root.
    final = users[-1].split(None, 1)[1].strip().strip('"').lower()
    return final in {"root", "0", "root:root", "0:0"}
