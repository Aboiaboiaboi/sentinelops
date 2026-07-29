"""Tests for the architecture scanner.

Each check gets a repository that should trigger it and one that should not.
False positives matter as much as misses here — a scanner that flags a healthy
repository trains people to ignore it.
"""

from pathlib import Path

import pytest

from app.scanners.architecture import ArchitectureScanner
from app.scanners.base import Severity

SCANNER = ArchitectureScanner()


def _titles(repo: Path) -> set[str]:
    return {finding.title for finding in SCANNER.scan(repo)}


@pytest.fixture
def healthy_repo(tmp_path: Path) -> Path:
    """A repository that should produce no findings at all."""
    (tmp_path / "README.md").write_text("# service\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_x(): ...\n", encoding="utf-8")
    return tmp_path


class TestHealthyRepository:
    def test_produces_no_findings(self, healthy_repo: Path) -> None:
        assert SCANNER.scan(healthy_repo) == []

    def test_every_finding_belongs_to_this_category(self, tmp_path: Path) -> None:
        (tmp_path / "main.py").write_text("x = 1\n", encoding="utf-8")

        assert {f.category for f in SCANNER.scan(tmp_path)} == {"architecture"}

    def test_impacts_cannot_exceed_the_category_weight(self, tmp_path: Path) -> None:
        """Everything failing at once must score the category zero, not below."""
        for index in range(20):
            (tmp_path / f"mod{index}.py").write_text("x\n" * 700, encoding="utf-8")

        assert sum(f.score_impact for f in SCANNER.scan(tmp_path)) <= 20


class TestTests:
    def test_flags_a_repository_with_no_tests(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

        assert "No automated tests found" in _titles(tmp_path)

    def test_is_the_most_severe_check(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

        finding = next(f for f in SCANNER.scan(tmp_path) if f.title.startswith("No automated"))
        assert finding.severity is Severity.HIGH

    @pytest.mark.parametrize(
        ("path", "name"),
        [
            ("tests", "test_app.py"),
            ("spec", "app_spec.rb"),
            ("__tests__", "app.test.ts"),
            ("src", "app.spec.ts"),
            ("src", "app_test.go"),
        ],
    )
    def test_recognises_test_conventions(self, tmp_path: Path, path: str, name: str) -> None:
        """Different ecosystems mark tests differently; missing one would flag a
        well-tested repository."""
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / path).mkdir(exist_ok=True)
        (tmp_path / path / name).write_text("\n", encoding="utf-8")

        assert "No automated tests found" not in _titles(tmp_path)

    def test_an_empty_repository_is_not_untested(self, tmp_path: Path) -> None:
        """Reporting a missing test suite for a repo with no code is noise."""
        (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")

        assert "No automated tests found" not in _titles(tmp_path)


class TestLockfiles:
    def test_flags_a_manifest_with_no_lockfile(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")

        assert "Dependencies are not locked" in _titles(tmp_path)

    @pytest.mark.parametrize("lockfile", ["package-lock.json", "yarn.lock", "pnpm-lock.yaml"])
    def test_accepts_any_of_the_package_managers(self, tmp_path: Path, lockfile: str) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        (tmp_path / lockfile).write_text("", encoding="utf-8")

        assert "Dependencies are not locked" not in _titles(tmp_path)

    def test_a_repository_with_no_manifest_is_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("# docs\n", encoding="utf-8")

        assert "Dependencies are not locked" not in _titles(tmp_path)


class TestFileSize:
    def test_flags_an_oversized_file(self, tmp_path: Path) -> None:
        (tmp_path / "god.py").write_text("x = 1\n" * 700, encoding="utf-8")

        assert "Source files are too large to review safely" in _titles(tmp_path)

    def test_reports_once_naming_the_worst(self, tmp_path: Path) -> None:
        """One finding per category of problem, not one per file — twenty
        findings saying the same thing is not twenty times as useful."""
        (tmp_path / "big.py").write_text("x = 1\n" * 700, encoding="utf-8")
        (tmp_path / "bigger.py").write_text("x = 1\n" * 900, encoding="utf-8")

        matching = [f for f in SCANNER.scan(tmp_path) if f.title.startswith("Source files are")]
        assert len(matching) == 1
        assert "bigger.py" in matching[0].description

    def test_normal_files_are_not_flagged(self, healthy_repo: Path) -> None:
        assert "Source files are too large to review safely" not in _titles(healthy_repo)


class TestLayout:
    def test_flags_a_flat_pile_of_source_files(self, tmp_path: Path) -> None:
        for index in range(20):
            (tmp_path / f"mod{index}.py").write_text("x = 1\n", encoding="utf-8")

        assert "Source files are not organised into modules" in _titles(tmp_path)

    def test_a_small_root_is_fine(self, tmp_path: Path) -> None:
        for index in range(3):
            (tmp_path / f"mod{index}.py").write_text("x = 1\n", encoding="utf-8")

        assert "Source files are not organised into modules" not in _titles(tmp_path)

    def test_entry_points_beside_a_real_package_are_fine(self, tmp_path: Path) -> None:
        """A handful of root scripts next to a properly organised package is a
        normal layout, not a flat one."""
        for index in range(15):
            (tmp_path / f"script{index}.py").write_text("x = 1\n", encoding="utf-8")
        package = tmp_path / "app"
        package.mkdir()
        for index in range(30):
            (package / f"mod{index}.py").write_text("x = 1\n", encoding="utf-8")

        assert "Source files are not organised into modules" not in _titles(tmp_path)


class TestReadme:
    def test_flags_a_missing_readme(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")

        assert "No README" in _titles(tmp_path)

    @pytest.mark.parametrize("name", ["README.md", "readme.md", "README.rst", "README"])
    def test_accepts_the_usual_spellings(self, tmp_path: Path, name: str) -> None:
        (tmp_path / name).write_text("hi\n", encoding="utf-8")

        assert "No README" not in _titles(tmp_path)


class TestRobustness:
    def test_vendored_code_is_ignored(self, tmp_path: Path) -> None:
        """Findings about someone else's dependencies are noise the user cannot
        act on — and node_modules alone would trigger every check."""
        (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
        vendored = tmp_path / "node_modules" / "left-pad"
        vendored.mkdir(parents=True)
        (vendored / "index.js").write_text("x\n" * 900, encoding="utf-8")

        assert SCANNER.scan(tmp_path) == []

    def test_an_empty_repository_does_not_raise(self, tmp_path: Path) -> None:
        SCANNER.scan(tmp_path)

    def test_a_binary_file_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "blob.py").write_bytes(b"\xff\xfe\x00\x01")

        SCANNER.scan(tmp_path)
