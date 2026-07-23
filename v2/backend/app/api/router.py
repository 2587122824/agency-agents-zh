from __future__ import annotations

import hashlib
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from ..contracts.project import (
    ArchiveProject,
    DecisionCreate,
    DecisionRead,
    DecisionResolve,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    QueueRequest,
    RestoreProject,
    WorkItemRead,
)
from ..control.contracts import ProjectAuditLedgerView, ProjectControlSummary, ProjectControlView
from ..control.service import project_audit_ledger, project_control_view, project_controls
from ..core.config import RUNTIME_ROOT, settings
from ..delivery.contracts import (
    AuthorizeDelivery,
    DeliveryAttemptRead,
    DeliveryWorkspaceView,
    RegisterDeliveryOutput,
    VerifyDelivery,
)
from ..delivery.service import (
    DeliveryConflictError,
    DeliveryNotFoundError,
    authorize_delivery,
    delivery_upload_limit,
    delivery_workspace,
    register_delivery_output,
    verify_delivery,
)
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
    ConversationSessionRead,
    GenerateCandidate,
    InitializeCreativeConversation,
    MessageCreate,
    MessageRead,
    RejectCandidate,
    ResolveClarification,
    RetryCreativeTurn,
    SelectCreativeSuggestion,
    StartConversationSession,
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
    initialize_creative_conversation,
    register_attachment,
    reject_candidate,
    resolve_clarification,
    retry_creative_turn,
    select_creative_suggestion,
    start_conversation_session,
)
from ..creation.agent_gateway import AgentGatewayError, CreativeAgentGateway, get_creative_agent_gateway
from ..db.session import get_session
from ..decisions.service import DecisionConflictError, add_decision, resolve_decision
from ..events.service import project_event_stream
from ..impact.contracts import (
    AnalyzeDecisionChangeImpact,
    DecisionChangeImpactAnalysisRead,
    DecisionChangeImpactWorkspace,
    DecisionImpactGraphView,
)
from ..impact.service import (
    ImpactConflictError,
    ImpactNotFoundError,
    analyze_decision_change,
    decision_change_impact_workspace,
    decision_impact_graph_view,
)
from ..editor.contracts import (
    ApproveQualityStage,
    ConfirmTimeline,
    CreateTimelineCandidate,
    EditorWorkspaceView,
    GenerateEditorTimeline,
    RetryEditorTimeline,
    ReviseTimelineCandidate,
    TimelineRead,
    ValidateTimeline,
)
from ..editor.service import (
    EditorConflictError,
    EditorNotFoundError,
    approve_quality_stage,
    confirm_timeline,
    create_timeline_candidate,
    editor_workspace,
    generate_editor_timeline,
    retry_editor_timeline,
    revise_timeline_candidate,
    validate_timeline,
)
from ..editor.agent_gateway import EditorAssistantGateway, get_editor_assistant_gateway
from ..planning.contracts import (
    CreativeBriefCandidateRead,
    DecideBrief,
    DecideShotPlan,
    CancelShotPlanRevision,
    GenerateBrief,
    GenerateShotPlan,
    PlanningCenterView,
    PlanVersionRead,
    RegenerateBriefWithCurrentContract,
    RetryBrief,
    RetryShotPlan,
    ReviseBrief,
    ReviseShotPlan,
    ReviseShotPlanWithDirector,
    StartShotPlanRevision,
    ShotPlanCandidateRead,
)
from ..planning.agent_gateway import ContentPlannerGateway, get_content_planner_gateway
from ..planning.director_gateway import DirectorGateway, get_director_gateway
from ..planning.service import (
    decide_brief,
    decide_shot_plan,
    cancel_shot_plan_revision,
    generate_brief,
    generate_shot_plan,
    planning_center_view,
    regenerate_failed_brief_with_current_contract,
    retry_failed_brief,
    retry_failed_shot_plan,
    revise_brief,
    revise_shot_plan,
    revise_shot_plan_with_director,
    start_shot_plan_revision,
)
from ..providers.contracts import ProviderReadinessView
from ..providers.readiness import provider_readiness
from ..production.contracts import (
    ActivateProductionSnapshot,
    AnalyzeProductionImpact,
    ApproveImagePhase,
    BlockedProductionClosedRead,
    CloseBlockedProduction,
    CreateProductionSnapshot,
    DecideProductionPlanCandidate,
    GenerateProductionPlanCandidate,
    ImpactAnalysisRead,
    LockProductionSnapshot,
    ProductionExecutionView,
    ProductionPreparationView,
    ProductionPlanCandidateRead,
    ProductionSnapshotRead,
    SubmitProduction,
    RetryProductionPlanner,
)
from ..production.agent_gateway import ProductionPlannerGateway, get_production_planner_gateway
from ..production.planning_service import (
    decide_production_plan_candidate,
    generate_production_plan_candidate,
    retry_production_planner,
)
from ..production.service import (
    ProductionConflictError,
    ProductionNotFoundError,
    activate_snapshot,
    analyze_impact,
    approve_image_phase,
    close_blocked_production,
    create_snapshot,
    execution_view,
    lock_snapshot,
    preparation_view,
    submit_production,
)
from ..projects.service import (
    ProjectConflictError,
    archive_project,
    confirm_project,
    create_project,
    get_project,
    list_projects,
    queue_contract_validation,
    restore_project,
)
from ..quality.contracts import (
    AssetRead,
    QCReportCandidateRead,
    QCReportRead,
    QualityReviewView,
    RegisterAttemptAsset,
    RetryAssetQC,
    ReviewAsset,
    RunAssetQC,
    VerifyAsset,
)
from ..quality.agent_gateway import QCGateway, get_qc_gateway
from ..quality.service import (
    QualityConflictError,
    QualityNotFoundError,
    asset_content_path,
    quality_review_view,
    register_attempt_asset,
    retry_failed_asset_qc,
    review_asset,
    run_asset_qc,
    verify_asset,
)
from ..registry.contracts import EntityRegistryView
from ..registry.service import (
    RegistryConflictError,
    RegistryNotFoundError,
    attachment_content_path,
    entity_registry_view,
)
from ..revision import (
    AssetRevisionRequestRead,
    CancelAssetRevisionRequest,
    CreateAssetRevisionRequest,
    RevisionConflictError,
    RevisionNotFoundError,
    RevisionRequestResult,
    create_asset_revision_request,
    cancel_asset_revision_request,
    get_asset_revision_request,
)
from ..contact_sheet.contracts import MaterialContactSheetView
from ..contact_sheet.service import material_contact_sheet_view


