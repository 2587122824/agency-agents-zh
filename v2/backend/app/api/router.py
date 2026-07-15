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
from ..configuration.contracts import (
    CloneConfiguration,
    ComponentSummary,
    ConfigurationDiffRead,
    ConfigurationVersionRead,
    ConfigurationVersionSummary,
    CreateConfiguration,
    PublishConfiguration,
    RetireConfiguration,
    ReviseConfiguration,
    ValidateConfiguration,
)
from ..configuration.service import (
    ConfigurationConflictError,
    ConfigurationNotFoundError,
    clone_configuration,
    component_versions,
    configuration_diff,
    configuration_read,
    create_configuration,
    list_configurations,
    publish_configuration,
    require_configuration,
    retire_configuration,
    revise_configuration,
    validate_configuration,
    workflow_slot_versions,
)
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
from ..planning.contracts import (
    CreativeBriefCandidateRead,
    DecideBrief,
    DecideShotPlan,
    GenerateBrief,
    GenerateShotPlan,
    PlanningCenterView,
    PlanVersionRead,
    ShotPlanCandidateRead,
)
from ..planning.service import (
    decide_brief,
    decide_shot_plan,
    generate_brief,
    generate_shot_plan,
    planning_center_view,
)
from ..production.contracts import (
    ActivateProductionSnapshot,
    AnalyzeProductionImpact,
    CreateProductionSnapshot,
    ImpactAnalysisRead,
    LockProductionSnapshot,
    ProductionExecutionView,
    ProductionPreparationView,
    ProductionSnapshotRead,
    SubmitProduction,
)
from ..production.service import (
    ProductionConflictError,
    ProductionNotFoundError,
    activate_snapshot,
    analyze_impact,
    create_snapshot,
    execution_view,
    lock_snapshot,
    preparation_view,
    submit_production,
)
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


