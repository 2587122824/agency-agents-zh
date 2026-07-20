from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.completeness import evaluate_requirement
from ..creation.agent_gateway import AgentGatewayError
from ..creation.service import (
    CreationConflictError,
    CreationNotFoundError,
    _event,
    _receipt,
    _receipt_result,
    _save_receipt,
    active_requirement,
    sync_clarifications,
)
from ..db.models import (
    AgentInputManifest,
    AgentRun,
    AssetRevisionRequest,
    CreativeBriefCandidate,
    PlanVersion,
    ProductionSnapshot,
    Project,
    RequirementVersion,
    Shot,
    ShotPlanCandidate,
    utc_now,
)
from ..repositories import PlanningRepository, SqlAlchemyDecisionRepository, SqlAlchemyPlanningRepository
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from .agent_gateway import ContentPlannerGateway, ContentPlannerSelection
from .contracts import DecideBrief, DecideShotPlan, GenerateBrief, GenerateShotPlan, PlanningNextAction, RetryBrief, RetryShotPlan, ReviseBrief, ReviseShotPlan, ReviseShotPlanWithDirector
from .director_gateway import DirectorGateway, DirectorSelection


def _planning(session: Session) -> PlanningRepository:
    return SqlAlchemyPlanningRepository(session)


def _agent_run_view(session: Session, run: AgentRun | None) -> dict | None:
    if run is None:
        return None
    manifest = session.get(AgentInputManifest, run.input_manifest_id)
    return {
        "id": run.id,
        "agent_role": run.agent_role,
        "status": run.status,
        "input_manifest_id": run.input_manifest_id,
        "model_provider": run.model_provider,
        "model_name": run.model_name,
        "production_config_version_id": run.production_config_version_id,
        "model_config_version_id": run.model_config_version_id,
        "provider_config_version_id": run.provider_config_version_id,
        "prompt_contract_version": run.prompt_contract_version,
        "output_schema_version": run.output_schema_version,
        "parsed_candidate_id": run.parsed_candidate_id,
        "parsed_proposal_id": run.parsed_proposal_id,
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "provider_request_id": run.provider_request_id,
        "token_usage": run.token_usage or {},
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "input_manifest": manifest,
    }


def _content_planner_manifest_payload(
    session: Session,
    project: Project,
    requirement: RequirementVersion,
) -> dict:
    repository = _planning(session)
    decisions = SqlAlchemyDecisionRepository(session).resolved_for_project(project.id)
    entity_rows = repository.active_entity_versions(project.id)
    fields = dict(requirement.fields)
    return {
        "contract_version": "content-planner-input.v2",
        "project_id": project.id,
        "requirement_version": {"id": requirement.id, "fields": fields},
        "confirmed_decisions": [
            {"id": item.id, "key": item.key, "value": item.value}
            for item in decisions
        ],
        "confirmed_entity_versions": [
            {
                "id": version.id,
                "entity_id": entity.id,
                "entity_type": entity.entity_type,
                "display_name": entity.display_name,
                "version_number": version.version_number,
                "attributes": version.attributes,
            }
            for version, entity in entity_rows
            if version.status == "confirmed" and entity.status == "active"
        ],
        "delivery_constraints": {
            "duration_ms": int(fields["duration_seconds"]) * 1000,
            "aspect_ratio": fields["aspect_ratio"],
        },
        "audio_policy": fields["audio_mode"],
        "platform": fields.get("platform"),
        "template_version_id": fields.get("template_version_id"),
    }


def _director_manifest_payload(
    session: Session,
    project: Project,
    requirement: RequirementVersion,
    brief: CreativeBriefCandidate,
) -> dict:
    repository = _planning(session)
    decisions = SqlAlchemyDecisionRepository(session).resolved_for_project(project.id)
    entity_rows = repository.active_entity_versions(project.id)
    fields = dict(requirement.fields)
    entities = []
    for version, entity in entity_rows:
        if version.status != "confirmed" or entity.status != "active":
            continue
        attachment = repository.attachment(version.source_attachment_id) if version.source_attachment_id else None
        entities.append({
            "id": version.id,
            "entity_id": entity.id,
            "entity_type": entity.entity_type,
            "display_name": entity.display_name,
            "version_number": version.version_number,
            "attributes": version.attributes,
            "source_attachment": ({
                "id": attachment.id,
                "mime_type": attachment.mime_type,
                "verified": attachment.verification_status == "verified",
            } if attachment else None),
        })
    return {
        "contract_version": "director-input.v2",
        "project_id": project.id,
        "requirement_version": {"id": requirement.id, "fields": fields},
        "accepted_creative_brief": {"id": brief.id, "brief": brief.brief},
        "confirmed_decisions": [{"id": item.id, "key": item.key, "value": item.value} for item in decisions],
        "confirmed_entity_versions": entities,
        "delivery_constraints": {
            "duration_ms": int(fields["duration_seconds"]) * 1000,
            "aspect_ratio": fields["aspect_ratio"],
        },
        "audio_policy": fields["audio_mode"],
    }


