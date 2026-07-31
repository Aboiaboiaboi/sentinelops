"""Tests for credential resolution before a clone.

The invariant every case defends: resolution failing must degrade to an
anonymous clone, never to a failed scan. A public repository has to keep
scanning when the App is unconfigured, the user has no installations, or
GitHub is down — the worst case for a *private* repository is then exactly
the failure it would have had anyway.
"""

import base64

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GitHubInstallation, User
from app.services.github_app_service import GitHubAppAuth, GitHubAppNotConfigured
from app.workers import scan_tasks
from app.workers.scan_tasks import _github_full_name, resolve_credential

PRIVATE_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)


class TestGitHubFullName:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://github.com/acme/widget", "acme/widget"),
            ("https://github.com/acme/widget.git", "acme/widget"),
            ("https://www.github.com/acme/widget", "acme/widget"),
        ],
    )
    def test_extracts_owner_and_repo(self, url: str, expected: str) -> None:
        assert _github_full_name(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/acme/widget",  # not github
            "https://github.com/acme",  # no repo segment
            "https://github.com/acme/widget/tree/main",  # deep link, not a clone URL
            "https://github.com.evil.example/acme/widget",  # hostname must match exactly
        ],
    )
    def test_everything_else_is_none(self, url: str) -> None:
        assert _github_full_name(url) is None


async def _user_with_installations(db: AsyncSession, *installation_ids: int) -> User:
    user = User(email="owner@example.com", password_hash="x")
    db.add(user)
    await db.flush()
    for installation_id in installation_ids:
        db.add(
            GitHubInstallation(
                user_id=user.id, installation_id=installation_id, account_login="octocat"
            )
        )
    await db.flush()
    return user


def _mock_auth(handler) -> GitHubAppAuth:
    return GitHubAppAuth(
        client_id="Iv23liTESTCLIENTID",
        private_key_pem=PRIVATE_PEM,
        transport=httpx.MockTransport(handler),
    )


def _install_auth(monkeypatch: pytest.MonkeyPatch, auth: GitHubAppAuth) -> None:
    monkeypatch.setattr(scan_tasks, "get_github_app_auth", lambda: auth)


class TestResolveCredential:
    async def test_no_installations_is_anonymous_without_touching_github(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await _user_with_installations(session)  # none

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("GitHub must not be called")

        _install_auth(monkeypatch, _mock_auth(handler))

        header = await resolve_credential(
            session, repository_url="https://github.com/acme/widget", user_id=user.id
        )

        assert header is None

    async def test_a_non_github_url_is_anonymous_without_touching_github(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await _user_with_installations(session, 111)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("GitHub must not be called")

        _install_auth(monkeypatch, _mock_auth(handler))

        header = await resolve_credential(
            session, repository_url="https://gitlab.com/acme/widget", user_id=user.id
        )

        assert header is None

    async def test_an_unconfigured_app_is_anonymous(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Installations may exist as rows while the deployment lost its key;
        public scanning must not break because of it."""
        user = await _user_with_installations(session, 111)

        def raise_unconfigured() -> GitHubAppAuth:
            raise GitHubAppNotConfigured("no credentials")

        monkeypatch.setattr(scan_tasks, "get_github_app_auth", raise_unconfigured)

        header = await resolve_credential(
            session, repository_url="https://github.com/acme/widget", user_id=user.id
        )

        assert header is None

    async def test_a_covered_repository_yields_the_documented_header(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await _user_with_installations(session, 111)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/app/installations/111/access_tokens":
                return httpx.Response(
                    201, json={"token": "ghs_test", "expires_at": "2026-01-01T00:00:00Z"}
                )
            if request.url.path == "/repos/acme/widget":
                return httpx.Response(200, json={"full_name": "acme/widget"})
            return httpx.Response(404, json={})

        _install_auth(monkeypatch, _mock_auth(handler))

        header = await resolve_credential(
            session, repository_url="https://github.com/acme/widget.git", user_id=user.id
        )

        assert header is not None
        scheme, encoded = header.removeprefix("Authorization: ").split(" ")
        assert scheme == "Basic"
        assert base64.b64decode(encoded).decode() == "x-access-token:ghs_test"

    async def test_the_first_installation_that_grants_access_wins(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Installation 111 does not cover the repo; 222 does."""
        user = await _user_with_installations(session, 222, 111)

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/access_tokens"):
                installation = path.split("/")[3]
                return httpx.Response(
                    201,
                    json={
                        "token": f"ghs_for_{installation}",
                        "expires_at": "2026-01-01T00:00:00Z",
                    },
                )
            if path == "/repos/acme/widget":
                granted = "ghs_for_222" in request.headers["Authorization"]
                return httpx.Response(200 if granted else 404, json={})
            return httpx.Response(404, json={})

        _install_auth(monkeypatch, _mock_auth(handler))

        header = await resolve_credential(
            session, repository_url="https://github.com/acme/widget", user_id=user.id
        )

        assert header is not None
        assert (
            "ghs_for_222" in base64.b64decode(header.removeprefix("Authorization: Basic ")).decode()
        )

    async def test_github_being_down_degrades_to_anonymous(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = await _user_with_installations(session, 111)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        _install_auth(monkeypatch, _mock_auth(handler))

        header = await resolve_credential(
            session, repository_url="https://github.com/acme/widget", user_id=user.id
        )

        assert header is None
