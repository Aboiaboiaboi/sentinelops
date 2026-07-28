"""Scan and finding business logic.

Ownership is enforced by joining through Projects on every query — a scan has no
user of its own, so reaching one always means proving the project it belongs to
is yours.

Functions return None to mean "not yours or not there"; the route turns that
into a 404. Nothing here raises HTTP errors, so the worker can call the same
functions with no request in scope.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SCAN_CATEGORIES, CategoryStatus, Finding, Project, Scan, User


def initial_category_status() -> dict[str, str]:
    """Every category pending, before any of them has run.

    An empty map would be defensible, but the client renders one bar per entry —
    so an empty one shows a scan with no visible categories at all rather than
    six waiting to start.
    """
    return dict.fromkeys(SCAN_CATEGORIES, CategoryStatus.PENDING.value)


async def _owned_project(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> Project | None:
    return await db.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == owner.id)
    )


async def create_scan(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> Scan | None:
    """Queue a scan and return immediately.

    The row is created `pending` and nothing runs inline — the endpoint must
    answer in milliseconds regardless of how long a scan takes. Handing the job
    to a worker arrives with the scanning engine; until then the scan stays
    pending, which the client already renders and polls correctly.
    """
    project = await _owned_project(db, owner=owner, project_id=project_id)
    if project is None:
        return None

    scan = Scan(project_id=project.id, category_status=initial_category_status())
    db.add(scan)
    await db.commit()
    return scan


async def list_scans(
    db: AsyncSession, *, owner: User, project_id: uuid.UUID
) -> Sequence[Scan] | None:
    """Scan history, newest first. None means the project is not the caller's.

    Distinct from an empty list on purpose: a project with no scans is a 200 and
    an empty array, while someone else's project is a 404.
    """
    project = await _owned_project(db, owner=owner, project_id=project_id)
    if project is None:
        return None

    result = await db.scalars(
        select(Scan).where(Scan.project_id == project.id).order_by(Scan.created_at.desc())
    )
    return result.all()


async def get_scan(db: AsyncSession, *, owner: User, scan_id: uuid.UUID) -> Scan | None:
    return await db.scalar(
        select(Scan)
        .join(Project, Scan.project_id == Project.id)
        .where(Scan.id == scan_id, Project.user_id == owner.id)
    )


async def list_findings(
    db: AsyncSession, *, owner: User, scan_id: uuid.UUID
) -> Sequence[Finding] | None:
    scan = await get_scan(db, owner=owner, scan_id=scan_id)
    if scan is None:
        return None

    result = await db.scalars(
        select(Finding)
        .where(Finding.scan_id == scan.id)
        # The Postgres enum was declared LOW..CRITICAL, so descending puts the
        # most severe first — which is the order a report is read in.
        .order_by(Finding.severity.desc(), Finding.score_impact.desc())
    )
    return result.all()
