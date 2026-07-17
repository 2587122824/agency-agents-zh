from __future__ import annotations

import hashlib
import json

from sqlalchemy.orm import Session

from ..creation.completeness import evaluate_requirement
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
    CreativeBriefCandidate,
    PlanVersion,
    Project,
    RequirementVersion,
    Shot,
    ShotPlanCandidate,
    utc_now,
)
from ..repositories import PlanningRepository, SqlAlchemyDecisionRepository, SqlAlchemyPlanningRepository
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from .contracts import DecideBrief, DecideShotPlan, GenerateBrief, GenerateShotPlan, PlanningNextAction, ReviseShotPlan


def _planning(session: Session) -> PlanningRepository:
    return SqlAlchemyPlanningRepository(session)


def _confirmed_binding_versions(session: Session, project_id: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {
        "identity_reference": [],
        "outfit_reference": [],
        "scene_reference": [],
        "product_reference": [],
        "voice_sample": [],
    }
    rows = _planning(session).confirmed_binding_versions(project_id)
    for row in rows:
        if row.binding_type in result and row.entity_version_id not in result[row.binding_type]:
            result[row.binding_type].append(row.entity_version_id)
    return result


def _create_manifest(
    session: Session,
    project: Project,
    requirement: RequirementVersion,
    role: str,
    extra: dict | None = None,
) -> AgentInputManifest:
    repository = _planning(session)
    bindings = _confirmed_binding_versions(session, project.id)
    decisions = SqlAlchemyDecisionRepository(session).resolved_for_project(project.id)
    payload = {
        "active_requirement": {"id": requirement.id, "fields": requirement.fields},
        "confirmed_entity_versions": bindings,
        "confirmed_decisions": [
            {"id": item.id, "key": item.key, "label": item.label, "value": item.value, "source": item.source}
            for item in decisions
        ],
        "system_config_version": "v2.creation.mock.v1",
        **(extra or {}),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    binding_ids = repository.confirmed_binding_ids(project.id)
    manifest = AgentInputManifest(
        project_id=project.id,
        base_requirement_version_id=requirement.id,
        decision_ids=[item.id for item in decisions],
        attachment_binding_ids=binding_ids,
        input_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        payload=payload,
        system_config_version=f"v2.{role}.mock.v1",
    )
    repository.add(manifest)
    repository.flush()
    return manifest


def _start_run(session: Session, project: Project, manifest: AgentInputManifest, role: str, schema: str) -> AgentRun:
    repository = _planning(session)
    run = AgentRun(
        project_id=project.id,
        agent_role=role,
        status="running",
        input_manifest_id=manifest.id,
        model_provider="mock",
        model_name=f"deterministic-{role}-v1",
        prompt_contract_version=f"{role}.v1",
        output_schema_version=schema,
        started_at=utc_now(),
    )
    repository.add(run)
    repository.flush()
    _event(session, project.id, "agent.run_created.v1", f"{role.title()} Mock Agent 已开始", {"agent_run_id": run.id})
    return run


def _finish_run(session: Session, project: Project, run: AgentRun, candidate_id: str, raw_output: dict) -> None:
    run.status = "succeeded"
    run.parsed_candidate_id = candidate_id
    run.raw_output = raw_output
    run.finished_at = utc_now()
    _event(session, project.id, "agent.run_succeeded.v1", f"{run.agent_role.title()} Mock Agent 已返回候选", {
        "agent_run_id": run.id,
        "candidate_id": candidate_id,
    })


def generate_brief(session: Session, project: Project, payload: GenerateBrief) -> CreativeBriefCandidate:
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
    manifest = _create_manifest(session, project, requirement, "creative")
    run = _start_run(session, project, manifest, "creative", "creative-brief.v1")
    bindings = manifest.payload["confirmed_entity_versions"]
    brief = {
        "core_intent": requirement.fields["core_topic"],
        "duration_seconds": requirement.fields["duration_seconds"],
        "aspect_ratio": requirement.fields["aspect_ratio"],
        "audio_mode": requirement.fields["audio_mode"],
        "narrative_structure": ["建立主题", "展开主题", "收束主题"],
        "visual_style": None,
        "character_refs": bindings["identity_reference"],
        "outfit_refs": bindings["outfit_reference"],
        "scene_refs": bindings["scene_reference"],
        "product_refs": bindings["product_reference"],
        "voice_refs": bindings["voice_sample"],
        "assumptions": [],
    }
    sources = {
        "core_intent": {"type": "requirement", "reference_id": requirement.id},
        "duration_seconds": {"type": "requirement", "reference_id": requirement.id},
        "aspect_ratio": {"type": "requirement", "reference_id": requirement.id},
        "audio_mode": {"type": "requirement", "reference_id": requirement.id},
        "narrative_structure": {"type": "agent_proposal", "reference_id": run.id},
        "visual_style": {"type": "unspecified", "reference_id": None},
        "character_refs": {"type": "confirmed_binding", "reference_id": None},
        "outfit_refs": {"type": "confirmed_binding", "reference_id": None},
        "scene_refs": {"type": "confirmed_binding", "reference_id": None},
        "product_refs": {"type": "confirmed_binding", "reference_id": None},
        "voice_refs": {"type": "confirmed_binding", "reference_id": None},
    }
    candidate = CreativeBriefCandidate(
        project_id=project.id,
        requirement_version_id=requirement.id,
        agent_run_id=run.id,
        brief=brief,
        field_sources=sources,
    )
    repository.add(candidate)
    repository.flush()
    _finish_run(session, project, run, candidate.id, {"creative_brief_candidate": brief})
    _save_receipt(session, project.id, payload.command_id, "brief.generate", "creative_brief_candidate", candidate.id)
    _event(session, project.id, "plan.brief_candidate_created.v1", "创意方案候选等待用户审核", {"candidate_id": candidate.id})
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


def _build_shots(requirement: RequirementVersion, brief: CreativeBriefCandidate) -> list[dict]:
    total_ms = int(requirement.fields["duration_seconds"]) * 1000
    first = total_ms * 30 // 100
    second = total_ms * 40 // 100
    durations = [first, second, total_ms - first - second]
    characters = list(brief.brief.get("character_refs") or [])
    outfits = list(brief.brief.get("outfit_refs") or [])
    scenes = list(brief.brief.get("scene_refs") or [])
    products = list(brief.brief.get("product_refs") or [])
    core = str(brief.brief["core_intent"])
    phases = [
        ("建立镜头", "建立主题", "moderate"),
        ("主体镜头", "展开主题", "significant"),
        ("收束镜头", "收束主题", "moderate"),
    ]
    return [
        {
            "shot_code": f"SH-{index:03d}",
            "sequence_number": index,
            "duration_ms": durations[index - 1],
            "shot_type": "character_action" if characters else "concept",
            "scene_entity_version_id": scenes[0] if scenes else None,
            "character_entity_version_ids": characters,
            "outfit_entity_version_ids": outfits,
            "product_entity_version_ids": products,
            "primary_reference_entity_version_id": None,
            "face_visibility": "optional" if characters else "not_visible",
            "text_policy": "forbidden",
            "motion_requirement": phase[2],
            "composition": phase[0],
            "action": f"{core}：{phase[1]}",
            "visual_prompt": f"{core}。{phase[1]}，{phase[0]}。",
            "negative_prompt": None,
        }
        for index, phase in enumerate(phases, start=1)
    ]


def validate_shots(session: Session, project_id: str, requirement: RequirementVersion, shots: list[dict]) -> list[dict]:
    repository = _planning(session)
    errors: list[dict] = []
    if not shots:
        return [{"code": "SHOTS_REQUIRED", "field": "shots"}]
    expected_duration = int(requirement.fields["duration_seconds"]) * 1000
    if sum(int(item.get("duration_ms", 0)) for item in shots) != expected_duration:
        errors.append({"code": "SHOT_DURATION_MISMATCH", "field": "duration_ms"})
    codes = [item.get("shot_code") for item in shots]
    sequences = [item.get("sequence_number") for item in shots]
    if len(codes) != len(set(codes)) or len(sequences) != len(set(sequences)) or sequences != list(range(1, len(shots) + 1)):
        errors.append({"code": "SHOT_ID_OR_SEQUENCE_INVALID", "field": "shot_code"})
    referenced = set()
    for item in shots:
        shot_code = item.get("shot_code")
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
        if item.get("text_policy") not in {"forbidden", "allowed", "required"}:
            errors.append({"code": "TEXT_POLICY_INVALID", "shot_code": item.get("shot_code")})
        if item.get("motion_requirement") not in {"static", "moderate", "significant"}:
            errors.append({"code": "MOTION_REQUIREMENT_INVALID", "shot_code": item.get("shot_code")})
    for version_id in referenced:
        version = repository.entity_version(version_id)
        if not version or version.project_id != project_id or version.status != "confirmed":
            errors.append({"code": "ENTITY_VERSION_NOT_FOUND", "entity_version_id": version_id})
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


def generate_shot_plan(session: Session, project: Project, payload: GenerateShotPlan) -> ShotPlanCandidate:
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
    manifest = _create_manifest(session, project, requirement, "director", {
        "accepted_creative_brief": {"id": brief.id, "brief": brief.brief},
    })
    run = _start_run(session, project, manifest, "director", "shot-plan.v2")
    shots = _build_shots(requirement, brief)
    errors = validate_shots(session, project.id, requirement, shots)
    candidate = ShotPlanCandidate(
        project_id=project.id,
        requirement_version_id=requirement.id,
        creative_brief_candidate_id=brief.id,
        agent_run_id=run.id,
        revision_number=1,
        source="director_agent",
        status="validation_failed" if errors else "awaiting_review",
        shots=shots,
        validation_errors=errors,
        created_by=payload.actor_id,
    )
    repository.add(candidate)
    repository.flush()
    _finish_run(session, project, run, candidate.id, {"shot_plan_candidate": shots})
    if errors:
        run.status = "validation_failed"
        _event(session, project.id, "plan.shot_candidate_validation_failed.v1", "分镜候选合同验证失败", {
            "candidate_id": candidate.id,
            "errors": errors,
        })
    else:
        _event(session, project.id, "plan.shot_candidate_created.v1", "分镜候选等待用户审核", {"candidate_id": candidate.id})
        transition_project(
            session,
            project,
            ProjectStateTrigger.SHOT_CANDIDATE_CREATED,
            actor_type="system",
            actor_id="director-agent",
            event_data={"candidate_id": candidate.id},
        )
    _save_receipt(session, project.id, payload.command_id, "shot_plan.generate", "shot_plan_candidate", candidate.id)
    session.commit()
    return candidate


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
    if candidate.status != "awaiting_review":
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
    errors = validate_shots(session, project.id, requirement, revised_shots)
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
    errors = validate_shots(session, project.id, requirement, candidate.shots)
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
    brief = repository.creative_brief(candidate.creative_brief_candidate_id)
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
    version_number = repository.next_plan_version_number(project.id)
    plan = PlanVersion(
        project_id=project.id,
        version_number=version_number,
        requirement_version_id=requirement.id,
        shot_plan_candidate_id=candidate.id,
        creative_brief=brief.brief,
        contract_schema_version="shot-plan.v2",
        confirmed_by=payload.actor_id,
    )
    repository.add(plan)
    repository.flush()
    for item in candidate.shots:
        repository.add(Shot(project_id=project.id, plan_version_id=plan.id, **item))
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
        "shot_type": shot.shot_type,
        "scene_entity_version_id": shot.scene_entity_version_id,
        "character_entity_version_ids": shot.character_entity_version_ids,
        "outfit_entity_version_ids": shot.outfit_entity_version_ids,
        "product_entity_version_ids": shot.product_entity_version_ids,
        "primary_reference_entity_version_id": shot.primary_reference_entity_version_id,
        "face_visibility": shot.face_visibility,
        "text_policy": shot.text_policy,
        "motion_requirement": shot.motion_requirement,
        "composition": shot.composition,
        "action": shot.action,
        "visual_prompt": shot.visual_prompt,
        "negative_prompt": shot.negative_prompt,
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
    active_plan = next((item for item in plans if item.requirement_version_id == requirement.id and item.is_active), None)
    if active_plan:
        next_action = PlanningNextAction(code="PLAN_CONFIRMED", label="方案已确认，等待创建生产快照", target_ids=[active_plan.id])
    elif current_shot:
        next_action = PlanningNextAction(code="REVIEW_SHOT_PLAN", label="审核分镜候选", target_ids=[current_shot.id])
    elif accepted_brief:
        next_action = PlanningNextAction(code="GENERATE_SHOT_PLAN", label="生成分镜候选", target_ids=[accepted_brief.id])
    elif current_brief:
        next_action = PlanningNextAction(code="REVIEW_CREATIVE_BRIEF", label="审核创意方案", target_ids=[current_brief.id])
    else:
        next_action = PlanningNextAction(code="GENERATE_CREATIVE_BRIEF", label="生成创意方案候选")
    entity_rows = repository.active_entity_versions(project.id)
    return {
        "project_id": project.id,
        "active_requirement": requirement,
        "current_brief_candidate": current_brief,
        "accepted_brief_candidate": accepted_brief,
        "current_shot_candidate": current_shot,
        "active_plan": _plan_dict(session, active_plan) if active_plan else None,
        "brief_history": briefs,
        "shot_plan_history": shot_candidates,
        "plan_history": [_plan_dict(session, item) for item in plans],
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
