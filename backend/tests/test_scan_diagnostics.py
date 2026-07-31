"""Tests for why a scan failed.

The load-bearing test in this file is the one asserting a token never reaches
the database. git's stderr echoes the clone URL, and for a private repository
that URL carries an installation token — so stderr may be read to classify a
failure and must never be stored.
"""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, ScanErrorCategory, ScanStatus, User
from app.services import scan_service
from app.workers.repo import CloneFailed, CloneTimedOut, CloneTooLarge, redact_credentials
from app.workers.scan_tasks import classify_clone_failure, execute_scan
from tests.helpers import CloneSettings, reload_scan


class TestRedactCredentials:
    """Applied to git's stderr before it goes anywhere — including the worker
    log, which is the one place the text is deliberately kept."""

    def test_masks_a_token_in_a_clone_url(self) -> None:
        text = (
            "fatal: unable to access "
            "'https://x-access-token:ghs_SECRET@github.com/acme/private.git/'"
        )

        redacted = redact_credentials(text)

        assert "ghs_SECRET" not in redacted
        assert "x-access-token" not in redacted
        # The useful part survives, or redaction would defeat classification.
        assert "github.com/acme/private.git" in redacted

    def test_masks_basic_auth_in_any_scheme(self) -> None:
        assert "hunter2" not in redact_credentials("ssh://user:hunter2@host/repo.git")

    def test_leaves_ordinary_text_alone(self) -> None:
        text = "fatal: 'C:/nope/missing' does not appear to be a git repository"

        assert redact_credentials(text) == text

    def test_leaves_a_url_without_credentials_alone(self) -> None:
        text = "fatal: could not read from 'https://github.com/acme/widget.git'"

        assert redact_credentials(text) == text


class TestClassifyCloneFailure:
    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: Authentication failed for 'https://github.com/acme/private'",
            "remote: Permission denied",
            "fatal: unable to access 'https://x/': The requested URL returned error: 403",
        ],
    )
    def test_credentials_refused_is_an_authentication_failure(self, stderr: str) -> None:
        """Only when credentials were supplied and rejected."""
        category, _ = classify_clone_failure(CloneFailed(stderr))

        assert category is ScanErrorCategory.AUTHENTICATION

    @pytest.mark.parametrize(
        "stderr",
        [
            "remote: Repository not found.",
            "fatal: repository 'https://github.com/acme/nope/' "
            "does not appear to be a git repository",
        ],
    )
    def test_recognises_a_missing_repository(self, stderr: str) -> None:
        category, _ = classify_clone_failure(CloneFailed(stderr))

        assert category is ScanErrorCategory.REPOSITORY_NOT_FOUND

    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled",
            "fatal: could not read Password for 'https://github.com': terminal prompts disabled",
        ],
    )
    def test_git_asking_for_credentials_is_not_an_auth_failure(self, stderr: str) -> None:
        """Found by scanning a repository that genuinely did not exist and
        watching it come back as "authentication".

        GitHub answers 404 identically for a missing repository and a private
        one you cannot see, so git asks for a username. That says nothing about
        credentials being wrong, and calling it an auth failure would tell
        someone to connect GitHub over a URL typo.
        """
        category, detail = classify_clone_failure(CloneFailed(stderr))

        assert category is ScanErrorCategory.REPOSITORY_NOT_FOUND
        # The wording has to hold both possibilities honestly.
        assert "private" in detail.lower()

    @pytest.mark.parametrize(
        "stderr",
        [
            "fatal: unable to access 'https://x/': Could not resolve host: github.com",
            "fatal: unable to access 'https://x/': Failed to connect to github.com port 443",
        ],
    )
    def test_recognises_an_unreachable_host(self, stderr: str) -> None:
        category, _ = classify_clone_failure(CloneFailed(stderr))

        assert category is ScanErrorCategory.NETWORK_UNREACHABLE

    def test_size_and_timeout_come_from_their_own_types(self) -> None:
        """Not from stderr — these exceptions carry messages this codebase
        wrote, which is why their text is safe to keep and worth keeping: it
        names the limit that was hit."""
        size_category, size_detail = classify_clone_failure(
            CloneTooLarge("Repository has more than 50000 files.")
        )
        time_category, time_detail = classify_clone_failure(
            CloneTimedOut("Cloning took longer than 120s.")
        )

        assert size_category is ScanErrorCategory.REPOSITORY_TOO_LARGE
        assert "50000" in size_detail
        assert time_category is ScanErrorCategory.TIMEOUT
        assert "120s" in time_detail

    def test_an_unrecognised_failure_still_gets_a_category(self) -> None:
        category, detail = classify_clone_failure(CloneFailed("fatal: something new"))

        assert category is ScanErrorCategory.CLONE_FAILED
        assert detail

    def test_the_detail_never_repeats_git_stderr(self) -> None:
        """The whole point. A clone URL in stderr can carry a token, so the
        stored detail is fixed text chosen by the match — never the text that
        produced the match."""
        stderr = (
            "fatal: unable to access "
            "'https://x-access-token:ghs_SUPERSECRETTOKEN@github.com/acme/private.git/': "
            "The requested URL returned error: 403"
        )

        _, detail = classify_clone_failure(CloneFailed(stderr))

        assert "ghs_SUPERSECRETTOKEN" not in detail
        assert "x-access-token" not in detail
        assert "acme/private" not in detail