def _execute_director(
    session: Session,
    project: Project,
    requirement: RequirementVersion,
    brief: CreativeBriefCandidate,
    manifest: AgentInputManifest,
    payload: GenerateShotPlan | RetryShotPlan | ReviseShotPlanWithDirector,
    gateway: DirectorGateway,
    selection: DirectorSelection,
    *,
    retry_of_agent_run_id: str | None = None,
    source_candidate: ShotPlanCandidate | None = None,
) -> ShotPlanCandidate:
    repository = _planning(session)
    run = AgentRun(
        project_id=project.id,
        agent_role="director",
        status="running",
        input_manifest_id=manifest.id,
        model_provider=selection.model_provider,
        model_name=selection.model_name,
        production_config_version_id=selection.production_config_version_id,
        model_config_version_id=selection.model_config_version_id,
        provider_config_version_id=selection.provider_config_version_id,
        prompt_contract_version=selection.prompt_contract_version,
        output_schema_version=selection.output_schema_version,
        started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, project.id, "agent.run_created.v1", "分镜导演智能体已开始生成候选", {
        "agent_run_id": run.id,
        "model_config_version_id": selection.model_config_version_id,
        "provider_config_version_id": selection.provider_config_version_id,
        "retry_of_agent_run_id": retry_of_agent_run_id,
    })
    session.commit()
    try:
        result = gateway.invoke(selection, manifest.payload)
    except AgentGatewayError as exc:
        failed_run = session.get(AgentRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_code = exc.code
            failed_run.error_detail = (
                f"{exc} {json.dumps(exc.diagnostics, ensure_ascii=False, separators=(',', ':'), default=str)}"
                if exc.diagnostics else str(exc)
            )
            failed_run.raw_output = exc.raw_output
            failed_run.finished_at = utc_now()
            _event(session, project.id, "agent.run_failed.v1", "分镜导演智能体本轮运行失败", {
                "agent_run_id": failed_run.id,
                "error_code": exc.code,
                "retry_of_agent_run_id": retry_of_agent_run_id,
            })
            session.commit()
        raise
    shots = [item.model_dump(mode="json") for item in result.output.shots]
    errors = validate_shots(session, project.id, requirement, shots, brief)
    if errors:
        failed_run = session.get(AgentRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_code = "DIRECTOR_OUTPUT_CONTRACT_INVALID"
            failed_run.error_detail = "分镜导演输出未通过项目分镜合同验证。"
            failed_run.raw_output = result.raw_output
            failed_run.provider_request_id = result.provider_request_id
            failed_run.token_usage = result.token_usage
            failed_run.finished_at = utc_now()
            _event(session, project.id, "agent.run_failed.v1", "分镜导演智能体本轮运行失败", {
                "agent_run_id": failed_run.id,
                "error_code": failed_run.error_code,
                "retry_of_agent_run_id": retry_of_agent_run_id,
            })
            session.commit()
        raise AgentGatewayError(
            "DIRECTOR_OUTPUT_CONTRACT_INVALID",
            "分镜导演输出未通过项目分镜合同验证。",
            raw_output=result.raw_output,
            diagnostics=errors,
        )
    candidate = ShotPlanCandidate(
        project_id=project.id,
        requirement_version_id=requirement.id,
        creative_brief_candidate_id=brief.id,
        agent_run_id=run.id,
        supersedes_candidate_id=source_candidate.id if source_candidate else None,
        revision_number=(source_candidate.revision_number + 1) if source_candidate else 1,
        source="director_revision" if source_candidate else "director_agent",
        status="awaiting_review",
        shots=shots,
        validation_errors=[],
        created_by=payload.actor_id,
    )
    repository.add(candidate)
    repository.flush()
    if source_candidate and not repository.transition_reviewable_shot_plan(
        source_candidate.id,
        getattr(payload, "expected_candidate_row_version", source_candidate.row_version),
        "superseded",
        utc_now(),
    ):
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，AI 修订结果没有替换原候选。")
    run.status = "succeeded"
    run.parsed_candidate_id = candidate.id
    run.raw_output = result.raw_output
    run.provider_request_id = result.provider_request_id
    run.token_usage = result.token_usage
    run.finished_at = utc_now()
    if source_candidate:
        revision_request = session.scalar(select(AssetRevisionRequest).where(
            AssetRevisionRequest.project_id == project.id,
            (
                (AssetRevisionRequest.draft_candidate_id == source_candidate.id)
                | (AssetRevisionRequest.resulting_candidate_id == source_candidate.id)
            ),
            AssetRevisionRequest.status.in_(("draft_created", "candidate_created")),
        ))
        if revision_request:
            revision_request.status = "candidate_created"
            revision_request.resulting_candidate_id = candidate.id
    command_type = "shot_plan.director_revision" if source_candidate else ("shot_plan.retry" if retry_of_agent_run_id else "shot_plan.generate")
    _save_receipt(session, project.id, payload.command_id, command_type, "shot_plan_candidate", candidate.id)
    _event(session, project.id, "agent.run_succeeded.v1", "分镜导演智能体已返回严格候选", {
        "agent_run_id": run.id,
        "candidate_id": candidate.id,
        "retry_of_agent_run_id": retry_of_agent_run_id,
    })
    _event(session, project.id, "plan.shot_candidate_created.v1", "分镜候选等待用户审核", {"candidate_id": candidate.id})
    transition_project(
        session,
        project,
        ProjectStateTrigger.SHOT_CANDIDATE_REVISED if source_candidate else ProjectStateTrigger.SHOT_CANDIDATE_CREATED,
        actor_type="system",
        actor_id="director-agent",
        event_data={"candidate_id": candidate.id},
    )
    session.commit()
    return candidate


def _execute_content_planner(
    session: Session,
    project: Project,
    requirement: RequirementVersion,
    manifest: AgentInputManifest,
    payload: GenerateBrief | RetryBrief | ReviseBrief,
    gateway: ContentPlannerGateway,
    selection: ContentPlannerSelection,
    *,
    retry_of_agent_run_id: str | None = None,
) -> CreativeBriefCandidate:
    repository = _planning(session)
    run = AgentRun(
        project_id=project.id,
        agent_role="planner",
        status="running",
        input_manifest_id=manifest.id,
        model_provider=selection.model_provider,
        model_name=selection.model_name,
        production_config_version_id=selection.production_config_version_id,
        model_config_version_id=selection.model_config_version_id,
        provider_config_version_id=selection.provider_config_version_id,
        prompt_contract_version=selection.prompt_contract_version,
        output_schema_version=selection.output_schema_version,
        started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, project.id, "agent.run_created.v1", "内容策划智能体已开始生成候选方案", {
        "agent_run_id": run.id,
        "model_config_version_id": selection.model_config_version_id,
        "provider_config_version_id": selection.provider_config_version_id,
        "retry_of_agent_run_id": retry_of_agent_run_id,
    })
    session.commit()
    try:
        result = gateway.invoke(selection, manifest.payload)
    except AgentGatewayError as exc:
        failed_run = session.get(AgentRun, run.id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error_code = exc.code
            failed_run.error_detail = (
                f"{exc} {json.dumps(exc.diagnostics, ensure_ascii=False, separators=(',', ':'), default=str)}"
                if exc.diagnostics else str(exc)
            )
            failed_run.raw_output = exc.raw_output
            failed_run.finished_at = utc_now()
            _event(session, project.id, "agent.run_failed.v1", "内容策划智能体本轮运行失败", {
                "agent_run_id": failed_run.id,
                "error_code": exc.code,
                "retry_of_agent_run_id": retry_of_agent_run_id,
            })
            session.commit()
        raise
    brief = result.output.model_dump(mode="json")
    brief.update({
        "duration_seconds": requirement.fields["duration_seconds"],
        "aspect_ratio": requirement.fields["aspect_ratio"],
        "audio_mode": requirement.fields["audio_mode"],
    })
    sources = {
        key: {"type": "agent_proposal", "reference_id": run.id}
        for key in type(result.output).model_fields
    }
    for key in ("duration_seconds", "aspect_ratio", "audio_mode"):
        sources[key] = {"type": "requirement", "reference_id": requirement.id}
    revision_request = manifest.payload.get("revision_request")
    source_candidate = (
        repository.creative_brief(revision_request["source_candidate_id"])
        if revision_request else None
    )
    candidate = CreativeBriefCandidate(
        project_id=project.id,
        requirement_version_id=requirement.id,
        agent_run_id=run.id,
        supersedes_candidate_id=source_candidate.id if source_candidate else None,
        revision_number=(source_candidate.revision_number + 1 if source_candidate else 1),
        source="planner_revision" if source_candidate else "planner_agent",
        brief=brief,
        field_sources=sources,
        created_by=payload.actor_id,
    )
    repository.add(candidate)
    repository.flush()
    if source_candidate and source_candidate.status in {"awaiting_review", "rejected"}:
        source_candidate.status = "superseded"
        source_candidate.decided_at = utc_now()
    run.status = "succeeded"
    run.parsed_candidate_id = candidate.id
    run.raw_output = result.raw_output
    run.provider_request_id = result.provider_request_id
    run.token_usage = result.token_usage
    run.finished_at = utc_now()
    command_type = "brief.retry" if retry_of_agent_run_id else "brief.revise" if revision_request else "brief.generate"
    _save_receipt(session, project.id, payload.command_id, command_type, "creative_brief_candidate", candidate.id)
    _event(session, project.id, "agent.run_succeeded.v1", "内容策划智能体已返回严格候选", {
        "agent_run_id": run.id,
        "candidate_id": candidate.id,
        "retry_of_agent_run_id": retry_of_agent_run_id,
    })
    _event(session, project.id, "plan.brief_candidate_created.v1", "内容方案候选等待用户审核", {
        "candidate_id": candidate.id,
        "supersedes_candidate_id": source_candidate.id if source_candidate else None,
        "revision_number": candidate.revision_number,
    })
    transition_project(
        session,
        project,
        ProjectStateTrigger.BRIEF_CANDIDATE_CREATED,
        actor_type="system",
        actor_id="creative-agent",
        event_data={"candidate_id": candidate.id},
    )
    session.commit()
    return candidate


def generate_brief(
    session: Session,
    project: Project,
    payload: GenerateBrief,
    gateway: ContentPlannerGateway,
) -> CreativeBriefCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, CreativeBriefCandidate)
    requirement = active_requirement(session, project.id)
    if not requirement or requirement.id != payload.expected_requirement_version_id:
        raise CreationConflictError("REQUIREMENT_VERSION_CONFLICT", "活动需求版本已变化，请刷新后重试。")
    if evaluate_requirement(requirement.fields, requirement.field_sources):
        sync_clarifications(session, project, requirement)
        session.commit()
        raise CreationConflictError("REQUIREMENT_INCOMPLETE", "需求仍有阻断字段，不能生成创意方案。")
    pending_decisions = [item.key for item in project.decisions if item.status == "pending"]
    if pending_decisions:
        raise CreationConflictError(
            "PROJECT_DECISIONS_UNRESOLVED",
            f"仍有未解决决策：{', '.join(sorted(pending_decisions))}。",
        )
    existing = repository.active_brief_for_requirement(project.id, requirement.id)
    if existing:
        raise CreationConflictError("BRIEF_ALREADY_EXISTS", "当前需求版本已有待审或已接受的创意方案。")
    previous_attempt = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "planner",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .limit(1)
    )
    if previous_attempt:
        raise CreationConflictError(
            "CONTENT_PLANNER_ALREADY_ATTEMPTED",
            "当前需求版本已经运行过内容策划；系统不会自动或重复调用模型。",
        )
    selection = gateway.select(session)
    manifest_payload = _content_planner_manifest_payload(session, project, requirement)
    manifest_payload["system_config_version"] = selection.production_config_version_id
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=requirement.id,
        decision_ids=[item["id"] for item in manifest_payload["confirmed_decisions"]],
        attachment_binding_ids=repository.confirmed_binding_ids(project.id),
        system_config_version=selection.production_config_version_id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    return _execute_content_planner(session, project, requirement, manifest, payload, gateway, selection)


def revise_brief(
    session: Session,
    project: Project,
    source_candidate_id: str,
    payload: ReviseBrief,
    gateway: ContentPlannerGateway,
) -> CreativeBriefCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, CreativeBriefCandidate)
    if not payload.confirm_model_cost:
        raise CreationConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次调整会调用一次当前内容策划模型。")
    requirement = active_requirement(session, project.id)
    source_candidate = repository.creative_brief(source_candidate_id)
    if not requirement or requirement.id != payload.expected_requirement_version_id:
        raise CreationConflictError("REQUIREMENT_VERSION_CONFLICT", "活动需求版本已变化，不能调整旧方案。")
    if (
        not source_candidate
        or source_candidate.project_id != project.id
        or source_candidate.requirement_version_id != requirement.id
    ):
        raise CreationNotFoundError("Creative brief candidate not found")
    if source_candidate.status not in {"awaiting_review", "rejected"}:
        raise CreationConflictError("BRIEF_NOT_REVISABLE", f"内容方案状态为 {source_candidate.status}，不能调整。")
    active_brief = repository.active_brief_for_requirement(project.id, requirement.id)
    if active_brief and active_brief.id != source_candidate.id:
        raise CreationConflictError("BRIEF_REVISION_SOURCE_NOT_CURRENT", "只能调整当前待审核或最近被拒绝的内容方案。")
    if session.scalar(select(AgentRun).where(
        AgentRun.project_id == project.id,
        AgentRun.agent_role == "planner",
        AgentRun.status == "running",
    ).limit(1)):
        raise CreationConflictError("CONTENT_PLANNER_RUN_IN_PROGRESS", "当前已有内容策划运行，不能重复提交调整。")
    selection = gateway.select(session)
    manifest_payload = _content_planner_manifest_payload(session, project, requirement)
    manifest_payload["revision_request"] = {
        "source_candidate_id": source_candidate.id,
        "source_revision_number": source_candidate.revision_number,
        "source_brief": source_candidate.brief,
        "instruction": payload.revision_instruction.strip(),
    }
    manifest_payload["system_config_version"] = selection.production_config_version_id
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=requirement.id,
        decision_ids=[item["id"] for item in manifest_payload["confirmed_decisions"]],
        attachment_binding_ids=repository.confirmed_binding_ids(project.id),
        system_config_version=selection.production_config_version_id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    return _execute_content_planner(session, project, requirement, manifest, payload, gateway, selection)


