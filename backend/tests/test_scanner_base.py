"""Tests for the scanner boundary helpers.

`iter_files` and `read_text` exist so no scanner has to write a filesystem walk
itself — each getting it subtly wrong in its own way is how a boundary like this
stops being safe. So their edge cases are worth pinning down once, here.
"""

from pathlib import Path

import pytest

from app.scanners.base import (
    CONCLUSIVE_OUTCOMES,
    MAX_READ_BYTES,
    CheckOutcome,
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Scanner,
    Severity,
    errored,
    findings_of,
    iter_files,
    passed,
    read_text,
    skipped,
)


def _names(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in iter_files(root)}


class TestIterFiles:
    def test_yields_nested_files(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "README.md").write_text("x", encoding="utf-8")
        (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")

        assert _names(tmp_path) == {"README.md", "src/main.py"}

    def test_skips_vcs_and_vendor_directories(self, tmp_path: Path) -> None:
        """Findings about someone else's vendored dependencies are noise the
        user cannot act on."""
        for directory in (".git", "node_modules", ".venv", "__pycache__", "dist"):
            (tmp_path / directory).mkdir()
            (tmp_path / directory / "junk.py").write_text("x", encoding="utf-8")
        (tmp_path / "app.py").write_text("x", encoding="utf-8")

        assert _names(tmp_path) == {"app.py"}

    def test_skips_symlinked_files(self, tmp_path: Path) -> None:
        """A repository is untrusted input and can link anywhere on the
        filesystem."""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "real.py").write_text("x", encoding="utf-8")
        try:
            (repo / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")

        assert _names(repo) == {"real.py"}

    def test_does_not_descend_into_symlinked_directories(self, tmp_path: Path) -> None:
        """Following one that points at its own parent would not terminate."""
        target = tmp_path / "target"
        target.mkdir()
        (target / "hidden.py").write_text("x", encoding="utf-8")
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "real.py").write_text("x", encoding="utf-8")
        try:
            (repo / "linked").symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")

        assert _names(repo) == {"real.py"}

    def test_empty_directory_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_files(tmp_path)) == []


class TestReadText:
    def test_reads_a_text_file(self, tmp_path: Path) -> None:
        path = tmp_path / "a.txt"
        path.write_text("hello", encoding="utf-8")

        assert read_text(path) == "hello"

    def test_missing_file_is_empty_not_an_error(self, tmp_path: Path) -> None:
        """Raising would fail the whole category and cost the user its weight."""
        assert read_text(tmp_path / "nope.txt") == ""

    def test_a_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert read_text(tmp_path) == ""

    def test_binary_content_does_not_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "blob.bin"
        path.write_bytes(b"\xff\xfe\x00\x01")

        assert isinstance(read_text(path), str)

    def test_reads_at_most_the_cap(self, tmp_path: Path) -> None:
        """A repository can contain a multi-gigabyte blob."""
        path = tmp_path / "huge.txt"
        path.write_text("a" * (MAX_READ_BYTES + 5_000), encoding="utf-8")

        assert len(read_text(path)) == MAX_READ_BYTES


class TestScanFinding:
    def test_is_immutable(self) -> None:
        """A value object the worker translates — not something a later stage
        should be able to edit in place."""
        finding = ScanFinding(
            category="architecture",
            severity=Severity.HIGH,
            title="t",
            description="d",
            recommendation="r",
            score_impact=5,
        )

        with pytest.raises(AttributeError):
            finding.score_impact = 1  # type: ignore[misc]

    def test_severity_values_are_uppercase(self) -> None:
        """The client uses these strings directly as style lookup keys."""
        assert [s.value for s in Severity] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class TestErroredOutcome:
    """A check our own tooling could not complete.

    The distinction being defended: `skipped` says the question did not apply to
    the repository, which is a statement *about the repository*. When a sandbox
    times out or a tool crashes, nothing about the repository has been
    established — and saying otherwise moves our failure onto the user.
    """

    CHECK = CheckSpec("security.example", "Example")

    def test_carries_the_reason(self) -> None:
        result = errored(self.CHECK, "the sandbox timed out")

        assert result.outcome is CheckOutcome.ERRORED
        assert result.reason == "the sandbox timed out"

    def test_carries_no_finding(self) -> None:
        """A check that did not finish has not established that anything is
        wrong, so it must not deduct."""
        result = errored(self.CHECK, "no sandbox available")

        assert result.finding is None
        assert findings_of([result]) == []

    def test_is_not_skipped(self) -> None:
        assert errored(self.CHECK, "x").outcome is not skipped(self.CHECK, "x").outcome

    def test_reaches_no_verdict(self) -> None:
        """Which is what the worker uses to decide a category assessed nothing.
        Errored and skipped agree here for different reasons; passed and failed
        are the only outcomes that concluded anything."""
        assert CONCLUSIVE_OUTCOMES == {CheckOutcome.PASSED, CheckOutcome.FAILED}
        assert errored(self.CHECK, "x").outcome not in CONCLUSIVE_OUTCOMES
        assert skipped(self.CHECK, "x").outcome not in CONCLUSIVE_OUTCOMES


class TestScannerProtocol:
    def test_a_conforming_object_satisfies_it(self) -> None:
        class Fake:
            category = "architecture"
            CHECKS = (CheckSpec("architecture.example", "Example"),)

            def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
                return [passed(self.CHECKS[0])]

        assert isinstance(Fake(), Scanner)

    def test_a_scanner_without_declared_checks_does_not_satisfy_it(self) -> None:
        """Declaring CHECKS is what lets a shared test assert a run accounts
        for every check, so the protocol requires it rather than trusting each
        scanner to remember."""

        class MissingChecks:
            category = "architecture"

            def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
                return []

        assert not isinstance(MissingChecks(), Scanner)

    def test_the_orm_reuses_this_severity(self) -> None:
        """One definition, so a value can never mean different things either
        side of the boundary."""
        from app.models import Severity as ModelSeverity

        assert ModelSeverity is Severity
