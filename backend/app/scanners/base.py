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
from dataclasses import dataclass, field
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

    def scan(self, repo: "RepositoryIndex") -> list[ScanFinding]:
        """Inspect a checkout and report what is wrong with it.

        Takes the shared index rather than a path, so that six scanners do not
        each walk the same tree. See `RepositoryIndex`.

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


# Directory names that mean "the tests live here".
TEST_DIRECTORIES = frozenset({"tests", "test", "spec", "specs", "__tests__", "e2e"})

# Filename shapes that mean "this file is a test", across the ecosystems the
# framework detector recognises.
TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.", "test.")


def is_test_file(path: Path, root: Path) -> bool:
    """Whether a file is part of the test suite rather than the product.

    Shared because the distinction cuts both ways: the architecture scanner
    wants to know tests *exist*, while every other category wants to ignore
    them. A test that deliberately swallows an exception or calls out without a
    timeout is not a production reliability problem, and flagging it is how a
    scanner earns a reputation for crying wolf.
    """
    relative = path.relative_to(root)
    if any(part.lower() in TEST_DIRECTORIES for part in relative.parts[:-1]):
        return True
    name = path.name.lower()
    return any(marker in name for marker in TEST_MARKERS)


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


# How much file content the index will hold on to. Beyond this, reads still
# work — they just stop being remembered. A bound rather than "cache
# everything" because a repository is allowed to be 500 MB, and holding a
# meaningful fraction of that per concurrent scan would put the worker under
# memory pressure for a saving nobody asked for.
MAX_CACHED_BYTES = 32_000_000


@dataclass
class RepositoryIndex:
    """One pass over a checkout, shared by every scanner.

    Built once per scan and handed to all six. Before this existed each scanner
    walked the tree itself — the deployment scanner managed it twice in one
    call — so a six-scanner run meant something like a dozen traversals of the
    same directory to answer questions that never change mid-scan.

    Also memoises reads, so several scanners grepping the same source files
    costs one read rather than one each.

    Not a general-purpose cache: it is built after the clone and thrown away
    with it, so nothing here has to worry about the tree changing underneath.
    """

    path: Path
    #: Every regular file, symlinks and vendored directories already excluded.
    files: tuple[Path, ...]
    #: The subset that is hand-written source.
    source_files: tuple[Path, ...]
    #: Source files belonging to the test suite.
    test_files: tuple[Path, ...]
    #: Source files that are not tests — what actually ships.
    production_files: tuple[Path, ...]
    #: Lower-cased names of entries directly in the repository root, for the
    #: many checks that are really "is there a README / lockfile / CI config".
    root_names: frozenset[str]

    #: What the framework detector made of this repository, or None. Lets a
    #: check ask "is this even a service?" — a CLI tool with no health endpoint
    #: is correct, not broken, and flagging it would be a false positive.
    framework: str | None = None

    _cache: dict[Path, str] = field(default_factory=dict, repr=False)
    _cached_bytes: int = field(default=0, repr=False)

    @classmethod
    def build(cls, repo_path: Path, *, framework: str | None = None) -> "RepositoryIndex":
        """Walk the tree once. Blocking — callers run it off the event loop."""
        files = tuple(iter_files(repo_path))
        try:
            root_names = frozenset(entry.name.lower() for entry in repo_path.iterdir())
        except OSError:
            root_names = frozenset()

        source_files = tuple(p for p in files if is_source_file(p))
        test_files = tuple(p for p in source_files if is_test_file(p, repo_path))
        return cls(
            path=repo_path,
            files=files,
            source_files=source_files,
            test_files=test_files,
            production_files=tuple(p for p in source_files if p not in set(test_files)),
            root_names=root_names,
            framework=framework,
        )

    def read(self, path: Path, *, max_bytes: int = MAX_READ_BYTES) -> str:
        """Read a file, remembering the result within the byte budget."""
        cached = self._cache.get(path)
        if cached is not None:
            return cached

        content = read_text(path, max_bytes=max_bytes)
        if self._cached_bytes + len(content) <= MAX_CACHED_BYTES:
            self._cache[path] = content
            self._cached_bytes += len(content)
        return content

    def relative(self, path: Path) -> str:
        """A path as the user would recognise it, with forward slashes."""
        return path.relative_to(self.path).as_posix()

    def has_root_entry(self, *names: str) -> bool:
        """Whether any of `names` sits directly in the repository root."""
        return any(name.lower() in self.root_names for name in names)
