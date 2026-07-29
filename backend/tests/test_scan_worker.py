"""Tests for queueing a scan and for the worker that runs it.

The task itself is split so these can exercise the logic directly: `run_scan`
only opens a session, and `execute_scan` takes one — which is what lets the
transactional fixture keep these isolated.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, ScanStatus, User
from app.services import scan_service
from app.utils.queue import InMemoryQueue
from app.workers.scan_tasks import execute_scan

PROJECT = {"name": "api", "repository_url": "https://github.com/acme/api"}


@pytest.fixture
async def owned_project(session: AsyncSession) -> Project:
    user = User(email="owner@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    project = Project(user_id=user.id, name="api", repository_url="https://github.com/acme/api")
    session.add(project)
    await session.commit()
    return project


async def _reload(session: AsyncSession, scan_id: uuid.UUID) -> Scan:
    session.expire_all()
    return await session.scalar(select(Scan).where(Scan.id == scan_id))


class TestQueueingOnCreate:
    async def test_publishes_a_job(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        user = await session.get(User, owned_project.user_id)

        await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        assert len(queue.published) == 1
        task, payload = queue.published[0]
        assert task == "run_scan"

    async def test_job_carries_the_scan_id(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        user = await session.get(User, owned_project.user_id)

        scan = await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        _, payload = queue.published[0]
        assert payload["scan_id"] == str(scan.id)

    async def test_job_id_is_derived_from_the_scan(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        """Deduplication key. A double-clicked Run scan, or a retried publish,
        must not run the same repository twice."""
        user = await session.get(User, owned_project.user_id)

        scan = await scan_service.create_scan(session, owner=user, project_id=owned_project.id)

        _, payload = queue.published[0]
        assert payload["_job_id"] == f"scan:{scan.id}"

    async def test_nothing_is_published_for_another_users_project(
        self, session: AsyncSession, owned_project: Project, queue: InMemoryQueue
    ) -> None:
        intruder = User(email="intruder@example.com", password_hash="x")
        session.add(intruder)
        await session.commit()

        result = await scan_service.create_scan(
            session, owner=intruder, project_id=owned_project.id
        )

        assert result is None
        assert queue.published == []

    async def test_a_queue_failure_marks_the_scan_failed(
        self, session: AsyncSession, owned_project: Project, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The row is committed before publishing, so an unqueued scan would
        otherwise sit pending forever with the client polling it."""

        class BrokenQueue:
            async def publish(self, task: str, /, **payload: object) -> str:
                raise ConnectionError("redis is down")

        monkeypatch.setattr("app.services.scan_service.get_queue", lambda: BrokenQueue())
        user = await session.get(User, owned_project.user_id)
        # Read before expiring: afterwards this attribute would trigger a reload.
        project_id = owned_project.id

        with pytest.raises(ConnectionError):
            await scan_service.create_scan(session, owner=user, project_id=project_id)

        session.expire_all()
        scan = await session.scalar(select(Scan).where(Scan.project_id == project_id))
        assert scan is not None
        assert scan.status is ScanStatus.FAILED

    async def test_the_endpoint_still_returns_pending(
        self, authed_client: AsyncClient, queue: InMemoryQueue
    ) -> None:
        """Queueing must not delay the response or change its shape."""
        project = (await authed_client.post("/projects", json=PROJECT)).json()

        response = await authed_client.post(f"/projects/{project['id']}/scans")

        assert response.status_code == 201
        assert response.json()["status"] == "pending"
        assert len(queue.published) == 1


class TestExecuteScan:
    @pytest.fixture
    async def pending_scan(self, session: AsyncSession, owned_project: Project) -> Scan:
        scan = Scan(
            project_id=owned_project.id,
            category_status=scan_service.initial_category_status(),
        )
        session.add(scan)
        await session.commit()
        return scan

    async def test_drives_the_scan_to_completed(
        self, session: AsyncSession, pending_scan: Scan
    ) -> None:
        await execute_scan(session, scan_id=pending_scan.id)

        assert (await _reload(session, pending_scan.id)).status is ScanStatus.COMPLETED

    async def test_writes_a_score_and_version(
        self, session: AsyncSession, pending_scan: Scan
    ) -> None:
        await execute_scan(session, scan_id=pending_scan.id)

        finished = await _reload(session, pending_scan.id)
        assert finished.score == 0
        assert finished.scoring_version == "v1"

    async def test_leaves_no_category_pending(
        self, session: AsyncSession, pending_scan: Scan
    ) -> None:
        """The client renders pending as "Scanning…", so a finished scan with
        pending categories would claim to still be running."""
        await execute_scan(session, scan_id=pending_scan.id)

        statuses = (await _reload(session, pending_scan.id)).category_status
        assert "pending" not in statuses.values()
        assert len(statuses) == 6

    async def test_a_second_delivery_is_a_no_op(
        self, session: AsyncSession, pending_scan: Scan
    ) -> None:
        """arq redelivers on retry; a duplicate must not restart finished work."""
        await execute_scan(session, scan_id=pending_scan.id)
        first = await _reload(session, pending_scan.id)

        await execute_scan(session, scan_id=pending_scan.id)

        second = await _reload(session, pending_scan.id)
        assert second.status is ScanStatus.COMPLETED
        assert second.score == first.score

    async def test_unknown_scan_is_a_no_op(self, session: AsyncSession) -> None:
        await execute_scan(session, scan_id=uuid.uuid4())

    async def test_a_crash_marks_the_scan_failed(
        self, session: AsyncSession, pending_scan: Scan, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the row stays running and the client polls forever."""

        async def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("scanner exploded")

        monkeypatch.setattr("app.workers.scan_tasks.scan_service.record_category_result", boom)

        with pytest.raises(RuntimeError):
            await execute_scan(session, scan_id=pending_scan.id)

        assert (await _reload(session, pending_scan.id)).status is ScanStatus.FAILED
