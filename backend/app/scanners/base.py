"""The scanner boundary.

A scanner takes a path and returns findings. It knows nothing about the
database, the API, the queue, or the other scanners — which is what makes adding
a category later cheap, and what makes every scanner testable against a
directory in tmp_path with no infrastructure at all.

Severity lives here rather than in models/ so that importing a scanner does not
drag SQLAlchemy in behind it. The ORM imports this definition; the dependency
runs models -> scanners, never the other way, and this module imports nothing
but the standard library.

Scanners are deliberately **synchronous**. They are file and subprocess work,
and running one directly inside a coroutine would stall every other job on the
worker — the same mistake that made an unrelated endpoint take 1561ms under
concurrent logins. The worker dispatches them with asyncio.to_thread.
"""

import enum
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class Severity(enum.StrEnum):
    """How bad a finding is.

    Uppercase on the wire — the only enum in the API contract that is. The
    frontend uses these strings directly as lookup keys for badge styling, so
    the casing is load-bearing rather than cosmetic.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class ScanFinding:
    """One thing a scanner noticed.

    Deliberately not the ORM `Finding`. A scanner that returned database rows
    would need a session, an identity map and a transaction to be tested; this
    is a value object the worker translates once it has somewhere to put it.
    """

    category: str
    severity: Severity
    title: str
    description: str
    recommendation: str
    # Points deducted from this category's weight. Stored per finding so a score
    # can be explained line by line rather than presented as an opaque total.
    score_impact: int


@runtime_checkable
class Scanner(Protocol):
    """What the worker requires of a scanner."""

    category: str

    def scan(self, repo_path: Path) -> list[ScanFinding]:
        """Inspect a checkout and report what is wrong with it.

        Must not raise for ordinary problems — an unreadable file or an
        unfamiliar layout is a finding or a silence, not an exception. Raising
        marks the whole category failed, which costs the user its entire weight.
        """
        ...


# Directories whose contents are not the author's code. Walking them wastes time
# and produces findings about other people's dependencies, which is noise the
# user cannot act on.
SKIPPED_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".next",
        ".terraform",
    }
)

# Reading more than this from one file is pointless for the kind of structural
# checks these scanners do, and a repository can contain a multi-gigabyte blob.
MAX_READ_BYTES = 1_000_000

# What counts as hand-written source, as opposed to configuration, data or
# documentation. Shared rather than per-scanner: every category needs the same
# answer, and six copies would drift apart one language at a time.
SOURCE_SUFFIXES = frozenset(
    {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".ex",
        ".exs",
        ".go",
        ".h",
        ".hpp",
        ".java",
        ".js",
        ".jsx",
        ".kt",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".scala",
        ".swift",
        ".ts",
        ".tsx",
    }
)


def is_source_file(path: Path) -> bool:
    return path.suffix.lower() in SOURCE_SUFFIXES


def iter_files(root: Path) -> Iterator[Path]:
    """Yield regular files under `root`, safely.

    Symlinks are skipped entirely rather than followed. A repository is
    untrusted input and can contain a link to anywhere on the filesystem;
    following one would read outside the checkout, and following a link that
    points at its own parent would not terminate.

    Provided here so that no scanner has to write this walk itself — each one
    getting it subtly wrong in its own way is exactly how a boundary like this
    stops being safe.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIPPED_DIRECTORIES and not (Path(dirpath) / name).is_symlink()
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            yield path


def read_text(path: Path, *, max_bytes: int = MAX_READ_BYTES) -> str:
    """Read a text file, or return an empty string.

    Never raises. A binary file, a permission error or a file that vanished
    mid-scan is not a reason to fail a category and cost the user its weight.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes)
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")
