"""The sandbox boundary.

These tests are about *containment*, not about Docker. Almost all of them fake
`subprocess.run` and assert on the argv that would have been executed, because
the properties that matter — no network, no writes, no capabilities, no
credentials — are decided entirely by those flags. A flag deleted during a
refactor is a silent loss of isolation, and only an assertion notices.

One integration test at the bottom runs a real container, and skips when there
is no daemon to run it against.
"""

import functools
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.utils.sandbox import (
    CACHE_MOUNT,
    MAX_OUTPUT_BYTES,
    REPO_MOUNT,
    REPO_PLACEHOLDER,
    DockerSandbox,
    NullSandbox,
    SandboxResult,
    SandboxRunner,
    SandboxSpec,
    SandboxUnavailable,
    get_sandbox,
    set_sandbox,
)

SPEC = SandboxSpec(
    image="ghcr.io/gitleaks/gitleaks:v8.30.1",
    command=("dir", REPO_PLACEHOLDER),
    timeout_seconds=60,
)


class RecordingRun:
    """Stands in for subprocess.run, remembering every call."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self._result = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append((list(command), kwargs))
        return self._result

    @property
    def command(self) -> list[str]:
        return self.calls[0][0]

    @property
    def environment(self) -> dict[str, str]:
        return self.calls[0][1]["env"]


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> RecordingRun:
    recorder = RecordingRun()
    monkeypatch.setattr(subprocess, "run", recorder)
    return recorder


def _mounts(command: list[str]) -> list[str]:
    return [value for flag, value in zip(command, command[1:], strict=False) if flag == "--mount"]


# ---------------------------------------------------------------------------
# The spec refuses to describe an unpinned run
# ---------------------------------------------------------------------------


def test_a_pinned_tag_is_accepted() -> None:
    assert SandboxSpec(image="aquasec/trivy:0.58.1", command=(), timeout_seconds=1)


def test_a_digest_is_accepted() -> None:
    """The strongest pin there is, so it must not be rejected for lacking a tag."""
    assert SandboxSpec(image="alpine@sha256:" + "a" * 64, command=(), timeout_seconds=1)


@pytest.mark.parametrize(
    "image",
    [
        "aquasec/trivy",  # no tag at all — resolves to :latest
        "aquasec/trivy:latest",
        "registry.example.com:5000/trivy",  # a port is not a tag
    ],
)
def test_an_unpinned_image_is_refused(image: str) -> None:
    """An image that can change under a scan makes a score change unexplainable."""
    with pytest.raises(ValueError, match="pinned|latest"):
        SandboxSpec(image=image, command=(), timeout_seconds=1)


# ---------------------------------------------------------------------------
# Containment flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        "--cpus=1",
    ],
)
def test_every_containment_flag_is_present(run: RecordingRun, flag: str, tmp_path: Path) -> None:
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert flag in run.command


def test_the_container_runs_as_nobody(run: RecordingRun, tmp_path: Path) -> None:
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert "--user" in run.command
    assert run.command[run.command.index("--user") + 1] == "65534:65534"


def test_memory_and_swap_are_capped_together(run: RecordingRun, tmp_path: Path) -> None:
    """--memory alone leaves swap unbounded, so the tool swaps instead of dying."""
    DockerSandbox().run(
        SandboxSpec(image="x/y:1", command=(), timeout_seconds=1, memory_mb=256),
        repo_path=tmp_path,
    )

    assert "--memory=256m" in run.command
    assert "--memory-swap=256m" in run.command


def test_writable_scratch_is_a_bounded_tmpfs(run: RecordingRun, tmp_path: Path) -> None:
    """--read-only would otherwise break tools that need a temp file, and an
    unbounded tmpfs is charged to the worker's memory."""
    DockerSandbox().run(SPEC, repo_path=tmp_path)
    tmpfs = [mount for mount in _mounts(run.command) if "type=tmpfs" in mount]

    assert tmpfs == [f"type=tmpfs,destination=/tmp,tmpfs-size={256 * 1024 * 1024}"]


# ---------------------------------------------------------------------------
# Nothing of the worker's crosses into the run
# ---------------------------------------------------------------------------


def test_no_environment_is_forwarded_to_the_container(run: RecordingRun, tmp_path: Path) -> None:
    """`docker run` passes nothing by default. The rule is that it stays that way."""
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert "-e" not in run.command
    assert not any(argument.startswith("--env") for argument in run.command)


