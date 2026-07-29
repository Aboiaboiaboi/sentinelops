"""Architecture checks.

Structural properties visible from the file tree: is there a test suite, are
builds reproducible, is the code organised, is any of it explained. Nothing here
parses source code — that would be a different and much larger project, and
these checks already separate a repository somebody maintains from one somebody
abandoned.

The impacts sum to exactly this category's weight of 20, so a repository failing
every check scores zero for architecture and no lower. They are not tuned to be
individually precise; they are tuned so the ordering is defensible — no tests
costs more than no README, because it should.
"""

from collections import Counter
from pathlib import Path

from app.scanners.base import ScanFinding, Severity, is_source_file, iter_files, read_text

CATEGORY = "architecture"

# Impacts, summing to the category weight of 20.
_NO_TESTS = 8
_NO_LOCKFILE = 4
_OVERSIZED_FILE = 3
_FLAT_LAYOUT = 3
_NO_README = 2

# Directory names that mean "the tests live here".
_TEST_DIRECTORIES = frozenset({"tests", "test", "spec", "specs", "__tests__", "e2e"})

# Filename shapes that mean "this file is a test", across the ecosystems the
# framework detector already recognises.
_TEST_MARKERS = ("test_", "_test.", ".test.", ".spec.", "_spec.", "test.")

# Dependency manifest -> the lockfiles that would make its install reproducible.
_LOCKFILES: dict[str, tuple[str, ...]] = {
    "package.json": ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"),
    "pyproject.toml": ("uv.lock", "poetry.lock", "pdm.lock", "requirements.txt"),
    "Pipfile": ("Pipfile.lock",),
    "go.mod": ("go.sum",),
    "Cargo.toml": ("Cargo.lock",),
    "Gemfile": ("Gemfile.lock",),
    "composer.json": ("composer.lock",),
}

_README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")

# Beyond this a file is doing too much to review or test in one piece. Generous
# on purpose: the point is to catch the 3000-line module nobody wants to touch,
# not to enforce a style guide.
_MAX_FILE_LINES = 600

# A root holding more source files than this, with no package directories, is
# not organised — it is a pile.
_MAX_ROOT_SOURCE_FILES = 12


def _is_test_path(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if any(part.lower() in _TEST_DIRECTORIES for part in relative.parts[:-1]):
        return True
    name = path.name.lower()
    return any(marker in name for marker in _TEST_MARKERS)


class ArchitectureScanner:
    category = CATEGORY

    def scan(self, repo_path: Path) -> list[ScanFinding]:
        source_files: list[Path] = []
        test_files: list[Path] = []
        root_names = {p.name.lower() for p in repo_path.iterdir()} if repo_path.is_dir() else set()

        for path in iter_files(repo_path):
            if not is_source_file(path):
                continue
            source_files.append(path)
            if _is_test_path(path, repo_path):
                test_files.append(path)

        findings: list[ScanFinding] = []
        findings.extend(self._check_tests(source_files, test_files))
        findings.extend(self._check_lockfiles(repo_path, root_names))
        findings.extend(self._check_file_sizes(source_files, repo_path))
        findings.extend(self._check_layout(source_files, repo_path))
        findings.extend(self._check_readme(root_names))
        return findings

    def _check_tests(self, source_files: list[Path], test_files: list[Path]) -> list[ScanFinding]:
        # An empty repository is not an untested one. Reporting a missing test
        # suite for a repo with no code would be noise.
        if not source_files or test_files:
            return []
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.HIGH,
                title="No automated tests found",
                description=(
                    f"{len(source_files)} source files were found and none of them look like "
                    "tests. Nothing verifies that a change is safe before it ships, so every "
                    "deployment carries the full risk of the change."
                ),
                recommendation=(
                    "Add a test suite under tests/ and run it in CI. Start with the paths that "
                    "would be most expensive to break rather than aiming for coverage."
                ),
                score_impact=_NO_TESTS,
            )
        ]

    def _check_lockfiles(self, repo_path: Path, root_names: set[str]) -> list[ScanFinding]:
        unlocked = [
            manifest
            for manifest, locks in _LOCKFILES.items()
            if manifest.lower() in root_names
            and not any(lock.lower() in root_names for lock in locks)
        ]
        if not unlocked:
            return []
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="Dependencies are not locked",
                description=(
                    f"{', '.join(unlocked)} declares dependencies but no lockfile was committed. "
                    "Two builds of the same commit can resolve different versions, so what was "
                    "tested is not necessarily what ships."
                ),
                recommendation=(
                    "Commit the lockfile your package manager generates, and install from it in "
                    "CI and in your image build."
                ),
                score_impact=_NO_LOCKFILE,
            )
        ]

    def _check_file_sizes(self, source_files: list[Path], repo_path: Path) -> list[ScanFinding]:
        oversized = []
        for path in source_files:
            lines = read_text(path).count("\n")
            if lines > _MAX_FILE_LINES:
                oversized.append((path.relative_to(repo_path).as_posix(), lines))

        if not oversized:
            return []

        oversized.sort(key=lambda item: item[1], reverse=True)
        worst, worst_lines = oversized[0]
        others = f" and {len(oversized) - 1} other files" if len(oversized) > 1 else ""
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="Source files are too large to review safely",
                description=(
                    f"{worst} is {worst_lines} lines{others}. A file this size is difficult to "
                    "review, test in isolation, or change without touching unrelated behaviour."
                ),
                recommendation=(
                    f"Split files over roughly {_MAX_FILE_LINES} lines along the seams that "
                    "already exist in them — usually one responsibility per module."
                ),
                score_impact=_OVERSIZED_FILE,
            )
        ]

    def _check_layout(self, source_files: list[Path], repo_path: Path) -> list[ScanFinding]:
        depths = Counter(len(p.relative_to(repo_path).parts) for p in source_files)
        at_root = depths.get(1, 0)
        nested = sum(count for depth, count in depths.items() if depth > 1)

        if at_root <= _MAX_ROOT_SOURCE_FILES or nested > at_root:
            return []
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.LOW,
                title="Source files are not organised into modules",
                description=(
                    f"{at_root} source files sit at the repository root with only {nested} in "
                    "subdirectories. A flat layout gives no signal about which parts of the "
                    "system depend on which, so boundaries erode without anyone noticing."
                ),
                recommendation=(
                    "Group files into directories that reflect responsibility, and keep the root "
                    "for configuration and entry points."
                ),
                score_impact=_FLAT_LAYOUT,
            )
        ]

    def _check_readme(self, root_names: set[str]) -> list[ScanFinding]:
        if any(name in root_names for name in _README_NAMES):
            return []
        return [
            ScanFinding(
                category=CATEGORY,
                severity=Severity.LOW,
                title="No README",
                description=(
                    "The repository root has no README. Anyone new to the service — including "
                    "whoever is on call for it at 3am — has to infer what it does and how to run "
                    "it from the source."
                ),
                recommendation=(
                    "Add a README covering what the service does, how to run it locally, and how "
                    "to run its tests."
                ),
                score_impact=_NO_README,
            )
        ]
