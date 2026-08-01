import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.models import Project
from app.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate
from app.services import project_service

router = APIRouter(prefix="/projects", tags=["projects"])


def _not_found() -> HTTPException:
    """404 for a project that is missing *or* belongs to someone else.

    Deliberately not 403. A 403 would confirm the id exists, and a 401 would be
    worse still — the frontend treats any 401 as a dead session and signs the
    user out, so returning one here would log someone out for opening a stale
    bookmark.
    """
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
async def create_project(data: ProjectCreate, user: CurrentUser, db: DbSession) -> Project:
    return await project_service.create_project(db, owner=user, data=data)


@router.get("", response_model=list[ProjectRead])
async def list_projects(user: CurrentUser, db: DbSession) -> list[Project]:
    """Deliberately does not inline each project's latest scan or score — the
    client fetches those separately, so this stays one query regardless of how
    many scans a project has accumulated."""
    return list(await project_service.list_projects(db, owner=user))


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> Project:
    project = await project_service.get_project(db, owner=user, project_id=project_id)
    if project is None:
        raise _not_found()
    return project


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: uuid.UUID, data: ProjectUpdate, user: CurrentUser, db: DbSession
) -> Project:
    """Rename a project, or repoint it while that is still allowed.

    409 rather than 403 for a locked URL: the caller is permitted to make this
    request, and it conflicts with the project's state rather than their
    rights. The detail explains which state and why.
    """
    try:
        project = await project_service.update_project(
            db, owner=user, project_id=project_id, data=data
        )
    except project_service.RepositoryUrlLocked as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if project is None:
        raise _not_found()
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    deleted = await project_service.delete_project(db, owner=user, project_id=project_id)
    if not deleted:
        raise _not_found()
