"""Tests for reading the HEAD commit of a checkout.

Commit context is an extra on top of a scan, never a precondition for one, so
the cases that matter most are the ones where it is unavailable or hostile:
they must produce silence or sanitised text, never an exception that fails a
scan which had already succeeded.
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, User
from app.services import scan_service
from app.workers.repo import (
    MAX_COMMIT_AUTHOR,
    MAX_COMMIT_MESSAGE,
    read_head_commit,
)
from app.workers.scan_tasks import execute_scan
from tests.helpers import CloneSettings, git, init_repo, reload_scan


def _commit(repo: Path, message: str, *, author: str = "Ada Lovelace") -> None:
    # Content varies per call: git refuses to commit an unchanged tree, so a
    # fixed body makes the second commit in a test silently fail.
    (repo / "file.txt").write_text(f"content {message}\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git(
        "-c",
        f"user.name={author}",
        "-c",
        "user.email=author@example.com",
        "commit",
        "--quiet",
        "-m",
        message,
        cwd=repo,
    )


class TestReadHeadCommit:
    def test_reads_the_head_commit(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _commit(repo, "Fix the thing that was broken")

        commit = read_head_commit(repo)

        assert commit is not None
        assert commit.message == "Fix the thing that was broken"
        assert commit.author == "Ada Lovelace"
        assert len(commit.sha) == 40
        assert commit.committed_at.tzinfo is not None

    def test_reports_the_latest_commit_not_the_first(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _commit(repo, "first")
        _commit(repo, "second")

        commit = read_head_commit(repo)

        assert commit is not None
        assert commit.message == "second"

    def test_a_repository_with_no_commits_is_silence_not_an_error(self, tmp_path: Path) -> None:
        """The empty-repository case. It has no HEAD, which is not a failure —
        and a scan of it still completed."""
        repo = init_repo(tmp_path / "empty")

        assert read_head_commit(repo) is None

    def test_a_directory_that_is_not_a_repository_is_silence(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()

        assert read_head_commit(plain) is None

    def test_a_missing_directory_is_silence(self, tmp_path: Path) -> None:
        assert read_head_commit(tmp_path / "nope") is None


class TestHostileInput:
    """A commit message is written by whoever owns the repository, which for
    this product is a stranger."""

    def test_a_huge_message_is_truncated(self, tmp_path: Path) -> None:
        """git imposes no length limit, and storing megabytes of subject line
        would bloat a row nobody wants to read."""
        repo = init_repo(tmp_path / "repo")
        _commit(repo, "A" * 5000)

        commit = read_head_commit(repo)

        assert commit is not None
        assert len(commit.message) == MAX_COMMIT_MESSAGE

    def test_a_huge_author_is_truncated(self, tmp_path: Path) -> None:
        repo = init_repo(tmp_path / "repo")
        _commit(repo, "ordinary", author="B" * 900)

        commit = read_head_commit(repo)

        assert commit is not None
        assert len(commit.author) == MAX_COMMIT_AUTHOR

    def test_control_characters_are_stripped(self, tmp_path: Path) -> None:
        """Postgres rejects NUL in a text column outright, so an unsanitised
        subject is an insert that fails the whole scan."""
        repo = init_repo(tmp_path / "repo")
        _commit(repo, "before\x07\x1bafter")

        commit = read_head_commit(repo)

        assert commit is not None
        assert "\x07" not in commit.message
        assert "\x1b" not in commit.message
        assert commit.message == "beforeafter"

    @pytest.mark.parametrize(
        "message",
        [
            "fix(api): handle 100% of cases — see #42",
            "café: fix la configuration",
            "修复了一个错误",
            "fix: resolve the crash 🎉",
        ],
    )
    def test_non_ascii_messages_survive_intact(self, tmp_path: Path, message: str) -> None:
        """Sanitising must not mangle a normal message, and neither must the
        decoder. `text=True` decodes with the locale encoding — cp1252 on
        Windows — which corrupted every one of these until the subprocess was
        told to read UTF-8 explicitly.
        """
        repo = init_repo(tmp_path / "repo")
        _commit(repo, message)

        commit = read_head_commit(repo)

        assert commit is not None
        assert commit.message == message


class TestPersistence:
    async def test_a_scan_records_the_commit_it_looked_at(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through the worker: the value reaching the database is
        the real commit of the real checkout."""
        repo = init_repo(tmp_path / "source")
        (repo / "app").mkdir()
        (repo / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "--quiet", "-m", "Add the entrypoint", cwd=repo)
        expected = read_head_commit(repo)
        assert expected is not None

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url=repo.as_uri())
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.commit_sha == expected.sha
        assert finished.commit_message == "Add the entrypoint"
        assert finished.commit_author == "Test"
        assert finished.committed_at is not None

    async def test_an_empty_repository_records_no_commit_and_still_finishes(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No HEAD to read, and that must not fail the scan."""
        repo = init_repo(tmp_path / "empty")
        (repo / "README.md").write_text("# readme\n", encoding="utf-8")
        git("add", "-A", cwd=repo)
        git("commit", "--quiet", "-m", "readme", cwd=repo)
        # Committed, then rewound to no commits at all.
        git("update-ref", "-d", "HEAD", cwd=repo)

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="empty", repository_url=repo.as_uri())
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.commit_sha is None
        assert finished.status is not None


@pytest.mark.parametrize("message", ["", "   "])
def test_an_empty_message_is_still_a_commit(tmp_path: Path, message: str) -> None:
    """`git commit --allow-empty-message` is legal. The commit is real even if
    the subject is not, so the sha and author must still come through."""
    repo = init_repo(tmp_path / "repo")
    (repo / "file.txt").write_text("x\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "--allow-empty-message", "-m", message, cwd=repo)

    commit = read_head_commit(repo)

    assert commit is not None
    assert commit.message == ""
    assert len(commit.sha) == 40
