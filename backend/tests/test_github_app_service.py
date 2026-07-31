"""Tests for GitHub App authentication.

Everything runs against a locally generated RSA key and a mock transport —
no App registration, no network. What is being pinned down: the JWT claims
GitHub actually validates (issuer, the backdated iat, the 10-minute cap),
that tokens are cached in memory and re-minted at the margin, and that every
failure mode maps to a distinct exception the scan pipeline can explain.
"""

import asyncio
import base64
import time

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.services import github_app_service
from app.services.github_app_service import (
    JWT_BACKDATE_SECONDS,
    JWT_LIFETIME_SECONDS,
    GitHubAppAuth,
    GitHubAppMisconfigured,
    GitHubAppNotConfigured,
    GitHubAppNotInstalled,
    InstallationTokenError,
    decode_private_key,
    get_github_app_auth,
    reset_github_app_auth,
)

# One keypair for the whole module — generation is the slow part.
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)

CLIENT_ID = "Iv23liTESTCLIENTID"


def _auth(transport: httpx.AsyncBaseTransport | None = None) -> GitHubAppAuth:
    return GitHubAppAuth(client_id=CLIENT_ID, private_key_pem=PRIVATE_PEM, transport=transport)


def _token_response(token: str = "ghs_short_lived") -> httpx.Response:
    return httpx.Response(201, json={"token": token, "expires_at": "2026-01-01T00:00:00Z"})


class TestAppJwt:
    def test_is_rs256_signed_by_the_app_key(self) -> None:
        token = _auth().build_app_jwt()

        header = pyjwt.get_unverified_header(token)
        claims = pyjwt.decode(token, PUBLIC_PEM, algorithms=["RS256"])

        assert header["alg"] == "RS256"
        assert claims["iss"] == CLIENT_ID

    def test_iat_is_backdated_for_clock_drift(self) -> None:
        """A machine whose clock runs slightly ahead of GitHub's otherwise
        presents a token "issued in the future" and gets intermittent 401s."""
        now = time.time()

        claims = pyjwt.decode(_auth().build_app_jwt(now=now), PUBLIC_PEM, algorithms=["RS256"])

        assert claims["iat"] == int(now) - JWT_BACKDATE_SECONDS

    def test_expiry_stays_inside_githubs_ten_minute_cap(self) -> None:
        now = time.time()

        claims = pyjwt.decode(_auth().build_app_jwt(now=now), PUBLIC_PEM, algorithms=["RS256"])

        assert claims["exp"] - int(now) == JWT_LIFETIME_SECONDS
        assert claims["exp"] - int(now) <= 10 * 60


class TestInstallationToken:
    async def test_mints_a_token_with_the_app_jwt(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["auth"] = request.headers["Authorization"]
            return _token_response()

        auth = _auth(httpx.MockTransport(handler))

        token = await auth.installation_token(12345)

        assert token == "ghs_short_lived"
        assert seen["path"] == "/app/installations/12345/access_tokens"
        # The bearer credential is the App JWT itself — provable, not just
        # any string that starts with "Bearer".
        bearer = seen["auth"].removeprefix("Bearer ")
        assert pyjwt.decode(bearer, PUBLIC_PEM, algorithms=["RS256"])["iss"] == CLIENT_ID

    async def test_caches_the_token_in_memory(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _token_response()

        auth = _auth(httpx.MockTransport(handler))

        first = await auth.installation_token(1)
        second = await auth.installation_token(1)

        assert first == second
        assert calls == 1

    async def test_remints_once_the_safety_margin_is_reached(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _token_response(f"ghs_number_{calls}")

        auth = _auth(httpx.MockTransport(handler))
        await auth.installation_token(1)
        # Reach into the cache and expire the token, rather than sleeping.
        auth._cache[1].usable_until = time.time() - 1

        renewed = await auth.installation_token(1)

        assert renewed == "ghs_number_2"
        assert calls == 2

    async def test_installations_are_cached_independently(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            installation = request.url.path.split("/")[3]
            return _token_response(f"ghs_for_{installation}")

        auth = _auth(httpx.MockTransport(handler))

        assert await auth.installation_token(1) == "ghs_for_1"
        assert await auth.installation_token(2) == "ghs_for_2"

    async def test_concurrent_requests_mint_once(self) -> None:
        """N scans of the same user's repos arriving together must not spend
        N round trips and N slots of rate-limit budget on identical tokens."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return _token_response()

        auth = _auth(httpx.MockTransport(handler))

        tokens = await asyncio.gather(*(auth.installation_token(1) for _ in range(5)))

        assert set(tokens) == {"ghs_short_lived"}
        assert calls == 1

    async def test_a_404_means_the_app_was_uninstalled(self) -> None:
        """There are no webhooks, so this is how uninstallation surfaces —
        it must be distinguishable to become a clear scan error."""
        auth = _auth(httpx.MockTransport(lambda r: httpx.Response(404, json={})))

        with pytest.raises(GitHubAppNotInstalled):
            await auth.installation_token(999)

    async def test_other_rejections_raise_with_the_status(self) -> None:
        auth = _auth(
            httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "bad creds"}))
        )

        with pytest.raises(InstallationTokenError, match="401"):
            await auth.installation_token(1)

    async def test_an_unreachable_github_is_an_error_not_a_hang(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        auth = _auth(httpx.MockTransport(handler))

        with pytest.raises(InstallationTokenError, match="unreachable"):
            await auth.installation_token(1)

    async def test_a_failed_mint_is_not_cached(self) -> None:
        responses = [httpx.Response(500, json={}), _token_response()]

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        auth = _auth(httpx.MockTransport(handler))

        with pytest.raises(InstallationTokenError):
            await auth.installation_token(1)

        assert await auth.installation_token(1) == "ghs_short_lived"


class TestPrivateKeyDecoding:
    def test_round_trips_a_real_key(self) -> None:
        encoded = base64.b64encode(PRIVATE_PEM.encode()).decode()

        assert decode_private_key(encoded) == PRIVATE_PEM

    def test_rejects_text_that_is_not_base64(self) -> None:
        """The likeliest mistake: pasting the .pem contents, or its filename,
        instead of the encoded form."""
        with pytest.raises(GitHubAppMisconfigured, match="not valid base64"):
            decode_private_key("-----BEGIN RSA PRIVATE KEY-----")

    def test_rejects_base64_of_something_other_than_a_key(self) -> None:
        with pytest.raises(GitHubAppMisconfigured, match="not to a PEM"):
            decode_private_key(base64.b64encode(b"just some text").decode())


class TestSharedInstance:
    @pytest.fixture(autouse=True)
    def _fresh(self):
        reset_github_app_auth()
        yield
        reset_github_app_auth()

    def test_unconfigured_raises_with_the_variables_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            github_app_service, "get_settings", lambda: Settings(github_app_client_id="")
        )

        with pytest.raises(GitHubAppNotConfigured, match="GITHUB_APP_CLIENT_ID"):
            get_github_app_auth()

    def test_configured_builds_one_shared_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The instance is the token cache — a fresh one per call would mint
        every time and cache nothing."""
        settings = Settings(
            github_app_client_id=CLIENT_ID,
            github_app_private_key_b64=base64.b64encode(PRIVATE_PEM.encode()).decode(),
        )
        monkeypatch.setattr(github_app_service, "get_settings", lambda: settings)

        assert get_github_app_auth() is get_github_app_auth()