class TestFailedScanRecordsWhy:
    async def _scan_of(self, session: AsyncSession, url: str) -> Scan:
        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url=url)
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()
        return scan

    async def test_a_missing_repository_is_recorded_as_such(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scan = await self._scan_of(session, (tmp_path / "does-not-exist").as_uri())
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.FAILED
        assert finished.error_category == ScanErrorCategory.REPOSITORY_NOT_FOUND.value
        assert finished.error_detail

    async def test_a_failed_scan_leaves_no_category_still_scanning(
        self, session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed scan is terminal, so nothing about it is still running.
        Categories left `pending` render as "Scanning…" and pulse forever under
        a dead scan."""
        scan = await self._scan_of(session, (tmp_path / "does-not-exist").as_uri())
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert set(finished.category_status.values()) == {"failed"}

    async def test_a_failed_scan_still_has_no_score(
        self, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        """Recording why must not start recording a zero — a failed scan has no
        score, and zero would read as a genuinely terrible repository."""
        scan = await self._scan_of(session, (tmp_path / "gone").as_uri())
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.score is None

    async def test_a_successful_scan_records_no_error(
        self, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        from tests.helpers import commit_all, init_repo

        repo = init_repo(tmp_path / "source")
        (repo / "app").mkdir()
        (repo / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        commit_all(repo)
        scan = await self._scan_of(session, repo.as_uri())
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.COMPLETED
        assert finished.error_category is None
        assert finished.error_detail is None


class TestApiSurface:
    async def test_a_failed_scan_explains_itself(
        self, authed_client, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        created = await authed_client.post(
            "/projects",
            json={"name": "gone", "repository_url": "https://github.com/acme/definitely-missing"},
        )
        project_id = created.json()["id"]
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]

        import uuid as _uuid

        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )
        await execute_scan(session, scan_id=_uuid.UUID(scan_id))

        body = (await authed_client.get(f"/scans/{scan_id}")).json()

        assert body["status"] == "failed"
        assert body["error_category"] is not None
        assert body["error_detail"]
        # The hint is derived from the category, not stored, so it is always
        # present alongside one.
        assert body["error_hint"]

    async def test_a_pending_scan_has_null_error_fields_not_missing(self, authed_client) -> None:
        created = await authed_client.post(
            "/projects",
            json={"name": "x", "repository_url": "https://github.com/acme/x"},
        )
        scan = (await authed_client.post(f"/projects/{created.json()['id']}/scans")).json()

        for field in ("error_category", "error_detail", "error_hint"):
            assert field in scan
            assert scan[field] is None