def test_the_docker_client_gets_a_built_environment_not_the_worker_s(
    run: RecordingRun, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker holds a GitHub App key that can mint access to every installed
    user's repositories. A dictionary that never contained it cannot leak it."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@host/db")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_B64", "c2VjcmV0")
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")

    DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert "DATABASE_URL" not in run.environment
    assert "GITHUB_APP_PRIVATE_KEY_B64" not in run.environment
    # ...but the CLI still has to be able to find its daemon.
    assert run.environment["DOCKER_HOST"] == "unix:///var/run/docker.sock"


# ---------------------------------------------------------------------------
# Mounting the checkout — the thing most likely to be wrong
# ---------------------------------------------------------------------------


def test_a_host_worker_bind_mounts_the_real_path(run: RecordingRun, tmp_path: Path) -> None:
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    expected = f"type=bind,source={tmp_path.resolve()},target={REPO_MOUNT},readonly"
    assert expected in _mounts(run.command)
    # The command is rewritten to the path the container will actually see.
    assert run.command[-1] == REPO_MOUNT


def test_a_containerised_worker_mounts_the_volume_at_the_same_path(
    run: RecordingRun,
) -> None:
    """The worker is itself a container and `docker run` is served by the host
    daemon, so a path that only exists inside the worker cannot be bind-mounted.
    Mounting the same volume at the same place means no translation is needed."""
    repo = Path("/data/repos/scan-abc/repo")

    DockerSandbox(volume="sentinelops_worker_data").run(SPEC, repo_path=repo)

    expected = "type=volume,source=sentinelops_worker_data,target=/data,readonly"
    assert expected in _mounts(run.command)
    assert run.command[-1] == "/data/repos/scan-abc/repo"


def test_the_checkout_is_never_writable(run: RecordingRun, tmp_path: Path) -> None:
    """A tool that could edit the repository could change what a later scan sees."""
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    repo_mounts = [mount for mount in _mounts(run.command) if "type=tmpfs" not in mount]
    assert repo_mounts
    assert all(mount.endswith(",readonly") for mount in repo_mounts)


def test_the_cache_volume_is_mounted_read_only(run: RecordingRun, tmp_path: Path) -> None:
    """The spec asks for the cache; the runner knows which volume that is. A
    scanner naming a volume would be a scanner reading deployment config."""
    DockerSandbox(cache_volume="sentinelops_sandbox_cache").run(
        SandboxSpec(image="aquasec/trivy:0.72.0", command=(), timeout_seconds=1, needs_cache=True),
        repo_path=tmp_path,
    )

    expected = f"type=volume,source=sentinelops_sandbox_cache,target={CACHE_MOUNT},readonly"
    assert expected in _mounts(run.command)


def test_a_tool_needing_an_unconfigured_cache_is_refused(run: RecordingRun, tmp_path: Path) -> None:
    """Trivy with no vulnerability database finds no vulnerabilities, which is
    indistinguishable from a repository that has none — the worst answer this
    system could give. Refusing produces an errored check instead."""
    with pytest.raises(SandboxUnavailable, match="cache"):
        DockerSandbox().run(
            SandboxSpec(
                image="aquasec/trivy:0.72.0", command=(), timeout_seconds=1, needs_cache=True
            ),
            repo_path=tmp_path,
        )

    assert not run.calls, "nothing should have been started"


def test_a_spec_may_not_exceed_the_operator_s_ceilings(run: RecordingRun, tmp_path: Path) -> None:
    """A tool asks for what it needs; the operator decides what any one tool may
    consume on this machine, and the smaller wins."""
    DockerSandbox(max_memory_mb=256, max_timeout_seconds=30).run(
        SandboxSpec(image="x/y:1", command=(), timeout_seconds=600, memory_mb=4096),
        repo_path=tmp_path,
    )

    assert "--memory=256m" in run.command
    assert run.calls[0][1]["timeout"] <= 30 + 10  # the spec's own grace period


def test_no_cache_volume_means_no_cache_mount(run: RecordingRun, tmp_path: Path) -> None:
    DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert not any(CACHE_MOUNT in mount for mount in _mounts(run.command))


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


def test_a_tool_s_output_comes_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        subprocess, "run", RecordingRun(returncode=1, stdout='{"findings": []}', stderr="noise")
    )

    result = DockerSandbox().run(SPEC, repo_path=tmp_path)

    # Exit code 1 is not failure for every tool — Gitleaks uses it for "leaks
    # found" — so it is reported rather than interpreted here.
    assert result.exit_code == 1
    assert result.stdout == '{"findings": []}'
    assert result.timed_out is False


