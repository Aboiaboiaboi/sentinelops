"""GitHub App authentication: short-lived tokens for private repositories.

The flow has two steps, and the second is the one that matters. First the App
proves who *it* is with a JWT signed by its private key. Then it trades that
JWT for an **installation token** scoped to one user's installation — and that
token expires in an hour and is never written anywhere. This is the entire
reason a GitHub App was chosen over stored personal access tokens: a breach of
our database yields no credential to anyone's source code.

Tokens are cached in memory only, per installation, and re-minted inside a
safety margin. Losing the cache (a restart) costs one extra HTTP call, which
is the correct price for never persisting a credential.
"""

import asyncio
import base64
import binascii
import time
from dataclasses import dataclass

import httpx
import jwt

from app.config import get_settings

GITHUB_API = "https://api.github.com"

# GitHub rejects App JWTs valid for more than 10 minutes. Nine keeps a margin
# for the request itself; the 60-second backdate absorbs clock drift between
# us and GitHub, which otherwise surfaces as intermittent 401s that only
# happen on machines whose clock runs slightly ahead.
JWT_BACKDATE_SECONDS = 60
JWT_LIFETIME_SECONDS = 9 * 60

# An installation token lives an hour. Stop using a cached one this many
# seconds before it dies, so a token handed to a clone that then queues for a
# while cannot expire mid-fetch.
TOKEN_SAFETY_MARGIN_SECONDS = 5 * 60


class GitHubAppNotConfigured(Exception):
    """The App credentials are absent — private repositories are off."""


class GitHubAppMisconfigured(Exception):
    """Credentials are present but unusable, e.g. a key that is not base64."""


class GitHubAppNotInstalled(Exception):
    """GitHub reports no such installation.

    The user uninstalled the App (there are no webhooks to tell us), so this
    surfaces at scan time as a clear error rather than silent breakage.
    """


class InstallationTokenError(Exception):
    """GitHub refused to mint a token for a reason other than 404."""


@dataclass
class _CachedToken:
    token: str
    # Monotonic-ish deadline in time.time() terms, already including the
    # safety margin, so callers compare against now and nothing else.
    usable_until: float


class GitHubAppAuth:
    """Mints and caches installation tokens for one configured App.

    Instantiated directly in tests with a generated key and a mock transport;
    the application uses the module-level `get_github_app_auth()` built from
    settings.
    """

    def __init__(
        self,
        *,
        client_id: str,
        private_key_pem: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._private_key_pem = private_key_pem
        self._transport = transport
        self._cache: dict[int, _CachedToken] = {}
        # One mint at a time. Without this, N concurrent scans for the same
        # installation each notice the missing token and mint N times — not
        # incorrect, but N-1 wasted round trips and rate-limit budget.
        self._lock = asyncio.Lock()

    def build_app_jwt(self, *, now: float | None = None) -> str:
        """The JWT that proves we are the App. Step one of two.

        RS256 with the App's private key; `iss` is the client id. GitHub
        also accepts the numeric App ID as issuer, but the client id works
        for both old and new Apps, so there is one code path.
        """
        issued_at = int(now if now is not None else time.time())
        payload = {
            "iss": self._client_id,
            "iat": issued_at - JWT_BACKDATE_SECONDS,
            "exp": issued_at + JWT_LIFETIME_SECONDS,
        }
        return jwt.encode(payload, self._private_key_pem, algorithm="RS256")

    async def installation_token(self, installation_id: int) -> str:
        """A one-hour token scoped to one installation. Step two.

        Never persisted anywhere: memory only, re-minted inside the safety
        margin. The caller puts it in an Authorization header — see
        workers/repo.py for why it must never appear in a URL.
        """
        async with self._lock:
            cached = self._cache.get(installation_id)
            if cached is not None and time.time() < cached.usable_until:
                return cached.token

            minted = await self._mint(installation_id)
            self._cache[installation_id] = minted
            return minted.token

    async def _mint(self, installation_id: int) -> _CachedToken:
        async with httpx.AsyncClient(
            base_url=GITHUB_API,
            transport=self._transport,
            timeout=15.0,
            headers={
                "Authorization": f"Bearer {self.build_app_jwt()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        ) as client:
            try:
                response = await client.post(f"/app/installations/{installation_id}/access_tokens")
            except httpx.HTTPError as exc:
                raise InstallationTokenError(f"GitHub unreachable: {exc}") from exc

        if response.status_code == 404:
            raise GitHubAppNotInstalled(
                f"installation {installation_id} not found — the App was likely uninstalled"
            )
        if response.status_code != 201:
            # The JWT being rejected (401) usually means a wrong key or a
            # clock far enough off that the backdate could not absorb it.
            raise InstallationTokenError(
                f"GitHub returned {response.status_code} minting a token: {response.text[:200]}"
            )

        body = response.json()
        return _CachedToken(
            token=body["token"],
            usable_until=time.time() + 3600 - TOKEN_SAFETY_MARGIN_SECONDS,
        )


def decode_private_key(encoded: str) -> str:
    """The base64-encoded PEM from settings, decoded and sanity-checked.

    Misconfiguration fails here with a message naming the actual problem,
    rather than surfacing later as an unexplained RS256 error from PyJWT.
    """
    try:
        pem = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise GitHubAppMisconfigured(
            "GITHUB_APP_PRIVATE_KEY_B64 is not valid base64. Encode the "
            "downloaded .pem file itself, not its filename."
        ) from exc
    if "PRIVATE KEY" not in pem:
        raise GitHubAppMisconfigured(
            "GITHUB_APP_PRIVATE_KEY_B64 decoded, but not to a PEM private key."
        )
    return pem


_auth: GitHubAppAuth | None = None


def get_github_app_auth() -> GitHubAppAuth:
    """The application's shared instance, built from settings on first use.

    Cached because the instance *is* the token cache — a new one per request
    would mint a fresh token every time and cache nothing.
    """
    global _auth
    if _auth is None:
        settings = get_settings()
        if not settings.github_app_client_id or not settings.github_app_private_key_b64:
            raise GitHubAppNotConfigured(
                "Set GITHUB_APP_CLIENT_ID and GITHUB_APP_PRIVATE_KEY_B64 to "
                "enable private repositories."
            )
        _auth = GitHubAppAuth(
            client_id=settings.github_app_client_id,
            private_key_pem=decode_private_key(settings.github_app_private_key_b64),
        )
    return _auth


def reset_github_app_auth() -> None:
    """Testing seam, mirroring set_queue(): drop the shared instance."""
    global _auth
    _auth = None
