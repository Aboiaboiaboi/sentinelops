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
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
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


# Variables the operating system itself needs in any child process, passed
# through when present. On Windows a process without SystemRoot cannot
# initialise winsock, so every DNS lookup dies with "getaddrinfo() thread
# failed to start" — found the first time a host-run worker cloned over HTTPS,
# because the tests clone file:// URLs that never resolve a name, and the
# Docker worker is Linux, where the minimal environment is enough.
_OS_PASSTHROUGH_VARIABLES = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP")


def _git_environment() -> dict[str, str]:
    """A minimal environment, so nothing on the host leaks into the clone."""
    environment = {
        name: value
        for name in _OS_PASSTHROUGH_VARIABLES
        if (value := os.environ.get(name)) is not None
    }
    return environment | {
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


# The userinfo portion of a URL — `scheme://anything@host`. An installation
# token reaches git through a header rather than the URL, so this should never
# match on our own clones; it exists because "should never" is not a control,
# and a user-supplied URL can carry credentials of its own.
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][\w+.-]*://)[^/\s@]*@")


def redact_credentials(text: str) -> str:
    """Mask anything that looks like a credential in a URL.

    Applied to git's stderr before it goes anywhere — including the worker log,
    which is the one place it *is* kept.
    """
    return _URL_CREDENTIALS.sub(r"\g<scheme>***@", text)


@dataclass(frozen=True, slots=True)
class CommitInfo:
    """The HEAD commit of a checkout.

    What makes a score change attributable: "this dropped 6 points" is far more
    useful as "this dropped 6 points *at this commit*".
    """

    sha: str
    message: str
    author: str
    committed_at: datetime


# Longest commit subject and author name kept. A repository is untrusted input
# and neither field is length-limited by git — a subject can be megabytes, and
# storing that verbatim would bloat a row nobody wants to read.
MAX_COMMIT_MESSAGE = 500
MAX_COMMIT_AUTHOR = 255

# NUL separates the fields: it is the one byte git will not emit inside any of
# them, so the split cannot be confused by a subject containing whatever a
# repository author felt like typing.
_COMMIT_FORMAT = "%H%x00%an%x00%aI%x00%s"


def _sanitise(value: str, limit: int) -> str:
    """Strip control characters and truncate.

    Postgres rejects NUL in a text column outright, so an unsanitised commit
    subject from a hostile repository is an insert that fails the whole scan.
    Other control characters are dropped because this is display text.
    """
    cleaned = "".join(char for char in value if char.isprintable() or char in " \t")
    return cleaned.strip()[:limit]


def read_head_commit(checkout: Path) -> CommitInfo | None:
    """The checkout's HEAD commit, or None if it has none.

    Never raises. A repository with no commits at all has no HEAD — which is
    not an error, it is the empty-repository case — and neither is a git that
    declines to answer. Commit context is a nice-to-have on top of a scan, so
    nothing here may fail the scan that already succeeded.
    """
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "log", "-1", f"--format={_COMMIT_FORMAT}"],
            cwd=checkout,
            capture_output=True,
            # Explicit UTF-8, not text=True. text=True decodes with the
            # *locale* encoding, which on Windows is cp1252 — so a commit
            # message containing an em-dash, an accent, an emoji or any CJK
            # text came back mangled. git emits UTF-8 regardless of platform.
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=_git_environment(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    parts = result.stdout.split("\0")
    if len(parts) != 4:
        return None

    sha, author, committed_at_raw, message = parts
    try:
        committed_at = datetime.fromisoformat(committed_at_raw.strip())
    except ValueError:
        return None

    if not sha.strip():
        return None

    return CommitInfo(
        sha=sha.strip()[:40],
        message=_sanitise(message, MAX_COMMIT_MESSAGE),
        author=_sanitise(author, MAX_COMMIT_AUTHOR),
        committed_at=committed_at,
    )


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
            # UTF-8 rather than text=True, for the same reason as
            # read_head_commit: the locale decoder mangles any non-ASCII in
            # git's output, and a repository name can contain anything.
            encoding="utf-8",
            errors="replace",
            timeout=limits.timeout_seconds,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloneTimedOut(f"Cloning took longer than {limits.timeout_seconds}s.") from exc

    if result.returncode != 0:
        # Full stderr, redacted, rather than the last line alone.
        #
        # Keeping one line looked like a safety measure and was not: the last
        # line can carry a URL just as easily as any other, while throwing away
        # the line that says *why*. git puts the diagnosis in the middle —
        # "does not appear to be a git repository" — and ends with generic
        # advice, so truncating left every local failure unclassifiable.
        #
        # The actual control is two-part: credentials are stripped here, and
        # nothing derived from this text is ever persisted (see
        # classify_clone_failure, which reads it and stores fixed text).
        raise CloneFailed(redact_credentials(result.stderr or "").strip() or "git clone failed")

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
