"""Tests for the /auth endpoints.

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


class TestMe:
    async def test_returns_the_signed_in_user(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.get("/auth/me")

        assert response.status_code == 200
        assert response.json()["email"] == CREDENTIALS["email"]

    async def test_shape_matches_login_and_signup(self, client: AsyncClient) -> None:
        """The frontend caches whichever of the three it saw last, so a field
        present in one and missing from another would break on refresh only."""
        signup = await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.get("/auth/me")

        assert response.json() == signup.json()
        assert set(response.json()) == {"id", "email", "created_at"}

    async def test_without_a_cookie_is_401(self, client: AsyncClient) -> None:
        response = await client.get("/auth/me")

        assert response.status_code == 401

    async def test_with_a_forged_cookie_is_401(self, client: AsyncClient) -> None:
        client.cookies.set(COOKIE_NAME, "not.a.jwt")

        response = await client.get("/auth/me")

        assert response.status_code == 401

    async def test_deleted_account_is_401(self, client: AsyncClient, session: AsyncSession) -> None:
        """A valid signature is not a valid session. The token outlives the row
        it names, and nothing else would notice until it expired on its own."""
        await client.post("/auth/signup", json=CREDENTIALS)
        user = await session.scalar(select(User).where(User.email == CREDENTIALS["email"]))
        assert user is not None
        await session.delete(user)
        await session.commit()

        response = await client.get("/auth/me")

        assert response.status_code == 401

    async def test_never_exposes_the_password(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.get("/auth/me")

        assert "password_hash" not in response.text


class TestLogout:
    async def test_returns_204_with_no_body(self, client: AsyncClient) -> None:
        await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.post("/auth/logout")

        assert response.status_code == 204
        assert response.content == b""

    async def test_the_session_is_actually_over(self, client: AsyncClient) -> None:
        """The assertion that matters: the client's own jar, after a real
        logout, no longer authenticates. Checking the header alone would pass
        against a cookie the browser never replaces."""
        await client.post("/auth/signup", json=CREDENTIALS)
        assert (await client.get("/auth/me")).status_code == 200

        await client.post("/auth/logout")

        assert (await client.get("/auth/me")).status_code == 401

    async def test_clears_the_cookie_on_the_path_that_issued_it(self, client: AsyncClient) -> None:
        """A delete on a different path adds a second cookie instead of
        removing the first, and the original keeps being sent."""
        signup = await client.post("/auth/signup", json=CREDENTIALS)

        response = await client.post("/auth/logout")

        attrs = _cookie_attrs(response)
        assert attrs[COOKIE_NAME] == '""'
        assert attrs["path"] == _cookie_attrs(signup)["path"]
        assert attrs["max-age"] == "0"

    async def test_without_a_session_is_still_204(self, client: AsyncClient) -> None:
        """401 here would refuse to clear the cookie in the one case where the
        caller most needs it gone."""
        response = await client.post("/auth/logout")

        assert response.status_code == 204

    async def test_with_a_forged_cookie_is_still_204(self, client: AsyncClient) -> None:
        client.cookies.set(COOKIE_NAME, "not.a.jwt")

        response = await client.post("/auth/logout")

        assert response.status_code == 204
