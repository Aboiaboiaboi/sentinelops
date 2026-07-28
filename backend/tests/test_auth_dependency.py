"""Tests for get_current_user.

No route uses this dependency yet — the project endpoints that will are the next
commit. Rather than ship the piece that decides who a request belongs to with no
coverage, these mount it on a probe app built for the test.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import auth
from app.api.deps import COOKIE_NAME, CurrentUser, get_db
from app.auth.jwt import create_access_token
from app.models import User

CREDENTIALS = {"email": "dependency@example.com", "password": "correct horse battery"}


@pytest.fixture
async def probe(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An app with one protected route, plus the real auth router to log in with."""
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/whoami")
    async def whoami(user: CurrentUser) -> dict[str, str]:
        return {"id": str(user.id), "email": user.email}

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_identifies_the_signed_in_user(probe: AsyncClient) -> None:
    signup = await probe.post("/auth/signup", json=CREDENTIALS)

    response = await probe.get("/whoami")

    assert response.status_code == 200
    assert response.json()["id"] == signup.json()["id"]


async def test_no_cookie_is_401(probe: AsyncClient) -> None:
    response = await probe.get("/whoami")

    assert response.status_code == 401


def _cookie(token: str) -> dict[str, str]:
    """Send the cookie as a header.

    httpx deprecates per-request `cookies=` because its persistence behaviour is
    ambiguous, and setting it on the client would leak the value into every
    later request in the same test.
    """
    return {"Cookie": f"{COOKIE_NAME}={token}"}


async def test_garbage_cookie_is_401(probe: AsyncClient) -> None:
    response = await probe.get("/whoami", headers=_cookie("not-a-jwt"))

    assert response.status_code == 401


async def test_token_signed_with_another_key_is_401(probe: AsyncClient) -> None:
    import jwt as pyjwt

    from app.config import get_settings

    forged = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "exp": 9_999_999_999},
        "a-different-secret-of-sufficient-length",
        algorithm=get_settings().jwt_algorithm,
    )

    response = await probe.get("/whoami", headers=_cookie(forged))

    assert response.status_code == 401


async def test_valid_token_for_a_deleted_user_is_401(
    probe: AsyncClient, session: AsyncSession
) -> None:
    """A token outlives the account it names.

    Without the existence check in get_current_user this would keep working for
    the full seven-day expiry after the account was gone.
    """
    signup = await probe.post("/auth/signup", json=CREDENTIALS)
    user_id = uuid.UUID(signup.json()["id"])

    await session.execute(delete(User).where(User.id == user_id))
    await session.commit()

    response = await probe.get("/whoami")

    assert response.status_code == 401


async def test_every_failure_returns_the_same_body(probe: AsyncClient) -> None:
    """Missing, malformed, and unknown-user tokens must be indistinguishable."""
    missing = await probe.get("/whoami")
    malformed = await probe.get("/whoami", headers=_cookie("not-a-jwt"))
    # Correctly signed and unexpired, but names a user that was never created.
    unknown_user = await probe.get("/whoami", headers=_cookie(create_access_token(uuid.uuid4())))

    assert missing.json() == malformed.json() == unknown_user.json()
