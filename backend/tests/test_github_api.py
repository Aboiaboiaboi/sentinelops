"""Tests for the GitHub connect flow endpoints.

GitHub itself is a mock transport behind the dependency override; the database
side is real. The ownership cases follow the projects suite: another user's
installations are invisible, not forbidden.
"""

from collections.abc import Iterator

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

from app.api.github import _github_auth
from app.main import app as fastapi_app
from app.services.github_app_service import GitHubAppAuth

INSTALLATION_ID = 43126780

# A real key even though the mock GitHub never verifies signatures: the auth
# instance signs an App JWT before every App-authenticated call, and signing
# needs a parseable key regardless of who checks it.
PRIVATE_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)


def _github(handler) -> GitHubAppAuth:
    """An auth instance whose GitHub is the given handler."""
    return GitHubAppAuth(
        client_id="Iv23liTESTCLIENTID",
        private_key_pem=PRIVATE_PEM,
        transport=httpx.MockTransport(handler),
    )


def _happy_github(request: httpx.Request) -> httpx.Response:
    """A GitHub that knows one installation with two repositories."""
    path = request.url.path
    if path == f"/app/installations/{INSTALLATION_ID}":
        return httpx.Response(200, json={"account": {"login": "octocat"}})
    if path == f"/app/installations/{INSTALLATION_ID}/access_tokens":
        return httpx.Response(201, json={"token": "ghs_test", "expires_at": "2026-01-01T00:00:00Z"})
    if path == "/installation/repositories":
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "repositories": [
                    {
                        "full_name": "octocat/zebra",
                        "private": True,
                        "clone_url": "https://github.com/octocat/zebra.git",
                    },
                    {
                        "full_name": "octocat/aardvark",
                        "private": False,
                        "clone_url": "https://github.com/octocat/aardvark.git",
                    },
                ],
            },
        )
    return httpx.Response(404, json={})


@pytest.fixture
def github() -> Iterator[None]:
    """Point the API's GitHub dependency at the happy mock."""

    def override() -> GitHubAppAuth:
        return _github(_happy_github)

    fastapi_app.dependency_overrides[_github_auth] = override
    yield
    # The client fixture clears all overrides on teardown; removing just ours
    # keeps this fixture usable in either order.
    fastapi_app.dependency_overrides.pop(_github_auth, None)


async def _connect(client: AsyncClient) -> httpx.Response:
    return await client.get(
        "/github/setup",
        params={"installation_id": INSTALLATION_ID, "setup_action": "install"},
    )


class TestAuthentication:
    async def test_all_routes_require_a_cookie(self, client: AsyncClient, github: None) -> None:
        assert (await client.get("/github/install")).status_code == 401
        assert (await _connect(client)).status_code == 401
        assert (await client.get("/github/installations")).status_code == 401
        assert (await client.get("/github/repositories")).status_code == 401


class TestInstallRedirect:
    async def test_redirects_to_the_apps_install_page(
        self, authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import get_settings

        # The cached instance, not the class: pydantic stores field values on
        # the instance, which would shadow anything set on the type.
        monkeypatch.setattr(get_settings(), "github_app_slug", "sentinelops-dev")

        response = await authed_client.get("/github/install")

        assert response.status_code == 307
        assert (
            response.headers["location"]
            == "https://github.com/apps/sentinelops-dev/installations/new"
        )

    async def test_unconfigured_is_a_503_naming_the_variable(
        self, authed_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.config import get_settings

        # Forced empty rather than assumed empty: the developer's own .env may
        # legitimately configure the App, and this test's subject is the
        # unconfigured path, not the developer's machine.
        monkeypatch.setattr(get_settings(), "github_app_slug", "")

        response = await authed_client.get("/github/install")

        assert response.status_code == 503
        assert "GITHUB_APP_SLUG" in response.json()["detail"]


class TestSetupCallback:
    async def test_records_the_installation_and_redirects_home(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        response = await _connect(authed_client)

        assert response.status_code == 303
        assert response.headers["location"].endswith("/dashboard?github=connected")

        listed = (await authed_client.get("/github/installations")).json()
        assert len(listed) == 1
        assert listed[0]["installation_id"] == INSTALLATION_ID
        assert listed[0]["account_login"] == "octocat"

    async def test_a_forged_installation_id_records_nothing(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        """The query parameter is claimed, not trusted. An id GitHub does not
        answer for must not become a row."""
        response = await authed_client.get(
            "/github/setup", params={"installation_id": 999999, "setup_action": "install"}
        )

        assert response.status_code == 404
        assert (await authed_client.get("/github/installations")).json() == []

    async def test_running_setup_twice_keeps_one_row(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        """Changing repository access re-runs the flow with the same id."""
        await _connect(authed_client)
        await _connect(authed_client)

        assert len((await authed_client.get("/github/installations")).json()) == 1

    async def test_reconnecting_from_another_account_moves_the_installation(
        self, authed_client: AsyncClient, other_client: AsyncClient, github: None
    ) -> None:
        """GitHub proved whoever completes the flow controls the installation,
        so the row follows them; one installation never has two owners."""
        await _connect(authed_client)
        await _connect(other_client)

        assert (await authed_client.get("/github/installations")).json() == []
        moved = (await other_client.get("/github/installations")).json()
        assert len(moved) == 1

    async def test_github_being_down_is_a_502_not_a_500(self, authed_client: AsyncClient) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        fastapi_app.dependency_overrides[_github_auth] = lambda: _github(down)

        assert (await _connect(authed_client)).status_code == 502


class TestListInstallations:
    async def test_empty_before_connecting(self, authed_client: AsyncClient) -> None:
        assert (await authed_client.get("/github/installations")).json() == []

    async def test_has_every_contract_field(self, authed_client: AsyncClient, github: None) -> None:
        await _connect(authed_client)

        body = (await authed_client.get("/github/installations")).json()

        assert set(body[0]) == {"id", "installation_id", "account_login", "created_at"}

    async def test_another_users_installations_are_invisible(
        self, authed_client: AsyncClient, other_client: AsyncClient, github: None
    ) -> None:
        await _connect(authed_client)

        assert (await other_client.get("/github/installations")).json() == []


class TestListRepositories:
    async def test_lists_what_the_installation_grants_sorted(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        await _connect(authed_client)

        body = (await authed_client.get("/github/repositories")).json()

        assert [repo["full_name"] for repo in body] == ["octocat/aardvark", "octocat/zebra"]
        assert body[0] == {
            "full_name": "octocat/aardvark",
            "private": False,
            "url": "https://github.com/octocat/aardvark.git",
            "installation_id": INSTALLATION_ID,
        }

    async def test_no_installations_means_an_empty_list(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        assert (await authed_client.get("/github/repositories")).json() == []

    async def test_an_uninstalled_installation_is_skipped_not_fatal(
        self, authed_client: AsyncClient, github: None
    ) -> None:
        """No webhooks: uninstallation surfaces as GitHub refusing the id.
        The picker should show what still works, not error entirely."""
        await _connect(authed_client)
        fastapi_app.dependency_overrides[_github_auth] = lambda: _github(
            lambda r: httpx.Response(404, json={})
        )

        response = await authed_client.get("/github/repositories")

        assert response.status_code == 200
        assert response.json() == []
