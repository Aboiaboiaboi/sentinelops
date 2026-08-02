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
import re
import threading
from collections.abc import Iterable, Iterator
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


class CheckOutcome(enum.StrEnum):
    """What happened to one check.

    `passed` and `skipped` are the distinction this type exists for. Before it,
    a scanner returned findings only, so "this service has a health endpoint"
    and "this is a CLI tool, the question does not apply" were both an empty
    list — the difference destroyed at the moment it was known.

    `errored` is the same argument one level further. A check backed by a tool
    that timed out, crashed, or had no sandbox to run in has not established
    anything — but it is *our* failure, not a property of the repository.
    Recording it as `skipped` would tell somebody the question did not apply to
    their code, which is untrue; recording it as `passed` would be worse.
    """

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERRORED = "errored"


#: Outcomes that mean the check actually reached a verdict about the repository.
#: `skipped` and `errored` are both "no verdict", for different reasons, and the
#: worker uses this to decide whether a category assessed anything at all.
CONCLUSIVE_OUTCOMES = frozenset({CheckOutcome.PASSED, CheckOutcome.FAILED})


@dataclass(frozen=True, slots=True)
class CheckSpec:
    """One thing a scanner looks for, declared once.

    `id` is stable and stored; renaming a check must not orphan the history of
    scans that reported it. `title` is what a person reads.
    """

    id: str
    title: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check on one repository.

    Carries `title` rather than looking it up from the spec at render time, so
    a scan stays a faithful snapshot: a check reworded next year does not
    retroactively change what an old scan said it examined.
    """

    id: str
    title: str
    outcome: CheckOutcome
    #: Why there is no verdict — that it did not apply (SKIPPED), or that it
    #: could not be completed (ERRORED). Never set for PASSED or FAILED.
    reason: str | None = None
    #: The problem found. Only ever set for FAILED.
    finding: ScanFinding | None = None


def passed(check: CheckSpec) -> CheckResult:
    return CheckResult(id=check.id, title=check.title, outcome=CheckOutcome.PASSED)


def skipped(check: CheckSpec, reason: str) -> CheckResult:
    """Not applicable here, with the reason a user can read.

    A reason is required, not optional: "skipped" with no explanation is the
    same dead end as the silence this type replaced.
    """
    return CheckResult(id=check.id, title=check.title, outcome=CheckOutcome.SKIPPED, reason=reason)


def failed(check: CheckSpec, finding: ScanFinding) -> CheckResult:
    return CheckResult(id=check.id, title=check.title, outcome=CheckOutcome.FAILED, finding=finding)


def errored(check: CheckSpec, reason: str) -> CheckResult:
    """The check could not be completed, with the reason a user can read.

    Never carries a finding: a check that did not finish has not established
    that anything is wrong, and deducting points for our own failure would
    charge the repository for our outage.
    """
    return CheckResult(id=check.id, title=check.title, outcome=CheckOutcome.ERRORED, reason=reason)


def findings_of(results: Iterable[CheckResult]) -> list[ScanFinding]:
    """The findings among a scanner's results, for scoring and storage.

    Scoring is unchanged by check results — it reads exactly the findings it
    always did, and this is the one line that keeps that true.
    """
    return [result.finding for result in results if result.finding is not None]


@runtime_checkable
class Scanner(Protocol):
    """What the worker requires of a scanner."""

    category: str

    #: Every check this scanner performs. Declared so that the set of checks is
    #: knowable without running the scanner, and so a shared test can assert
    #: that a run accounts for every one of them.
    CHECKS: tuple[CheckSpec, ...]

    def scan(self, repo: "RepositoryIndex") -> list[CheckResult]:
        """Inspect a checkout and report the outcome of every check.

        Takes the shared index rather than a path, so that six scanners do not
        each walk the same tree. See `RepositoryIndex`.

        Returns one result per declared check — passed, failed or skipped.
        Returning results rather than findings is what makes a score
        explainable: a category showing full marks can say *what it verified*
        rather than merely having nothing to complain about.

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


# Directory names that mean "the tests live here" — including the places test
# *support* files live. Fixtures, mocks and testdata joined the list when the
# security scanner reported the fake certificates under a fixtures/ directory
# as committed credentials: everything under these directories is non-production
# by definition, whatever its extension.
TEST_DIRECTORIES = frozenset(
    {
        "tests",
        "test",
        "spec",
        "specs",
        "__tests__",
        "e2e",
        "fixtures",
        "fixture",
        "__mocks__",
        "mocks",
        "testdata",
    }
)

# Filename shapes that mean "this file is a test", across the ecosystems the
# framework detector recognises.
TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.", "test.")


# Frameworks that serve traffic. Several categories ask "is this even a
# service?" before checking something only a service should have — a CLI tool
# with no health endpoint or no metrics is correct, not broken.
SERVICE_FRAMEWORKS = frozenset(
    {
        "FastAPI",
        "Django",
        "Flask",
        "Pyramid",
        "Tornado",
        "Express",
        "NestJS",
        "Next.js",
        "Nuxt",
        "Spring Boot",
        "Quarkus",
        "Micronaut",
        "Ruby on Rails",
        "Sinatra",
        "Laravel",
        "Symfony",
    }
)