def retry_failed_brief(
    session: Session,
    project: Project,
    payload: RetryBrief,
    gateway: ContentPlannerGateway,
) -> CreativeBriefCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, CreativeBriefCandidate)
    if not payload.confirm_model_cost:
        raise CreationConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次重跑会再次调用当前内容策划模型。")
    requirement = active_requirement(session, project.id)
    if not requirement or requirement.id != payload.expected_requirement_version_id:
        raise CreationConflictError("REQUIREMENT_VERSION_CONFLICT", "活动需求版本已变化，不能重跑旧策划。")
    latest_run = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "planner",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    if not latest_run or latest_run.id != payload.failed_agent_run_id:
        raise CreationConflictError("CONTENT_PLANNER_FAILED_RUN_NOT_LATEST", "只能重跑当前需求版本最近一次失败的内容策划。")
    if latest_run.status != "failed":
        raise CreationConflictError("CONTENT_PLANNER_RUN_NOT_FAILED", "指定的内容策划运行不是失败状态。")
    manifest = session.get(AgentInputManifest, latest_run.input_manifest_id)
    if not manifest or manifest.base_requirement_version_id != requirement.id:
        raise CreationConflictError("CONTENT_PLANNER_MANIFEST_MISSING", "失败运行的输入清单不存在或需求版本不一致。")
    active_brief = repository.active_brief_for_requirement(project.id, requirement.id)
    revision_source_id = manifest.payload.get("revision_request", {}).get("source_candidate_id")
    if active_brief and active_brief.id != revision_source_id:
        raise CreationConflictError("BRIEF_ALREADY_EXISTS", "当前需求版本已有其他待审或已接受的内容方案。")
    selection = gateway.select(session)
    expected_config = (
        latest_run.production_config_version_id,
        latest_run.model_config_version_id,
        latest_run.provider_config_version_id,
        latest_run.prompt_contract_version,
        latest_run.output_schema_version,
    )
    actual_config = (
        selection.production_config_version_id,
        selection.model_config_version_id,
        selection.provider_config_version_id,
        selection.prompt_contract_version,
        selection.output_schema_version,
    )
    if actual_config != expected_config:
        raise CreationConflictError("CONTENT_PLANNER_RETRY_CONFIG_CHANGED", "当前内容策划配置与失败运行不一致，不能静默更换模型或合同后重跑。")
    return _execute_content_planner(
        session,
        project,
        requirement,
        manifest,
        payload,
        gateway,
        selection,
        retry_of_agent_run_id=latest_run.id,
    )


