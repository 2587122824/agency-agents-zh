from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentGatewayError
from ..db.models import (
    AgentInputManifest,
    AgentRun,
    ProductionPlanCandidate,
    Project,
    ProjectEvent,
    VideoSpecVersion,
    WorkflowSlotVersion,
    utc_now,
)
from ..repositories import (
    ProductionRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyProductionRepository,
)
from .agent_gateway import ProductionPlannerGateway, ProductionPlannerResult, ProductionPlannerSelection
from .contracts import DecideProductionPlanCandidate, GenerateProductionPlanCandidate, RetryProductionPlanner
from .service import ProductionConflictError, ProductionNotFoundError, _shot_contract


def _repository(session: Session) -> ProductionRepository:
    return SqlAlchemyProductionRepository(session)


def _candidate_dict(candidate: ProductionPlanCandidate) -> dict[str, Any]:
    return {column.name: getattr(candidate, column.name) for column in candidate.__table__.columns}


def _run_dict(run: AgentRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
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
        "provider_request_id": run.provider_request_id,
        "token_usage": run.token_usage or {},
        "error_code": run.error_code,
        "error_detail": run.error_detail,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def production_planner_run_dict(run: AgentRun | None) -> dict[str, Any] | None:
    return _run_dict(run)


def _event(session: Session, project: Project, event_type: str, message: str, data: dict[str, Any], *, aggregate_type: str, aggregate_id: str, actor_type: str = "system", actor_id: str = "production-planner") -> None:
    SqlAlchemyEventRepository(session).add(ProjectEvent(
        project_id=project.id,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        actor_type=actor_type,
        actor_id=actor_id,
        message=message,
        data=data,
    ))


def _receipt(session: Session, project_id: str, command_id: str, command_type: str) -> ProductionPlanCandidate | None:
    receipt = SqlAlchemyCommandRepository(session).get(project_id, command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise ProductionConflictError("COMMAND_ID_REUSED", f"命令 ID 已用于 {receipt.command_type}。")
    result = _repository(session).production_plan_candidate(receipt.result_id)
    if not result:
        raise ProductionConflictError("COMMAND_RESULT_MISSING", "制作规划命令结果不存在。")
    return result


def _save_receipt(session: Session, project_id: str, command_id: str, command_type: str, candidate_id: str) -> None:
    SqlAlchemyCommandRepository(session).add(project_id, command_id, command_type, "production_plan_candidate", candidate_id)


def _required_sources(workflow: WorkflowSlotVersion | None) -> list[str]:
    if workflow is None:
        return []
    return sorted({
        str(item.get("value_source"))
        for item in (workflow.node_info_list or [])
        if isinstance(item, dict) and item.get("required") is True and item.get("value_source")
    })


def _input_sources(workflow: WorkflowSlotVersion | None) -> list[str]:
    if workflow is None:
        return []
    return sorted({
        str(item.get("value_source"))
        for item in (workflow.node_info_list or [])
        if isinstance(item, dict) and item.get("value_source")
    })


def _manifest_payload(
    repository: ProductionRepository,
    project: Project,
    plan,
    video_spec: VideoSpecVersion,
    workflows: list[WorkflowSlotVersion],
) -> dict[str, Any]:
    shots = repository.plan_shots(plan.id)
    return {
        "contract_version": "production-planner-input.v1",
        "project_id": project.id,
        "plan": {
            "id": plan.id,
            "contract_schema_version": plan.contract_schema_version,
            "aspect_ratio": plan.creative_brief.get("aspect_ratio"),
            "audio_mode": plan.creative_brief.get("audio_mode"),
        },
        "selected_video_spec": {
            "id": video_spec.id,
            "display_name": video_spec.display_name,
            "aspect_ratio": video_spec.aspect_ratio,
            "width": video_spec.width,
            "height": video_spec.height,
            "fps": video_spec.fps,
            "duration_min_seconds": video_spec.duration_min_seconds,
            "duration_max_seconds": video_spec.duration_max_seconds,
        },
        "shots": [_shot_contract(shot) for shot in shots],
        "available_workflow_slots": [{
            "id": workflow.id,
            "display_name": workflow.display_name,
            "operation_kind": workflow.operation_kind,
            "supported_video_spec_ids": workflow.supported_video_spec_ids or [],
            "capability_tags": workflow.capability_tags or [],
            "input_sources": _input_sources(workflow),
            "required_input_sources": _required_sources(workflow),
        } for workflow in workflows if workflow.operation_kind in {"image_generation", "video_generation", "text_to_video_generation"}],
    }


def _validate_route_assignments(
    assignments: list[dict[str, Any]],
    shots,
    workflows: list[WorkflowSlotVersion],
    video_spec: VideoSpecVersion,
    *,
    validate_reported_inputs: bool,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    workflow_by_id = {item.id: item for item in workflows}
    assignment_by_code: dict[str, dict[str, Any]] = {}
    for index, assignment in enumerate(assignments):
        code = assignment.get("shot_code")
        if code in assignment_by_code:
            errors.append({"code": "PRODUCTION_PLAN_SHOT_DUPLICATE", "path": f"assignments.{index}.shot_code", "shot_code": code})
        elif isinstance(code, str):
            assignment_by_code[code] = assignment
    expected_codes = [shot.shot_code for shot in shots]
    if [item.get("shot_code") for item in assignments] != expected_codes:
        errors.append({"code": "PRODUCTION_PLAN_SHOT_SET_MISMATCH", "message": "制作规划必须按当前分镜顺序逐镜头完整返回。"})
    for shot in shots:
        assignment = assignment_by_code.get(shot.shot_code)
        if assignment is None:
            continue
        keyframe_id = assignment.get("keyframe_workflow_slot_version_id")
        video_id = assignment.get("video_workflow_slot_version_id")
        keyframe = workflow_by_id.get(keyframe_id) if isinstance(keyframe_id, str) else None
        video = workflow_by_id.get(video_id) if isinstance(video_id, str) else None
        path = f"shots.{shot.shot_code}"
        if keyframe_id is not None and keyframe is None:
            errors.append({"code": "PRODUCTION_PLAN_KEYFRAME_SLOT_UNKNOWN", "path": path, "shot_code": shot.shot_code})
        if video is None:
            errors.append({"code": "PRODUCTION_PLAN_VIDEO_SLOT_UNKNOWN", "path": path, "shot_code": shot.shot_code})
            continue
        if keyframe and keyframe.operation_kind != "image_generation":
            errors.append({"code": "PRODUCTION_PLAN_KEYFRAME_KIND_INVALID", "path": path, "shot_code": shot.shot_code})
        if video.operation_kind not in {"video_generation", "text_to_video_generation"}:
            errors.append({"code": "PRODUCTION_PLAN_VIDEO_KIND_INVALID", "path": path, "shot_code": shot.shot_code})
        for workflow in (keyframe, video):
            if workflow and (workflow.status != "published" or video_spec.id not in (workflow.supported_video_spec_ids or [])):
                errors.append({"code": "PRODUCTION_PLAN_VIDEO_SPEC_UNSUPPORTED", "path": path, "shot_code": shot.shot_code, "workflow_slot_version_id": workflow.id})
        if video.operation_kind == "video_generation" and keyframe is None:
            errors.append({"code": "PRODUCTION_PLAN_I2V_KEYFRAME_REQUIRED", "path": path, "shot_code": shot.shot_code})
        if video.operation_kind == "text_to_video_generation" and keyframe is not None:
            errors.append({"code": "PRODUCTION_PLAN_T2V_KEYFRAME_NOT_ALLOWED", "path": path, "shot_code": shot.shot_code})
        required_sources = sorted(set(_required_sources(keyframe) + _required_sources(video)))
        if validate_reported_inputs:
            reported_sources = assignment.get("required_input_sources") or []
            if len(reported_sources) != len(set(reported_sources)):
                errors.append({
                    "code": "PRODUCTION_PLAN_REQUIRED_INPUTS_DUPLICATE",
                    "path": path,
                    "shot_code": shot.shot_code,
                    "actual": reported_sources,
                })
            elif set(reported_sources) != set(required_sources):
                errors.append({
                    "code": "PRODUCTION_PLAN_REQUIRED_INPUTS_MISMATCH",
                    "path": path,
                    "shot_code": shot.shot_code,
                    "expected": required_sources,
                    "actual": reported_sources,
                })
        requirements = shot.generation_requirements or {}
        keyframe_sources = set(_input_sources(keyframe))
        keyframe_tags = set(keyframe.capability_tags or []) if keyframe else set()
        video_tags = set(video.capability_tags or [])
        if requirements.get("reference_image_required") and "reference_image.primary" not in keyframe_sources:
            errors.append({"code": "PRODUCTION_PLAN_REFERENCE_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code})
        if requirements.get("reference_image_required") and not shot.primary_reference_entity_version_id:
            errors.append({"code": "PRODUCTION_PLAN_PRIMARY_REFERENCE_MISSING", "path": path, "shot_code": shot.shot_code})
        if requirements.get("identity_consistency_required") and ("reference_image.primary" not in keyframe_sources or not shot.primary_reference_entity_version_id):
            errors.append({"code": "PRODUCTION_PLAN_IDENTITY_ROUTE_INVALID", "path": path, "shot_code": shot.shot_code})
        if requirements.get("multi_frame_required") and "multi_frame" not in video_tags:
            errors.append({"code": "PRODUCTION_PLAN_MULTI_FRAME_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code})
        if requirements.get("precise_text_required") and "precise_text" not in keyframe_tags:
            errors.append({"code": "PRODUCTION_PLAN_PRECISE_TEXT_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code})
    unknown_codes = sorted(set(assignment_by_code) - set(expected_codes))
    for code in unknown_codes:
        errors.append({"code": "PRODUCTION_PLAN_SHOT_UNKNOWN", "shot_code": code})
    return errors


def _route_feasibility_errors(
    shots,
    workflows: list[WorkflowSlotVersion],
    video_spec: VideoSpecVersion,
) -> list[dict[str, Any]]:
    """Prove that every shot has at least one valid configured route before invoking a model."""
    keyframes = [item for item in workflows if item.operation_kind == "image_generation"]
    videos = [
        item for item in workflows
        if item.operation_kind in {"video_generation", "text_to_video_generation"}
    ]
    errors: list[dict[str, Any]] = []
    for shot in shots:
        feasible = False
        for video in videos:
            keyframe_options = [None] if video.operation_kind == "text_to_video_generation" else keyframes
            for keyframe in keyframe_options:
                assignment = {
                    "shot_code": shot.shot_code,
                    "keyframe_workflow_slot_version_id": keyframe.id if keyframe else None,
                    "video_workflow_slot_version_id": video.id,
                }
                if not _validate_route_assignments(
                    [assignment], [shot], workflows, video_spec, validate_reported_inputs=False,
                ):
                    feasible = True
                    break
            if feasible:
                break
        if feasible:
            continue

        requirements = shot.generation_requirements or {}
        causes: list[str] = []
        supported_keyframes = [
            item for item in keyframes
            if item.status == "published" and video_spec.id in (item.supported_video_spec_ids or [])
        ]
        supported_videos = [
            item for item in videos
            if item.status == "published" and video_spec.id in (item.supported_video_spec_ids or [])
        ]
        if not supported_videos:
            causes.append("PRODUCTION_PLAN_VIDEO_SPEC_UNSUPPORTED")
        if requirements.get("precise_text_required") and not any(
            "precise_text" in (item.capability_tags or []) for item in supported_keyframes
        ):
            causes.append("PRODUCTION_PLAN_PRECISE_TEXT_CAPABILITY_MISSING")
        if requirements.get("multi_frame_required") and not any(
            "multi_frame" in (item.capability_tags or []) for item in supported_videos
        ):
            causes.append("PRODUCTION_PLAN_MULTI_FRAME_CAPABILITY_MISSING")
        if requirements.get("reference_image_required") or requirements.get("identity_consistency_required"):
            if not shot.primary_reference_entity_version_id:
                causes.append("PRODUCTION_PLAN_PRIMARY_REFERENCE_MISSING")
            if not any(
                "reference_image.primary" in _input_sources(item) for item in supported_keyframes
            ):
                causes.append("PRODUCTION_PLAN_REFERENCE_CAPABILITY_MISSING")
        errors.append({
            "code": "PRODUCTION_PLAN_NO_FEASIBLE_ROUTE",
            "shot_code": shot.shot_code,
            "causes": sorted(set(causes)) or ["PRODUCTION_PLAN_WORKFLOW_COMBINATION_INVALID"],
        })
    return errors


def _execute(
    session: Session,
    project: Project,
    manifest: AgentInputManifest,
    gateway: ProductionPlannerGateway,
    selection: ProductionPlannerSelection,
    *,
    retry_of_agent_run_id: str | None = None,
) -> ProductionPlanCandidate:
    repository = _repository(session)
    run = AgentRun(
        project_id=project.id,
        agent_role="production_planner",
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
    _event(session, project, "agent.run_created.v1", "制作规划智能体运行已开始", {
        "agent_role": "production_planner", "retry_of_agent_run_id": retry_of_agent_run_id,
    }, aggregate_type="agent_run", aggregate_id=run.id)
    try:
        result: ProductionPlannerResult = gateway.invoke(selection, manifest.payload)
        plan = repository.plan(manifest.payload["plan"]["id"])
        video_spec = repository.component(VideoSpecVersion, manifest.payload["selected_video_spec"]["id"])
        if not plan or not video_spec:
            raise AgentGatewayError("PRODUCTION_PLANNER_MANIFEST_STALE", "制作规划输入绑定的方案或画面规格不存在。", raw_output=result.raw_output)
        workflows = repository.workflows(selection.production_config_version_id)
        assignments = [item.model_dump(mode="json") for item in result.output.assignments]
        validation_errors = _validate_route_assignments(
            assignments, repository.plan_shots(plan.id), workflows, video_spec, validate_reported_inputs=True,
        )
        if validation_errors:
            raise AgentGatewayError(
                "PRODUCTION_PLANNER_OUTPUT_CONTRACT_INVALID",
                "制作规划智能体返回了不满足分镜能力要求的路线。",
                raw_output=result.raw_output,
                diagnostics=validation_errors,
            )
        candidate = ProductionPlanCandidate(
            project_id=project.id,
            plan_version_id=plan.id,
            production_config_version_id=selection.production_config_version_id,
            video_spec_version_id=video_spec.id,
            agent_run_id=run.id,
            status="awaiting_review",
            proposed_assignments=assignments,
            validation_errors=[],
        )
        repository.add(candidate)
        repository.flush()
        run.status = "succeeded"
        run.raw_output = result.raw_output
        run.provider_request_id = result.provider_request_id
        run.token_usage = result.token_usage
        run.parsed_candidate_id = candidate.id
        run.finished_at = utc_now()
        _event(session, project, "production.plan_candidate_created.v1", "制作规划候选已生成，等待用户确认", {
            "candidate_id": candidate.id, "plan_version_id": plan.id,
            "production_config_version_id": selection.production_config_version_id,
            "video_spec_version_id": video_spec.id,
        }, aggregate_type="production_plan_candidate", aggregate_id=candidate.id)
        session.commit()
        return candidate
    except AgentGatewayError as exc:
        run.status = "failed"
        run.error_code = exc.code
        run.error_detail = str(exc)
        run.raw_output = exc.raw_output
        run.finished_at = utc_now()
        _event(session, project, "agent.run_failed.v1", "制作规划智能体运行失败", {
            "agent_role": "production_planner", "error_code": exc.code,
            "diagnostics": exc.diagnostics,
        }, aggregate_type="agent_run", aggregate_id=run.id)
        session.commit()
        raise


def generate_production_plan_candidate(session: Session, project: Project, payload: GenerateProductionPlanCandidate, gateway: ProductionPlannerGateway) -> ProductionPlanCandidate:
    existing_receipt = _receipt(session, project.id, payload.command_id, "production_plan.generate")
    if existing_receipt:
        return existing_receipt
    repository = _repository(session)
    plan = repository.plan(payload.plan_version_id)
    if not plan or plan.project_id != project.id:
        raise ProductionNotFoundError("Plan version not found in project")
    if not plan.is_active or plan.status != "confirmed" or plan.contract_schema_version != "shot-plan.v3":
        raise ProductionConflictError("PRODUCTION_PLANNER_PLAN_INVALID", "制作规划只能读取当前已确认的新版分镜方案。")
    config = repository.configuration(payload.production_config_version_id)
    if not config or config.status != "published":
        raise ProductionConflictError("PRODUCTION_PLANNER_CONFIGURATION_INVALID", "制作规划必须绑定已发布制作配置。")
    video_spec = repository.component(VideoSpecVersion, payload.video_spec_version_id)
    if not video_spec or video_spec.production_config_version_id != config.id or video_spec.status != "published":
        raise ProductionConflictError("PRODUCTION_PLANNER_VIDEO_SPEC_INVALID", "所选画面规格不属于当前已发布制作配置。")
    if video_spec.aspect_ratio != plan.creative_brief.get("aspect_ratio"):
        raise ProductionConflictError("PRODUCTION_PLANNER_ASPECT_RATIO_MISMATCH", "画面规格与已确认项目画幅不一致。")
    for candidate in repository.production_plan_candidates(project.id, plan.id):
        if candidate.production_config_version_id == config.id and candidate.video_spec_version_id == video_spec.id and candidate.status in {"awaiting_review", "accepted"}:
            raise ProductionConflictError("PRODUCTION_PLAN_CANDIDATE_ALREADY_EXISTS", "当前方案、制作配置和画面规格已有可用的制作规划候选。")
    for run in repository.production_planner_runs(project.id, plan.id):
        manifest = repository.agent_manifest(run.input_manifest_id)
        if manifest and manifest.payload.get("selected_video_spec", {}).get("id") == video_spec.id and run.status == "failed":
            raise ProductionConflictError("PRODUCTION_PLANNER_ALREADY_ATTEMPTED", "同一制作规划已经失败，请确认模型调用费用后精确重跑。")
    selection = gateway.select(session, config.id)
    if selection.production_config_version_id != config.id:
        raise ProductionConflictError("PRODUCTION_PLANNER_CONFIGURATION_CHANGED", "制作规划模型不属于用户选择的制作配置。")
    workflows = repository.workflows(config.id)
    feasibility_errors = _route_feasibility_errors(
        repository.plan_shots(plan.id), workflows, video_spec,
    )
    if feasibility_errors:
        shot_codes = ", ".join(item["shot_code"] for item in feasibility_errors)
        cause_codes = ", ".join(sorted({
            cause for item in feasibility_errors for cause in item["causes"]
        }))
        raise ProductionConflictError(
            "PRODUCTION_PLAN_NO_FEASIBLE_ROUTE",
            f"当前制作配置无法满足这些镜头的能力要求：{shot_codes}。冲突：{cause_codes}。请先调整分镜能力要求或发布具备对应能力的工作流。",
        )
    manifest_payload = _manifest_payload(repository, project, plan, video_spec, workflows)
    serialized = json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=plan.requirement_version_id,
        system_config_version=config.id,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=manifest_payload,
    )
    repository.add(manifest)
    repository.flush()
    candidate = _execute(session, project, manifest, gateway, selection)
    _save_receipt(session, project.id, payload.command_id, "production_plan.generate", candidate.id)
    session.commit()
    return candidate


def retry_production_planner(session: Session, project: Project, payload: RetryProductionPlanner, gateway: ProductionPlannerGateway) -> ProductionPlanCandidate:
    existing_receipt = _receipt(session, project.id, payload.command_id, "production_plan.retry")
    if existing_receipt:
        return existing_receipt
    if not payload.confirm_model_cost:
        raise ProductionConflictError("MODEL_COST_CONFIRMATION_REQUIRED", "请明确确认本次重跑会再次调用同一个制作规划模型。")
    repository = _repository(session)
    failed = repository.agent_run(payload.failed_agent_run_id)
    if not failed or failed.project_id != project.id or failed.agent_role != "production_planner" or failed.status != "failed":
        raise ProductionConflictError("PRODUCTION_PLANNER_FAILED_RUN_INVALID", "只能重跑当前项目中明确失败的制作规划运行。")
    manifest = repository.agent_manifest(failed.input_manifest_id)
    if not manifest:
        raise ProductionConflictError("PRODUCTION_PLANNER_MANIFEST_MISSING", "失败运行的冻结输入清单不存在。")
    plan_id = manifest.payload.get("plan", {}).get("id")
    latest = repository.latest_production_planner_run(project.id, plan_id)
    if not latest or latest.id != failed.id:
        raise ProductionConflictError("PRODUCTION_PLANNER_FAILED_RUN_NOT_LATEST", "只能重跑该方案最近一次失败的制作规划运行。")
    config_id = failed.production_config_version_id
    if not config_id:
        raise ProductionConflictError("PRODUCTION_PLANNER_CONFIG_MISSING", "失败运行没有冻结制作配置。")
    selection = gateway.select(session, config_id)
    expected = (failed.production_config_version_id, failed.model_config_version_id, failed.provider_config_version_id, failed.prompt_contract_version, failed.output_schema_version)
    actual = (selection.production_config_version_id, selection.model_config_version_id, selection.provider_config_version_id, selection.prompt_contract_version, selection.output_schema_version)
    if actual != expected:
        raise ProductionConflictError("PRODUCTION_PLANNER_RETRY_CONFIG_CHANGED", "当前配置与失败运行不一致，不能静默更换模型或合同。")
    candidate = _execute(session, project, manifest, gateway, selection, retry_of_agent_run_id=failed.id)
    _save_receipt(session, project.id, payload.command_id, "production_plan.retry", candidate.id)
    session.commit()
    return candidate


def decide_production_plan_candidate(session: Session, project: Project, candidate_id: str, payload: DecideProductionPlanCandidate) -> ProductionPlanCandidate:
    existing_receipt = _receipt(session, project.id, payload.command_id, "production_plan.decide")
    if existing_receipt:
        return existing_receipt
    repository = _repository(session)
    candidate = repository.production_plan_candidate(candidate_id)
    if not candidate or candidate.project_id != project.id:
        raise ProductionNotFoundError("Production plan candidate not found")
    if candidate.status != "awaiting_review" or candidate.row_version != payload.expected_row_version:
        raise ProductionConflictError("PRODUCTION_PLAN_CANDIDATE_CHANGED", "制作规划候选已经处理或版本已变化。")
    if payload.accept:
        if not payload.confirm_candidate_scope or payload.confirmed_assignments is None:
            raise ProductionConflictError("PRODUCTION_PLAN_CONFIRMATION_REQUIRED", "请明确确认逐镜头制作路线。")
        plan = repository.plan(candidate.plan_version_id)
        video_spec = repository.component(VideoSpecVersion, candidate.video_spec_version_id)
        if not plan or not plan.is_active or not video_spec:
            raise ProductionConflictError("PRODUCTION_PLAN_SOURCE_CHANGED", "候选绑定的正式分镜或画面规格已经变化。")
        assignments = [item.model_dump(mode="json") for item in payload.confirmed_assignments]
        errors = _validate_route_assignments(
            assignments,
            repository.plan_shots(plan.id),
            repository.workflows(candidate.production_config_version_id),
            video_spec,
            validate_reported_inputs=False,
        )
        if errors:
            raise ProductionConflictError("PRODUCTION_PLAN_CONFIRMED_ROUTE_INVALID", json.dumps(errors, ensure_ascii=False))
        candidate.confirmed_assignments = assignments
        candidate.status = "accepted"
    else:
        if payload.confirmed_assignments is not None:
            raise ProductionConflictError("PRODUCTION_PLAN_REJECTION_HAS_ASSIGNMENTS", "拒绝候选时不能同时提交制作路线。")
        candidate.status = "rejected"
    candidate.row_version += 1
    candidate.decided_at = utc_now()
    _save_receipt(session, project.id, payload.command_id, "production_plan.decide", candidate.id)
    _event(session, project, "production.plan_candidate_decided.v1", "用户已处理制作规划候选", {
        "candidate_id": candidate.id, "decision": candidate.status,
    }, aggregate_type="production_plan_candidate", aggregate_id=candidate.id, actor_type="user", actor_id=payload.actor_id)
    session.commit()
    return candidate
