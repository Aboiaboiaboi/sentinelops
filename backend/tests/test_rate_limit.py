"""Tests for auth rate limiting.

The counters are reset before every test by an autouse fixture in conftest, so
these assert the limit fires without depending on what ran before them.
"""

from httpx import AsyncClient

from app.config import get_settings

PASSWORD = "correct horse battery"


def _limit_count(limit: str) -> int:
    """ "10/minute" -> 10."""
    return int(limit.split("/")[0])


class TestLoginRateLimit:
    async def test_allows_requests_up_to_the_limit(self, client: AsyncClient) -> None:
        allowed = _limit_count(get_settings().login_rate_limit)

        for _ in range(allowed):
            response = await client.post(
                "/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
            )
            # 401 because the account does not exist — the point is that it is
            # not yet 429.
            assert response.status_code == 401

    async def test_blocks_the_request_after_the_limit(self, client: AsyncClient) -> None:
        allowed = _limit_count(get_settings().login_rate_limit)
        payload = {"email": "nobody@example.com", "password": PASSWORD}

        for _ in range(allowed):
            await client.post("/auth/login", json=payload)
        response = await client.post("/auth/login", json=payload)

        assert response.status_code == 429

    async def test_429_uses_the_detail_error_shape(self, client: AsyncClient) -> None:
        """slowapi's built-in handler returns {"error": ...}, which the client
        cannot read — it would show the bare status text instead."""
        payload = {"email": "nobody@example.com", "password": PASSWORD}
        for _ in range(_limit_count(get_settings().login_rate_limit) + 1):
            response = await client.post("/auth/login", json=payload)

        body = response.json()
        assert "detail" in body
        assert isinstance(body["detail"], str)

    async def test_429_says_when_to_retry(self, client: AsyncClient) -> None:
        payload = {"email": "nobody@example.com", "password": PASSWORD}
        for _ in range(_limit_count(get_settings().login_rate_limit) + 1):
            response = await client.post("/auth/login", json=payload)

        assert "retry-after" in {k.lower() for k in response.headers}

    async def test_a_correct_password_still_counts_toward_the_limit(
        self, client: AsyncClient
    ) -> None:
        """Otherwise an attacker who guessed one valid credential could keep
        hammering the endpoint for free."""
        await client.post("/auth/signup", json={"email": "real@example.com", "password": PASSWORD})
        payload = {"email": "real@example.com", "password": PASSWORD}

        for _ in range(_limit_count(get_settings().login_rate_limit)):
            await client.post("/auth/login", json=payload)
        response = await client.post("/auth/login", json=payload)

        assert response.status_code == 429


class TestSignupRateLimit:
    async def test_blocks_bulk_account_creation(self, client: AsyncClient) -> None:
        allowed = _limit_count(get_settings().signup_rate_limit)

        for i in range(allowed):
            response = await client.post(
                "/auth/signup", json={"email": f"user{i}@example.com", "password": PASSWORD}
            )
            assert response.status_code == 201

        response = await client.post(
            "/auth/signup", json={"email": "one-too-many@example.com", "password": PASSWORD}
        )

        assert response.status_code == 429

    async def test_signup_and_login_have_separate_budgets(self, client: AsyncClient) -> None:
        """Exhausting signups must not lock a legitimate user out of logging in."""
        for i in range(_limit_count(get_settings().signup_rate_limit) + 1):
            await client.post(
                "/auth/signup", json={"email": f"bulk{i}@example.com", "password": PASSWORD}
            )

        response = await client.post(
            "/auth/login", json={"email": "bulk0@example.com", "password": PASSWORD}
        )

        assert response.status_code == 200


class TestUnlimitedEndpoints:
    async def test_polling_is_not_rate_limited(self, authed_client: AsyncClient) -> None:
        """GET /scans/{id} is polled every three seconds per open tab. A global
        limit low enough to matter would break it, which is why only the auth
        routes are limited."""
        project = (
            await authed_client.post(
                "/projects",
                json={"name": "p", "repository_url": "https://github.com/a/b"},
            )
        ).json()
        scan = (await authed_client.post(f"/projects/{project['id']}/scans")).json()

        for _ in range(40):
            response = await authed_client.get(f"/scans/{scan['id']}")
            assert response.status_code == 200