def decide_brief(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: DecideBrief,
    accept: bool,
) -> CreativeBriefCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, CreativeBriefCandidate)
    candidate = repository.creative_brief(candidate_id)
    requirement = active_requirement(session, project.id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Creative brief candidate not found")
    if not requirement or requirement.id != payload.expected_requirement_version_id or candidate.requirement_version_id != requirement.id:
        raise CreationConflictError("BRIEF_BASE_VERSION_STALE", "创意方案基于旧需求版本，不能处理。")
    if candidate.status != "awaiting_review":
        raise CreationConflictError("BRIEF_NOT_REVIEWABLE", f"创意方案状态为 {candidate.status}。")
    if accept and candidate.brief.get("facts_requiring_confirmation"):
        raise CreationConflictError(
            "BRIEF_FACTS_UNCONFIRMED",
            "内容方案仍包含未经确认的事实，请先逐项确认并生成修订版。",
        )
    if accept and candidate.brief.get("open_questions"):
        raise CreationConflictError(
            "BRIEF_OPEN_QUESTIONS_UNRESOLVED",
            "内容方案仍有未确认问题，请先补充需求并重新生成候选。",
        )
    candidate.status = "accepted" if accept else "rejected"
    candidate.decided_at = utc_now()
    if not accept:
        candidate.validation_errors = [{"code": "USER_REJECTED", "message": payload.reason or "用户拒绝候选"}]
    command_type = "brief.accept" if accept else "brief.reject"
    _save_receipt(session, project.id, payload.command_id, command_type, "creative_brief_candidate", candidate.id)
    _event(session, project.id, f"plan.brief_candidate_{'accepted' if accept else 'rejected'}.v1", "创意方案候选审核完成", {
        "candidate_id": candidate.id,
        "accepted": accept,
    })
    transition_project(
        session,
        project,
        ProjectStateTrigger.BRIEF_ACCEPTED if accept else ProjectStateTrigger.BRIEF_REJECTED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"candidate_id": candidate.id},
    )
    session.commit()
    return candidate


def validate_shots(
    session: Session,
    project_id: str,
    requirement: RequirementVersion,
    shots: list[dict],
    brief: CreativeBriefCandidate | None = None,
) -> list[dict]:
    repository = _planning(session)
    errors: list[dict] = []
    if not shots:
        return [{"code": "SHOTS_REQUIRED", "field": "shots"}]
    expected_duration = int(requirement.fields["duration_seconds"]) * 1000
    if sum(int(item.get("duration_ms", 0)) for item in shots) != expected_duration:
        errors.append({"code": "SHOT_DURATION_MISMATCH", "field": "duration_ms"})
    codes = [item.get("shot_code") for item in shots]
    sequences = [item.get("sequence_number") for item in shots]
    expected_codes = [f"SH-{index:03d}" for index in range(1, len(shots) + 1)]
    if codes != expected_codes or len(sequences) != len(set(sequences)) or sequences != list(range(1, len(shots) + 1)):
        errors.append({"code": "SHOT_ID_OR_SEQUENCE_INVALID", "field": "shot_code"})
    beat_durations = {
        item.get("beat_code"): int(item.get("target_duration_ms", 0))
        for item in ((brief.brief.get("narrative_beats") if brief else None) or [])
    }
    script_segments = {
        item.get("segment_code"): item.get("beat_code")
        for item in ((brief.brief.get("script_segments") if brief else None) or [])
    }
    covered_segments: set[str] = set()
    actual_by_beat = {code: 0 for code in beat_durations}
    continuity: dict[str, tuple] = {}
    referenced = set()
    for item in shots:
        shot_code = item.get("shot_code")
        beat_code = item.get("narrative_beat_code")
        if beat_code not in beat_durations:
            errors.append({"code": "NARRATIVE_BEAT_REFERENCE_INVALID", "shot_code": shot_code})
        else:
            actual_by_beat[beat_code] += int(item.get("duration_ms", 0))
        if item.get("action_count") != 1:
            errors.append({"code": "SHOT_PRIMARY_ACTION_COUNT_INVALID", "shot_code": shot_code})
        segment_codes = item.get("brief_segment_codes")
        if not isinstance(segment_codes, list) or not segment_codes or len(segment_codes) != len(set(segment_codes)):
            errors.append({"code": "SHOT_SEGMENT_REFERENCES_INVALID", "shot_code": shot_code})
        else:
            unknown_segments = [code for code in segment_codes if code not in script_segments]
            wrong_beat_segments = [code for code in segment_codes if code in script_segments and script_segments[code] != beat_code]
            if unknown_segments:
                errors.append({"code": "SCRIPT_SEGMENT_REFERENCE_INVALID", "shot_code": shot_code, "segment_codes": unknown_segments})
            if wrong_beat_segments:
                errors.append({"code": "SCRIPT_SEGMENT_BEAT_MISMATCH", "shot_code": shot_code, "segment_codes": wrong_beat_segments})
            covered_segments.update(code for code in segment_codes if code in script_segments)
        enum_fields = {
            "shot_purpose": {"establish", "develop", "demonstrate", "contrast", "transition", "resolve"},
            "framing": {"extreme_close_up", "close_up", "medium", "full", "wide"},
            "camera_angle": {"eye_level", "high", "low", "top_down", "over_shoulder"},
            "camera_motion": {"locked", "pan", "tilt", "dolly", "tracking", "handheld"},
            "subject_motion": {"none", "subtle", "moderate", "significant"},
            "continuity_relation": {"same_moment", "time_jump", "location_change", "outfit_change"},
        }
        for field, allowed in enum_fields.items():
            if item.get(field) not in allowed:
                errors.append({"code": "SHOT_STRUCTURED_FIELD_INVALID", "shot_code": shot_code, "field": field})
        audio_requirement = item.get("audio_requirement")
        allowed_audio = {"off", "lip_motion_only"} if requirement.fields.get("audio_mode") == "off" else {"off", "lip_motion_only", "configured"}
        if audio_requirement not in allowed_audio:
            errors.append({"code": "SHOT_AUDIO_REQUIREMENT_INVALID", "shot_code": shot_code})
        visual_prompt = item.get("visual_prompt")
        if not isinstance(visual_prompt, str) or not visual_prompt.strip() or len(visual_prompt.strip()) > 4000:
            errors.append({"code": "VISUAL_PROMPT_INVALID", "shot_code": shot_code})
        negative_prompt = item.get("negative_prompt")
        if negative_prompt is not None and (
            not isinstance(negative_prompt, str)
            or not negative_prompt.strip()
            or len(negative_prompt.strip()) > 2000
        ):
            errors.append({"code": "NEGATIVE_PROMPT_INVALID", "shot_code": shot_code})
        if item.get("scene_entity_version_id"):
            referenced.add(item["scene_entity_version_id"])
        shot_references: set[str] = set()
        if item.get("scene_entity_version_id"):
            shot_references.add(item["scene_entity_version_id"])
        for field in (
            "character_entity_version_ids",
            "outfit_entity_version_ids",
            "product_entity_version_ids",
        ):
            values = item.get(field) or []
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(values) != len(set(values)):
                errors.append({"code": "ENTITY_VERSION_LIST_INVALID", "shot_code": shot_code, "field": field})
                continue
            referenced.update(values)
            shot_references.update(values)
        primary_reference = item.get("primary_reference_entity_version_id")
        if primary_reference is not None and primary_reference not in shot_references:
            errors.append({
                "code": "PRIMARY_REFERENCE_NOT_DECLARED",
                "shot_code": shot_code,
                "entity_version_id": primary_reference,
            })
        if item.get("face_visibility") not in {"required", "optional", "not_visible"}:
            errors.append({"code": "FACE_VISIBILITY_INVALID", "shot_code": item.get("shot_code")})
        face_subjects = item.get("face_subject_entity_version_ids")
        characters = item.get("character_entity_version_ids") or []
        if not isinstance(face_subjects, list) or len(face_subjects) != len(set(face_subjects)):
            errors.append({"code": "FACE_SUBJECTS_INVALID", "shot_code": shot_code})
        elif item.get("face_visibility") == "required" and not face_subjects:
            errors.append({"code": "FACE_SUBJECT_REQUIRED", "shot_code": shot_code})
        elif item.get("face_visibility") != "required" and face_subjects:
            errors.append({"code": "FACE_SUBJECT_NOT_ALLOWED", "shot_code": shot_code})
        elif set(face_subjects) - set(characters):
            errors.append({"code": "FACE_SUBJECT_NOT_CHARACTER", "shot_code": shot_code})
        if item.get("text_policy") not in {"forbidden", "allowed", "required"}:
            errors.append({"code": "TEXT_POLICY_INVALID", "shot_code": item.get("shot_code")})
        required_text = item.get("required_on_screen_text")
        if item.get("text_policy") == "required" and (not isinstance(required_text, str) or not required_text.strip()):
            errors.append({"code": "REQUIRED_ON_SCREEN_TEXT_MISSING", "shot_code": shot_code})
        if item.get("text_policy") == "forbidden" and required_text is not None:
            errors.append({"code": "FORBIDDEN_ON_SCREEN_TEXT_PRESENT", "shot_code": shot_code})
        if not isinstance(item.get("new_information"), str) or not item["new_information"].strip():
            errors.append({"code": "SHOT_NEW_INFORMATION_REQUIRED", "shot_code": shot_code})
        requirements = item.get("generation_requirements")
        requirement_keys = {"reference_image_required", "multi_frame_required", "identity_consistency_required", "precise_text_required"}
        if not isinstance(requirements, dict) or set(requirements) != requirement_keys or any(type(value) is not bool for value in requirements.values()):
            errors.append({"code": "GENERATION_REQUIREMENTS_INVALID", "shot_code": shot_code})
        elif requirements["identity_consistency_required"] and not requirements["reference_image_required"]:
            errors.append({"code": "IDENTITY_REFERENCE_REQUIREMENT_INVALID", "shot_code": shot_code})
        elif requirements["precise_text_required"] and item.get("text_policy") != "required":
            errors.append({"code": "PRECISE_TEXT_REQUIREMENT_INVALID", "shot_code": shot_code})
        continuity_group_id = item.get("continuity_group_id")
        if continuity_group_id is not None:
            signature = (
                item.get("scene_entity_version_id"),
                tuple(item.get("character_entity_version_ids") or []),
                tuple(item.get("outfit_entity_version_ids") or []),
            )
            previous = continuity.setdefault(continuity_group_id, signature)
            if previous != signature:
                errors.append({"code": "CONTINUITY_GROUP_ENTITY_MISMATCH", "shot_code": shot_code, "continuity_group_id": continuity_group_id})
    for beat_code, target_duration in beat_durations.items():
        if actual_by_beat[beat_code] != target_duration:
            errors.append({"code": "NARRATIVE_BEAT_DURATION_MISMATCH", "narrative_beat_code": beat_code})
    missing_segments = sorted(set(script_segments) - covered_segments)
    if missing_segments:
        errors.append({"code": "SCRIPT_SEGMENT_COVERAGE_MISSING", "segment_codes": missing_segments})
    previous = None
    for item in shots:
        if previous is not None:
            scene_changed = previous.get("scene_entity_version_id") != item.get("scene_entity_version_id")
            outfit_changed = (previous.get("outfit_entity_version_ids") or []) != (item.get("outfit_entity_version_ids") or [])
            if scene_changed and item.get("continuity_relation") != "location_change":
                errors.append({"code": "SCENE_CHANGE_RELATION_REQUIRED", "shot_code": item.get("shot_code")})
            elif not scene_changed and outfit_changed and item.get("continuity_relation") != "outfit_change":
                errors.append({"code": "OUTFIT_CHANGE_RELATION_REQUIRED", "shot_code": item.get("shot_code")})
        previous = item
    for version_id in referenced:
        version = repository.entity_version(version_id)
        if not version or version.project_id != project_id or version.status != "confirmed":
            errors.append({"code": "ENTITY_VERSION_NOT_FOUND", "entity_version_id": version_id})
    for item in shots:
        for version_id in item.get("face_subject_entity_version_ids") or []:
            version = repository.entity_version(version_id)
            entity = repository.entity(version.entity_id) if version else None
            if not entity or entity.entity_type != "character":
                errors.append({"code": "FACE_SUBJECT_NOT_CHARACTER", "shot_code": item.get("shot_code"), "entity_version_id": version_id})
    for item in shots:
        primary_reference = item.get("primary_reference_entity_version_id")
        if primary_reference is None:
            continue
        version = repository.entity_version(primary_reference)
        if not version or version.project_id != project_id or version.status != "confirmed":
            continue
        if not version.source_attachment_id:
            errors.append({
                "code": "PRIMARY_REFERENCE_ATTACHMENT_REQUIRED",
                "shot_code": item.get("shot_code"),
                "entity_version_id": primary_reference,
            })
            continue
        attachment = repository.attachment(version.source_attachment_id)
        if (
            not attachment
            or attachment.project_id != project_id
            or attachment.verification_status != "verified"
            or not attachment.mime_type.startswith("image/")
        ):
            errors.append({
                "code": "PRIMARY_REFERENCE_ATTACHMENT_INVALID",
                "shot_code": item.get("shot_code"),
                "entity_version_id": primary_reference,
            })
    return errors


