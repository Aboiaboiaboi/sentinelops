"""Tests for the scan progress transitions.

These four functions are the only place a scan's status, score, scoring_version
or category_status may be written, so their guarantees matter more than most:
a lost category result or a double-claimed job would be invisible in normal use
and only show up as inconsistent scans under load.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.models import CategoryStatus, Project, Scan, ScanStatus, User
from app.services import scan_service


@pytest.fixture
async def scan(session: AsyncSession) -> Scan:
    """A pending scan with all six categories pending, as create_scan makes it."""
    user = User(email="owner@example.com", password_hash="x")
    session.add(user)
    await session.flush()

    project = Project(user_id=user.id, name="p", repository_url="https://github.com/a/b")
    session.add(project)
    await session.flush()

    row = Scan(project_id=project.id, category_status=scan_service.initial_category_status())
    session.add(row)
    await session.commit()
    return row


async def _reload(session: AsyncSession, scan_id: uuid.UUID) -> Scan:
    """Re-read from the database rather than trusting the in-memory object.

    The transitions run raw UPDATE statements, so an ORM instance held from
    before them is stale by definition.
    """
    session.expire_all()
    return await session.scalar(select(Scan).where(Scan.id == scan_id))


class TestClaimScan:
    async def test_moves_pending_to_running(self, session: AsyncSession, scan: Scan) -> None:
        won = await scan_service.claim_scan(session, scan_id=scan.id)

        assert won is True
        assert (await _reload(session, scan.id)).status is ScanStatus.RUNNING

    async def test_only_one_caller_can_claim(self, session: AsyncSession, scan: Scan) -> None:
        """Two workers taking the same job would clone and scan the same
        repository twice, and race each other writing results."""
        first = await scan_service.claim_scan(session, scan_id=scan.id)
        second = await scan_service.claim_scan(session, scan_id=scan.id)

        assert (first, second) == (True, False)

    async def test_cannot_claim_a_finished_scan(self, session: AsyncSession, scan: Scan) -> None:
        await scan_service.claim_scan(session, scan_id=scan.id)
        await scan_service.complete_scan(session, scan_id=scan.id, score=90, scoring_version="v1")

        assert await scan_service.claim_scan(session, scan_id=scan.id) is False

    async def test_unknown_scan_is_false_not_an_error(self, session: AsyncSession) -> None:
        assert await scan_service.claim_scan(session, scan_id=uuid.uuid4()) is False


class TestRecordCategoryResult:
    async def test_sets_one_category(self, session: AsyncSession, scan: Scan) -> None:
        await scan_service.record_category_result(
            session, scan_id=scan.id, category="security", status=CategoryStatus.COMPLETED
        )

        assert (await _reload(session, scan.id)).category_status["security"] == "completed"

    async def test_leaves_the_other_categories_alone(
        self, session: AsyncSession, scan: Scan
    ) -> None:
        await scan_service.record_category_result(
            session, scan_id=scan.id, category="security", status=CategoryStatus.COMPLETED
        )

        statuses = (await _reload(session, scan.id)).category_status
        assert statuses["architecture"] == "pending"
        assert len(statuses) == 6

    async def test_parallel_sessions_do_not_lose_each_others_results(
        self, engine: AsyncEngine
    ) -> None:
        """The reason this uses jsonb_set rather than read-modify-write.

        Uses independent sessions rather than the shared rolled-back one,
        because the hazard is between *processes*: a bulk UPDATE expires the
        issuing session's cache, so a single-session test cannot reproduce it.
        Six categories run in parallel, and a read-modify-write implementation
        would have each write the whole map back from its own snapshot — last
        writer wins and the other five results vanish.

        Committed for real and cleaned up in a finally, since it deliberately
        steps outside the rollback fixture.
        """
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            user = User(email="parallel@example.com", password_hash="x")
            setup.add(user)
            await setup.flush()
            project = Project(user_id=user.id, name="p", repository_url="https://github.com/a/b")
            setup.add(project)
            await setup.flush()
            row = Scan(
                project_id=project.id, category_status=scan_service.initial_category_status()
            )
            setup.add(row)
            await setup.commit()
            scan_id, user_id = row.id, user.id

        async def record(category: str, status: CategoryStatus) -> None:
            async with AsyncSession(engine, expire_on_commit=False) as own:
                await scan_service.record_category_result(
                    own, scan_id=scan_id, category=category, status=status
                )

        try:
            await asyncio.gather(
                record("security", CategoryStatus.COMPLETED),
                record("deployment", CategoryStatus.FAILED),
                record("reliability", CategoryStatus.COMPLETED),
            )

            async with AsyncSession(engine) as check:
                statuses = (
                    await check.scalar(select(Scan).where(Scan.id == scan_id))
                ).category_status

            assert statuses["security"] == "completed"
            assert statuses["deployment"] == "failed"
            assert statuses["reliability"] == "completed"
            # Untouched categories must survive too.
            assert statuses["architecture"] == "pending"
            assert len(statuses) == 6
        finally:
            async with AsyncSession(engine) as cleanup:
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.commit()

    async def test_records_a_failed_category(self, session: AsyncSession, scan: Scan) -> None:
        """failed and pending are different states — one sandbox dying must not
        read as still running."""
        await scan_service.record_category_result(
            session, scan_id=scan.id, category="observability", status=CategoryStatus.FAILED
        )

        assert (await _reload(session, scan.id)).category_status["observability"] == "failed"

    async def test_rejects_an_unknown_category(self, session: AsyncSession, scan: Scan) -> None:
        """A typo would otherwise add a key the frontend renders as an unknown
        category with zero weight."""
        with pytest.raises(ValueError, match="Unknown scanner category"):
            await scan_service.record_category_result(
                session, scan_id=scan.id, category="architcture", status=CategoryStatus.COMPLETED
            )


class TestCompleteScan:
    async def test_writes_score_and_version(self, session: AsyncSession, scan: Scan) -> None:
        await scan_service.claim_scan(session, scan_id=scan.id)

        assert await scan_service.complete_scan(
            session, scan_id=scan.id, score=82, scoring_version="v1"
        )

        finished = await _reload(session, scan.id)
        assert finished.status is ScanStatus.COMPLETED
        assert finished.score == 82
        assert finished.scoring_version == "v1"

    async def test_cannot_complete_a_scan_that_never_started(
        self, session: AsyncSession, scan: Scan
    ) -> None:
        assert (
            await scan_service.complete_scan(
                session, scan_id=scan.id, score=82, scoring_version="v1"
            )
            is False
        )
        assert (await _reload(session, scan.id)).status is ScanStatus.PENDING

    async def test_cannot_resurrect_a_failed_scan(self, session: AsyncSession, scan: Scan) -> None:
        """A duplicate job delivery must not turn a failure into a success."""
        await scan_service.claim_scan(session, scan_id=scan.id)
        await scan_service.fail_scan(session, scan_id=scan.id)

        assert (
            await scan_service.complete_scan(
                session, scan_id=scan.id, score=99, scoring_version="v1"
            )
            is False
        )
        assert (await _reload(session, scan.id)).status is ScanStatus.FAILED


class TestFailScan:
    async def test_fails_a_running_scan(self, session: AsyncSession, scan: Scan) -> None:
        await scan_service.claim_scan(session, scan_id=scan.id)

        assert await scan_service.fail_scan(session, scan_id=scan.id)
        assert (await _reload(session, scan.id)).status is ScanStatus.FAILED

    async def test_fails_a_scan_that_was_never_claimed(
        self, session: AsyncSession, scan: Scan
    ) -> None:
        """A job can die between being queued and being claimed. That scan must
        not poll forever."""
        assert await scan_service.fail_scan(session, scan_id=scan.id)
        assert (await _reload(session, scan.id)).status is ScanStatus.FAILED

    async def test_leaves_score_null(self, session: AsyncSession, scan: Scan) -> None:
        """A zero would be indistinguishable from a genuinely terrible repo."""
        await scan_service.claim_scan(session, scan_id=scan.id)
        await scan_service.fail_scan(session, scan_id=scan.id)

        assert (await _reload(session, scan.id)).score is None

    async def test_cannot_fail_a_completed_scan(self, session: AsyncSession, scan: Scan) -> None:
        await scan_service.claim_scan(session, scan_id=scan.id)
        await scan_service.complete_scan(session, scan_id=scan.id, score=70, scoring_version="v1")

        assert await scan_service.fail_scan(session, scan_id=scan.id) is False
        assert (await _reload(session, scan.id)).status is ScanStatus.COMPLETED
