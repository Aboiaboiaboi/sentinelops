"""GitHub installation records.

The database side of the connect flow: which installation belongs to which
user. Ownership is scoped in the WHERE clause exactly as project_service does
it, and nothing HTTP-shaped is raised from here.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GitHubInstallation, User


async def record_installation(
    db: AsyncSession, *, owner: User, installation_id: int, account_login: str
) -> GitHubInstallation:
    """Record an installation against this user, taking over an existing row.

    The setup flow can legitimately run twice for one installation — the user
    changes which repositories we can see, or reconnects from a different
    SentinelOps account. installation_id is unique, so the second run
    re-points the existing row rather than violating the constraint; GitHub
    has already proved whoever completed the flow controls the installation.
    """
    existing = await db.scalar(
        select(GitHubInstallation).where(GitHubInstallation.installation_id == installation_id)
    )
    if existing is not None:
        existing.user_id = owner.id
        existing.account_login = account_login
        await db.commit()
        return existing

    installation = GitHubInstallation(
        user_id=owner.id,
        installation_id=installation_id,
        account_login=account_login,
    )
    db.add(installation)
    await db.commit()
    return installation


async def installation_ids_for_user(db: AsyncSession, *, user_id: uuid.UUID) -> list[int]:
    """The installation ids a user has connected, for the worker.

    Takes a bare user id rather than a User: the worker resolves credentials
    from a ScanTarget and has no loaded ORM user to hand over.
    """
    result = await db.scalars(
        select(GitHubInstallation.installation_id)
        .where(GitHubInstallation.user_id == user_id)
        .order_by(GitHubInstallation.created_at.desc())
    )
    return list(result.all())


async def list_installations(db: AsyncSession, *, owner: User) -> Sequence[GitHubInstallation]:
    result = await db.scalars(
        select(GitHubInstallation)
        .where(GitHubInstallation.user_id == owner.id)
        .order_by(GitHubInstallation.created_at.desc())
    )
    return result.all()
