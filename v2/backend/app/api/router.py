from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..contracts.project import (
    DecisionCreate,
    DecisionRead,
    DecisionResolve,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    QueueRequest,
    WorkItemRead,
)
from ..core.config import settings
from ..db.session import get_session
from ..decisions.service import DecisionConflictError, add_decision, resolve_decision
from ..events.service import project_event_stream
from ..projects.service import (
    ProjectConflictError,
    confirm_project,
    create_project,
    get_project,
    list_projects,
    queue_contract_validation,
)


router = APIRouter()


def require_project(session: Session, project_id: str):
    project = get_project(session, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/projects", response_model=list[ProjectRead])
def projects(session: Session = Depends(get_session)):
    return list_projects(session)


@router.post("/projects", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def projects_create(payload: ProjectCreate, session: Session = Depends(get_session)):
    return create_project(session, payload)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def projects_get(project_id: str, session: Session = Depends(get_session)):
    return require_project(session, project_id)


@router.post("/projects/{project_id}/decisions", response_model=DecisionRead, status_code=status.HTTP_201_CREATED)
def decisions_create(project_id: str, payload: DecisionCreate, session: Session = Depends(get_session)):
    project = require_project(session, project_id)
    try:
        return add_decision(session, project, payload)
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/decisions/{decision_id}/resolve", response_model=DecisionRead)
def decisions_resolve(
    project_id: str,
    decision_id: str,
    payload: DecisionResolve,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return resolve_decision(session, project, decision_id, payload.value)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Decision not found") from exc
    except DecisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/confirm", response_model=ProjectDetail)
def projects_confirm(project_id: str, session: Session = Depends(get_session)):
    project = require_project(session, project_id)
    try:
        return confirm_project(session, project)
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/queue", response_model=WorkItemRead, status_code=status.HTTP_202_ACCEPTED)
def projects_queue(
    project_id: str,
    payload: QueueRequest,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    if payload.kind != "contract_validation":
        raise HTTPException(status_code=422, detail="Unsupported work item kind")
    try:
        return queue_contract_validation(session, project)
    except ProjectConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/events")
def project_events(
    project_id: str,
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    session: Session = Depends(get_session),
):
    require_project(session, project_id)
    return StreamingResponse(
        project_event_stream(project_id, after=last_event_id or 0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