def generate_shot_plan(
    session: Session,
    project: Project,
    payload: GenerateShotPlan,
    gateway: DirectorGateway,
) -> ShotPlanCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, ShotPlanCandidate)
    requirement = active_requirement(session, project.id)
    brief = repository.creative_brief(payload.creative_brief_candidate_id)
    if not requirement or requirement.id != payload.expected_requirement_version_id:
        raise CreationConflictError("REQUIREMENT_VERSION_CONFLICT", "活动需求版本已变化。")
    if not brief or brief.project_id != project.id or brief.requirement_version_id != requirement.id:
        raise CreationNotFoundError("Accepted creative brief not found")
    if brief.status != "accepted":
        raise CreationConflictError("BRIEF_NOT_ACCEPTED", "创意方案必须先由用户确认。")
    existing = repository.reviewable_shot_plan_for_requirement(project.id, requirement.id)
    if existing:
        raise CreationConflictError("SHOT_PLAN_ALREADY_EXISTS", "当前需求版本已有待审分镜候选。")
    previous_attempt = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "director",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .limit(1)
    )
    if previous_attempt:
        raise CreationConflictError("DIRECTOR_ALREADY_ATTEMPTED", "当前需求版本已经运行过分镜导演；系统不会自动或重复调用模型。")
    selection = gateway.select(session)
    manifest_payload = _director_manifest_payload(session, project, requirement, brief)
    manifest_payload["system_config_version"] = selection.production_config_version_id
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=requirement.id,
        decision_ids=[item["id"] for item in manifest_payload["confirmed_decisions"]],
        attachment_binding_ids=repository.confirmed_binding_ids(project.id),
        system_config_version=selection.production_config_version_id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    return _execute_director(session, project, requirement, brief, manifest, payload, gateway, selection)


