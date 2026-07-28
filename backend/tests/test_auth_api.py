"""Tests for POST /auth/signup and POST /auth/login.

These assert the wire contract the frontend depends on, not just that the
endpoints work — field names, status codes, and the cookie flags are all things
the client would break on silently.
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import COOKIE_NAME
from app.models import User

CREDENTIALS = {"email": "engineer@example.com", "password": "correct horse battery"}


def _cookie_attrs(response) -> dict[str, str]:
    """Parse the Set-Cookie header, which httpx does not expose structurally."""
    header = response.headers["set-cookie"]
    attrs: dict[str, str] = {}
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        attrs[key.lower()] = value
    return attrs


class TestSignup:
    async def test_creates_a_user_and_returns_it(self, client: AsyncClient) -> None:
        response = await client.post("/auth/signup", json=CREDENTIALS)

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == CREDENTIALS["email"]
        assert uuid.UUID(body["id"])

    async def test_response_never_exposes_the_password(self, client: AsyncClient) -> None:
        response = await client.post("/auth/signup", json=CREDENTIALS)

        body = response.json()
        assert set(body) == {"id", "email", "created_at"}
        assert "password_hash" not in response.text
        assert CREDENTIALS["password"] not in response.text

    async def test_stores_a_hash_not_the_password(
        self, client: AsyncClient, session: AsyncSession
    ) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        user = await session.scalar(select(User).where(User.email == CREDENTIALS["email"]))
        assert user is not None
        assert user.password_hash != CREDENTIALS["password"]
        assert user.password_hash.startswith("$2b$")

    async def test_sets_an_httponly_cookie(self, client: AsyncClient) -> None:
        response = await client.post("/auth/signup", json=CREDENTIALS)

        attrs = _cookie_attrs(response)
        assert COOKIE_NAME in attrs
        assert "httponly" in attrs
        assert attrs["samesite"].lower() == "lax"

    async def test_cookie_is_not_secure_in_development(self, client: AsyncClient) -> None:
        """A Secure cookie is dropped over plain http, so local login would
        appear to succeed and then not persist."""
        response = await client.post("/auth/signup", json=CREDENTIALS)

        assert "secure" not in _cookie_attrs(response)

    async def test_created_at_is_z_suffixed_utc(self, client: AsyncClient) -> None:
        """Matches the frontend's own Date.toISOString() output."""
        response = await client.post("/auth/signup", json=CREDENTIALS)

        assert response.json()["created_at"].endswith("Z")

    async def test_duplicate_email_is_a_conflict(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)
        response = await client.post("/auth/signup", json=CREDENTIALS)

        assert response.status_code == 409
        assert "detail" in response.json()

    async def test_email_is_matched_case_insensitively(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)
        response = await client.post(
            "/auth/signup", json={**CREDENTIALS, "email": "Engineer@Example.com"}
        )

        assert response.status_code == 409

    async def test_rejects_an_invalid_email(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/signup", json={**CREDENTIALS, "password": "abcdefgh", "email": "not-an-email"}
        )

        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)

    async def test_rejects_a_short_password(self, client: AsyncClient) -> None:
        response = await client.post("/auth/signup", json={**CREDENTIALS, "password": "short"})

        assert response.status_code == 422

    async def test_over_long_password_is_422_not_500(self, client: AsyncClient) -> None:
        """20 emoji is 20 characters but 80 bytes — past what bcrypt accepts.
        A character-based limit would let this through and crash the hasher."""
        response = await client.post("/auth/signup", json={**CREDENTIALS, "password": "😀" * 20})

        assert response.status_code == 422


class TestLogin:
    async def test_correct_credentials_return_the_user_and_a_cookie(
        self, client: AsyncClient
    ) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.post("/auth/login", json=CREDENTIALS)

        assert response.status_code == 200
        assert response.json()["email"] == CREDENTIALS["email"]
        assert "httponly" in _cookie_attrs(response)

    async def test_wrong_password_is_401(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.post(
            "/auth/login", json={**CREDENTIALS, "password": "wrong password"}
        )

        assert response.status_code == 401

    async def test_unknown_email_is_401(self, client: AsyncClient) -> None:
        response = await client.post(
            "/auth/login", json={**CREDENTIALS, "email": "nobody@example.com"}
        )

        assert response.status_code == 401

    async def test_wrong_password_and_unknown_email_are_indistinguishable(
        self, client: AsyncClient
    ) -> None:
        """Different messages here would let anyone enumerate registered emails."""
        await client.post("/auth/signup", json=CREDENTIALS)

        wrong_password = await client.post(
            "/auth/login", json={**CREDENTIALS, "password": "wrong password"}
        )
        unknown_email = await client.post(
            "/auth/login", json={**CREDENTIALS, "email": "nobody@example.com"}
        )

        assert wrong_password.json() == unknown_email.json()

    async def test_login_works_with_differently_cased_email(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.post(
            "/auth/login", json={**CREDENTIALS, "email": "ENGINEER@EXAMPLE.COM"}
        )

        assert response.status_code == 200
