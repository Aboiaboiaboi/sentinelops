"""arq task definitions.

Deliberately thin. Every task is a wrapper that opens a database session and
hands it to a function in `services/`, which is the same layer the API calls —
so the work is reachable from a test without arq running at all.

That split is not stylistic. The test fixtures bind every session to one
connection that is rolled back afterwards; a task that opened its own session
would escape that isolation and write to the development database instead.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_scan(ctx: dict[str, Any], scan_id: str) -> None:
    """Run a scan to completion.

    Currently a placeholder that only records having been called. Claiming the
    scan, cloning, scanning and scoring arrive in the milestones after this one;
    this exists so the queue and worker can be proven end to end on their own.
    """
    logger.info(
        "scan job received",
        extra={"scan_id": scan_id, "job_id": ctx.get("job_id"), "job_try": ctx.get("job_try")},
    )