router = APIRouter()


def creation_error(exc: CreationConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def configuration_error(exc: ConfigurationConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def production_error(exc: ProductionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def quality_error(exc: QualityConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def revision_error(exc: RevisionConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def delivery_error(exc: DeliveryConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def editor_error(exc: EditorConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code})


def require_project(session: Session, project_id: str):
    project = get_project(session, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": settings.app_version}


@router.get("/project-controls", response_model=list[ProjectControlSummary])
def project_control_list(
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
):
    return project_controls(session, include_archived=include_archived)


@router.get("/projects/{project_id}/control-center", response_model=ProjectControlView)
def project_control_detail(project_id: str, session: Session = Depends(get_session)):
    return project_control_view(session, require_project(session, project_id))


@router.get("/projects/{project_id}/audit-ledger", response_model=ProjectAuditLedgerView)
def project_audit_ledger_view(
    project_id: str,
    before_sequence: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return project_audit_ledger(
        session,
        require_project(session, project_id),
        before_sequence=before_sequence,
        limit=limit,
    )


@router.get("/projects/{project_id}/contact-sheet", response_model=MaterialContactSheetView)
def project_contact_sheet(project_id: str, session: Session = Depends(get_session)):
    return material_contact_sheet_view(session, require_project(session, project_id))


@router.get("/projects/{project_id}/decision-impact-graph", response_model=DecisionImpactGraphView)
def project_decision_impact_graph(project_id: str, session: Session = Depends(get_session)):
    return decision_impact_graph_view(session, require_project(session, project_id))


@router.get(
    "/projects/{project_id}/decision-change-impact-analyses",
    response_model=DecisionChangeImpactWorkspace,
)
def project_decision_change_impacts(project_id: str, session: Session = Depends(get_session)):
    return decision_change_impact_workspace(session, require_project(session, project_id))


@router.post(
    "/projects/{project_id}/decisions/{decision_id}/change-impact-analyses",
    response_model=DecisionChangeImpactAnalysisRead,
    status_code=status.HTTP_201_CREATED,
)
def project_decision_change_impact_analyze(
    project_id: str,
    decision_id: str,
    payload: AnalyzeDecisionChangeImpact,
    session: Session = Depends(get_session),
):
    try:
        return analyze_decision_change(session, require_project(session, project_id), decision_id, payload)
    except ImpactNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImpactConflictError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.get("/entity-registry", response_model=EntityRegistryView)
def entity_registry(session: Session = Depends(get_session)):
    return entity_registry_view(session)


@router.get("/system-config/provider-readiness", response_model=ProviderReadinessView)
def system_config_provider_readiness(session: Session = Depends(get_session)):
    return provider_readiness(session)


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
def projects(
    include_archived: bool = Query(default=False),
    session: Session = Depends(get_session),
):
    return list_projects(session, include_archived=include_archived)


@router.post("/projects", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def projects_create(payload: ProjectCreate, session: Session = Depends(get_session)):
    return create_project(session, payload)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def projects_get(project_id: str, session: Session = Depends(get_session)):
    return require_project(session, project_id)


@router.post("/projects/{project_id}:archive", response_model=ProjectDetail)
def projects_archive(
    project_id: str,
    payload: ArchiveProject,
    session: Session = Depends(get_session),
):
    try:
        return archive_project(session, require_project(session, project_id), payload)
    except ProjectConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post("/projects/{project_id}:restore", response_model=ProjectDetail)
def projects_restore(
    project_id: str,
    payload: RestoreProject,
    session: Session = Depends(get_session),
):
    try:
        return restore_project(session, require_project(session, project_id), payload)
    except ProjectConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


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
    "/projects/{project_id}/production-plan-candidates:generate",
    response_model=ProductionPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def production_plan_candidate_generate(
    project_id: str,
    payload: GenerateProductionPlanCandidate,
    gateway: ProductionPlannerGateway = Depends(get_production_planner_gateway),
    session: Session = Depends(get_session),
):
    try:
        return generate_production_plan_candidate(session, require_project(session, project_id), payload, gateway)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/production-planner-runs/{run_id}:retry",
    response_model=ProductionPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def production_planner_run_retry(
    project_id: str,
    run_id: str,
    payload: RetryProductionPlanner,
    gateway: ProductionPlannerGateway = Depends(get_production_planner_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    try:
        return retry_production_planner(session, require_project(session, project_id), payload, gateway)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/production-plan-candidates/{candidate_id}:decide",
    response_model=ProductionPlanCandidateRead,
)
def production_plan_candidate_decide(
    project_id: str,
    candidate_id: str,
    payload: DecideProductionPlanCandidate,
    session: Session = Depends(get_session),
):
    try:
        return decide_production_plan_candidate(session, require_project(session, project_id), candidate_id, payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


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


@router.post(
    "/projects/{project_id}/production-snapshots/{snapshot_id}:approve-image-phase",
    response_model=ProductionExecutionView,
)
def production_snapshot_approve_image_phase(
    project_id: str,
    snapshot_id: str,
    payload: ApproveImagePhase,
    session: Session = Depends(get_session),
):
    try:
        return approve_image_phase(session, require_project(session, project_id), snapshot_id, payload)
    except ProductionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProductionConflictError as exc:
        session.rollback()
        raise production_error(exc) from exc


@router.post(
    "/projects/{project_id}/production-snapshots/{snapshot_id}:close-blocked-production",
    response_model=BlockedProductionClosedRead,
)
def production_snapshot_close_blocked(
    project_id: str,
    snapshot_id: str,
    payload: CloseBlockedProduction,
    session: Session = Depends(get_session),
):
    try:
        return close_blocked_production(session, require_project(session, project_id), snapshot_id, payload)
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


@router.get("/projects/{project_id}/quality-review", response_model=QualityReviewView)
def project_quality_review(project_id: str, session: Session = Depends(get_session)):
    return quality_review_view(session, require_project(session, project_id))


@router.get("/projects/{project_id}/editor-workspace", response_model=EditorWorkspaceView)
def project_editor_workspace(project_id: str, session: Session = Depends(get_session)):
    return editor_workspace(session, require_project(session, project_id))


@router.post(
    "/projects/{project_id}/editor-assistant:generate",
    response_model=TimelineRead,
    status_code=status.HTTP_201_CREATED,
)
def project_editor_assistant_generate(
    project_id: str,
    payload: GenerateEditorTimeline,
    gateway: EditorAssistantGateway = Depends(get_editor_assistant_gateway),
    session: Session = Depends(get_session),
):
    try:
        return generate_editor_timeline(session, require_project(session, project_id), payload, gateway)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/editor-assistant-runs/{run_id}:retry",
    response_model=TimelineRead,
    status_code=status.HTTP_201_CREATED,
)
def project_editor_assistant_retry(
    project_id: str,
    run_id: str,
    payload: RetryEditorTimeline,
    gateway: EditorAssistantGateway = Depends(get_editor_assistant_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="Run ID mismatch", headers={"X-Error-Code": "EDITOR_RUN_ID_MISMATCH"})
    try:
        return retry_editor_timeline(session, require_project(session, project_id), payload, gateway)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.get("/projects/{project_id}/delivery-workspace", response_model=DeliveryWorkspaceView)
def project_delivery_workspace(project_id: str, session: Session = Depends(get_session)):
    return delivery_workspace(session, require_project(session, project_id))


@router.post(
    "/projects/{project_id}/deliveries:authorize",
    response_model=DeliveryAttemptRead,
    status_code=status.HTTP_201_CREATED,
)
def project_delivery_authorize(
    project_id: str,
    payload: AuthorizeDelivery,
    session: Session = Depends(get_session),
):
    try:
        return authorize_delivery(session, require_project(session, project_id), payload)
    except DeliveryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeliveryConflictError as exc:
        session.rollback()
        raise delivery_error(exc) from exc


@router.post(
    "/projects/{project_id}/delivery-attempts/{attempt_id}/output",
    response_model=DeliveryAttemptRead,
    status_code=status.HTTP_201_CREATED,
)
async def project_delivery_output_register(
    project_id: str,
    attempt_id: str,
    command_id: str = Form(..., min_length=8, max_length=80),
    actor_id: str = Form("local-user", min_length=1, max_length=48),
    expected_request_fingerprint: str = Form(..., min_length=64, max_length=64),
    expected_row_version: int = Form(..., ge=1),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        maximum_bytes = delivery_upload_limit(session, project, attempt_id)
    except DeliveryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeliveryConflictError as exc:
        raise delivery_error(exc) from exc
    if file.content_type != "video/mp4":
        raise HTTPException(status_code=422, detail="最终交付当前只接受 video/mp4 文件。")

    temporary_dir = RUNTIME_ROOT / "uploads" / "delivery"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary_path = temporary_dir / f"{uuid4().hex}.upload"
    digest = hashlib.sha256()
    byte_size = 0
    try:
        with temporary_path.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                byte_size += len(chunk)
                if byte_size > maximum_bytes:
                    raise HTTPException(status_code=413, detail="交付文件超过当前快照的存储策略上限。")
                digest.update(chunk)
                output.write(chunk)
        if byte_size == 0:
            raise HTTPException(status_code=422, detail="交付文件不能为空。")
        payload = RegisterDeliveryOutput(
            command_id=command_id,
            actor_id=actor_id,
            expected_request_fingerprint=expected_request_fingerprint,
            expected_row_version=expected_row_version,
            original_filename=file.filename or "delivery.mp4",
            mime_type=file.content_type,
            content_hash=digest.hexdigest(),
            byte_size=byte_size,
        )
        return register_delivery_output(session, project, attempt_id, payload, temporary_path)
    except DeliveryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeliveryConflictError as exc:
        session.rollback()
        raise delivery_error(exc) from exc
    finally:
        await file.close()
        temporary_path.unlink(missing_ok=True)


@router.post(
    "/projects/{project_id}/delivery-attempts/{attempt_id}:verify",
    response_model=DeliveryAttemptRead,
)
def project_delivery_verify(
    project_id: str,
    attempt_id: str,
    payload: VerifyDelivery,
    session: Session = Depends(get_session),
):
    try:
        return verify_delivery(session, require_project(session, project_id), attempt_id, payload)
    except DeliveryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DeliveryConflictError as exc:
        session.rollback()
        raise delivery_error(exc) from exc


@router.post("/projects/{project_id}/quality-stage:approve", response_model=EditorWorkspaceView)
def project_quality_stage_approve(
    project_id: str,
    payload: ApproveQualityStage,
    session: Session = Depends(get_session),
):
    try:
        return approve_quality_stage(session, require_project(session, project_id), payload)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc


@router.post(
    "/projects/{project_id}/timeline-candidates",
    response_model=TimelineRead,
    status_code=status.HTTP_201_CREATED,
)
def project_timeline_candidate_create(
    project_id: str,
    payload: CreateTimelineCandidate,
    session: Session = Depends(get_session),
):
    try:
        return create_timeline_candidate(session, require_project(session, project_id), payload)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc


@router.post(
    "/projects/{project_id}/timelines/{timeline_id}:revise",
    response_model=TimelineRead,
    status_code=status.HTTP_201_CREATED,
)
def project_timeline_revise(
    project_id: str,
    timeline_id: str,
    payload: ReviseTimelineCandidate,
    session: Session = Depends(get_session),
):
    try:
        return revise_timeline_candidate(session, require_project(session, project_id), timeline_id, payload)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc


@router.post("/projects/{project_id}/timelines/{timeline_id}:validate", response_model=TimelineRead)
def project_timeline_validate(
    project_id: str,
    timeline_id: str,
    payload: ValidateTimeline,
    session: Session = Depends(get_session),
):
    try:
        return validate_timeline(session, require_project(session, project_id), timeline_id, payload)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc


@router.post("/projects/{project_id}/timelines/{timeline_id}:confirm", response_model=TimelineRead)
def project_timeline_confirm(
    project_id: str,
    timeline_id: str,
    payload: ConfirmTimeline,
    session: Session = Depends(get_session),
):
    try:
        return confirm_timeline(session, require_project(session, project_id), timeline_id, payload)
    except EditorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EditorConflictError as exc:
        session.rollback()
        raise editor_error(exc) from exc


@router.post(
    "/projects/{project_id}/work-attempts/{attempt_id}/assets",
    response_model=AssetRead,
    status_code=status.HTTP_201_CREATED,
)
def work_attempt_asset_register(
    project_id: str,
    attempt_id: str,
    payload: RegisterAttemptAsset,
    session: Session = Depends(get_session),
):
    try:
        return register_attempt_asset(session, require_project(session, project_id), attempt_id, payload)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        session.rollback()
        raise quality_error(exc) from exc


@router.post("/projects/{project_id}/assets/{asset_id}:verify", response_model=AssetRead)
def project_asset_verify(
    project_id: str,
    asset_id: str,
    payload: VerifyAsset,
    session: Session = Depends(get_session),
):
    try:
        return verify_asset(session, require_project(session, project_id), asset_id, payload)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        session.rollback()
        raise quality_error(exc) from exc


@router.post(
    "/projects/{project_id}/assets/{asset_id}:request-revision",
    response_model=RevisionRequestResult,
    status_code=status.HTTP_201_CREATED,
)
def project_asset_revision_request(
    project_id: str,
    asset_id: str,
    payload: CreateAssetRevisionRequest,
    session: Session = Depends(get_session),
):
    try:
        return create_asset_revision_request(session, require_project(session, project_id), asset_id, payload)
    except RevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RevisionConflictError as exc:
        session.rollback()
        raise revision_error(exc) from exc


@router.get(
    "/projects/{project_id}/asset-revision-requests/{request_id}",
    response_model=AssetRevisionRequestRead,
)
def project_asset_revision_request_read(
    project_id: str,
    request_id: str,
    session: Session = Depends(get_session),
):
    try:
        return get_asset_revision_request(session, require_project(session, project_id), request_id)
    except RevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/projects/{project_id}/asset-revision-requests/{request_id}:cancel",
    response_model=AssetRevisionRequestRead,
)
def project_asset_revision_request_cancel(
    project_id: str,
    request_id: str,
    payload: CancelAssetRevisionRequest,
    session: Session = Depends(get_session),
):
    try:
        return cancel_asset_revision_request(session, require_project(session, project_id), request_id, payload)
    except RevisionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RevisionConflictError as exc:
        session.rollback()
        raise revision_error(exc) from exc


@router.post("/projects/{project_id}/assets/{asset_id}:run-qc", response_model=QCReportCandidateRead | QCReportRead)
def project_asset_run_qc(
    project_id: str,
    asset_id: str,
    payload: RunAssetQC,
    gateway: QCGateway = Depends(get_qc_gateway),
    session: Session = Depends(get_session),
):
    try:
        return run_asset_qc(session, require_project(session, project_id), asset_id, payload, gateway)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        session.rollback()
        raise quality_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post("/projects/{project_id}/assets/{asset_id}/qc-runs/{run_id}:retry", response_model=QCReportCandidateRead)
def project_asset_qc_retry(
    project_id: str,
    asset_id: str,
    run_id: str,
    payload: RetryAssetQC,
    gateway: QCGateway = Depends(get_qc_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    try:
        return retry_failed_asset_qc(session, require_project(session, project_id), asset_id, payload, gateway)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        session.rollback()
        raise quality_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


def _review_asset_command(project_id: str, asset_id: str, payload: ReviewAsset, decision: str, session: Session):
    try:
        return review_asset(session, require_project(session, project_id), asset_id, payload, decision)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        session.rollback()
        raise quality_error(exc) from exc


@router.post("/projects/{project_id}/assets/{asset_id}:approve", response_model=AssetRead)
def project_asset_approve(project_id: str, asset_id: str, payload: ReviewAsset, session: Session = Depends(get_session)):
    return _review_asset_command(project_id, asset_id, payload, "approved", session)


@router.post("/projects/{project_id}/assets/{asset_id}:reject", response_model=AssetRead)
def project_asset_reject(project_id: str, asset_id: str, payload: ReviewAsset, session: Session = Depends(get_session)):
    return _review_asset_command(project_id, asset_id, payload, "rejected", session)


@router.get("/projects/{project_id}/assets/{asset_id}/content")
def project_asset_content(project_id: str, asset_id: str, session: Session = Depends(get_session)):
    try:
        path, media_type = asset_content_path(session, require_project(session, project_id), asset_id)
        return FileResponse(path, media_type=media_type)
    except QualityNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except QualityConflictError as exc:
        raise quality_error(exc) from exc


@router.get("/projects/{project_id}/attachments/{attachment_id}/content")
def project_attachment_content(project_id: str, attachment_id: str, session: Session = Depends(get_session)):
    try:
        path, media_type = attachment_content_path(session, require_project(session, project_id), attachment_id)
        return FileResponse(path, media_type=media_type)
    except RegistryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RegistryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/creative-brief-candidates:generate",
    response_model=CreativeBriefCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_brief_generate(
    project_id: str,
    payload: GenerateBrief,
    gateway: ContentPlannerGateway = Depends(get_content_planner_gateway),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return generate_brief(session, project, payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/creative-brief-candidates/{candidate_id}:revise",
    response_model=CreativeBriefCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_brief_revise(
    project_id: str,
    candidate_id: str,
    payload: ReviseBrief,
    gateway: ContentPlannerGateway = Depends(get_content_planner_gateway),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return revise_brief(session, project, candidate_id, payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/content-planner-runs/{run_id}:retry",
    response_model=CreativeBriefCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def content_planner_run_retry(
    project_id: str,
    run_id: str,
    payload: RetryBrief,
    gateway: ContentPlannerGateway = Depends(get_content_planner_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    project = require_project(session, project_id)
    try:
        return retry_failed_brief(session, project, payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/content-planner-runs/{run_id}:regenerate-with-current-contract",
    response_model=CreativeBriefCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def content_planner_run_regenerate_with_current_contract(
    project_id: str,
    run_id: str,
    payload: RegenerateBriefWithCurrentContract,
    gateway: ContentPlannerGateway = Depends(get_content_planner_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    project = require_project(session, project_id)
    try:
        return regenerate_failed_brief_with_current_contract(session, project, payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


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
def shot_plan_generate(
    project_id: str,
    payload: GenerateShotPlan,
    gateway: DirectorGateway = Depends(get_director_gateway),
    session: Session = Depends(get_session),
):
    try:
        return generate_shot_plan(session, require_project(session, project_id), payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/director-runs/{run_id}:retry",
    response_model=ShotPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def director_run_retry(
    project_id: str,
    run_id: str,
    payload: RetryShotPlan,
    gateway: DirectorGateway = Depends(get_director_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    try:
        return retry_failed_shot_plan(session, require_project(session, project_id), payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


@router.post(
    "/projects/{project_id}/shot-plan-revisions",
    response_model=ShotPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def shot_plan_revision_start(
    project_id: str,
    payload: StartShotPlanRevision,
    session: Session = Depends(get_session),
):
    try:
        return start_shot_plan_revision(session, require_project(session, project_id), payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates/{candidate_id}:cancel-revision",
    response_model=ShotPlanCandidateRead,
)
def shot_plan_revision_cancel(
    project_id: str,
    candidate_id: str,
    payload: CancelShotPlanRevision,
    session: Session = Depends(get_session),
):
    try:
        return cancel_shot_plan_revision(session, require_project(session, project_id), candidate_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates/{candidate_id}:revise",
    response_model=ShotPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def shot_plan_revise(
    project_id: str,
    candidate_id: str,
    payload: ReviseShotPlan,
    session: Session = Depends(get_session),
):
    try:
        return revise_shot_plan(session, require_project(session, project_id), candidate_id, payload)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/shot-plan-candidates/{candidate_id}:revise-with-director",
    response_model=ShotPlanCandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def shot_plan_revise_with_director(
    project_id: str,
    candidate_id: str,
    payload: ReviseShotPlanWithDirector,
    gateway: DirectorGateway = Depends(get_director_gateway),
    session: Session = Depends(get_session),
):
    try:
        return revise_shot_plan_with_director(
            session, require_project(session, project_id), candidate_id, payload, gateway
        )
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc), headers={"X-Error-Code": exc.code}) from exc


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
    "/projects/{project_id}/conversation-sessions",
    response_model=ConversationSessionRead,
    status_code=status.HTTP_201_CREATED,
)
def conversation_session_start(
    project_id: str,
    payload: StartConversationSession,
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return start_conversation_session(session, project, payload)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc


@router.post(
    "/projects/{project_id}/creative-conversation:initialize",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_conversation_initialize(
    project_id: str,
    payload: InitializeCreativeConversation,
    gateway: CreativeAgentGateway = Depends(get_creative_agent_gateway),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return initialize_creative_conversation(session, project, payload, gateway)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/requirement-candidates:generate",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def requirement_candidate_generate(
    project_id: str,
    payload: GenerateCandidate,
    gateway: CreativeAgentGateway = Depends(get_creative_agent_gateway),
    session: Session = Depends(get_session),
):
    project = require_project(session, project_id)
    try:
        return generate_candidate(session, project, payload, gateway)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/creative-agent-runs/{run_id}:retry",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_agent_run_retry(
    project_id: str,
    run_id: str,
    payload: RetryCreativeTurn,
    gateway: CreativeAgentGateway = Depends(get_creative_agent_gateway),
    session: Session = Depends(get_session),
):
    if payload.failed_agent_run_id != run_id:
        raise HTTPException(status_code=409, detail="失败运行编号与路径不一致。")
    project = require_project(session, project_id)
    try:
        return retry_creative_turn(session, project, payload, gateway)
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post(
    "/projects/{project_id}/creative-proposals/{proposal_id}:select",
    response_model=CandidateRead,
    status_code=status.HTTP_201_CREATED,
)
def creative_suggestion_select(
    project_id: str,
    proposal_id: str,
    payload: SelectCreativeSuggestion,
    session: Session = Depends(get_session),
    gateway: CreativeAgentGateway = Depends(get_creative_agent_gateway),
):
    project = require_project(session, project_id)
    try:
        return select_creative_suggestion(session, project, proposal_id, payload, gateway)
    except CreationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CreationConflictError as exc:
        raise creation_error(exc) from exc
    except AgentGatewayError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


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
