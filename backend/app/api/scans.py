import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models import Scan
from app.schemas.scan import ScanRead
from app.services import scan_service

# No prefix: these routes live under both /projects and /scans, so each declares
# its full path rather than pretending to share one root.
router = APIRouter(tags=["scans"])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found.")


@router.post(
    "/projects/{project_id}/scans",
    response_model=ScanRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_scan(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Scan:
    """Start a scan. Takes no body — the repository is already on the project."""
    scan = await scan_service.create_scan(db, owner=user, project_id=project_id)
    if scan is None:
        raise _not_found("Project")
    return scan


@router.get("/projects/{project_id}/scans", response_model=list[ScanRead])
async def list_scans(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> list[Scan]:
    scans = await scan_service.list_scans(db, owner=user, project_id=project_id)
    if scans is None:
        raise _not_found("Project")
    return list(scans)


@router.get("/scans/{scan_id}", response_model=ScanRead)
async def get_scan(scan_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Scan:
    """Polled every few seconds while a scan is in flight, so it stays a single
    indexed lookup and loads no findings."""
    scan = await scan_service.get_scan(db, owner=user, scan_id=scan_id)
    if scan is None:
        raise _not_found("Scan")
    return scan