def test_runaway_output_is_truncated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Captured output lives in the worker's memory, so it cannot be unbounded."""
    monkeypatch.setattr(subprocess, "run", RecordingRun(stdout="x" * (MAX_OUTPUT_BYTES + 5000)))

    result = DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert len(result.stdout) == MAX_OUTPUT_BYTES
    assert result.truncated


def test_ordinary_output_is_not_reported_as_truncated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(subprocess, "run", RecordingRun(stdout="[]"))

    assert not DockerSandbox().run(SPEC, repo_path=tmp_path).truncated


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_a_timeout_kills_the_container_it_stopped_waiting_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """subprocess's timeout kills the docker *client*. The container keeps
    running, holding its memory, invisible to the scan that gave up on it."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(list(command))
        if command[1] == "run":
            raise subprocess.TimeoutExpired(cmd=command, timeout=1)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = DockerSandbox().run(SPEC, repo_path=tmp_path)

    assert result.timed_out
    assert result.stdout == ""

    removal = calls[1]
    assert removal[1:3] == ["rm", "--force"]
    # The same container this run started, not a wildcard.
    started = calls[0]
    assert removal[3] == started[started.index("--name") + 1]


def test_a_missing_docker_binary_is_reported_as_no_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Indistinguishable from having no sandbox at all, so it must not be
    distinguishable to the caller either — both produce an errored check."""

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SandboxUnavailable):
        DockerSandbox().run(SPEC, repo_path=tmp_path)


def test_verify_reports_an_unreachable_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", RecordingRun(returncode=1))

    assert DockerSandbox().verify() == "the Docker daemon is not reachable"


