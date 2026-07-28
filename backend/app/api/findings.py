import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models import Finding
from app.schemas.finding import FindingRead
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