# The conventional "a machine wrote this" header. The Go form is an actual
# specification (golang.org/s/generatedcode) and `@generated` is recognised by
# most code-review tooling; the rest are what protobuf and friends emit.
#
# Reading .gitignore was considered for this and measured as useless: a clone
# contains only tracked files, so ignored paths were never there. Generated code
# is the opposite — deliberately committed, and on kubernetes/client-go it is
# 82% of the repository.
GENERATED_MARKER = re.compile(
    r"(?:Code generated .{0,80}DO NOT EDIT"
    r"|@generated"
    r"|Autogenerated by"
    r"|Generated by the protocol buffer"
    r"|AUTO-GENERATED)",
    re.IGNORECASE,
)

# Only the header is examined. The marker is a header convention, and reading
# whole files just to classify them would defeat the point of skipping them.
GENERATED_HEADER_BYTES = 2000


def is_generated_file(path: Path) -> bool:
    """Whether a file announces that a machine wrote it.

    Excluded from the production set: nobody is going to split a 4000-line
    generated client into modules, and reporting it as a problem is advice the
    author cannot act on.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(GENERATED_HEADER_BYTES)
    except OSError:
        return False
    return bool(GENERATED_MARKER.search(head.decode("utf-8", errors="replace")))


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


# A whole-line comment, across the comment syntaxes of the source languages the
# index recognises. Used by `code_only`.
_COMMENT_LINE = re.compile(r"^\s*(?:#|//|/\*|\*(?!/)|--)")


def code_only(content: str) -> str:
    """Drop whole-line comments, preserving line structure for the rest.

    For checks whose evidence must come from code rather than commentary about
    code. A comment explaining why pooling was disabled is not pooling being
    disabled, and prose naming a secret pattern is not a secret. The scalability
    scanner learned this by reporting its own regex definitions during the
    self-scan; shared here so the security scanner does not relearn it.

    Line-based on purpose: trailing comments are kept, because stripping them
    would need per-language string-literal parsing to avoid mangling a `#`
    inside a value — and a false *negative* from a trailing comment is cheaper
    than corrupted evidence.
    """
    return "\n".join(line for line in content.splitlines() if not _COMMENT_LINE.match(line))


# How much file content the index will hold on to. Beyond this, reads still
# work — they just stop being remembered. A bound rather than "cache
# everything" because a repository is allowed to be 500 MB, and holding a
# meaningful fraction of that per concurrent scan would put the worker under
# memory pressure for a saving nobody asked for.
MAX_CACHED_BYTES = 32_000_000

# Root-level files that declare dependencies, across the ecosystems the
# framework detector recognises.
_MANIFEST_NAMES = (
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "setup.py",
    "setup.cfg",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
    "composer.json",
)


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
    #: Source files a machine wrote, identified by their header.
    generated_files: tuple[Path, ...]
    #: Hand-written, non-test source — what a person is responsible for, and
    #: what almost every check should look at.
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
    # One index is now shared by threads: the security scanner runs its three
    # tools concurrently and every one of them is handed this object. The dict
    # itself is fine under the GIL, but `_cached_bytes += len(content)` is a
    # read-modify-write, and two threads interleaving there lose an update and
    # let the cache grow past its budget. compare=False because a lock is not
    # part of what makes two indexes equal, and it is unpicklable besides.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

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
        excluded = set(test_files)

        # Only non-test files are checked for the generated marker, so a large
        # repository pays one small header read per candidate rather than a full
        # read of files it is about to ignore.
        generated_files = tuple(
            p for p in source_files if p not in excluded and is_generated_file(p)
        )
        excluded |= set(generated_files)

        return cls(
            path=repo_path,
            files=files,
            source_files=source_files,
            test_files=test_files,
            generated_files=generated_files,
            production_files=tuple(p for p in source_files if p not in excluded),
            root_names=root_names,
            framework=framework,
        )

    def read(self, path: Path, *, max_bytes: int = MAX_READ_BYTES) -> str:
        """Read a file, remembering the result within the byte budget.

        Safe to call from several threads. The read itself is deliberately
        outside the lock: it is the slow part, the tree cannot change during a
        scan, and holding a lock across file I/O would serialise the readers
        this exists to speed up. Two threads racing the same new path both read
        it and agree on the answer — wasteful once, never wrong.
        """
        cached = self._cache.get(path)
        if cached is not None:
            return cached

        content = read_text(path, max_bytes=max_bytes)
        with self._lock:
            if path not in self._cache and self._cached_bytes + len(content) <= MAX_CACHED_BYTES:
                self._cache[path] = content
                self._cached_bytes += len(content)
        return content

    @property
    def is_service(self) -> bool:
        """Whether this repository serves traffic, as far as detection can tell.

        Several checks only make sense for a service. Asking a CLI tool for a
        health endpoint, or a library for its own metrics, produces findings the
        author cannot act on and should not want to.
        """
        return self.framework in SERVICE_FRAMEWORKS

    def manifest_text(self) -> str:
        """Every root-level dependency manifest, concatenated.

        Declared dependencies are the most reliable evidence a project uses
        something: a library can be imported in one file out of five hundred,
        but it is always in the manifest.
        """
        return "".join(
            self.read(self.path / name)
            for name in _MANIFEST_NAMES
            if name.lower() in self.root_names
        )

    def relative(self, path: Path) -> str:
        """A path as the user would recognise it, with forward slashes."""
        return path.relative_to(self.path).as_posix()

    def has_root_entry(self, *names: str) -> bool:
        """Whether any of `names` sits directly in the repository root."""
        return any(name.lower() in self.root_names for name in names)
