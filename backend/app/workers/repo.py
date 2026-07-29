"""Cloning repositories for scanning.

Everything here treats the repository as hostile. It is arbitrary user-submitted
code, and the fact that this phase only *reads* the files does not make the
clone itself safe — git is a large program being pointed at an attacker-chosen
URL.

The scheme is validated where the URL enters the system, in schemas/project.py,
which is why this module does not re-check it: a single validation point that is
tested beats two that can disagree. What this module owns is everything that can
go wrong once git is actually running.

Container-level isolation arrives with the security scanners, when third-party
binaries start executing against this directory. These limits are what is
proportionate until then.
"""

import asyncio
import logging
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)


class CloneError(Exception):
    """A repository could not be made available for scanning."""


class CloneFailed(CloneError):
    """git exited non-zero — unreachable, private, or not a repository."""


class CloneTimedOut(CloneError):
    """git did not finish within the allowed time."""


class CloneTooLarge(CloneError):
    """The working tree exceeded the size or file-count limit."""


@dataclass(frozen=True)
class CloneLimits:
    timeout_seconds: int
    max_bytes: int
    max_files: int

    @classmethod
    def from_settings(cls) -> "CloneLimits":
        settings = get_settings()
        return cls(
            timeout_seconds=settings.clone_timeout_seconds,
            max_bytes=settings.clone_max_bytes,
            max_files=settings.clone_max_files,
        )


def _git_command(url: str, destination: Path, hooks_dir: Path) -> list[str]:
    return [
        "git",
        # A hooks directory we own and leave empty. Nothing in a cloned
        # repository can put an executable somewhere git will run from.
        "-c",
        f"core.hooksPath={hooks_dir}",
        # ext:: URLs hand git a shell command to run. There is no legitimate use
        # for one here, and it is a direct path from a submitted URL to code
        # execution on the worker.
        "-c",
        "protocol.ext.allow=never",
        "clone",
        # No history: a scan reads the current tree, and full history on a large
        # repository is most of the download for none of the value.
        "--depth",
        "1",
        "--single-branch",
        "--no-tags",
        # Submodules are deliberately not fetched. They are attacker-controlled
        # URLs pointing anywhere, resolved by us, at arbitrary depth.
        "--quiet",
        "--",
        url,
        str(destination),
    ]


def _git_environment() -> dict[str, str]:
    """A minimal environment, so nothing on the host leaks into the clone."""
    return {
        "PATH": os.environ.get("PATH", ""),
        # Without this, an authenticating URL blocks on a credential prompt that
        # nobody will ever answer, and the job holds a worker slot until the
        # timeout fires.
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "",
        # Ignore host-level git configuration entirely. The worker's behaviour
        # should not depend on whatever is in /etc/gitconfig.
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        # LFS pointers stay as pointers. Smudging them would fetch objects with
        # no size limit and no relevance to reading source files.
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


def _measure_tree(root: Path, limits: CloneLimits) -> tuple[int, int]:
    """Total size and file count, raising as soon as either limit is passed.

    Symlinks are counted but never followed or measured through. A repository
    can contain a link to anywhere on the filesystem, and following one would
    both mis-measure the tree and walk outside it.
    """
    total_bytes = 0
    total_files = 0

    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath) / name
            total_files += 1
            if total_files > limits.max_files:
                raise CloneTooLarge(f"Repository has more than {limits.max_files} files.")
            if path.is_symlink():
                continue
            try:
                total_bytes += path.stat().st_size
            except OSError:
                # A file that vanished or cannot be stat'd is not worth failing
                # the whole scan over.
                continue
            if total_bytes > limits.max_bytes:
                raise CloneTooLarge(f"Repository is larger than {limits.max_bytes} bytes.")

    return total_bytes, total_files


def _remove_workspace(workspace: Path) -> None:
    """Delete a clone, including the parts git makes read-only.

    git writes its object files without a write bit. POSIX only needs write
    permission on the *directory* to unlink them, so this is invisible on Linux
    — but Windows refuses, and rmtree(ignore_errors=True) would swallow that
    and leave the checkout behind with no signal at all. Clearing the bit and
    retrying handles both.

    A failure is logged rather than raised: the scan itself already succeeded or
    failed on its own merits, and a leaked directory should not change that. But
    it must be visible, because silently leaking clones is how a worker fills
    its disk with other people's code.
    """

    def clear_readonly_and_retry(func, target, _exc) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        shutil.rmtree(workspace, onexc=clear_readonly_and_retry)
    except OSError:
        logger.warning("could not remove clone workspace", extra={"path": str(workspace)})


def clone_repository(
    url: str,
    destination: Path,
    *,
    hooks_dir: Path,
    limits: CloneLimits,
    credential_header: str | None = None,
) -> tuple[int, int]:
    """Clone `url` into `destination`, returning (bytes, files).

    Blocking on purpose. Callers run it off the event loop — a clone is seconds
    to minutes of subprocess and disk, and a coroutine holding the loop for that
    long stalls every other job the worker is running.

    `credential_header` is unused until private repositories are supported. It
    is passed through the environment rather than the command line so the token
    appears neither in the cloned repo's config nor in a process listing.
    """
    command = _git_command(url, destination, hooks_dir)
    env = _git_environment()

    if credential_header is not None:
        env["SENTINELOPS_GIT_AUTH"] = credential_header
        command[1:1] = ["--config-env=http.extraHeader=SENTINELOPS_GIT_AUTH"]

    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            command,
            capture_output=True,
            text=True,
            timeout=limits.timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloneTimedOut(f"Cloning took longer than {limits.timeout_seconds}s.") from exc

    if result.returncode != 0:
        # git's stderr can echo the URL, which for a private repository would
        # carry a token in some formats. Only the last line is kept and it is
        # never returned to the API — it goes to the worker log.
        detail = (result.stderr or "").strip().splitlines()
        raise CloneFailed(detail[-1] if detail else "git clone failed")

    return _measure_tree(destination, limits)


@asynccontextmanager
async def cloned_repository(
    url: str,
    *,
    limits: CloneLimits | None = None,
    credential_header: str | None = None,
) -> AsyncIterator[Path]:
    """Clone into a disposable directory and remove it afterwards.

    The cleanup is in a `finally`, so a repository is deleted whether the scan
    succeeded, failed, or the clone itself blew a limit. Without that the disk
    fills with abandoned checkouts of other people's code.
    """
    limits = limits or CloneLimits.from_settings()
    root = get_settings().clone_root
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)

    workspace = Path(await asyncio.to_thread(tempfile.mkdtemp, dir=root, prefix="scan-"))
    checkout = workspace / "repo"
    hooks = workspace / "nohooks"
    await asyncio.to_thread(hooks.mkdir)

    try:
        size, files = await asyncio.to_thread(
            clone_repository,
            url,
            checkout,
            hooks_dir=hooks,
            limits=limits,
            credential_header=credential_header,
        )
        logger.info("repository cloned", extra={"bytes": size, "files": files})
        yield checkout
    finally:
        await asyncio.to_thread(_remove_workspace, workspace)