def retry_failed_shot_plan(
    session: Session,
    project: Project,
    payload: RetryShotPlan,
    gateway: DirectorGateway,
) -> ShotPlanCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, ShotPlanCandidate)
    if not payload.confirm_model_cost:
        raise CreationConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次重跑会再次调用当前分镜导演模型。")
    requirement = active_requirement(session, project.id)
    if not requirement or requirement.id != payload.expected_requirement_version_id:
        raise CreationConflictError("REQUIREMENT_VERSION_CONFLICT", "活动需求版本已变化，不能重跑旧分镜。")
    latest_run = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "director",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    if not latest_run or latest_run.id != payload.failed_agent_run_id:
        raise CreationConflictError("DIRECTOR_FAILED_RUN_NOT_LATEST", "只能重跑当前需求版本最近一次失败的分镜导演。")
    if latest_run.status != "failed":
        raise CreationConflictError("DIRECTOR_RUN_NOT_FAILED", "指定的分镜导演运行不是失败状态。")
    manifest = session.get(AgentInputManifest, latest_run.input_manifest_id)
    if not manifest or manifest.base_requirement_version_id != requirement.id:
        raise CreationConflictError("DIRECTOR_MANIFEST_MISSING", "失败运行的输入清单不存在或需求版本不一致。")
    revision_request = manifest.payload.get("revision_request")
    source_candidate = None
    if isinstance(revision_request, dict):
        source_id = revision_request.get("source_candidate_id")
        source_candidate = repository.shot_plan(source_id) if isinstance(source_id, str) else None
        if not source_candidate or source_candidate.project_id != project.id or source_candidate.status not in {"awaiting_review", "rejected"}:
            raise CreationConflictError("DIRECTOR_REVISION_SOURCE_CHANGED", "失败修订绑定的原分镜候选不再可用。")
    elif repository.reviewable_shot_plan_for_requirement(project.id, requirement.id):
        raise CreationConflictError("SHOT_PLAN_ALREADY_EXISTS", "当前需求版本已有待审分镜候选。")
    brief_id = manifest.payload.get("accepted_creative_brief", {}).get("id")
    brief = repository.creative_brief(brief_id) if isinstance(brief_id, str) else None
    if not brief or brief.project_id != project.id or brief.requirement_version_id != requirement.id or brief.status != "accepted":
        raise CreationConflictError("DIRECTOR_BRIEF_CHANGED", "失败运行绑定的已接受内容方案不再可用。")
    selection = gateway.select(session)
    expected_config = (
        latest_run.production_config_version_id,
        latest_run.model_config_version_id,
        latest_run.provider_config_version_id,
        latest_run.prompt_contract_version,
        latest_run.output_schema_version,
    )
    actual_config = (
        selection.production_config_version_id,
        selection.model_config_version_id,
        selection.provider_config_version_id,
        selection.prompt_contract_version,
        selection.output_schema_version,
    )
    if actual_config != expected_config:
        raise CreationConflictError("DIRECTOR_RETRY_CONFIG_CHANGED", "当前分镜导演配置与失败运行不一致，不能静默更换模型或合同后重跑。")
    return _execute_director(
        session, project, requirement, brief, manifest, payload, gateway, selection,
        retry_of_agent_run_id=latest_run.id,
        source_candidate=source_candidate,
    )


def revise_shot_plan(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: ReviseShotPlan,
) -> ShotPlanCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, ShotPlanCandidate)
    candidate = repository.shot_plan(candidate_id)
    requirement = active_requirement(session, project.id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Shot plan candidate not found")
    if not requirement or requirement.id != payload.expected_requirement_version_id or candidate.requirement_version_id != requirement.id:
        raise CreationConflictError("SHOT_PLAN_BASE_VERSION_STALE", "分镜候选基于旧需求版本，不能修订。")
    if candidate.status not in {"awaiting_review", "rejected", "revision_draft"}:
        raise CreationConflictError("SHOT_PLAN_NOT_REVISABLE", f"分镜候选状态为 {candidate.status}，不能修订。")
    if candidate.row_version != payload.expected_candidate_row_version:
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重新编辑。")
    targets = [item.target_shot_code for item in payload.patches]
    if len(targets) != len(set(targets)):
        raise CreationConflictError("SHOT_PATCH_TARGET_DUPLICATE", "一次修订中不能重复修改同一个镜头。")
    indexed = {str(item["shot_code"]): dict(item) for item in candidate.shots}
    missing = [target for target in targets if target not in indexed]
    if missing:
        raise CreationConflictError("SHOT_PATCH_TARGET_NOT_FOUND", f"找不到待修改镜头：{', '.join(missing)}。")
    for patch in payload.patches:
        changes = patch.changes.model_dump(exclude_unset=True)
        indexed[patch.target_shot_code].update(changes)
    revised_shots = sorted(indexed.values(), key=lambda item: int(item["sequence_number"]))
    brief = repository.creative_brief(candidate.creative_brief_candidate_id)
    errors = validate_shots(session, project.id, requirement, revised_shots, brief)
    if errors:
        codes = ", ".join(sorted({str(item.get("code")) for item in errors}))
        raise CreationConflictError("SHOT_PLAN_REVISION_INVALID", f"修订后的分镜合同验证失败：{codes}。")
    revised = ShotPlanCandidate(
        project_id=project.id,
        requirement_version_id=requirement.id,
        creative_brief_candidate_id=candidate.creative_brief_candidate_id,
        agent_run_id=None,
        supersedes_candidate_id=candidate.id,
        revision_number=candidate.revision_number + 1,
        source="user_revision",
        status="awaiting_review",
        shots=revised_shots,
        validation_errors=[],
        created_by=payload.actor_id,
    )
    repository.add(revised)
    repository.flush()
    revision_request = session.scalar(select(AssetRevisionRequest).where(
        AssetRevisionRequest.project_id == project.id,
        (
            (AssetRevisionRequest.draft_candidate_id == candidate.id)
            | (AssetRevisionRequest.resulting_candidate_id == candidate.id)
        ),
        AssetRevisionRequest.status.in_(("draft_created", "candidate_created")),
    ))
    if revision_request:
        revision_request.status = "candidate_created"
        revision_request.resulting_candidate_id = revised.id
    if not repository.transition_reviewable_shot_plan(
        candidate.id,
        payload.expected_candidate_row_version,
        "superseded",
        utc_now(),
    ):
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重新编辑。")
    _save_receipt(session, project.id, payload.command_id, "shot_plan.revise", "shot_plan_candidate", revised.id)
    _event(session, project.id, "plan.shot_candidate_revised.v1", "用户创建了新的分镜候选修订", {
        "candidate_id": revised.id,
        "supersedes_candidate_id": candidate.id,
        "revision_number": revised.revision_number,
        "changed_shot_codes": targets,
        "actor_id": payload.actor_id,
    })
    transition_project(
        session,
        project,
        ProjectStateTrigger.SHOT_CANDIDATE_REVISED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"candidate_id": revised.id},
    )
    session.commit()
    return revised


