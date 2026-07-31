"""Scan and finding business logic.

Ownership is enforced by joining through Projects on every query — a scan has no
user of its own, so reaching one always means proving the project it belongs to
is yours.

Functions return None to mean "not yours or not there"; the route turns that
into a 404. Nothing here raises HTTP errors, so the worker can call the same
functions with no request in scope.
"""

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Text, cast, func, select, update
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SCAN_CATEGORIES, CategoryStatus, Finding, Project, Scan, ScanStatus, User
from app.scanners.base import ScanFinding
from app.utils.queue import get_queue

logger = logging.getLogger(__name__)


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


SCAN_TASK = "run_scan"


def scan_job_id(scan_id: uuid.UUID) -> str:
    """Derive the queue's deduplication key from the scan itself.

    A retried publish, or a user double-clicking Run scan, then lands on an id
    that is already queued and arq declines the second one instead of running
    the same repository twice.
    """
    return f"scan:{scan_id}"


async def create_scan(db: AsyncSession, *, owner: User, project_id: uuid.UUID) -> Scan | None:
    """Create a pending scan and hand the work to a worker.

    Nothing runs inline — the endpoint answers in milliseconds regardless of how
    long a scan takes.
    """
    project = await _owned_project(db, owner=owner, project_id=project_id)
    if project is None:
        return None

    scan = Scan(project_id=project.id, category_status=initial_category_status())
    db.add(scan)
    await db.commit()

    # Publishing after the commit is deliberate. A worker can start the instant
    # the job lands, and if it were published first it could look the scan up
    # before this transaction was visible and find nothing.
    try:
        await get_queue().publish(SCAN_TASK, scan_id=str(scan.id), _job_id=scan_job_id(scan.id))
    except Exception:
        # The row is already committed, so an unqueued scan would otherwise sit
        # pending forever with the client politely polling it. Marking it failed
        # says so, and re-raising tells the caller the system is degraded rather
        # than handing back a scan that will never run.
        logger.exception("failed to queue scan", extra={"scan_id": str(scan.id)})
        await fail_scan(db, scan_id=scan.id)
        raise

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


# ---------------------------------------------------------------------------
# Progress. These four functions are the ONLY place a scan's status, score,
# scoring_version or category_status may be written.
#
# Concentrating the writes here is what keeps a later change — caching scan
# status in Redis so the polling endpoint stops hitting Postgres — a change in
# one file rather than surgery across the worker.
#
# None of them take an owner: by the time a worker is running a scan, ownership
# was already proved when the scan was created. They are not reachable from a
# route.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanTarget:
    """What a worker needs to run a scan, without loading ORM relationships.

    Relationships are configured `lazy="raise_on_sql"`, so reaching a scan's
    project through the attribute would raise rather than quietly emitting a
    query. Selecting the columns needed is both explicit and one round trip.

    `user_id` is here so the worker can look up the owner's GitHub App
    installations when the repository turns out to need credentials.
    """

    project_id: uuid.UUID
    repository_url: str
    user_id: uuid.UUID


async def get_scan_target(db: AsyncSession, *, scan_id: uuid.UUID) -> ScanTarget | None:
    """The repository a scan should clone, or None if the scan is gone.

    No owner argument: ownership was proved when the scan was created, and a
    worker has no request to attribute this to.
    """
    row = (
        await db.execute(
            select(Project.id, Project.repository_url, Project.user_id)
            .join(Scan, Scan.project_id == Project.id)
            .where(Scan.id == scan_id)
        )
    ).first()
    if row is None:
        return None
    return ScanTarget(project_id=row[0], repository_url=row[1], user_id=row[2])


async def record_findings(
    db: AsyncSession, *, scan_id: uuid.UUID, findings: Iterable[ScanFinding]
) -> int:
    """Persist a category's findings, returning how many were written.

    Takes the scanner's value objects and turns them into rows here, so that
    scanners never touch the ORM and stay testable against a directory.
    """
    rows = [
        Finding(
            scan_id=scan_id,
            category=finding.category,
            severity=finding.severity,
            title=finding.title,
            description=finding.description,
            recommendation=finding.recommendation,
            score_impact=finding.score_impact,
        )
        for finding in findings
    ]
    if not rows:
        return 0

    db.add_all(rows)
    await db.commit()
    return len(rows)


