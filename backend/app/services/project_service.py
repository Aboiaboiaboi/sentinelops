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

from app.models import Project, User
from app.schemas.project import ProjectCreate


async def create_project(db: AsyncSession, *, owner: User, data: ProjectCreate) -> Project:
    project = Project(
        user_id=owner.id,
        name=data.name,
        repository_url=data.repository_url,
    )
    db.add(project)
    await db.commit()
    return project


async def list_projects(db: AsyncSession, *, owner: User) -> Sequence[Project]:
    """Newest first — a dashboard is read top-down and the newest project is the
    one most likely to be wanted."""
    result = await db.scalars(
        select(Project).where(Project.user_id == owner.id).order_by(Project.created_at.desc())
    )
    return result.all()


async def get_project(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> Project | None:
    """Fetch one project belonging to this user.

    Ownership is part of the WHERE clause, not a check afterwards. Someone
    else's project and a nonexistent one are therefore the same result, which is
    what stops the endpoint confirming that an id exists.
    """
    return await db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == owner.id)
    )


async def set_framework(db: AsyncSession, *, project_id: uuid.UUID, framework: str | None) -> None:
    """Record the stack a scan detected.

    No owner argument, and no ownership check: this is called by a worker
    running a scan whose ownership was already proved when it was created. It is
    not reachable from a route.
    """
    await db.execute(update(Project).where(Project.id == project_id).values(framework=framework))
    await db.commit()


async def delete_project(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> bool:
    """Delete a project, returning whether it was there to delete.

    Scans and findings go with it via ON DELETE CASCADE — no cleanup here.
    """
    result = await db.execute(
        delete(Project).where(Project.id == project_id, Project.user_id == owner.id)
    )
    await db.commit()
    return result.rowcount > 0