def test_verify_reports_a_missing_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """The expensive mistake: `source=` naming a volume that does not exist
    creates an empty one rather than failing, so every tool scans nothing."""

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        missing = command[1] == "volume"
        return subprocess.CompletedProcess(command, returncode=1 if missing else 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    reason = DockerSandbox(volume="nope").verify()

    assert reason is not None
    assert "nope" in reason


def test_a_missing_cache_volume_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitleaks needs no cache and must still run, so the caller decides how
    loudly to complain rather than verify() refusing the whole sandbox."""

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        missing = command[1] == "volume"
        return subprocess.CompletedProcess(command, returncode=1 if missing else 0, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    # The clone volume is what makes a sandbox usable at all; the cache only
    # decides whether two of the tools can run.
    sandbox = DockerSandbox()

    assert sandbox.verify() is None
    assert sandbox.volume_exists("sentinelops_sandbox_cache") is False


def test_an_existing_volume_is_reported_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", RecordingRun(returncode=0))

    assert DockerSandbox().volume_exists("sentinelops_sandbox_cache") is True


def test_verify_passes_when_the_daemon_and_volume_are_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", RecordingRun(returncode=0))

    assert DockerSandbox(volume="sentinelops_worker_data").verify() is None


# ---------------------------------------------------------------------------
# The default is to refuse
# ---------------------------------------------------------------------------


def test_the_null_sandbox_refuses(tmp_path: Path) -> None:
    with pytest.raises(SandboxUnavailable, match="gitleaks"):
        NullSandbox().run(SPEC, repo_path=tmp_path)


def test_nothing_is_installed_by_default() -> None:
    """A test run, or any environment without a runtime, must not silently
    report repositories as clean because no tool ever looked at them."""
    assert isinstance(get_sandbox(), NullSandbox)


def test_the_implementation_can_be_swapped() -> None:
    class Stub:
        def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
            return SandboxResult(exit_code=0, stdout="", stderr="", timed_out=False)

    original = get_sandbox()
    try:
        set_sandbox(Stub())
        assert get_sandbox() is not original
    finally:
        set_sandbox(original)


@pytest.mark.parametrize("implementation", [NullSandbox(), DockerSandbox()])
def test_both_implementations_satisfy_the_protocol(implementation: object) -> None:
    assert isinstance(implementation, SandboxRunner)


# ---------------------------------------------------------------------------
# What the worker installs at startup
# ---------------------------------------------------------------------------


class FakeSandbox:
    """A DockerSandbox that answers its probes without a daemon."""

    def __init__(self, *, reason: str | None = None, has_cache: bool = True, **kwargs: Any) -> None:
        del kwargs
        self._reason = reason
        self._has_cache = has_cache

    def verify(self) -> str | None:
        return self._reason

    def volume_exists(self, name: str) -> bool:
        del name
        return self._has_cache

    def run(self, spec: SandboxSpec, *, repo_path: Path) -> SandboxResult:
        raise AssertionError("not called")


@pytest.fixture
def worker_startup(monkeypatch: pytest.MonkeyPatch):
    """Runs the worker's on_startup with the global sandbox restored after."""
    from app.workers import settings as worker_settings

    original = get_sandbox()

    async def run(*, enabled: bool = True, cache: str = "", **sandbox_kwargs: Any) -> None:
        monkeypatch.setattr(worker_settings.settings, "sandbox_enabled", enabled)
        monkeypatch.setattr(worker_settings.settings, "sandbox_volume", "vol")
        monkeypatch.setattr(worker_settings.settings, "sandbox_cache_volume", cache)
        monkeypatch.setattr(
            worker_settings, "DockerSandbox", lambda **kwargs: FakeSandbox(**sandbox_kwargs)
        )
        await worker_settings.on_startup({})

    try:
        yield run
    finally:
        set_sandbox(original)


async def test_a_disabled_sandbox_leaves_the_null_one_installed(worker_startup) -> None:
    await worker_startup(enabled=False)

    assert isinstance(get_sandbox(), NullSandbox)


async def test_an_unusable_sandbox_is_not_installed(worker_startup) -> None:
    """A worker that cannot isolate anything must not hold a runner that would
    be asked to try. Errored checks are the correct outcome, not a crash."""
    await worker_startup(reason="the Docker daemon is not reachable")

    assert isinstance(get_sandbox(), NullSandbox)


async def test_a_working_sandbox_replaces_the_null_one(worker_startup) -> None:
    await worker_startup()

    assert isinstance(get_sandbox(), FakeSandbox)


async def test_a_missing_cache_warns_but_still_installs_the_sandbox(
    worker_startup, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gitleaks needs no cache. Refusing the whole sandbox over a missing
    vulnerability database would take a working tool down with a missing one.

    Asserted against stdout rather than caplog: on_startup reconfigures logging
    as its first act, which replaces every root handler — including the one
    caplog installs — so the JSON stream is the only place the line lands.
    """
    await worker_startup(cache="sentinelops_sandbox_cache", has_cache=False)

    assert isinstance(get_sandbox(), FakeSandbox)
    assert "cache volume is missing" in capsys.readouterr().out


async def test_a_warmed_cache_says_nothing(
    worker_startup, capsys: pytest.CaptureFixture[str]
) -> None:
    await worker_startup(cache="sentinelops_sandbox_cache", has_cache=True)

    assert "cache volume is missing" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Against a real daemon
# ---------------------------------------------------------------------------


@functools.cache
def _docker_is_available() -> bool:
    try:
        return (
            subprocess.run(  # noqa: S603, S607 — test-only probe
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=30,
                check=False,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


requires_docker = pytest.mark.skipif(
    not _docker_is_available(), reason="no Docker daemon is reachable"
)


@requires_docker
def test_a_real_container_reads_the_checkout_and_cannot_reach_the_network(
    tmp_path: Path,
) -> None:
    """The one test that proves the flags do what the others assert they say.

    Both halves matter: a sandbox that cannot see the repository finds nothing
    and calls it clean, and a sandbox that can reach the network can send that
    repository somewhere.
    """
    (tmp_path / "hello.txt").write_text("scanned", encoding="utf-8")

    result = DockerSandbox().run(
        SandboxSpec(
            image="alpine:3.21",
            command=(
                "sh",
                "-c",
                f"cat {REPO_PLACEHOLDER}/hello.txt; "
                "wget -q -T 3 -O- http://example.com >/dev/null 2>&1 "
                "&& echo NETWORK || echo NO_NETWORK",
            ),
            timeout_seconds=120,
        ),
        repo_path=tmp_path,
    )

    assert result.exit_code == 0, result.stderr
    assert "scanned" in result.stdout
    assert "NO_NETWORK" in result.stdout