def revise_shot_plan_with_director(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: ReviseShotPlanWithDirector,
    gateway: DirectorGateway,
) -> ShotPlanCandidate:
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        return _receipt_result(session, receipt, ShotPlanCandidate)
    if not payload.confirm_model_cost:
        raise CreationConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次 AI 调整会调用当前分镜导演模型。")
    candidate = repository.shot_plan(candidate_id)
    requirement = active_requirement(session, project.id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Shot plan candidate not found")
    if not requirement or requirement.id != payload.expected_requirement_version_id or candidate.requirement_version_id != requirement.id:
        raise CreationConflictError("SHOT_PLAN_BASE_VERSION_STALE", "分镜候选基于旧需求版本，不能由导演调整。")
    if candidate.status not in {"awaiting_review", "rejected", "revision_draft"}:
        raise CreationConflictError("SHOT_PLAN_NOT_REVISABLE", f"分镜候选状态为 {candidate.status}，不能由导演调整。")
    if candidate.row_version != payload.expected_candidate_row_version:
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重新提交。")
    available_codes = {str(item["shot_code"]) for item in candidate.shots}
    missing = sorted(set(payload.selected_shot_codes) - available_codes)
    if missing:
        raise CreationConflictError("SHOT_REVISION_TARGET_NOT_FOUND", f"找不到待调整镜头：{', '.join(missing)}。")
    brief = repository.creative_brief(candidate.creative_brief_candidate_id)
    if not brief or brief.status != "accepted":
        raise CreationConflictError("BRIEF_NOT_ACCEPTED", "关联的内容方案不再是已接受状态。")
    selection = gateway.select(session)
    manifest_payload = _director_manifest_payload(session, project, requirement, brief)
    manifest_payload["system_config_version"] = selection.production_config_version_id
    manifest_payload["revision_request"] = {
        "source_candidate_id": candidate.id,
        "source_shots": candidate.shots,
        "selected_shot_codes": payload.selected_shot_codes,
        "revision_instruction": payload.revision_instruction,
    }
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=requirement.id,
        decision_ids=[item["id"] for item in manifest_payload["confirmed_decisions"]],
        attachment_binding_ids=repository.confirmed_binding_ids(project.id),
        system_config_version=selection.production_config_version_id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    return _execute_director(
        session,
        project,
        requirement,
        brief,
        manifest,
        payload,
        gateway,
        selection,
        source_candidate=candidate,
    )


def decide_shot_plan(
    session: Session,
    project: Project,
    candidate_id: str,
    payload: DecideShotPlan,
    accept: bool,
):
    repository = _planning(session)
    receipt = _receipt(session, project.id, payload.command_id)
    if receipt:
        result = _receipt_result(session, receipt, PlanVersion if accept else ShotPlanCandidate)
        return _plan_dict(session, result) if accept else result
    candidate = repository.shot_plan(candidate_id)
    requirement = active_requirement(session, project.id)
    if not candidate or candidate.project_id != project.id:
        raise CreationNotFoundError("Shot plan candidate not found")
    if not requirement or requirement.id != payload.expected_requirement_version_id or candidate.requirement_version_id != requirement.id:
        raise CreationConflictError("SHOT_PLAN_BASE_VERSION_STALE", "分镜候选基于旧需求版本，不能处理。")
    if candidate.status != "awaiting_review":
        raise CreationConflictError("SHOT_PLAN_NOT_REVIEWABLE", f"分镜候选状态为 {candidate.status}。")
    if candidate.row_version != payload.expected_candidate_row_version:
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重试。")
    if not accept:
        rejection = [{"code": "USER_REJECTED", "message": payload.reason or "用户拒绝候选"}]
        if not repository.transition_reviewable_shot_plan(
            candidate.id,
            payload.expected_candidate_row_version,
            "rejected",
            utc_now(),
            rejection,
        ):
            raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重试。")
        _save_receipt(session, project.id, payload.command_id, "shot_plan.reject", "shot_plan_candidate", candidate.id)
        _event(session, project.id, "plan.shot_candidate_rejected.v1", "用户已拒绝分镜候选", {"candidate_id": candidate.id})
        transition_project(
            session,
            project,
            ProjectStateTrigger.SHOT_PLAN_REJECTED,
            actor_type="user",
            actor_id=payload.actor_id,
            event_data={"candidate_id": candidate.id},
        )
        session.commit()
        return candidate
    brief = repository.creative_brief(candidate.creative_brief_candidate_id)
    errors = validate_shots(session, project.id, requirement, candidate.shots, brief)
    if errors:
        if not repository.transition_reviewable_shot_plan(
            candidate.id,
            payload.expected_candidate_row_version,
            "validation_failed",
            utc_now(),
            errors,
        ):
            raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重试。")
        session.commit()
        raise CreationConflictError("SHOT_PLAN_VALIDATION_FAILED", "分镜候选合同验证失败。")
    if not brief or brief.status != "accepted":
        raise CreationConflictError("BRIEF_NOT_ACCEPTED", "关联的创意方案不再可用。")
    if not repository.transition_reviewable_shot_plan(
        candidate.id,
        payload.expected_candidate_row_version,
        "accepted",
        utc_now(),
    ):
        raise CreationConflictError("SHOT_PLAN_ROW_VERSION_MISMATCH", "分镜候选已变化，请刷新后重试。")
    for plan in repository.active_plans(project.id):
        plan.is_active = False
        plan.status = "superseded"
    revision_request = session.scalar(select(AssetRevisionRequest).where(
        AssetRevisionRequest.project_id == project.id,
        AssetRevisionRequest.resulting_candidate_id == candidate.id,
        AssetRevisionRequest.status == "candidate_created",
    ))
    if revision_request and project.active_snapshot_id:
        old_snapshot = session.get(ProductionSnapshot, project.active_snapshot_id)
        if old_snapshot:
            old_snapshot.status = "superseded"
        project.active_snapshot_id = None
    version_number = repository.next_plan_version_number(project.id)
    plan = PlanVersion(
        project_id=project.id,
        version_number=version_number,
        requirement_version_id=requirement.id,
        shot_plan_candidate_id=candidate.id,
        creative_brief=brief.brief,
        contract_schema_version="shot-plan.v3",
        confirmed_by=payload.actor_id,
    )
    repository.add(plan)
    repository.flush()
    for item in candidate.shots:
        repository.add(Shot(project_id=project.id, plan_version_id=plan.id, **item))
    if revision_request:
        revision_request.status = "plan_confirmed"
        revision_request.resulting_plan_version_id = plan.id
        revision_request.resolved_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "shot_plan.accept", "plan_version", plan.id)
    _event(session, project.id, "plan.confirmed.v1", "分镜候选已提升为不可变方案版本", {
        "candidate_id": candidate.id,
        "plan_version_id": plan.id,
        "version_number": plan.version_number,
    })
    transition_project(
        session,
        project,
        ProjectStateTrigger.SHOT_PLAN_ACCEPTED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"plan_version_id": plan.id},
    )
    session.commit()
    return _plan_dict(session, plan)


def _shot_dict(shot: Shot) -> dict:
    return {
        "shot_code": shot.shot_code,
        "sequence_number": shot.sequence_number,
        "duration_ms": shot.duration_ms,
        "narrative_beat_code": shot.narrative_beat_code,
        "brief_segment_codes": shot.brief_segment_codes,
        "continuity_group_id": shot.continuity_group_id,
        "continuity_relation": shot.continuity_relation,
        "action_count": shot.action_count,
        "shot_purpose": shot.shot_purpose,
        "framing": shot.framing,
        "camera_angle": shot.camera_angle,
        "camera_motion": shot.camera_motion,
        "subject_motion": shot.subject_motion,
        "scene_entity_version_id": shot.scene_entity_version_id,
        "character_entity_version_ids": shot.character_entity_version_ids,
        "outfit_entity_version_ids": shot.outfit_entity_version_ids,
        "product_entity_version_ids": shot.product_entity_version_ids,
        "primary_reference_entity_version_id": shot.primary_reference_entity_version_id,
        "face_visibility": shot.face_visibility,
        "face_subject_entity_version_ids": shot.face_subject_entity_version_ids,
        "text_policy": shot.text_policy,
        "required_on_screen_text": shot.required_on_screen_text,
        "audio_requirement": shot.audio_requirement,
        "composition": shot.composition,
        "action": shot.action,
        "visual_prompt": shot.visual_prompt,
        "negative_prompt": shot.negative_prompt,
        "new_information": shot.new_information,
        "generation_requirements": shot.generation_requirements,
    }