def configuration_error(exc: ConfigurationConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def production_error(exc: ProductionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def require_project(session: Session, project_id: str):
    project = get_project(session, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/system-config/versions", response_model=list[ConfigurationVersionSummary])
def system_config_versions(session: Session = Depends(get_session)):
    return list_configurations(session)


@router.post(
    "/system-config/versions",
    response_model=ConfigurationVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def system_config_create(payload: CreateConfiguration, session: Session = Depends(get_session)):
    try:
        return create_configuration(session, payload)
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.get("/system-config/versions/{config_id}", response_model=ConfigurationVersionRead)
def system_config_get(config_id: str, session: Session = Depends(get_session)):
    try:
        return configuration_read(session, require_configuration(session, config_id))
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/system-config/versions/{config_id}:revise", response_model=ConfigurationVersionRead)
def system_config_revise(config_id: str, payload: ReviseConfiguration, session: Session = Depends(get_session)):
    try:
        return revise_configuration(session, require_configuration(session, config_id), payload)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.post("/system-config/versions/{config_id}:validate", response_model=ConfigurationVersionRead)
def system_config_validate(config_id: str, payload: ValidateConfiguration, session: Session = Depends(get_session)):
    try:
        return validate_configuration(session, require_configuration(session, config_id), payload)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.post("/system-config/versions/{config_id}:publish", response_model=ConfigurationVersionRead)
def system_config_publish(config_id: str, payload: PublishConfiguration, session: Session = Depends(get_session)):
    try:
        return publish_configuration(session, require_configuration(session, config_id), payload)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.post("/system-config/versions/{config_id}:retire", response_model=ConfigurationVersionRead)
def system_config_retire(config_id: str, payload: RetireConfiguration, session: Session = Depends(get_session)):
    try:
        return retire_configuration(session, require_configuration(session, config_id), payload)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.post(
    "/system-config/versions/{config_id}:clone-draft",
    response_model=ConfigurationVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def system_config_clone(config_id: str, payload: CloneConfiguration, session: Session = Depends(get_session)):
    try:
        return clone_configuration(session, require_configuration(session, config_id), payload)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigurationConflictError as exc:
        session.rollback()
        raise configuration_error(exc) from exc


@router.get("/system-config/versions/{config_id}/diff", response_model=ConfigurationDiffRead)
def system_config_diff(config_id: str, base_version_id: str, session: Session = Depends(get_session)):
    try:
        return configuration_diff(
            session,
            require_configuration(session, config_id),
            require_configuration(session, base_version_id),
        )
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/system-config/versions/{config_id}/references", response_model=list[dict])
def system_config_references(config_id: str, session: Session = Depends(get_session)):
    try:
        return configuration_read(session, require_configuration(session, config_id))["references"]
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/system-config/components/{component_type}", response_model=list[ComponentSummary])
def system_config_components(component_type: str, session: Session = Depends(get_session)):
    try:
        return component_versions(session, component_type)
    except ConfigurationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/system-config/workflow-slots/{slot_key}/versions", response_model=list[ComponentSummary])
def system_config_workflow_versions(slot_key: str, session: Session = Depends(get_session)):
    return workflow_slot_versions(session, slot_key)


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


@router.get("/projects/{project_id}/planning-center", response_model=PlanningCenterView)
def planning_center(project_id: str, session: Session = Depends(get_session)):
    project = require_project(session, project_id)
    try:
        return planning_center_view(session, project)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.get("/projects/{project_id}/production-preparation", response_model=ProductionPreparationView)
def production_preparation(project_id: str, session: Session = Depends(get_session)):
    return preparation_view(session, require_project(session, project_id))


@router.post(
    "/projects/{project_id}/production-impact-analyses",
    response_model=ImpactAnalysisRead,
    status_code=status.HTTP_201_CREATED,
)
def production_impact_analyze(
    project_id: str,
    payload: AnalyzeProductionImpact,
    session: Session = Depends(get_session),
):
    try:
        return analyze_impact(session, require_project(session, project_id), payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-snapshots",
    response_model=ProductionSnapshotRead,
    status_code=status.HTTP_201_CREATED,
)
def production_snapshot_create(
    project_id: str,
    payload: CreateProductionSnapshot,
    session: Session = Depends(get_session),
):
    try:
        return create_snapshot(session, require_project(session, project_id), payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-snapshots/{snapshot_id}:lock",
    response_model=ProductionSnapshotRead,
)
def production_snapshot_lock(
    project_id: str,
    snapshot_id: str,
    payload: LockProductionSnapshot,
    session: Session = Depends(get_session),
):
    try:
        return lock_snapshot(session, require_project(session, project_id), snapshot_id, payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-snapshots/{snapshot_id}:activate",
    response_model=ProductionSnapshotRead,
)
def production_snapshot_activate(
    project_id: str,
    snapshot_id: str,
    payload: ActivateProductionSnapshot,
    session: Session = Depends(get_session),
):
    try:
        return activate_snapshot(session, require_project(session, project_id), snapshot_id, payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-snapshots/{snapshot_id}:submit",
    response_model=ProductionExecutionView,
    status_code=status.HTTP_202_ACCEPTED,
)
def production_snapshot_submit(
    project_id: str,
    snapshot_id: str,
    payload: SubmitProduction,
    session: Session = Depends(get_session),
):
    try:
        return submit_production(session, require_project(session, project_id), snapshot_id, payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.get(
    "/projects/{project_id}/production-execution",
    response_model=ProductionExecutionView,
)
def production_execution(project_id: str, session: Session = Depends(get_session)):
    return execution_view(session, require_project(session, project_id))


@router.post(
    "/projects/{project_id}/creative-brief-candidates:generate",
    response_model=CreativeBriefCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_brief_generate(project_id: str, payload: GenerateBrief, session: Session = Depends(get_session)):
    project = require_project(session, project_id)
    try:
        return generate_brief(session, project, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/creative-brief-candidates/{candidate_id}:accept",
    response_model=CreativeBriefCandidateRead,
)
def creative_brief_accept(
    project_id: str,
    candidate_id: str,
    payload: DecideBrief,
    session: Session = Depends(get_session),
):
    try:
        return decide_brief(session, require_project(session, project_id), candidate_id, payload, True)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/creative-brief-candidates/{candidate_id}:reject",
    response_model=CreativeBriefCandidateRead,
)
def creative_brief_reject(
    project_id: str,
    candidate_id: str,
    payload: DecideBrief,
    session: Session = Depends(get_session),
):
    try:
        return decide_brief(session, require_project(session, project_id), candidate_id, payload, False)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates:generate",
    response_model=ShotPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def shot_plan_generate(project_id: str, payload: GenerateShotPlan, session: Session = Depends(get_session)):
    try:
        return generate_shot_plan(session, require_project(session, project_id), payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates/{candidate_id}:accept",
    response_model=PlanVersionRead,
)
def shot_plan_accept(
    project_id: str,
    candidate_id: str,
    payload: DecideShotPlan,
    session: Session = Depends(get_session),
):
    try:
        return decide_shot_plan(session, require_project(session, project_id), candidate_id, payload, True)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates/{candidate_id}:reject",
    response_model=ShotPlanCandidateRead,
)
def shot_plan_reject(
    project_id: str,
    candidate_id: str,
    payload: DecideShotPlan,
    session: Session = Depends(get_session),
):
    try:
        return decide_shot_plan(session, require_project(session, project_id), candidate_id, payload, False)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


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
