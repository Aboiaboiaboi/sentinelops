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

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    failed,
    passed,
    skipped,
)

CATEGORY = "architecture"

_TESTS = CheckSpec("architecture.tests", "Automated tests")
_LOCKFILE = CheckSpec("architecture.lockfile", "Dependency locking")
_FILE_SIZE = CheckSpec("architecture.file_size", "Reviewable file sizes")
_LAYOUT = CheckSpec("architecture.layout", "Module organisation")
_README = CheckSpec("architecture.readme", "README")

# Several checks measure the source tree, so with no source there is nothing
# to measure — distinct from measuring it and finding it fine.
_NO_SOURCE = "the repository has no hand-written source files"

# Impacts, summing to the category weight of 20.
_NO_TESTS = 8
_NO_LOCKFILE = 4
_OVERSIZED_FILE = 3
_FLAT_LAYOUT = 3
_NO_README = 2

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


class ArchitectureScanner:
    category = CATEGORY
    CHECKS = (_TESTS, _LOCKFILE, _FILE_SIZE, _LAYOUT, _README)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        # Hand-written code only. Generated files are excluded because none of
        # these findings are actionable against them: nobody splits a 4000-line
        # generated client into modules, and on a repository like
        # kubernetes/client-go they are 82% of the tree, which would drown the
        # signal from the code somebody actually maintains.
        source_files = list(repo.production_files)
        test_files = list(repo.test_files)

        return [
            self._check_tests(source_files, test_files),
            self._check_lockfiles(repo),
            self._check_file_sizes(source_files, repo),
            self._check_layout(source_files, repo),
            self._check_readme(repo),
        ]

    def _check_tests(self, source_files: list[Path], test_files: list[Path]) -> CheckResult:
        # An empty repository is not an untested one. Reporting a missing test
        # suite for a repo with no code would be noise.
        if not source_files:
            return skipped(_TESTS, _NO_SOURCE)
        if test_files:
            return passed(_TESTS)
        return failed(
            _TESTS,
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
            ),
        )

    def _check_lockfiles(self, repo: RepositoryIndex) -> CheckResult:
        declared = [manifest for manifest in _LOCKFILES if repo.has_root_entry(manifest)]
        if not declared:
            return skipped(_LOCKFILE, "no dependency manifest was found to lock")
        unlocked = [
            manifest for manifest in declared if not repo.has_root_entry(*_LOCKFILES[manifest])
        ]
        if not unlocked:
            return passed(_LOCKFILE)
        return failed(
            _LOCKFILE,
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
            ),
        )

    def _check_file_sizes(self, source_files: list[Path], repo: RepositoryIndex) -> CheckResult:
        if not source_files:
            return skipped(_FILE_SIZE, _NO_SOURCE)
        oversized = []
        for path in source_files:
            # Through the index, so a later scanner grepping the same files
            # reads them from memory rather than off disk again.
            lines = repo.read(path).count("\n")
            if lines > _MAX_FILE_LINES:
                oversized.append((repo.relative(path), lines))

        if not oversized:
            return passed(_FILE_SIZE)

        oversized.sort(key=lambda item: item[1], reverse=True)
        worst, worst_lines = oversized[0]
        others = f" and {len(oversized) - 1} other files" if len(oversized) > 1 else ""
        return failed(
            _FILE_SIZE,
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
            ),
        )

    def _check_layout(self, source_files: list[Path], repo: RepositoryIndex) -> CheckResult:
        if not source_files:
            return skipped(_LAYOUT, _NO_SOURCE)

        depths = Counter(len(p.relative_to(repo.path).parts) for p in source_files)
        at_root = depths.get(1, 0)
        nested = sum(count for depth, count in depths.items() if depth > 1)

        if at_root <= _MAX_ROOT_SOURCE_FILES or nested > at_root:
            return passed(_LAYOUT)
        return failed(
            _LAYOUT,
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
            ),
        )

    def _check_readme(self, repo: RepositoryIndex) -> CheckResult:
        if repo.has_root_entry(*_README_NAMES):
            return passed(_README)
        return failed(
            _README,
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
            ),
        )
