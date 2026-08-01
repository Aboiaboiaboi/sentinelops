"""Isolated execution abstraction.

The third of the modules permitted to know about an execution backend — the
other two are queue.py and storage.py. Nothing in services/ or scanners/ may
start a container directly. That boundary is what keeps the security tooling
portable: Docker locally today, Cloud Run Jobs once there is somewhere to
deploy to, and a change here rather than in every tool wrapper.

This exists because the security scanners are the first thing in SentinelOps
that *executes* third-party binaries against an untrusted, user-submitted
repository. Reading files is one risk; handing that tree to Gitleaks, Trivy and
Semgrep — each a large program with its own parsers — is another, and the
containment for it belongs in one reviewable place.

Two implementations: `DockerSandbox` for real use, and `NullSandbox`, which is
the default and refuses. A missing sandbox must surface as an errored check,
never as a check that quietly passed.
"""

import logging
import os
import shlex
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Where a bind-mounted checkout appears inside the container. Commands refer to
# it through the placeholder below rather than hardcoding a path, because under
# a named volume the repository keeps the path it already has (see
# DockerSandbox for why).
REPO_MOUNT = "/repo"

# Substituted into a command's arguments with the path the *container* will see.
# `("detect", "--source", REPO_PLACEHOLDER)` is the whole convention.
REPO_PLACEHOLDER = "{repo}"

# Where the warmed vulnerability database and rule cache appear, read-only.
CACHE_MOUNT = "/cache"

# Enough scratch space for a tool that insists on a temp file, and no more. The
# root filesystem is read-only, so without this Trivy and Semgrep fail on their
# first write; with an unbounded tmpfs a hostile repository could fill the
# worker's memory, since tmpfs pages are charged to it.
TMPFS_BYTES = 256 * 1024 * 1024

# The nobody:nogroup ids. Present in every mainstream base image, and owning
# nothing, so a tool that escapes its own process still has no filesystem it can
# write outside the tmpfs.
SANDBOX_UID_GID = "65534:65534"

# A tool that writes unbounded output would be captured into the worker's
# memory. Trivy on a large lockfile is measured in megabytes; anything past this
# is a runaway, and truncating produces JSON that fails to parse, which the
# caller reports as errored. That is the honest outcome — better than the worker
# dying, and better than trusting a half-read document.
MAX_OUTPUT_BYTES = 32 * 1024 * 1024

# Grace beyond the spec's own timeout before the docker CLI itself is killed.
# The container is killed by name afterwards regardless — see _force_remove.
_CLI_GRACE_SECONDS = 10


class SandboxUnavailable(Exception):
    """No sandbox is available here.

    Raised rather than returned so it cannot be mistaken for a tool result. A
    caller catching this reports the check as ERRORED — never PASSED.
    """


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """One tool run: an image, a command, and what it is allowed to consume."""

    image: str
    command: tuple[str, ...]
    timeout_seconds: int
    memory_mb: int = 512
    # Whether this tool needs the pre-warmed database or rule set, mounted
    # read-only at CACHE_MOUNT. A flag rather than a volume name on purpose:
    # *which* volume holds it is a deployment fact, and a scanner that knew it
    # would be a scanner reading configuration. The runner supplies it, and
    # refuses to run the tool at all if it has none — a tool silently running
    # against no vulnerability database would report a clean repository.
    needs_cache: bool = False

    def __post_init__(self) -> None:
        # An unpinned image means the tool can change under a scan without a
        # commit, which turns "the score dropped" into an unanswerable question.
        # Reproducibility is the point, and it costs one comparison to make an
        # unpinned image impossible rather than merely discouraged.
        _, separator, tag = self.image.rpartition(":")
        if "@sha256:" in self.image:
            return
        if not separator or "/" in tag:
            raise ValueError(f"Sandbox image must be pinned to a tag or digest: {self.image!r}")
        if tag == "latest":
            raise ValueError(f"Sandbox image must not use :latest: {self.image!r}")


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """What a tool run produced.

    `stderr` is for logs only and is never persisted, the same rule that applies
    to git's stderr in workers/repo.py: it is text produced by a program reading
    attacker-controlled input, and storing it puts that text in front of a user.
    """

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    #: The path the *container* saw the checkout at. Tools report absolute paths
    #: from inside their own filesystem, and a wrapper needs this to turn one
    #: back into a repository-relative path. It differs by mount mode, which is
    #: precisely the detail a wrapper must not have to know about.
    repo_mount: str = ""

    @property
    def truncated(self) -> bool:
        """Whether output hit the cap, so `stdout` is not the whole document."""
        return len(self.stdout.encode("utf-8", errors="replace")) >= MAX_OUTPUT_BYTES


