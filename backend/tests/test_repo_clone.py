"""Tests for repository cloning.

Clones from local repositories built in tmp_path rather than over the network,
so the suite needs no internet and cannot be broken by someone else's GitHub
outage. URL-scheme validation is deliberately not tested here — it lives in
schemas/project.py where the URL enters the system, and is covered there.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.workers.repo import (
    CloneFailed,
    CloneLimits,
    CloneTimedOut,
    CloneTooLarge,
    _git_environment,
    clone_repository,
    cloned_repository,
)
from tests.helpers import CloneSettings, commit_all, git, init_repo

LIMITS = CloneLimits(timeout_seconds=30, max_bytes=10_000_000, max_files=1_000)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    """A real git repository with one commit, usable as a clone source."""
    repo = init_repo(tmp_path / "source")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    commit_all(repo)
    return repo


# Cloned via a file:// URL rather than a bare path: git silently ignores
# --depth for local-path clones (it hardlinks the object store instead), so a
# path would not exercise the shallow-clone behaviour the real thing depends on.


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    hooks = tmp_path / "nohooks"
    hooks.mkdir()
    return tmp_path / "checkout", hooks


class TestGitEnvironment:
    def test_passes_through_what_the_os_itself_needs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On Windows a child process without SystemRoot cannot initialise
        winsock, and every DNS lookup fails with "getaddrinfo() thread failed
        to start". Never caught here before because these tests clone file://
        URLs, which resolve no names."""
        monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")

        assert _git_environment()["SYSTEMROOT"] == r"C:\Windows"

    def test_does_not_invent_variables_that_are_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux the Windows variables do not exist, and an empty-string
        SystemRoot would be worse than none."""
        monkeypatch.delenv("SYSTEMROOT", raising=False)

        assert "SYSTEMROOT" not in _git_environment()

    def test_the_host_environment_does_not_leak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The allowlist is the whole point — a worker holds credentials that
        must never reach a git subprocess processing a hostile URL."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@host/db")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-git")

        environment = _git_environment()

        assert "DATABASE_URL" not in environment
        assert "AWS_SECRET_ACCESS_KEY" not in environment


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
        git("commit", "--quiet", "--allow-empty", "-m", "second", cwd=source_repo)
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

    def test_a_credential_header_leaves_no_trace_in_the_checkout(
        self, source_repo: Path, workspace: tuple[Path, Path]
    ) -> None:
        """The token reaches git through --config-env and an environment
        variable. Passed as `-c http.extraHeader=...` or embedded in the URL it
        would persist into the clone's .git/config — readable by every scanner
        and, in Phase 3, by sandboxed third-party tools."""
        checkout, hooks = workspace
        marker = "Authorization: Basic c2VjcmV0LW1hcmtlcg=="

        clone_repository(
            source_repo.as_uri(),
            checkout,
            hooks_dir=hooks,
            limits=LIMITS,
            credential_header=marker,
        )

        for config_name in ("config", "config.worktree"):
            config = checkout / ".git" / config_name
            if config.exists():
                content = config.read_text(encoding="utf-8")
                assert "extraHeader" not in content
                assert marker not in content
                assert "SENTINELOPS_GIT_AUTH" not in content

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
        commit_all(source_repo, "add link")

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
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
            assert (checkout / "README.md").exists()

    @pytest.mark.skipif(
        not hasattr(os, "getuid"), reason="POSIX permission bits; the containers are Linux"
    )
    async def test_the_checkout_is_reachable_by_the_sandbox_user(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """mkdtemp creates 0700, and the security tools run as uid 65534.

        Found by scanning a real repository: Gitleaks could not stat the
        checkout, exited non-zero with an empty report, and the result read as a
        repository with no secrets in it. A directory nobody else can enter is
        the right default for a temporary directory and the wrong one for a
        checkout that is about to be handed to another user deliberately.
        """
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
            mode = checkout.parent.stat().st_mode

        # Execute for other: without it, no other user can traverse into the
        # checkout at all, whatever the permissions inside it say.
        assert mode & stat.S_IXOTH
        assert mode & stat.S_IROTH

    async def test_removes_the_checkout_afterwards(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        root = tmp_path / "clones"
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: CloneSettings(root))

        async with cloned_repository(source_repo.as_uri(), limits=LIMITS) as checkout:
            workspace = checkout.parent

        assert not workspace.exists()

    async def test_removes_the_checkout_even_when_the_scan_raises(
        self, source_repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Otherwise the disk fills with abandoned copies of other people's code."""
        root = tmp_path / "clones"
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: CloneSettings(root))
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
        monkeypatch.setattr("app.workers.repo.get_settings", lambda: CloneSettings(root))

        with pytest.raises(CloneFailed):
            async with cloned_repository(str(tmp_path / "nope"), limits=LIMITS):
                pass

        assert list(root.iterdir()) == []
