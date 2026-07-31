import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models import Finding
from app.schemas.finding import FindingRead
from app.schemas.scan import CheckResultRead
from app.services import scan_service

router = APIRouter(tags=["findings"])


@router.get("/scans/{scan_id}/findings", response_model=list[FindingRead])
async def list_findings(scan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[Finding]:
    """Findings for one scan, most severe first.

    Separate from GET /scans/{id} because that one is polled on a timer and
    should not drag a growing list of findings along with every poll.
    """
    findings = await scan_service.list_findings(db, owner=user, scan_id=scan_id)
    if findings is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")
    return list(findings)


@router.get("/scans/{scan_id}/checks", response_model=list[CheckResultRead])
async def list_checks(scan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[dict]:
    """Every check the scan performed, with its outcome.

    Separate from GET /scans/{id} for the same reason findings are: that
    endpoint is polled every three seconds and must stay one cheap row read,
    while this is fetched once when somebody expands the detail.

    What it makes possible is the thing a score alone cannot say — that a
    category earned full marks *because these checks passed*, and that a check
    which did not apply was skipped rather than quietly counted as fine.
    """
    scan = await scan_service.get_scan(db, owner=user, scan_id=scan_id)
    if scan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan not found.")
    return list(scan.check_results or [])