@runtime_checkable
class SandboxRunner(Protocol):
    """What the rest of the application is allowed to assume about a sandbox.

    Synchronous on purpose. Scanners are synchronous and dispatched through
    asyncio.to_thread; a sandbox returning a coroutine would force the whole
    Scanner protocol async to no benefit. This is a subprocess wait, which is
    exactly what to_thread is for.
    """

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        """Run `spec` against the checkout at `repo_path`.

        Raises SandboxUnavailable if no isolation can be provided. It must never
        fall back to running the tool unisolated.
        """
        ...


class NullSandbox:
    """Refuses every run.

    The default, so tests and any environment without a container runtime
    produce errored checks with a clear reason rather than silently reporting a
    repository as clean because nothing looked at it.
    """

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        del repo_path
        raise SandboxUnavailable(
            f"No sandbox is configured, so {spec.image} was not run. "
            "Set SANDBOX_ENABLED=true with a container runtime available."
        )


# Variables the docker CLI needs to find its daemon, and the ones the operating
# system needs in any child process. Everything else is deliberately dropped —
# see _docker_environment.
_DOCKER_CLIENT_VARIABLES = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "DOCKER_CONFIG",
)
_OS_PASSTHROUGH_VARIABLES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP")


def _docker_environment() -> dict[str, str]:
    """A minimal environment for the docker CLI.

    Built from nothing rather than inherited, the same discipline as
    _git_environment() in workers/repo.py. The worker process holds
    DATABASE_URL, REDIS_URL and a GitHub App private key that can mint access to
    every installed user's repositories. This environment belongs to the CLI, not
    to the container — but the CLI is one flag away from forwarding it, and a
    dictionary that never contained a secret cannot leak one.
    """
    environment = {
        name: value
        for name in _DOCKER_CLIENT_VARIABLES + _OS_PASSTHROUGH_VARIABLES
        if (value := os.environ.get(name)) is not None
    }
    environment["PATH"] = os.environ.get("PATH", "")
    return environment


def _truncate(output: str) -> str:
    encoded = output.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return output
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")


