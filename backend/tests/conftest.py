"""Shared test fixtures.

Tests run against a real Postgres rather than SQLite. The schema uses JSONB,
native enums, and ON DELETE CASCADE — none of which SQLite reproduces, so a
SQLite suite would pass while the real database rejected the same code.
"""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401  — registers every table on Base.metadata
from app.config import get_settings
from app.database.base import Base
from app.main import app as fastapi_app


def _database_urls() -> tuple[URL, URL]:
    """Return (admin URL, test URL).

    The test database is the configured one with a _test suffix, so a stray run
    can never target the development database. The guard is cheap insurance
    against a config change quietly making them the same name.
    """
    admin_url = make_url(get_settings().database_url)
    test_url = admin_url.set(database=f"{admin_url.database}_test")
    if test_url.database == admin_url.database:
        raise RuntimeError(
            "Refusing to run: test and development databases resolved to the same name"
        )
    return admin_url, test_url


async def _drop_and_create(admin_url: URL, database: str, *, recreate: bool) -> None:
    """CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT."""
    engine = create_async_engine(
        admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    async with engine.connect() as conn:
        # WITH (FORCE) evicts any connection still holding the database open;
        # without it a leaked connection from a previous run blocks the drop.
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        if recreate:
            await conn.execute(text(f'CREATE DATABASE "{database}"'))
    await engine.dispose()


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    """A disposable test database, built once per run and dropped afterwards."""
    admin_url, test_url = _database_urls()
    await _drop_and_create(admin_url, test_url.database, recreate=True)

    test_engine = create_async_engine(test_url.render_as_string(hide_password=False))
    # create_all rather than running Alembic: alembic/env.py calls asyncio.run(),
    # which cannot be invoked from inside an already-running event loop. Migration
    # correctness is verified separately by autogenerate reporting no drift.
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield test_engine

    await test_engine.dispose()
    await _drop_and_create(admin_url, test_url.database, recreate=False)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose writes are always rolled back.

    The session is bound to a connection with an already-open transaction, so
    join_transaction_mode="create_savepoint" makes the code under test able to
    call commit() — it releases a savepoint rather than committing for real, and
    the outer rollback still undoes everything. Far faster than truncating
    tables between tests, and leaves no residue if a test fails midway.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        maker = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with maker() as db_session:
            yield db_session
        await transaction.rollback()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client that talks to the app in-process, with no socket involved.

    The database dependency override arrives with api/deps.py — until then this
    only exercises routes that touch no database.
    """
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
