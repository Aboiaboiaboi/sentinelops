"""Tests for repository cloning.

Clones from local repositories built in tmp_path rather than over the network,
so the suite needs no internet and cannot be broken by someone else's GitHub
outage. URL-scheme validation is deliberately not tested here — it lives in
schemas/project.py where the URL enters the system, and is covered there.
"""

import subprocess
from pathlib import Path

import pytest

from app.workers.repo import (
    CloneFailed,
    CloneLimits,
    CloneTimedOut,
    CloneTooLarge,
    clone_repository,
    cloned_repository,
)

LIMITS = CloneLimits(timeout_seconds=30, max_bytes=10_000_000, max_files=1_000)


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """A real git repository with one commit, usable as a clone source."""
    repo = tmp_path / "source"
    repo.mkdir()
    _run("git", "init", "--quiet", "--initial-branch=main", cwd=repo)
    _run("git", "config", "user.email", "test@example.com", cwd=repo)
    _run("git", "config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=repo)
    _run("git", "commit", "--quiet", "-m", "initial", cwd=repo)
    return repo


# Cloned via a file:// URL rather than a bare path: git silently ignores
# --depth for local-path clones (it hardlinks the object store instead), so a
# path would not exercise the shallow-clone behaviour the real thing depends on.


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    hooks = tmp_path / "nohooks"
    hooks.mkdir()
    return tmp_path / "checkout", hooks


class TestCloneRepository:
    def test_clones_the_working_tree(self, source_repo: Path, workspace: tuple[Path, Path]) -> None:
        checkout, hooks = workspace

        clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=LIMITS)

        assert (checkout / "README.md").read_text(encoding="utf-8") == "# demo\n"
        assert (checkout / "src" / "main.py").exists()

    def test_reports_size_and_file_count(
        self, source_repo: Path, workspace: tuple[Path, Path]
    ) -> None:
        checkout, hooks = workspace

        size, files = clone_repository(
            source_repo.as_uri(), checkout, hooks_dir=hooks, limits=LIMITS
        )

        assert size > 0
        assert files > 0

    def test_history_is_not_fetched(self, source_repo: Path, workspace: tuple[Path, Path]) -> None:
        """--depth 1. Full history on a large repository is most of the download
        for none of the value — a scan reads the current tree."""
        _run("git", "commit", "--quiet", "--allow-empty", "-m", "second", cwd=source_repo)
        checkout, hooks = workspace

        clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=LIMITS)

        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=checkout, capture_output=True, text=True, check=True
        )
        assert len(log.stdout.strip().splitlines()) == 1

    def test_unreachable_repository_raises(self, workspace: tuple[Path, Path]) -> None:
        checkout, hooks = workspace

        with pytest.raises(CloneFailed):
            clone_repository(
                str(checkout.parent / "does-not-exist"),
                checkout,
                hooks_dir=hooks,
                limits=LIMITS,
            )

    def test_timeout_raises_rather_than_hanging(
        self, source_repo: Path, workspace: tuple[Path, Path]
    ) -> None:
        """A repository that never finishes must not hold a worker slot forever."""
        checkout, hooks = workspace
        impossible = CloneLimits(timeout_seconds=0, max_bytes=10_000_000, max_files=1_000)

        with pytest.raises(CloneTimedOut):
            clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=impossible)

    def test_rejects_a_tree_with_too_many_files(
        self, source_repo: Path, workspace: tuple[Path, Path]
    ) -> None:
        checkout, hooks = workspace
        tiny = CloneLimits(timeout_seconds=30, max_bytes=10_000_000, max_files=1)

        with pytest.raises(CloneTooLarge, match="more than 1 files"):
            clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=tiny)

    def test_rejects_a_tree_that_is_too_large(
        self, source_repo: Path, workspace: tuple[Path, Path]
    ) -> None:
        checkout, hooks = workspace
        tiny = CloneLimits(timeout_seconds=30, max_bytes=1, max_files=1_000)

        with pytest.raises(CloneTooLarge, match="larger than 1 bytes"):
            clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=tiny)

    def test_symlinks_are_not_followed_when_measuring(
        self, source_repo: Path, workspace: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A repository can contain a link to anywhere on the filesystem.
        Following one would both mis-measure the tree and read outside it."""
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"x" * 5_000_000)
        try:
            (source_repo / "link").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")
        _run("git", "add", "-A", cwd=source_repo)
        _run("git", "commit", "--quiet", "-m", "add link", cwd=source_repo)

        checkout, hooks = workspace
        # Would blow the cap if the 5MB target were measured through the link.
        limits = CloneLimits(timeout_seconds=30, max_bytes=1_000_000, max_files=1_000)

        size, _ = clone_repository(source_repo.as_uri(), checkout, hooks_dir=hooks, limits=limits)

        assert size < 1_000_000


class TestClonedRepository:
    async def test_yields_a_usable_checkout(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: _settings_with_root(tmp_path / "clones")
        )

        async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
            assert (checkout / "README.md").exists()

    async def test_removes_the_checkout_afterwards(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = tmp_path / "clones"
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: _settings_with_root(root))

        async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
            workspace = checkout.parent

        assert not workspace.exists()

    async def test_removes_the_checkout_even_when_the_scan_raises(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Otherwise the disk fills with abandoned copies of other people's code."""
        root = tmp_path / "clones"
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: _settings_with_root(root))
        captured: Path | None = None

        with pytest.raises(RuntimeError):
            async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
                captured = checkout.parent
                raise RuntimeError("scanner exploded")

        assert captured is not None
        assert not captured.exists()

    async def test_leaves_nothing_behind_when_the_clone_fails(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = tmp_path / "clones"
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: _settings_with_root(root))

        with pytest.raises(CloneFailed):
            async with cloned_repository(str(tmp_path / "nope"), limits=LIMITS):
                pass

        assert list(root.iterdir()) == []


def _settings_with_root(root: Path):
    """A stand-in for Settings exposing only what repo.py reads."""

    class _Stub:
        clone_root = root
        clone_timeout_seconds = 30
        clone_max_bytes = 10_000_000
        clone_max_files = 1_000

    return _Stub()