class DockerSandbox:
    """Runs a tool in a container with no network, no credentials and no writes.

    **The mount is the part that decides whether this works at all.** The worker
    is itself a container, and `docker run` is handled by the *host* daemon — so
    bind-mounting `/data/repos/scan-x` would ask the host for a path that exists
    only inside the worker, and the tool would scan an empty directory or fail
    outright. Clones already live in the `worker_data` named volume, mounted at
    /data, so the sibling container mounts the same volume at the same place and
    the checkout keeps the path it already has.

    `volume` empty means the worker is running directly on the host — the
    development case — where a bind mount of the real path is correct and the
    checkout appears at REPO_MOUNT instead. Commands are written with
    REPO_PLACEHOLDER so the same spec works either way.

    Mounting the whole volume read-only means a tool container can see other
    in-flight clones. Read-only, no network, and gone when the container exits —
    but worth tightening to a single checkout (`volume-subpath`, Docker 25+)
    once there is a measurement saying the daemon in use supports it.
    """

    def __init__(
        self,
        *,
        volume: str = "",
        cache_volume: str = "",
        volume_mount: str = "/data",
        docker_binary: str = "docker",
        max_timeout_seconds: int = 300,
        max_memory_mb: int = 512,
    ) -> None:
        self._volume = volume
        self._cache_volume = cache_volume
        # Deployment ceilings, not per-tool values. A spec asks for what its
        # tool needs; the operator decides what any one tool may consume on this
        # machine, and the smaller of the two wins.
        self._max_timeout_seconds = max_timeout_seconds
        self._max_memory_mb = max_memory_mb
        # Matches CLONE_ROOT=/data/repos in the worker stage of the Dockerfile.
        # The two have to agree: this is the path the volume is mounted at in
        # the worker, and therefore the path the clone is already reachable by.
        self._volume_mount = volume_mount
        self._docker = docker_binary

    def _memory_for(self, spec: SandboxSpec) -> int:
        return min(spec.memory_mb, self._max_memory_mb)

    def _timeout_for(self, spec: SandboxSpec) -> int:
        return min(spec.timeout_seconds, self._max_timeout_seconds)

    def _mount_arguments(self, repo_path: Path) -> tuple[list[str], str]:
        """Mount flags, and the path the container will see the checkout at."""
        if self._volume:
            return (
                [
                    "--mount",
                    f"type=volume,source={self._volume},target={self._volume_mount},readonly",
                ],
                repo_path.as_posix(),
            )
        # --mount rather than -v throughout: its fields are comma-separated, so a
        # Windows source path keeps its drive-letter colon instead of being read
        # as a field separator. It is also explicit that `readonly` was asked
        # for, where -v silently accepts a misspelled third field.
        return (
            [
                "--mount",
                f"type=bind,source={repo_path.resolve()},target={REPO_MOUNT},readonly",
            ],
            REPO_MOUNT,
        )

    def _build_command(self, spec: SandboxSpec, repo_path: Path, name: str) -> list[str]:
        mounts, container_repo = self._mount_arguments(repo_path)

        command = [
            self._docker,
            "run",
            # Removed on exit. Without it every scan leaves a dead container
            # behind and the disk fills with them.
            "--rm",
            "--name",
            name,
            # No network at all. A tool that cannot reach the internet cannot
            # exfiltrate the repository it is reading, and cannot be talked into
            # fetching a payload by something in that repository. It is also why
            # milestone 3 exists: the vulnerability database has to arrive some
            # other way.
            "--network=none",
            # Both, together. --memory alone leaves swap unbounded, so a tool
            # over its limit swaps instead of being killed and takes the host's
            # I/O down with it.
            f"--memory={self._memory_for(spec)}m",
            f"--memory-swap={self._memory_for(spec)}m",
            # A fork bomb in a scanned repository is a plausible input, not a
            # hypothetical one.
            "--pids-limit=256",
            "--cpus=1",
            # No writes to the image's filesystem. Everything a tool legitimately
            # needs to write goes to the tmpfs below.
            "--read-only",
            "--mount",
            f"type=tmpfs,destination=/tmp,tmpfs-size={TMPFS_BYTES}",
            # Every Linux capability dropped: none of these tools need to bind a
            # port, change ownership, or load a module.
            "--cap-drop=ALL",
            # Blocks privilege escalation through a setuid binary inside the
            # image — the standard escape from an unprivileged container user.
            "--security-opt=no-new-privileges",
            "--user",
            SANDBOX_UID_GID,
            *mounts,
        ]

        if spec.needs_cache:
            command += [
                "--mount",
                f"type=volume,source={self._cache_volume},target={CACHE_MOUNT},readonly",
            ]

        # No -e, --env or --env-file anywhere above, and none here. `docker run`
        # passes nothing from this process by default; the rule is that it stays
        # that way, and a test asserts it.
        command.append(spec.image)
        command += [argument.replace(REPO_PLACEHOLDER, container_repo) for argument in spec.command]
        return command

    def _force_remove(self, name: str) -> None:
        """Kill a container the CLI stopped waiting for.

        subprocess's timeout kills the docker *client*, which does nothing to
        the container — it keeps running, holding its memory and CPU share,
        invisible to the scan that gave up on it. Every timeout has to be
        followed by this or a worker leaks a container per timed-out tool.
        """
        try:
            subprocess.run(  # noqa: S603 — fixed argv, no shell
                [self._docker, "rm", "--force", name],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=_docker_environment(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logger.warning("could not remove timed-out sandbox container", extra={"name": name})

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        if spec.needs_cache and not self._cache_volume:
            # Refused rather than run without it. Trivy with no vulnerability
            # database finds no vulnerabilities, and that is indistinguishable
            # from a repository with none — the single worst answer this system
            # could give.
            raise SandboxUnavailable(
                f"{spec.image} needs the warmed cache volume and none is configured."
            )

        name = f"sentinelops-scan-{uuid.uuid4().hex[:12]}"
        command = self._build_command(spec, repo_path, name)
        timeout = self._timeout_for(spec)

        logger.info("sandbox starting", extra={"image": spec.image, "timeout_seconds": timeout})

        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                command,
                capture_output=True,
                # Explicit UTF-8 rather than text=True, which decodes with the
                # locale encoding — cp1252 on Windows. Tool output carries file
                # paths and code excerpts from an arbitrary repository, and a
                # mangled one is a finding nobody can act on.
                encoding="utf-8",
                errors="replace",
                timeout=timeout + _CLI_GRACE_SECONDS,
                env=_docker_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._force_remove(name)
            logger.warning("sandbox timed out", extra={"image": spec.image})
            return SandboxResult(
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
                repo_mount=self._mount_arguments(repo_path)[1],
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # No docker binary, or no daemon to talk to. Indistinguishable from
            # the caller's point of view from having no sandbox at all, so it
            # raises the same exception and produces the same errored check.
            raise SandboxUnavailable(
                f"Could not start a sandbox for {spec.image}: {type(exc).__name__}"
            ) from exc

        logger.info(
            "sandbox finished",
            extra={"image": spec.image, "exit_code": result.returncode},
        )
        return SandboxResult(
            exit_code=result.returncode,
            stdout=_truncate(result.stdout or ""),
            stderr=_truncate(result.stderr or ""),
            timed_out=False,
            repo_mount=self._mount_arguments(repo_path)[1],
        )

    def verify(self) -> str | None:
        """Check the daemon is reachable and the clone volume really exists.

        Returns a reason it will not work, or None. Worth a subprocess at
        startup because of how the volume mistake fails: `source=` naming a
        volume that does not exist does not error, it *creates* an empty one —
        so every tool would scan nothing. Loud at boot beats mystifying at scan
        time.
        """
        if self._probe(["version", "--format", "{{.Server.Version}}"]) != 0:
            return "the Docker daemon is not reachable"
        if self._volume and not self.volume_exists(self._volume):
            return f"volume {self._volume!r} does not exist on the host daemon"
        return None

    def volume_exists(self, name: str) -> bool:
        """Whether the host daemon knows this volume.

        Separate from verify() because a missing *cache* volume is not fatal the
        way a missing clone volume is: Gitleaks needs no cache and should still
        run. The caller decides how loudly to complain.
        """
        return self._probe(["volume", "inspect", name]) == 0

    def _probe(self, arguments: list[str]) -> int:
        try:
            result = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [self._docker, *arguments],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                env=_docker_environment(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return -1
        return result.returncode

    def describe(self, spec: SandboxSpec, *, repo_path: Path) -> str:
        """The command that would run, for logs and for debugging by hand."""
        return shlex.join(self._build_command(spec, repo_path, "<name>"))


_sandbox: SandboxRunner = NullSandbox()


def get_sandbox() -> SandboxRunner:
    return _sandbox


def set_sandbox(sandbox: SandboxRunner) -> None:
    """Swap the implementation. Called by the worker at startup when a runtime
    is configured, and by tests that need a predictable one."""
    global _sandbox
    _sandbox = sandbox
