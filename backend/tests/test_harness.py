"""Tests for the fixtures themselves.

The transactional-rollback fixture is the thing every later test depends on for
isolation. If it silently stopped rolling back, suites would start passing or
failing based on execution order — so it is worth asserting directly rather
than trusting.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


async def test_session_starts_empty(session: AsyncSession) -> None:
    count = await session.scalar(select(func.count()).select_from(User))

    assert count == 0


async def test_committed_writes_are_still_rolled_back(session: AsyncSession) -> None:
    """A commit inside a test must not survive it.

    This is the case create_savepoint exists to handle: the code under test
    commits normally, but the fixture's outer transaction still discards it.
    """
    session.add(User(email="isolation@example.com", password_hash="x"))
    await session.commit()

    count = await session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_previous_test_left_nothing_behind(session: AsyncSession) -> None:
    """Runs after the test above and must not see its committed user."""
    count = await session.scalar(select(func.count()).select_from(User))

    assert count == 0
