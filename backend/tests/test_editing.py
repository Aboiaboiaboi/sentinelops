"""Tests for editing projects and naming scans.

The rule under test: a repository URL is editable until something depends on
it meaning what it means. A failed scan does not — it has no score and no
findings — which is what lets somebody correct a typo. A completed one does,
permanently, because the scan history would otherwise describe a repository
the project no longer points at.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, ScanStatus, User
from app.services import scan_service

PROJECT = {"name": "api", "repository_url": "https://github.com/acme/api"}
OTHER_URL = "https://github.com/acme/moved"


async def _add_scan(session: AsyncSession, project_id: str, status: ScanStatus) -> Scan:
    scan = Scan(
        project_id=uuid.UUID(project_id),
        status=status,
        category_status=scan_service.initial_category_status(),
        score=60 if status is ScanStatus.COMPLETED else None,
        scoring_version="v1" if status is ScanStatus.COMPLETED else None,
    )
    session.add(scan)
    await session.commit()
    return scan


async def _create(client: AsyncClient) -> str:
    return (await client.post("/projects", json=PROJECT)).json()["id"]


class TestRenaming:
    async def test_a_name_can_always_change(self, authed_client: AsyncClient) -> None:
        project_id = await _create(authed_client)

        response = await authed_client.patch(f"/projects/{project_id}", json={"name": "renamed"})

        assert response.status_code == 200
        assert response.json()["name"] == "renamed"

    async def test_renaming_leaves_the_url_alone(self, authed_client: AsyncClient) -> None:
        """Absence means "leave it", not "clear it"."""
        project_id = await _create(authed_client)

        body = (
            await authed_client.patch(f"/projects/{project_id}", json={"name": "renamed"})
        ).json()

        assert body["repository_url"] == PROJECT["repository_url"]

    async def test_a_name_stays_editable_after_a_completed_scan(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Only the URL freezes. A label corrupts no history."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.COMPLETED)

        response = await authed_client.patch(f"/projects/{project_id}", json={"name": "renamed"})

        assert response.status_code == 200

    async def test_a_blank_name_is_rejected(self, authed_client: AsyncClient) -> None:
        project_id = await _create(authed_client)

        response = await authed_client.patch(f"/projects/{project_id}", json={"name": "   "})

        assert response.status_code == 422


class TestUrlFreeze:
    async def test_a_project_with_no_scans_can_be_repointed(
        self, authed_client: AsyncClient
    ) -> None:
        project_id = await _create(authed_client)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": OTHER_URL}
        )

        assert response.status_code == 200
        assert response.json()["repository_url"] == OTHER_URL

    async def test_a_failed_scan_does_not_freeze_it(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """The case the rule exists for: the URL was wrong, the scan failed
        because of it, and fixing the typo must stay possible. A failed scan
        has no score and no findings, so no history is falsified."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.FAILED)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": OTHER_URL}
        )

        assert response.status_code == 200

    async def test_a_completed_scan_freezes_it(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.COMPLETED)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": OTHER_URL}
        )

        assert response.status_code == 409
        assert "completed scan" in response.json()["detail"]

    @pytest.mark.parametrize("status", [ScanStatus.PENDING, ScanStatus.RUNNING])
    async def test_a_scan_in_flight_freezes_it(
        self, authed_client: AsyncClient, session: AsyncSession, status: ScanStatus
    ) -> None:
        """A different reason: the worker already holds the old target, so an
        edit mid-flight would attribute results to the wrong repository."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, status)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": OTHER_URL}
        )

        assert response.status_code == 409
        assert "in progress" in response.json()["detail"]

    async def test_a_completed_scan_wins_over_one_in_flight(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Telling someone to wait would promise an unlock that never comes."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.COMPLETED)
        await _add_scan(session, project_id, ScanStatus.RUNNING)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": OTHER_URL}
        )

        assert "completed scan" in response.json()["detail"]

    async def test_resending_the_same_url_is_not_a_change(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """A client echoing the whole object back must not trip the lock."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.COMPLETED)

        response = await authed_client.patch(
            f"/projects/{project_id}",
            json={"name": "renamed", "repository_url": PROJECT["repository_url"]},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "renamed"

    async def test_repointing_clears_the_detected_framework(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """The stack belongs to the repository, and this is a different one."""
        project_id = await _create(authed_client)
        await session.execute(
            Project.__table__.update()
            .where(Project.id == uuid.UUID(project_id))
            .values(framework="FastAPI")
        )
        await session.commit()

        body = (
            await authed_client.patch(f"/projects/{project_id}", json={"repository_url": OTHER_URL})
        ).json()

        assert body["framework"] is None

    async def test_an_edit_is_held_to_the_same_url_rules_as_creation(
        self, authed_client: AsyncClient
    ) -> None:
        """A file:// URL rejected at creation must not be reachable by edit."""
        project_id = await _create(authed_client)

        response = await authed_client.patch(
            f"/projects/{project_id}", json={"repository_url": "file:///etc/passwd"}
        )

        assert response.status_code == 422


class TestEditableFlag:
    async def test_a_new_project_reports_editable(self, authed_client: AsyncClient) -> None:
        body = (await authed_client.post("/projects", json=PROJECT)).json()

        assert body["repository_url_editable"] is True

    async def test_a_scanned_project_reports_locked(
        self, authed_client: AsyncClient, session: AsyncSession
    ) -> None:
        """Sent so the client can disable the field with an explanation rather
        than letting somebody type a URL and discover on save it was never
        allowed."""
        project_id = await _create(authed_client)
        await _add_scan(session, project_id, ScanStatus.COMPLETED)

        detail = (await authed_client.get(f"/projects/{project_id}")).json()
        listed = (await authed_client.get("/projects")).json()

        assert detail["repository_url_editable"] is False
        assert listed[0]["repository_url_editable"] is False


class TestOwnership:
    async def test_another_users_project_cannot_be_edited(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        project_id = await _create(authed_client)

        response = await other_client.patch(f"/projects/{project_id}", json={"name": "theirs"})

        assert response.status_code == 404

    async def test_editing_requires_authentication(self, client: AsyncClient) -> None:
        response = await client.patch(f"/projects/{uuid.uuid4()}", json={"name": "x"})

        assert response.status_code == 401


class TestScanNaming:
    async def _scan_id(self, client: AsyncClient) -> str:
        project_id = await _create(client)
        return (await client.post(f"/projects/{project_id}/scans")).json()["id"]

    async def test_a_scan_starts_unnamed(self, authed_client: AsyncClient) -> None:
        scan_id = await self._scan_id(authed_client)

        body = (await authed_client.get(f"/scans/{scan_id}")).json()

        assert body["name"] is None

    async def test_a_scan_can_be_named(self, authed_client: AsyncClient) -> None:
        scan_id = await self._scan_id(authed_client)

        response = await authed_client.patch(
            f"/scans/{scan_id}", json={"name": "before the refactor"}
        )

        assert response.status_code == 200
        assert response.json()["name"] == "before the refactor"

    async def test_a_name_can_be_cleared(self, authed_client: AsyncClient) -> None:
        scan_id = await self._scan_id(authed_client)
        await authed_client.patch(f"/scans/{scan_id}", json={"name": "temporary"})

        body = (await authed_client.patch(f"/scans/{scan_id}", json={"name": ""})).json()

        assert body["name"] is None

    async def test_whitespace_is_not_a_name(self, authed_client: AsyncClient) -> None:
        scan_id = await self._scan_id(authed_client)

        body = (await authed_client.patch(f"/scans/{scan_id}", json={"name": "   "})).json()

        assert body["name"] is None

    async def test_another_users_scan_is_a_404(
        self, authed_client: AsyncClient, other_client: AsyncClient
    ) -> None:
        scan_id = await self._scan_id(authed_client)

        response = await other_client.patch(f"/scans/{scan_id}", json={"name": "theirs"})

        assert response.status_code == 404


class TestTimestampsAreSystemGenerated:
    async def test_a_running_scan_has_no_completion_time(self, authed_client: AsyncClient) -> None:
        project_id = await _create(authed_client)
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]

        body = (await authed_client.get(f"/scans/{scan_id}")).json()

        assert body["completed_at"] is None

    async def test_timestamps_cannot_be_set_through_the_api(
        self, authed_client: AsyncClient
    ) -> None:
        """ScanUpdate carries only a name, so anything else sent is ignored
        rather than applied — history stays system-generated."""
        project_id = await _create(authed_client)
        scan_id = (await authed_client.post(f"/projects/{project_id}/scans")).json()["id"]
        before = (await authed_client.get(f"/scans/{scan_id}")).json()["created_at"]

        await authed_client.patch(
            f"/scans/{scan_id}",
            json={"name": "x", "created_at": "2000-01-01T00:00:00Z", "score": 100},
        )

        after = (await authed_client.get(f"/scans/{scan_id}")).json()
        assert after["created_at"] == before
        assert after["score"] is None


class TestCompletedAtIsRecorded:
    async def test_a_completed_scan_records_when_it_stopped(
        self, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        from app.workers.scan_tasks import execute_scan
        from tests.helpers import CloneSettings, commit_all, init_repo, reload_scan

        repo = init_repo(tmp_path / "source")
        (repo / "app").mkdir()
        (repo / "app" / "main.py").write_text("print('hi')\n", encoding="utf-8")
        commit_all(repo)
        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(user_id=user.id, name="api", repository_url=repo.as_uri())
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.completed_at is not None
        assert finished.completed_at >= finished.created_at

    async def test_a_failed_scan_records_one_too(
        self, session: AsyncSession, tmp_path, monkeypatch
    ) -> None:
        """A null here would make a dead scan look like it is still running."""
        from app.workers.scan_tasks import execute_scan
        from tests.helpers import CloneSettings, reload_scan

        monkeypatch.setattr(
            "app.workers.repo.get_settings", lambda: CloneSettings(tmp_path / "clones")
        )

        user = User(email="owner@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        project = Project(
            user_id=user.id, name="gone", repository_url=(tmp_path / "missing").as_uri()
        )
        session.add(project)
        await session.flush()
        scan = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
        session.add(scan)
        await session.commit()

        await execute_scan(session, scan_id=scan.id)

        finished = await reload_scan(session, scan.id)
        assert finished.status is ScanStatus.FAILED
        assert finished.completed_at is not None
