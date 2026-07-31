"""The GitHub App connect flow.

Two browser navigations and two JSON endpoints. /install and /setup are
top-level navigations, not fetches — the SameSite=Lax auth cookie *is* sent on
those, which is what lets /setup know who is connecting without any state
parameter of our own.

There are no webhooks. A stale installation surfaces when token minting fails,
as a clear error, rather than being kept fresh by machinery this project does
not need yet.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentUser, DbSession
from app.config import get_settings
from app.models import GitHubInstallation
from app.schemas.github import GitHubInstallationRead, GitHubRepositoryRead
from app.services import github_service
from app.services.github_app_service import (
    GitHubApiError,
    GitHubAppAuth,
    GitHubAppNotConfigured,
    GitHubAppNotInstalled,
    InstallationTokenError,
    get_github_app_auth,
)

router = APIRouter(prefix="/github", tags=["github"])


def _github_auth() -> GitHubAppAuth:
    """The shared auth instance, as a dependency so tests can override it.

    Unconfigured is a 503 with the variables named: the deployment is missing
    something, and the message should say what rather than making someone read
    a traceback.
    """
    try:
        return get_github_app_auth()
    except GitHubAppNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


GitHubAuth = Annotated[GitHubAppAuth, Depends(_github_auth)]


def _bad_gateway(exc: Exception) -> HTTPException:
    """GitHub failing is not our caller's fault and not ours: 502."""
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get("/install")
async def start_installation(user: CurrentUser) -> RedirectResponse:
    """Send the browser to GitHub's install page for our App.

    Requires a signed-in user even though the redirect itself needs nothing,
    so an expired session fails here — before GitHub — rather than after the
    user has picked repositories on a consent screen whose result we then
    cannot attribute.
    """
    settings = get_settings()
    if not settings.github_app_slug:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Set GITHUB_APP_SLUG to enable private repositories.",
        )
    return RedirectResponse(
        f"https://github.com/apps/{settings.github_app_slug}/installations/new",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.get("/setup")
async def complete_installation(
    user: CurrentUser,
    db: DbSession,
    auth: GitHubAuth,
    installation_id: Annotated[int, Query()],
    setup_action: Annotated[str, Query()] = "install",
) -> RedirectResponse:
    """GitHub's post-install redirect: record the installation, bounce home.

    The installation id in the query string is claimed, not trusted — the only
    thing that makes it real is GitHub answering for it when we look up its
    account. A forged id fails that lookup and records nothing.
    """
    try:
        account_login = await auth.installation_account(installation_id)
    except GitHubAppNotInstalled as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (GitHubApiError, InstallationTokenError) as exc:
        raise _bad_gateway(exc) from exc

    await github_service.record_installation(
        db, owner=user, installation_id=installation_id, account_login=account_login
    )
    return RedirectResponse(
        f"{get_settings().frontend_url}/dashboard?github=connected",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/installations", response_model=list[GitHubInstallationRead])
async def list_installations(user: CurrentUser, db: DbSession) -> list[GitHubInstallation]:
    return list(await github_service.list_installations(db, owner=user))


@router.get("/repositories", response_model=list[GitHubRepositoryRead])
async def list_repositories(
    user: CurrentUser, db: DbSession, auth: GitHubAuth
) -> list[GitHubRepositoryRead]:
    """Everything the user's installations can see, for the picker.

    An installation GitHub no longer recognises is skipped rather than fatal:
    the user uninstalled the App there (no webhooks to tell us), and the
    repositories they still grant elsewhere should not vanish because of it.
    """
    repositories: list[GitHubRepositoryRead] = []
    for installation in await github_service.list_installations(db, owner=user):
        try:
            batch = await auth.list_repositories(installation.installation_id)
        except GitHubAppNotInstalled:
            continue
        except (GitHubApiError, InstallationTokenError) as exc:
            raise _bad_gateway(exc) from exc
        repositories.extend(
            GitHubRepositoryRead(
                full_name=repo["full_name"],
                private=repo["private"],
                url=repo["clone_url"],
                installation_id=installation.installation_id,
            )
            for repo in batch
        )

    repositories.sort(key=lambda repo: repo.full_name.lower())
    return repositories
