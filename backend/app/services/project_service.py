"""Project business logic.

Sits between api/ and models/. Everything here takes an owner and scopes its
query to them — ownership is enforced in one place rather than remembered at
each call site, so a missed check cannot expose another user's data.

Raises nothing HTTP-shaped: functions return None or False and the route decides
what status that is. That keeps this layer callable from the worker, which has
no request to raise into.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, Scan, ScanStatus, User
from app.schemas.project import ProjectCreate, ProjectUpdate


async def create_project(db: AsyncSession, *, owner: User, data: ProjectCreate) -> Project:
    project = Project(
        user_id=owner.id,
        name=data.name,
        repository_url=data.repository_url,
    )
    db.add(project)
    await db.commit()
    # A project with no scans can always be repointed; nothing depends on the
    # URL yet.
    project.repository_url_editable = True
    return project


async def list_projects(db: AsyncSession, *, owner: User) -> Sequence[Project]:
    """Newest first — a dashboard is read top-down and the newest project is the
    one most likely to be wanted."""
    rows = await db.execute(
        select(Project, _URL_LOCKED)
        .where(Project.user_id == owner.id)
        .order_by(Project.created_at.desc())
    )
    projects = []
    for project, locked in rows:
        # Set on the instance rather than fetched per project by the client:
        # the dashboard would otherwise need one request per row to know
        # whether its edit field should be enabled.
        project.repository_url_editable = not locked
        projects.append(project)
    return projects


async def get_project(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> Project | None:
    """Fetch one project belonging to this user.

    Ownership is part of the WHERE clause, not a check afterwards. Someone
    else's project and a nonexistent one are therefore the same result, which is
    what stops the endpoint confirming that an id exists.
    """
    row = (
        await db.execute(
            select(Project, _URL_LOCKED).where(
                Project.id == project_id, Project.user_id == owner.id
            )
        )
    ).first()
    if row is None:
        return None
    project, locked = row
    project.repository_url_editable = not locked
    return project


async def set_framework(db: AsyncSession, *, project_id: uuid.UUID, framework: str | None) -> None:
    """Record the stack a scan detected.

    No owner argument, and no ownership check: this is called by a worker
    running a scan whose ownership was already proved when it was created. It is
    not reachable from a route.
    """
    await db.execute(update(Project).where(Project.id == project_id).values(framework=framework))
    await db.commit()


# Scan states that freeze a project's repository URL, for two different
# reasons.
#
# `completed` is the substantive one: a scan that produced a score is history,
# and repointing the project would silently rewrite what that history was about
# — the scan list would show scores for a repository the project no longer
# names. A failed scan is not history in that sense; it has no score and no
# findings, so nothing is falsified by moving on. That is what makes "fix my
# typo" work: a URL that never resolved can still be corrected.
#
# `pending` and `running` freeze it for a mechanical reason instead — the
# worker already holds the old target, so an edit mid-flight would attribute
# the results to the wrong repository.
URL_LOCKING_STATUSES = (ScanStatus.COMPLETED, ScanStatus.PENDING, ScanStatus.RUNNING)

_URL_LOCKED = (
    select(1).where(Scan.project_id == Project.id, Scan.status.in_(URL_LOCKING_STATUSES)).exists()
)


class RepositoryUrlLocked(Exception):
    """The URL cannot change because scans depend on it meaning what it means."""


async def _url_lock_reason(db: AsyncSession, *, project_id: uuid.UUID) -> str | None:
    """Why this project's URL is frozen, or None if it can still change."""
    statuses = set(
        await db.scalars(
            select(Scan.status)
            .where(Scan.project_id == project_id, Scan.status.in_(URL_LOCKING_STATUSES))
            .distinct()
        )
    )
    if not statuses:
        return None
    # Completed wins when both apply: it is the permanent reason, and telling
    # someone to "wait for the scan to finish" would promise an unlock that
    # will never come.
    if ScanStatus.COMPLETED in statuses:
        return (
            "This project has a completed scan, so its repository URL is fixed — changing it "
            "would leave the scan history describing a repository the project no longer points "
            "at. Create a new project for a different repository."
        )
    return "A scan is in progress. Wait for it to finish before changing the repository URL."


async def update_project(
    db: AsyncSession, *, owner: User, project_id: uuid.UUID, data: ProjectUpdate
) -> Project | None:
    """Apply a partial update, or None if the project is not the caller's.

    Only fields the client actually sent are touched, so omitting `name` means
    "leave it alone" rather than "clear it".

    Raises RepositoryUrlLocked when the URL is frozen — a distinct signal from
    "not found", because the caller asked for something reasonable that the
    project's state forbids.
    """
    # get_project rather than a bare select: it scopes ownership and sets the
    # editable flag the response needs.
    project = await get_project(db, owner=owner, project_id=project_id)
    if project is None:
        return None

    changes = data.model_dump(exclude_unset=True)

    if "repository_url" in changes and changes["repository_url"] != project.repository_url:
        reason = await _url_lock_reason(db, project_id=project_id)
        if reason is not None:
            raise RepositoryUrlLocked(reason)
        project.repository_url = changes["repository_url"]
        # The stack is a property of the repository, and this is now a
        # different one. Leaving the old value would label the project with a
        # framework nothing has detected.
        project.framework = None

    if "name" in changes:
        project.name = changes["name"]

    await db.commit()
    return project


async def delete_project(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> bool:
    """Delete a project, returning whether it was there to delete.

    Scans and findings go with it via ON DELETE CASCADE — no cleanup here.
    """
    result = await db.execute(
        delete(Project).where(Project.id == project_id, Project.user_id == owner.id)
    )
    await db.commit()
    return result.rowcount > 0