async def claim_scan(db: AsyncSession, *, scan_id: uuid.UUID) -> bool:
    """Move a scan from pending to running, returning whether this caller won.

    The status check is part of the UPDATE rather than a read followed by a
    write. Postgres evaluates it while holding the row lock, so of two workers
    racing on the same job exactly one matches a `pending` row and the other
    matches nothing. Reading first and writing second would let both see
    `pending` and both proceed to scan the same repository.
    """
    result = await db.execute(
        update(Scan)
        .where(Scan.id == scan_id, Scan.status == ScanStatus.PENDING)
        .values(status=ScanStatus.RUNNING)
    )
    await db.commit()
    return result.rowcount == 1


async def record_category_result(
    db: AsyncSession, *, scan_id: uuid.UUID, category: str, status: CategoryStatus
) -> None:
    """Set one category's outcome without disturbing the others.

    Uses Postgres `jsonb_set` rather than loading the map, changing a key and
    writing it back. The six categories run in parallel, so two finishing at
    once would both read the same snapshot and the second write would silently
    discard the first one's result. `jsonb_set` applies to whatever is in the
    column at the moment the statement runs, so concurrent updates to different
    keys both survive.
    """
    if category not in SCAN_CATEGORIES:
        raise ValueError(f"Unknown scanner category: {category!r}")

    await db.execute(
        update(Scan)
        .where(Scan.id == scan_id)
        .values(
            category_status=func.jsonb_set(
                Scan.category_status,
                array([category], type_=Text),
                func.to_jsonb(cast(status.value, Text)),
            )
        )
    )
    await db.commit()


async def complete_scan(
    db: AsyncSession,
    *,
    scan_id: uuid.UUID,
    score: int,
    scoring_version: str,
    category_scores: Mapping[str, int] | None = None,
) -> bool:
    """Finish a scan, returning whether it was still running.

    Guarded on `running` so a duplicate delivery cannot overwrite a scan that
    already finished, and cannot resurrect one that failed.

    `category_scores` holds what each completed category earned, so the client
    can show a category's real score instead of assuming it scored full marks.
    """
    result = await db.execute(
        update(Scan)
        .where(Scan.id == scan_id, Scan.status == ScanStatus.RUNNING)
        .values(
            status=ScanStatus.COMPLETED,
            score=score,
            scoring_version=scoring_version,
            category_scores=dict(category_scores or {}),
        )
    )
    await db.commit()
    return result.rowcount == 1


async def record_commit_context(
    db: AsyncSession,
    *,
    scan_id: uuid.UUID,
    sha: str,
    message: str,
    author: str,
    committed_at: datetime,
) -> None:
    """Record which commit this scan looked at.

    Written as soon as the checkout exists rather than at completion, so a scan
    that later fails still says what it was looking at — which is exactly when
    someone wants to know.

    Unguarded on status, unlike the other writes here: this is descriptive
    metadata about a checkout, not a lifecycle transition, so there is no state
    it could race into an inconsistent place.
    """
    await db.execute(
        update(Scan)
        .where(Scan.id == scan_id)
        .values(
            commit_sha=sha,
            commit_message=message,
            commit_author=author,
            committed_at=committed_at,
        )
    )
    await db.commit()


async def fail_scan(db: AsyncSession, *, scan_id: uuid.UUID) -> bool:
    """Mark a scan failed, returning whether it was still in flight.

    Accepts `pending` as well as `running`: a job can die between being queued
    and being claimed, and that scan should not poll forever.

    `score` is deliberately left null. A failed scan has no score, and writing a
    zero would be indistinguishable from a genuinely terrible repository.
    """
    result = await db.execute(
        update(Scan)
        .where(Scan.id == scan_id, Scan.status.in_((ScanStatus.PENDING, ScanStatus.RUNNING)))
        .values(status=ScanStatus.FAILED)
    )
    await db.commit()
    return result.rowcount == 1


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
