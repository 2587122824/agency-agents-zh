from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
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
from ..creation.contracts import (
    AcceptCandidate,
    AttachmentCreate,
    AttachmentRead,
    BindingCreate,
    BindingRead,
    CandidateRead,
    CreationCenterView,
    GenerateCandidate,
    MessageCreate,
    MessageRead,
    RejectCandidate,
    ResolveClarification,
    RequirementVersionRead,
)
from ..creation.service import (
    CreationConflictError,
    CreationNotFoundError,
    accept_candidate,
    add_message,
    bind_attachment,
    creation_center_view,
    generate_candidate,
    register_attachment,
    reject_candidate,
    resolve_clarification,
)
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


def creation_error(exc: CreationConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


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


@router.get("/projects/{project_id}/creation-center", response_model=CreationCenterView)
def creation_center(project_id: str, session: Session = Depends(get_session)):
    return creation_center_view(session, require_project(session, project_id))


@router.post("/projects/{project_id}/messages", response_model=MessageRead, status_code=status.HTTP_201_CREATED)
def creation_message_add(project_id: str, payload: MessageCreate, session: Session = Depends(get_session)):
    project = require_project(session, project_id)
    try:
        return add_message(session, project, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/requirement-candidates:generate",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def requirement_candidate_generate(
    project_id: str,
    payload: GenerateCandidate,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return generate_candidate(session, project, payload)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/requirement-candidates/{candidate_id}:accept",
    response_model=RequirementVersionRead,
)
def requirement_candidate_accept(
    project_id: str,
    candidate_id: str,
    payload: AcceptCandidate,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return accept_candidate(session, project, candidate_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/requirement-candidates/{candidate_id}:reject",
    response_model=CandidateRead,
)
def requirement_candidate_reject(
    project_id: str,
    candidate_id: str,
    payload: RejectCandidate,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return reject_candidate(session, project, candidate_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/clarifications/{clarification_id}:resolve",
    response_model=RequirementVersionRead,
)
def clarification_resolve(
    project_id: str,
    clarification_id: str,
    payload: ResolveClarification,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return resolve_clarification(session, project, clarification_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/attachments",
    response_model=AttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def attachment_register(
    project_id: str,
    command_id: str = Form(..., min_length=8, max_length=80),
    actor_id: str = Form("local-user", min_length=1, max_length=48),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    content = await file.read(104_857_601)
    if not content or len(content) > 104_857_600:
        raise HTTPException(status_code=422, detail="附件必须介于 1 字节和 100 MB 之间")
    mime_type = file.content_type or ""
    if mime_type not in {"image/png", "image/jpeg", "image/webp", "audio/wav", "audio/mpeg", "video/mp4"}:
        raise HTTPException(status_code=422, detail="不支持该附件类型")
    payload = AttachmentCreate(
        command_id=command_id,
        actor_id=actor_id,
        original_filename=file.filename or "attachment",
        mime_type=mime_type,
        byte_size=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )
    try:
        return register_attachment(session, project, payload, content)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/attachments/{attachment_id}/bindings",
    response_model=BindingRead,
    status_code=status.HTTP_201_CREATED,
)
def attachment_binding_create(
    project_id: str,
    attachment_id: str,
    payload: BindingCreate,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return bind_attachment(session, project, attachment_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


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
