"""Tests for the shared repository index.

Every scanner reads the repository through this, so a mistake here is a mistake
in all six at once — and the whole reason it exists is that six scanners each
walking the tree themselves was both wasteful and six chances to get the
symlink handling wrong.
"""

from pathlib import Path

import pytest

from app.scanners.base import MAX_CACHED_BYTES, RepositoryIndex


def _write(root: Path, name: str, content: str = "x") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestBuild:
    def test_collects_every_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md")
        _write(tmp_path, "src/main.py")

        index = RepositoryIndex.build(tmp_path)

        assert {index.relative(p) for p in index.files} == {"README.md", "src/main.py"}

    def test_separates_source_files(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md")
        _write(tmp_path, "src/main.py")
        _write(tmp_path, "config.yaml")

        index = RepositoryIndex.build(tmp_path)

        assert {index.relative(p) for p in index.source_files} == {"src/main.py"}

    def test_root_names_are_lower_cased(self, tmp_path: Path) -> None:
        """So a check can ask for "readme.md" without worrying about spelling."""
        _write(tmp_path, "README.md")
        (tmp_path / "SRC").mkdir()

        index = RepositoryIndex.build(tmp_path)

        assert index.root_names == {"readme.md", "src"}

    def test_root_names_do_not_include_nested_entries(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/deep/buried.py")

        assert RepositoryIndex.build(tmp_path).root_names == {"src"}

    def test_skips_vendored_directories(self, tmp_path: Path) -> None:
        _write(tmp_path, "node_modules/pkg/index.js")
        _write(tmp_path, "app.py")

        index = RepositoryIndex.build(tmp_path)

        assert {index.relative(p) for p in index.files} == {"app.py"}

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        """A repository is untrusted input and can link anywhere."""
        outside = _write(tmp_path, "outside.txt", "secret")
        repo = tmp_path / "repo"
        repo.mkdir()
        _write(repo, "real.py")
        try:
            (repo / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")

        index = RepositoryIndex.build(repo)

        assert {index.relative(p) for p in index.files} == {"real.py"}

    def test_an_empty_repository_is_fine(self, tmp_path: Path) -> None:
        index = RepositoryIndex.build(tmp_path)

        assert index.files == ()
        assert index.root_names == frozenset()


class TestHasRootEntry:
    def test_matches_case_insensitively(self, tmp_path: Path) -> None:
        _write(tmp_path, "README.md")

        assert RepositoryIndex.build(tmp_path).has_root_entry("readme.md")

    def test_accepts_several_candidates(self, tmp_path: Path) -> None:
        _write(tmp_path, "yarn.lock")

        index = RepositoryIndex.build(tmp_path)

        assert index.has_root_entry("package-lock.json", "yarn.lock")

    def test_is_false_when_none_match(self, tmp_path: Path) -> None:
        assert not RepositoryIndex.build(tmp_path).has_root_entry("readme.md")


class TestRead:
    def test_reads_a_file(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "a.txt", "hello")
        index = RepositoryIndex.build(tmp_path)

        assert index.read(path) == "hello"

    def test_reads_the_same_file_only_once(self, tmp_path: Path) -> None:
        """The point of caching: several scanners grepping the same source
        files should cost one read, not one each."""
        path = _write(tmp_path, "a.txt", "original")
        index = RepositoryIndex.build(tmp_path)

        first = index.read(path)
        # Change it behind the index's back. A second real read would see this.
        path.write_text("changed", encoding="utf-8")

        assert first == "original"
        assert index.read(path) == "original"

    def test_a_missing_file_is_empty_not_an_error(self, tmp_path: Path) -> None:
        index = RepositoryIndex.build(tmp_path)

        assert index.read(tmp_path / "nope.txt") == ""

    def test_stops_caching_past_the_budget(self, tmp_path: Path) -> None:
        """Bounded rather than unbounded: a repository may be 500 MB, and
        holding a meaningful fraction of that per scan would starve the worker.
        Reads still work — they just stop being remembered.
        """
        index = RepositoryIndex.build(tmp_path)
        index._cached_bytes = MAX_CACHED_BYTES
        path = _write(tmp_path, "big.txt", "content")

        assert index.read(path) == "content"
        assert path not in index._cache

    def test_reads_still_work_after_the_budget_is_full(self, tmp_path: Path) -> None:
        index = RepositoryIndex.build(tmp_path)
        index._cached_bytes = MAX_CACHED_BYTES
        path = _write(tmp_path, "a.txt", "first")

        assert index.read(path) == "first"
        path.write_text("second", encoding="utf-8")
        # Uncached, so this one does see the change.
        assert index.read(path) == "second"


class TestRelative:
    def test_uses_forward_slashes(self, tmp_path: Path) -> None:
        """So a finding reads the same on Windows and Linux."""
        path = _write(tmp_path, "src/deep/main.py")

        assert RepositoryIndex.build(tmp_path).relative(path) == "src/deep/main.py"


class TestGeneratedFiles:
    """Committed machine-written code, excluded from what a person is judged on.

    Reading .gitignore was considered for this and measured as useless — a clone
    contains only tracked files, so ignored paths were never there. Generated
    code is the opposite: deliberately committed, and 82% of the tree on
    kubernetes/client-go.
    """

    @pytest.mark.parametrize(
        "header",
        [
            "// Code generated by protoc-gen-go. DO NOT EDIT.",
            "# @generated by codegen",
            "/* Autogenerated by thrift */",
            "# Generated by the protocol buffer compiler.  DO NOT EDIT!",
        ],
    )
    def test_recognises_the_conventional_markers(self, tmp_path: Path, header: str) -> None:
        _write(tmp_path, "api.py", f"{header}\ndef thing(): ...\n")

        index = RepositoryIndex.build(tmp_path)

        assert [index.relative(p) for p in index.generated_files] == ["api.py"]

    def test_generated_files_are_not_production(self, tmp_path: Path) -> None:
        _write(tmp_path, "api.py", "// Code generated by protoc. DO NOT EDIT.\nx = 1\n")
        _write(tmp_path, "main.py", "x = 1\n")

        index = RepositoryIndex.build(tmp_path)

        assert [index.relative(p) for p in index.production_files] == ["main.py"]

    def test_they_are_still_counted_as_source(self, tmp_path: Path) -> None:
        """Excluded from judgement, not pretended out of existence."""
        _write(tmp_path, "api.py", "// Code generated by protoc. DO NOT EDIT.\nx = 1\n")

        index = RepositoryIndex.build(tmp_path)

        assert len(index.source_files) == 1

    def test_the_marker_is_only_looked_for_in_the_header(self, tmp_path: Path) -> None:
        """A file discussing generated code is not itself generated."""
        filler = "# padding\n" * 400
        _write(tmp_path, "notes.py", f"{filler}# Code generated by something. DO NOT EDIT.\n")

        index = RepositoryIndex.build(tmp_path)

        assert index.generated_files == ()

    def test_ordinary_files_are_untouched(self, tmp_path: Path) -> None:
        _write(tmp_path, "main.py", "def main(): ...\n")

        index = RepositoryIndex.build(tmp_path)

        assert index.generated_files == ()
        assert len(index.production_files) == 1

    def test_a_generated_test_file_stays_a_test(self, tmp_path: Path) -> None:
        """Test classification wins, so a file cannot land in both buckets."""
        _write(tmp_path, "tests/test_api.py", "# @generated\nx = 1\n")

        index = RepositoryIndex.build(tmp_path)

        assert len(index.test_files) == 1
        assert index.generated_files == ()
        assert index.production_files == ()