def _plan_dict(session: Session, plan: PlanVersion) -> dict:
    shots = _planning(session).shots(plan.id)
    return {
        "id": plan.id,
        "version_number": plan.version_number,
        "requirement_version_id": plan.requirement_version_id,
        "shot_plan_candidate_id": plan.shot_plan_candidate_id,
        "status": plan.status,
        "creative_brief": plan.creative_brief,
        "contract_schema_version": plan.contract_schema_version,
        "is_active": plan.is_active,
        "confirmed_at": plan.confirmed_at,
        "confirmed_by": plan.confirmed_by,
        "created_at": plan.created_at,
        "shots": [_shot_dict(item) for item in shots],
    }


def planning_center_view(session: Session, project: Project) -> dict:
    repository = _planning(session)
    requirement = active_requirement(session, project.id)
    if not requirement:
        raise CreationConflictError("REQUIREMENT_NOT_FOUND", "项目没有活动需求版本。")
    briefs = repository.brief_history(project.id)
    shot_candidates = repository.shot_plan_history(project.id)
    plans = repository.plan_history(project.id)
    for item in briefs:
        if item.requirement_version_id != requirement.id and item.status == "awaiting_review":
            item.status = "stale"
            item.decided_at = utc_now()
    for item in shot_candidates:
        if item.requirement_version_id != requirement.id and item.status == "awaiting_review":
            item.status = "stale"
            item.decided_at = utc_now()
    session.commit()
    current_brief = next((item for item in briefs if item.requirement_version_id == requirement.id and item.status == "awaiting_review"), None)
    accepted_brief = next((item for item in briefs if item.requirement_version_id == requirement.id and item.status == "accepted"), None)
    current_shot = next((item for item in shot_candidates if item.requirement_version_id == requirement.id and item.status == "awaiting_review"), None)
    revision_draft = next((item for item in shot_candidates if item.requirement_version_id == requirement.id and item.status == "revision_draft"), None)
    rejected_shot = next((item for item in shot_candidates if item.requirement_version_id == requirement.id and item.status == "rejected"), None)
    active_plan = next((item for item in plans if item.requirement_version_id == requirement.id and item.is_active), None)
    planner_attempt = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "planner",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    director_attempt = session.scalar(
        select(AgentRun)
        .join(AgentInputManifest, AgentInputManifest.id == AgentRun.input_manifest_id)
        .where(
            AgentRun.project_id == project.id,
            AgentRun.agent_role == "director",
            AgentInputManifest.base_requirement_version_id == requirement.id,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    planner_manifest = session.get(AgentInputManifest, planner_attempt.input_manifest_id) if planner_attempt else None
    failed_revision = bool(
        planner_attempt
        and planner_attempt.status == "failed"
        and planner_manifest
        and planner_manifest.payload.get("revision_request")
    )
    if current_shot:
        next_action = PlanningNextAction(code="REVIEW_SHOT_PLAN", label="审核分镜候选", target_ids=[current_shot.id])
    elif revision_draft:
        next_action = PlanningNextAction(code="EDIT_ASSET_REVISION_DRAFT", label="根据成品反馈调整对应分镜", target_ids=[revision_draft.id])
    elif active_plan:
        next_action = PlanningNextAction(code="PLAN_CONFIRMED", label="方案已确认，等待创建生产快照", target_ids=[active_plan.id])
    elif rejected_shot:
        next_action = PlanningNextAction(code="REVISE_REJECTED_SHOT_PLAN", label="调整被拒绝的分镜方案", target_ids=[rejected_shot.id])
    elif accepted_brief and director_attempt and director_attempt.status == "failed":
        next_action = PlanningNextAction(
            code="RETRY_FAILED_SHOT_PLAN",
            label="确认模型调用并重跑失败的分镜导演",
            target_ids=[director_attempt.id],
            incurs_model_cost=True,
        )
    elif accepted_brief and director_attempt and director_attempt.status == "running":
        next_action = PlanningNextAction(code="WAIT_FOR_SHOT_PLAN", label="分镜导演正在生成候选", target_ids=[director_attempt.id])
    elif accepted_brief and director_attempt:
        next_action = PlanningNextAction(code="REVISE_REQUIREMENT_FOR_NEW_SHOT_PLAN", label="调整并确认需求后重新生成分镜", target_ids=[director_attempt.id])
    elif accepted_brief:
        next_action = PlanningNextAction(code="GENERATE_SHOT_PLAN", label="生成分镜候选", target_ids=[accepted_brief.id], incurs_model_cost=True)
    elif failed_revision:
        next_action = PlanningNextAction(
            code="RETRY_FAILED_CREATIVE_BRIEF",
            label="确认模型调用并重跑失败的方案调整",
            target_ids=[planner_attempt.id],
            incurs_model_cost=True,
        )
    elif current_brief:
        next_action = PlanningNextAction(code="REVIEW_CREATIVE_BRIEF", label="审核内容方案", target_ids=[current_brief.id])
    elif planner_attempt and planner_attempt.status == "failed":
        next_action = PlanningNextAction(
            code="RETRY_FAILED_CREATIVE_BRIEF",
            label="确认模型调用并重跑失败的内容策划",
            target_ids=[planner_attempt.id],
            incurs_model_cost=True,
        )
    elif planner_attempt and planner_attempt.status == "running":
        next_action = PlanningNextAction(
            code="WAIT_FOR_CREATIVE_BRIEF",
            label="内容策划正在生成方案",
            target_ids=[planner_attempt.id],
        )
    elif planner_attempt:
        next_action = PlanningNextAction(
            code="REVISE_REQUIREMENT_FOR_NEW_BRIEF",
            label="调整并确认需求后重新策划",
            target_ids=[planner_attempt.id],
        )
    else:
        next_action = PlanningNextAction(
            code="GENERATE_CREATIVE_BRIEF",
            label="生成内容方案候选",
            incurs_model_cost=True,
        )
    entity_rows = repository.active_entity_versions(project.id)
    revision_context = None
    revision_candidate = current_shot or revision_draft or rejected_shot
    if revision_candidate:
        revision_context = session.scalar(select(AssetRevisionRequest).where(
            AssetRevisionRequest.project_id == project.id,
            (
                (AssetRevisionRequest.resulting_candidate_id == revision_candidate.id)
                | (AssetRevisionRequest.draft_candidate_id == revision_candidate.id)
            ),
        ))
    return {
        "project_id": project.id,
        "active_requirement": requirement,
        "current_brief_candidate": current_brief,
        "accepted_brief_candidate": accepted_brief,
        "current_shot_candidate": current_shot,
        "revision_draft": revision_draft,
        "revision_context": revision_context,
        "active_plan": _plan_dict(session, active_plan) if active_plan else None,
        "brief_history": briefs,
        "shot_plan_history": shot_candidates,
        "plan_history": [_plan_dict(session, item) for item in plans],
        "latest_planner_run": _agent_run_view(session, planner_attempt),
        "latest_director_run": _agent_run_view(session, director_attempt),
        "entity_versions": [
            {
                "id": version.id,
                "entity_id": entity.id,
                "entity_type": entity.entity_type,
                "display_name": entity.display_name,
                "version_number": version.version_number,
                "source_attachment_id": version.source_attachment_id,
                "source_mime_type": (
                    repository.attachment(version.source_attachment_id).mime_type
                    if version.source_attachment_id and repository.attachment(version.source_attachment_id)
                    else None
                ),
                "source_attachment_verified": bool(
                    version.source_attachment_id
                    and repository.attachment(version.source_attachment_id)
                    and repository.attachment(version.source_attachment_id).verification_status == "verified"
                ),
            }
            for version, entity in entity_rows
        ],
        "next_action": next_action,
    }
