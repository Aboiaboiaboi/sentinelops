"""arq task definitions.

Deliberately thin. Each task opens a database session and hands it to a function
that does the work, which is the same shape `services/` already uses — so the
logic is reachable from a test without arq running at all.

That split is not stylistic. The test fixtures bind every session to one
connection that is rolled back afterwards; a task that opened its own session
would escape that isolation and write to the development database instead.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import SessionLocal
from app.models import SCAN_CATEGORIES, CategoryStatus
from app.services import scan_service, scoring_service

logger = logging.getLogger(__name__)


async def run_scan(ctx: dict[str, Any], scan_id: str) -> None:
    """arq entrypoint. Opens a session and delegates.

    Nothing but session management belongs here — see `execute_scan`.
    """
    async with SessionLocal() as db:
        await execute_scan(db, scan_id=uuid.UUID(scan_id))


async def execute_scan(db: AsyncSession, *, scan_id: uuid.UUID) -> None:
    """Drive one scan from pending to a terminal state.

    Cloning and scanning arrive with the first scanner. For now the scan is
    claimed and closed out immediately: every category is recorded as failed
    because no scanner exists to report one, and the score follows from that
    rather than being hardcoded.

    Marking the categories rather than leaving them pending matters — the client
    renders `pending` as "Scanning…", so a finished scan full of pending
    categories would claim to still be running.
    """
    claimed = await scan_service.claim_scan(db, scan_id=scan_id)
    if not claimed:
        # Already running, already finished, or gone. arq redelivers on retry
        # and a duplicate must not restart work that is underway.
        logger.info("scan not claimable, skipping", extra={"scan_id": str(scan_id)})
        return

    logger.info("scan started", extra={"scan_id": str(scan_id)})

    try:
        category_status: dict[str, str] = {}
        for category in SCAN_CATEGORIES:
            await scan_service.record_category_result(
                db, scan_id=scan_id, category=category, status=CategoryStatus.FAILED
            )
            category_status[category] = CategoryStatus.FAILED.value

        # Derived rather than hardcoded, so the number is right for whatever did
        # or did not report — today nothing does, and zero is the honest answer.
        score = scoring_service.score_scan([], category_status)

        await scan_service.complete_scan(
            db,
            scan_id=scan_id,
            score=score,
            scoring_version=scoring_service.SCORING_VERSION,
        )
        logger.info("scan completed", extra={"scan_id": str(scan_id), "score": score})
    except Exception:
        # A crash mid-scan must not leave the row running forever, with the
        # client polling something that will never change.
        logger.exception("scan failed", extra={"scan_id": str(scan_id)})
        await scan_service.fail_scan(db, scan_id=scan_id)
        raise
