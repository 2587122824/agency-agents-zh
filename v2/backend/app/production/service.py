from __future__ import annotations

import hashlib
import json
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import PurePath, PurePosixPath

from sqlalchemy.orm import Session

from ..core.config import RUNTIME_ROOT
from ..creation.service import detect_media_type
from ..db.models import (
    ConfigurationReference,
    CostEvent,
    DAGNode,
    DependencyEdge,
    PlanVersion,
    ProductionConfigVersion,
    ProductionImpactAnalysis,
    ProductionSnapshot,
    PricingCatalogVersion,
    ProviderConfigVersion,
    Project,
    ProjectEvent,
    Shot,
    SnapshotEntityVersion,
    StoragePolicyVersion,
    VideoSpecVersion,
    WorkflowSlotVersion,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from ..repositories import (
    ProductionRepository,
    SqlAlchemyCommandRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyProductionRepository,
)
from ..orchestration.project_transitions import ProjectStateTrigger, transition_project
from .contracts import (
    ActivateProductionSnapshot,
    AnalyzeProductionImpact,
    CreateProductionSnapshot,
    LockProductionSnapshot,
    SubmitProduction,
)


class ProductionConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProductionNotFoundError(ValueError):
    pass


def _production(session: Session) -> ProductionRepository:
    return SqlAlchemyProductionRepository(session)


def _event(session: Session, event: ProjectEvent) -> None:
    SqlAlchemyEventRepository(session).add(event)


def _hash(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _receipt(session: Session, project_id: str, command_id: str, command_type: str):
    receipt = SqlAlchemyCommandRepository(session).get(project_id, command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise ProductionConflictError("COMMAND_ID_REUSED", f"命令 ID 已用于 {receipt.command_type}。")
    return receipt


def _save_receipt(session: Session, project_id: str, command_id: str, command_type: str, result_type: str, result_id: str):
    SqlAlchemyCommandRepository(session).add(
        project_id,
        command_id,
        command_type,
        result_type,
        result_id,
    )


def _snapshot_contract(manifest: dict) -> dict:
    return {"schema_version": "production-snapshot.v2", **manifest}


def _impact_dict(row: ProductionImpactAnalysis) -> dict:
    result = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    result["snapshot_contract_hash"] = _hash(_snapshot_contract(row.manifest))
    return result


def _snapshot_dict(session: Session, row: ProductionSnapshot) -> dict:
    repository = _production(session)
    entities = repository.snapshot_entities(row.id)
    nodes = repository.snapshot_nodes(row.id, ordered=True)
    edges = repository.snapshot_edges(row.id)
    result = {column.name: getattr(row, column.name) for column in row.__table__.columns}
    result["entity_versions"] = [{"entity_version_id": item.entity_version_id, "role": item.role} for item in entities]
    result["nodes"] = [{column.name: getattr(item, column.name) for column in item.__table__.columns if column.name not in {"snapshot_id", "created_at"}} for item in nodes]
    result["edges"] = [{column.name: getattr(item, column.name) for column in item.__table__.columns if column.name not in {"snapshot_id", "created_at"}} for item in edges]
    return result


def _component(session: Session, model, component_id: str, config_id: str, label: str, errors: list[dict]):
    row = _production(session).component(model, component_id)
    if not row or row.production_config_version_id != config_id:
        errors.append({"code": f"{label.upper()}_NOT_IN_CONFIGURATION", "path": label, "message": f"{label} 不属于所选配置版本。"})
        return None
    if row.status != "published":
        errors.append({"code": f"{label.upper()}_NOT_PUBLISHED", "path": label, "message": f"{label} 不是已发布组件版本。"})
    return row


def _shot_contract(shot: Shot) -> dict:
    return {
        "shot_id": shot.id,
        "shot_code": shot.shot_code,
        "sequence_number": shot.sequence_number,
        "duration_ms": shot.duration_ms,
        "narrative_beat_code": shot.narrative_beat_code,
        "brief_segment_codes": shot.brief_segment_codes,
        "shot_purpose": shot.shot_purpose,
        "framing": shot.framing,
        "camera_angle": shot.camera_angle,
        "camera_motion": shot.camera_motion,
        "subject_motion": shot.subject_motion,
        "continuity_relation": shot.continuity_relation,
        "scene_entity_version_id": shot.scene_entity_version_id,
        "character_entity_version_ids": shot.character_entity_version_ids,
        "outfit_entity_version_ids": shot.outfit_entity_version_ids,
        "product_entity_version_ids": shot.product_entity_version_ids,
        "primary_reference_entity_version_id": shot.primary_reference_entity_version_id,
        "face_visibility": shot.face_visibility,
        "face_subject_entity_version_ids": shot.face_subject_entity_version_ids,
        "text_policy": shot.text_policy,
        "required_on_screen_text": shot.required_on_screen_text,
        "composition": shot.composition,
        "action": shot.action,
        "visual_prompt": shot.visual_prompt,
        "negative_prompt": shot.negative_prompt,
        "new_information": shot.new_information,
        "generation_requirements": shot.generation_requirements,
    }


def _attachment_uri(storage_path: str) -> str:
    relative = PurePath(storage_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("attachment storage path is not a safe relative path")
    return f"runtime://attachments/{PurePosixPath(*relative.parts).as_posix()}"


def _freeze_reference_image(
    repository: ProductionRepository,
    project_id: str,
    shot: Shot,
    storage: StoragePolicyVersion | None,
    errors: list[dict],
) -> dict | None:
    reference_id = shot.primary_reference_entity_version_id
    if reference_id is None:
        return None
    path_prefix = f"shots.{shot.shot_code}.primary_reference_entity_version_id"
    declared = set(
        ([shot.scene_entity_version_id] if shot.scene_entity_version_id else [])
        + (shot.character_entity_version_ids or [])
        + (shot.outfit_entity_version_ids or [])
        + (shot.product_entity_version_ids or [])
    )
    if reference_id not in declared:
        errors.append({"code": "PRIMARY_REFERENCE_NOT_DECLARED", "path": path_prefix})
        return None
    version = repository.entity_version(reference_id)
    if not version or version.project_id != project_id or version.status != "confirmed":
        errors.append({"code": "PRIMARY_REFERENCE_ENTITY_INVALID", "path": path_prefix})
        return None
    if not version.source_attachment_id:
        errors.append({"code": "PRIMARY_REFERENCE_ATTACHMENT_REQUIRED", "path": path_prefix})
        return None
    attachment = repository.attachment(version.source_attachment_id)
    if (
        not attachment
        or attachment.project_id != project_id
        or attachment.verification_status != "verified"
        or not attachment.mime_type.startswith("image/")
    ):
        errors.append({"code": "PRIMARY_REFERENCE_ATTACHMENT_INVALID", "path": path_prefix})
        return None
    if not storage:
        errors.append({"code": "STORAGE_POLICY_REQUIRED", "path": path_prefix})
        return None
    if attachment.mime_type not in (storage.allowed_mime_types or []):
        errors.append({"code": "REFERENCE_IMAGE_MIME_NOT_ALLOWED", "path": path_prefix})
        return None
    if attachment.byte_size > storage.max_file_size_bytes:
        errors.append({"code": "REFERENCE_IMAGE_TOO_LARGE", "path": path_prefix})
        return None
    root = RUNTIME_ROOT.resolve()
    file_path = (root / attachment.storage_path).resolve()
    if not file_path.is_relative_to(root):
        errors.append({"code": "REFERENCE_IMAGE_PATH_INVALID", "path": path_prefix})
        return None
    if not file_path.is_file():
        errors.append({"code": "REFERENCE_IMAGE_FILE_MISSING", "path": path_prefix})
        return None
    if file_path.stat().st_size != attachment.byte_size:
        errors.append({"code": "REFERENCE_IMAGE_SIZE_MISMATCH", "path": path_prefix})
        return None
    content = file_path.read_bytes()
    if detect_media_type(content) != attachment.mime_type:
        errors.append({"code": "REFERENCE_IMAGE_MIME_MISMATCH", "path": path_prefix})
        return None
    if hashlib.sha256(content).hexdigest() != attachment.content_hash:
        errors.append({"code": "REFERENCE_IMAGE_HASH_MISMATCH", "path": path_prefix})
        return None
    try:
        uri = _attachment_uri(attachment.storage_path)
    except ValueError:
        errors.append({"code": "REFERENCE_IMAGE_PATH_INVALID", "path": path_prefix})
        return None
    return {
        "role": "primary",
        "entity_version_id": version.id,
        "attachment_id": attachment.id,
        "uri": uri,
        "mime_type": attachment.mime_type,
        "byte_size": attachment.byte_size,
        "content_hash": attachment.content_hash,
    }


def _verify_frozen_reference_image(reference: dict | None) -> bool:
    if reference is None:
        return True
    required = {"role", "entity_version_id", "attachment_id", "uri", "mime_type", "byte_size", "content_hash"}
    if set(reference) != required or reference.get("role") != "primary":
        return False
    uri = str(reference.get("uri") or "")
    prefix = "runtime://attachments/"
    if not uri.startswith(prefix):
        return False
    relative = PurePosixPath(uri[len(prefix):])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        return False
    root = RUNTIME_ROOT.resolve()
    file_path = (root / PurePath(*relative.parts)).resolve()
    if not file_path.is_relative_to(root) or not file_path.is_file():
        return False
    if file_path.stat().st_size != reference.get("byte_size"):
        return False
    content = file_path.read_bytes()
    return (
        detect_media_type(content) == reference.get("mime_type")
        and hashlib.sha256(content).hexdigest() == reference.get("content_hash")
    )


def _compile_manifest(
    plan: PlanVersion,
    shots: list[Shot],
    selection: dict,
    output_spec: dict,
    audio_mode: str,
    references_by_shot_id: dict[str, dict | None],
) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    for shot in shots:
        image_key = f"{shot.shot_code}.keyframe"
        video_key = f"{shot.shot_code}.video"
        nodes.append({
            "node_key": image_key,
            "kind": "generate_keyframe",
            "shot_id": shot.id,
            "workflow_slot_version_id": selection["keyframe_workflow_slot_version_id"],
            "input_contract": {"shot": _shot_contract(shot), "entity_version_ids": sorted(set(
                ([shot.scene_entity_version_id] if shot.scene_entity_version_id else [])
                + shot.character_entity_version_ids + shot.outfit_entity_version_ids + shot.product_entity_version_ids
            )), "reference_image": references_by_shot_id.get(shot.id)},
            "output_contract": {"media_type": "image", "video_spec_version_id": selection["video_spec_version_id"]},
        })
        nodes.append({
            "node_key": video_key,
            "kind": "generate_i2v_clip",
            "shot_id": shot.id,
            "workflow_slot_version_id": selection["video_workflow_slot_version_id"],
            "input_contract": {
                "shot": _shot_contract(shot),
                "source_image_node_keys": [image_key],
                "duration_ms": shot.duration_ms,
            },
            "output_contract": {"media_type": "video", "video_spec_version_id": selection["video_spec_version_id"]},
        })
        edges.append({"parent_node_key": image_key, "child_node_key": video_key, "dependency_type": "required", "input_slot": "source_image"})
    timeline_inputs = [f"{shot.shot_code}.video" for shot in shots]
    if audio_mode == "voiceover":
        nodes.append({
            "node_key": "project.voiceover",
            "kind": "generate_tts",
            "shot_id": None,
            "workflow_slot_version_id": selection["tts_workflow_slot_version_id"],
            "input_contract": {"source": "confirmed_plan_voiceover"},
            "output_contract": {"media_type": "audio"},
        })
        timeline_inputs.append("project.voiceover")
    nodes.append({
        "node_key": "project.timeline",
        "kind": "assemble_timeline_contract",
        "shot_id": None,
        "workflow_slot_version_id": None,
        "input_contract": {"ordered_input_node_keys": timeline_inputs},
        "output_contract": {"media_type": "timeline", "output_spec": output_spec},
    })
    for key in timeline_inputs:
        edges.append({"parent_node_key": key, "child_node_key": "project.timeline", "dependency_type": "required", "input_slot": "timeline_input"})
    return {"nodes": nodes, "edges": edges}


def _price_dag(
    session: Session,
    pricing: PricingCatalogVersion,
    dag: dict,
    total_duration_seconds: Decimal,
) -> tuple[Decimal | None, list[dict]]:
    rules = _production(session).pricing_rules(pricing.id)
    rule_by_slot = {rule.workflow_slot_version_id: rule for rule in rules}
    errors: list[dict] = []
    total = Decimal("0")
    for node in dag["nodes"]:
        slot_id = node["workflow_slot_version_id"]
        if not slot_id:
            node["estimated_cost"] = None
            node["currency"] = None
            continue
        rule = rule_by_slot.get(slot_id)
        if not rule:
            errors.append({
                "code": "PRICING_RULE_MISSING",
                "path": f"dag.{node['node_key']}",
                "message": "所选价格目录没有该工作流槽位的精确规则。",
            })
            continue
        if rule.unit == "call":
            quantity = Decimal("1")
        elif rule.unit == "runtime_second" and rule.estimated_runtime_seconds is not None:
            quantity = Decimal(str(rule.estimated_runtime_seconds))
        elif rule.unit == "output_second" and node["kind"] == "generate_i2v_clip":
            quantity = Decimal(str(node["input_contract"]["duration_ms"])) / Decimal("1000")
        elif rule.unit == "output_second" and node["kind"] == "generate_tts":
            quantity = total_duration_seconds
        else:
            errors.append({
                "code": "PRICING_UNIT_NOT_APPLICABLE",
                "path": f"dag.{node['node_key']}",
                "message": f"计价单位 {rule.unit} 不适用于节点类型 {node['kind']}。",
            })
            continue
        amount = Decimal(str(rule.unit_price)) * quantity
        if rule.minimum_charge is not None:
            amount = max(amount, Decimal(str(rule.minimum_charge)))
        amount = _money(amount)
        node["estimated_cost"] = float(amount)
        node["currency"] = pricing.currency
        node["pricing_rule_id"] = rule.id
        node["pricing_quantity"] = float(quantity)
        node["pricing_unit"] = rule.unit
        total += amount
    return (None if errors else _money(total)), errors


def _validate_runninghub_keyframe_bindings(
    shots: list[Shot],
    references_by_shot_id: dict[str, dict | None],
    workflow: WorkflowSlotVersion | None,
    provider: ProviderConfigVersion | None,
    errors: list[dict],
) -> None:
    if not workflow or not provider or provider.adapter_kind != "runninghub":
        return
    bindings = [item for item in (workflow.node_info_list or []) if isinstance(item, dict)]
    visual_prompt_binding_count = sum(
        1 for item in bindings if item.get("value_source") == "shot.visual_prompt"
    )
    if visual_prompt_binding_count != 1:
        errors.append({
            "code": "RUNNINGHUB_VISUAL_PROMPT_BINDING_COUNT_INVALID",
            "path": "keyframe_workflow_slot_version_id",
            "message": "RunningHub 图片生成方案必须精确绑定一个画面生成描述。",
            "actual": visual_prompt_binding_count,
        })
    for shot in shots:
        for binding in bindings:
            if binding.get("required") is not True:
                continue
            source = binding.get("value_source")
            if source == "shot.negative_prompt" and shot.negative_prompt is None:
                errors.append({
                    "code": "REQUIRED_NEGATIVE_PROMPT_MISSING",
                    "path": f"shots.{shot.shot_code}.negative_prompt",
                })
            if source == "reference_image.primary" and references_by_shot_id.get(shot.id) is None:
                errors.append({
                    "code": "REQUIRED_PRIMARY_REFERENCE_MISSING",
                    "path": f"shots.{shot.shot_code}.primary_reference_entity_version_id",
                })


def _validate_generation_capabilities(
    shots: list[Shot],
    references_by_shot_id: dict[str, dict | None],
    keyframe_workflow: WorkflowSlotVersion | None,
    video_workflow: WorkflowSlotVersion | None,
    errors: list[dict],
) -> None:
    keyframe_bindings = list(keyframe_workflow.node_info_list or []) if keyframe_workflow else []
    keyframe_tags = set(keyframe_workflow.capability_tags or []) if keyframe_workflow else set()
    video_tags = set(video_workflow.capability_tags or []) if video_workflow else set()
    supports_reference = any(
        isinstance(item, dict) and item.get("value_source") == "reference_image.primary"
        for item in keyframe_bindings
    )
    for shot in shots:
        requirements = shot.generation_requirements or {}
        path = f"shots.{shot.shot_code}.generation_requirements"
        if requirements.get("reference_image_required") and not supports_reference:
            errors.append({"code": "REFERENCE_IMAGE_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code, "message": "所选图片生成方案没有显式参考图输入。"})
        if requirements.get("reference_image_required") and references_by_shot_id.get(shot.id) is None:
            errors.append({"code": "REQUIRED_REFERENCE_IMAGE_MISSING", "path": path, "shot_code": shot.shot_code, "message": "镜头要求参考图，但没有冻结可用的主参考图。"})
        if requirements.get("multi_frame_required") and "multi_frame" not in video_tags:
            errors.append({"code": "MULTI_FRAME_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code, "message": "所选视频生成方案未声明多帧输入能力。"})
        if requirements.get("identity_consistency_required") and (not supports_reference or references_by_shot_id.get(shot.id) is None):
            errors.append({"code": "IDENTITY_CONSISTENCY_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code, "message": "身份一致性要求需要主参考图和支持参考图的图片生成方案。"})
        if requirements.get("precise_text_required") and "precise_text" not in keyframe_tags:
            errors.append({"code": "PRECISE_TEXT_CAPABILITY_MISSING", "path": path, "shot_code": shot.shot_code, "message": "所选图片生成方案未声明精确文字能力。"})


def analyze_impact(session: Session, project: Project, payload: AnalyzeProductionImpact) -> dict:
    repository = _production(session)
    receipt = _receipt(session, project.id, payload.command_id, "production.impact.analyze")
    if receipt:
        row = repository.impact_analysis(receipt.result_id)
        if not row:
            raise ProductionConflictError("COMMAND_RESULT_MISSING", "影响分析命令结果不存在。")
        return _impact_dict(row)

    errors: list[dict] = []
    plan = repository.plan(payload.plan_version_id)
    if not plan or plan.project_id != project.id:
        raise ProductionNotFoundError("Plan version not found in project")
    if plan.status != "confirmed" or not plan.is_active:
        errors.append({"code": "PLAN_NOT_ACTIVE", "path": "plan_version_id", "message": "只能分析当前已确认方案。"})
    if plan.contract_schema_version != "shot-plan.v3":
        errors.append({
            "code": "SHOT_PLAN_SCHEMA_UNSUPPORTED",
            "path": "plan_version_id",
            "message": "旧版分镜方案不能自动升级，请创建并确认新的分镜方案。",
        })
    config = repository.configuration(payload.production_config_version_id)
    if not config:
        raise ProductionNotFoundError("Production configuration version not found")
    if config.status != "published":
        errors.append({"code": "CONFIGURATION_NOT_PUBLISHED", "path": "production_config_version_id", "message": "生产快照只能绑定已发布配置。"})

    audio_mode = str(plan.creative_brief.get("audio_mode", ""))
    aspect_ratio = str(plan.creative_brief.get("aspect_ratio", ""))
    if audio_mode not in {"off", "voiceover"}:
        errors.append({"code": "PLAN_AUDIO_MODE_INVALID", "path": "plan_version_id", "message": "已确认方案缺少有效音频模式。"})
    if aspect_ratio not in {"9:16", "16:9", "1:1"}:
        errors.append({"code": "PLAN_ASPECT_RATIO_INVALID", "path": "plan_version_id", "message": "已确认方案缺少有效画幅。"})

    video_spec = _component(session, VideoSpecVersion, payload.video_spec_version_id, config.id, "video_spec", errors)
    keyframe_slot = _component(session, WorkflowSlotVersion, payload.keyframe_workflow_slot_version_id, config.id, "keyframe_workflow_slot", errors)
    video_slot = _component(session, WorkflowSlotVersion, payload.video_workflow_slot_version_id, config.id, "video_workflow_slot", errors)
    tts_slot = None
    if payload.tts_workflow_slot_version_id:
        tts_slot = _component(session, WorkflowSlotVersion, payload.tts_workflow_slot_version_id, config.id, "tts_workflow_slot", errors)
    pricing = None
    if payload.pricing_catalog_version_id:
        pricing = _component(session, PricingCatalogVersion, payload.pricing_catalog_version_id, config.id, "pricing_catalog", errors)
    storage_policies = repository.storage_policies(config.id)
    storage = storage_policies[0] if len(storage_policies) == 1 else None
    if len(storage_policies) != 1:
        errors.append({
            "code": "STORAGE_POLICY_COUNT_INVALID",
            "path": "production_config_version_id",
            "message": "生产配置必须精确包含一个存储策略。",
        })
    elif storage.status != "published":
        errors.append({"code": "STORAGE_POLICY_NOT_PUBLISHED", "path": "production_config_version_id"})

    if video_spec and video_spec.aspect_ratio != aspect_ratio:
        errors.append({"code": "VIDEO_SPEC_ASPECT_RATIO_MISMATCH", "path": "video_spec_version_id", "message": "视频规格画幅与项目已确认画幅不一致。"})
    if keyframe_slot and keyframe_slot.operation_kind != "image_generation":
        errors.append({"code": "KEYFRAME_SLOT_KIND_INVALID", "path": "keyframe_workflow_slot_version_id", "message": "关键帧槽位必须是 image_generation。"})
    if video_slot and video_slot.operation_kind != "video_generation":
        errors.append({"code": "VIDEO_SLOT_KIND_INVALID", "path": "video_workflow_slot_version_id", "message": "视频槽位必须是 video_generation。"})
    for label, slot in (("keyframe", keyframe_slot), ("video", video_slot)):
        if slot and video_spec and video_spec.id not in (slot.supported_video_spec_ids or []):
            errors.append({"code": "WORKFLOW_VIDEO_SPEC_UNSUPPORTED", "path": f"{label}_workflow_slot_version_id", "message": f"{label} 槽位未显式声明支持所选视频规格。"})
    if audio_mode == "off" and tts_slot:
        errors.append({"code": "AUDIO_OFF_HAS_TTS", "path": "tts_workflow_slot_version_id", "message": "项目关闭音频时不得选择 TTS 槽位。"})
    if audio_mode == "voiceover" and not tts_slot:
        errors.append({"code": "VOICEOVER_TTS_REQUIRED", "path": "tts_workflow_slot_version_id", "message": "旁白模式必须显式选择 TTS 槽位。"})
    if tts_slot and tts_slot.operation_kind != "tts":
        errors.append({"code": "TTS_SLOT_KIND_INVALID", "path": "tts_workflow_slot_version_id", "message": "TTS 槽位的 operation_kind 必须是 tts。"})

    shots = repository.plan_shots(plan.id)
    if not shots:
        errors.append({"code": "PLAN_HAS_NO_SHOTS", "path": "plan_version_id", "message": "方案没有分镜。"})
    entity_ids: set[str] = set()
    references_by_shot_id: dict[str, dict | None] = {}
    for shot in shots:
        if not isinstance(shot.visual_prompt, str) or not shot.visual_prompt.strip():
            errors.append({"code": "VISUAL_PROMPT_REQUIRED", "path": f"shots.{shot.shot_code}.visual_prompt"})
        refs = (
            ([shot.scene_entity_version_id] if shot.scene_entity_version_id else [])
            + (shot.character_entity_version_ids or [])
            + (shot.outfit_entity_version_ids or [])
            + (shot.product_entity_version_ids or [])
        )
        for entity_id in refs:
            entity = repository.entity_version(entity_id)
            if not entity or entity.project_id != project.id or entity.status != "confirmed":
                errors.append({"code": "ENTITY_VERSION_INVALID", "path": f"shots.{shot.shot_code}", "entity_version_id": entity_id})
            else:
                entity_ids.add(entity_id)
        references_by_shot_id[shot.id] = _freeze_reference_image(
            repository,
            project.id,
            shot,
            storage,
            errors,
        )
        if video_spec and not (video_spec.duration_min_seconds * 1000 <= shot.duration_ms <= video_spec.duration_max_seconds * 1000):
            errors.append({"code": "SHOT_DURATION_UNSUPPORTED", "path": f"shots.{shot.shot_code}.duration_ms", "message": "镜头时长不在所选视频规格范围内。"})

    keyframe_provider = repository.provider(keyframe_slot.provider_config_version_id) if keyframe_slot else None
    _validate_runninghub_keyframe_bindings(
        shots,
        references_by_shot_id,
        keyframe_slot,
        keyframe_provider,
        errors,
    )
    _validate_generation_capabilities(shots, references_by_shot_id, keyframe_slot, video_slot, errors)

    selection = {
        "video_spec_version_id": payload.video_spec_version_id,
        "keyframe_workflow_slot_version_id": payload.keyframe_workflow_slot_version_id,
        "video_workflow_slot_version_id": payload.video_workflow_slot_version_id,
        "tts_workflow_slot_version_id": payload.tts_workflow_slot_version_id,
        "pricing_catalog_version_id": payload.pricing_catalog_version_id,
    }
    output_spec = {} if not video_spec else {
        "video_spec_version_id": video_spec.id,
        "width": video_spec.width,
        "height": video_spec.height,
        "aspect_ratio": video_spec.aspect_ratio,
        "fps": video_spec.fps,
        "container": video_spec.container,
        "video_codec": video_spec.video_codec,
        "pixel_format": video_spec.pixel_format,
    }
    dag = _compile_manifest(
        plan,
        shots,
        selection,
        output_spec,
        audio_mode,
        references_by_shot_id,
    ) if keyframe_slot and video_slot else {"nodes": [], "edges": []}
    estimated_cost = None
    if pricing:
        now = utc_now()
        if pricing.effective_from and now < _utc(pricing.effective_from):
            errors.append({"code": "PRICING_NOT_EFFECTIVE", "path": "pricing_catalog_version_id", "message": "价格目录尚未到生效时间。"})
        if pricing.effective_to and now >= _utc(pricing.effective_to):
            errors.append({"code": "PRICING_EXPIRED", "path": "pricing_catalog_version_id", "message": "价格目录已过有效期。"})
        priced_total, pricing_errors = _price_dag(
            session,
            pricing,
            dag,
            Decimal(str(sum(shot.duration_ms for shot in shots))) / Decimal("1000"),
        )
        errors.extend(pricing_errors)
        if not pricing_errors:
            estimated_cost = priced_total
    manifest = {
        "project_id": project.id,
        "plan_version_id": plan.id,
        "plan_contract_schema_version": plan.contract_schema_version,
        "production_config_version_id": config.id,
        "production_config_hash": config.config_hash,
        "audio_mode": audio_mode,
        "selection": selection,
        "output_spec": output_spec,
        "entity_version_ids": sorted(entity_ids),
        "shots": [_shot_contract(shot) for shot in shots],
        "dag": dag,
        "pricing": None if not pricing else {
            "pricing_catalog_version_id": pricing.id,
            "catalog_key": pricing.catalog_key,
            "currency": pricing.currency,
            "confirmation_threshold": pricing.confirmation_threshold,
        },
    }
    analysis_hash = _hash(manifest)
    estimated_calls = sum(1 for node in dag["nodes"] if node["workflow_slot_version_id"])
    blockers = [] if pricing and estimated_cost is not None and not errors else [{
        "code": "COST_ESTIMATE_REQUIRED",
        "message": "必须显式选择覆盖全部生产槽位的有效价格目录，快照才能锁定。",
    }]
    analysis = ProductionImpactAnalysis(
        project_id=project.id,
        plan_version_id=plan.id,
        production_config_version_id=config.id,
        pricing_catalog_version_id=pricing.id if pricing else None,
        status="blocked" if errors else "awaiting_confirmation",
        selection=selection,
        manifest=manifest,
        analysis_hash=analysis_hash,
        validation_errors=errors,
        execution_blockers=blockers,
        estimated_call_count=estimated_calls,
        cost_status="estimated" if estimated_cost is not None and not errors else "not_configured",
        estimated_cost=float(estimated_cost) if estimated_cost is not None and not errors else None,
        currency=pricing.currency if pricing and estimated_cost is not None and not errors else None,
        created_by=payload.actor_id,
    )
    repository.add(analysis)
    repository.flush()
    _save_receipt(session, project.id, payload.command_id, "production.impact.analyze", "production_impact_analysis", analysis.id)
    _event(session, ProjectEvent(project_id=project.id, event_type="production.impact_evaluated.v1", aggregate_type="production_impact_analysis", aggregate_id=analysis.id, actor_type="user", actor_id=payload.actor_id, causation_id=payload.command_id, message="生产影响分析已生成", data={"analysis_id": analysis.id, "analysis_hash": analysis.analysis_hash, "validation_errors": errors}))
    session.commit()
    return _impact_dict(analysis)


def create_snapshot(session: Session, project: Project, payload: CreateProductionSnapshot) -> dict:
    repository = _production(session)
    receipt = _receipt(session, project.id, payload.command_id, "production.snapshot.create")
    if receipt:
        row = repository.snapshot(receipt.result_id)
        if not row:
            raise ProductionConflictError("COMMAND_RESULT_MISSING", "快照命令结果不存在。")
        return _snapshot_dict(session, row)
    analysis = repository.impact_analysis(payload.impact_analysis_id)
    if not analysis or analysis.project_id != project.id:
        raise ProductionNotFoundError("Production impact analysis not found")
    if analysis.status != "awaiting_confirmation" or analysis.validation_errors:
        raise ProductionConflictError("IMPACT_ANALYSIS_BLOCKED", "影响分析存在确定性错误，不能创建快照。")
    if analysis.analysis_hash != payload.analysis_hash:
        raise ProductionConflictError("IMPACT_ANALYSIS_HASH_MISMATCH", "影响分析内容已变化，请重新确认。")
    if not payload.confirm_contract_scope:
        raise ProductionConflictError("CONTRACT_SCOPE_CONFIRMATION_REQUIRED", "创建不可变快照前必须确认精确生产范围。")
    plan = repository.plan(analysis.plan_version_id)
    config = repository.configuration(analysis.production_config_version_id)
    if not plan or plan.project_id != project.id or not plan.is_active or plan.status != "confirmed":
        raise ProductionConflictError("PLAN_CHANGED_AFTER_ANALYSIS", "当前方案已变化，必须重新分析。")
    if not config or config.status != "published" or config.config_hash != analysis.manifest.get("production_config_hash"):
        raise ProductionConflictError("CONFIGURATION_CHANGED_AFTER_ANALYSIS", "配置状态或哈希已变化，必须重新分析。")

    storage_policies = repository.storage_policies(config.id)
    if len(storage_policies) != 1 or storage_policies[0].status != "published":
        raise ProductionConflictError("STORAGE_POLICY_CHANGED_AFTER_ANALYSIS", "存储策略已变化，必须重新分析。")
    current_reference_errors: list[dict] = []
    frozen_nodes = {
        item["shot_id"]: item
        for item in analysis.manifest.get("dag", {}).get("nodes", [])
        if item.get("kind") == "generate_keyframe" and item.get("shot_id")
    }
    for shot in repository.plan_shots(plan.id):
        node = frozen_nodes.get(shot.id)
        if not node:
            raise ProductionConflictError("KEYFRAME_NODE_MISSING_AFTER_ANALYSIS", f"镜头 {shot.shot_code} 缺少冻结关键帧节点。")
        current_reference = _freeze_reference_image(
            repository,
            project.id,
            shot,
            storage_policies[0],
            current_reference_errors,
        )
        if current_reference != node.get("input_contract", {}).get("reference_image"):
            current_reference_errors.append({
                "code": "REFERENCE_IMAGE_CHANGED_AFTER_ANALYSIS",
                "path": f"shots.{shot.shot_code}.primary_reference_entity_version_id",
            })
    if current_reference_errors:
        codes = ", ".join(sorted({str(item["code"]) for item in current_reference_errors}))
        raise ProductionConflictError("REFERENCE_IMAGE_CHANGED_AFTER_ANALYSIS", f"参考图事实已变化，必须重新分析：{codes}。")

    contract = _snapshot_contract(analysis.manifest)
    contract_hash = _hash(contract)
    existing_snapshot = repository.snapshot_for_contract(project.id, contract_hash)
    if existing_snapshot:
        raise ProductionConflictError(
            "PRODUCTION_SNAPSHOT_DUPLICATE",
            f"相同制作方案已保存为制作方案 {existing_snapshot.snapshot_number}，不能重复创建。",
        )

    snapshot_number = repository.next_snapshot_number(project.id)
    snapshot = ProductionSnapshot(
        project_id=project.id,
        plan_version_id=plan.id,
        production_config_version_id=config.id,
        pricing_catalog_version_id=analysis.pricing_catalog_version_id,
        impact_analysis_id=analysis.id,
        snapshot_number=snapshot_number,
        status="preparing",
        audio_mode=analysis.manifest["audio_mode"],
        output_spec=analysis.manifest["output_spec"],
        selection=analysis.selection,
        contract=contract,
        contract_hash=contract_hash,
        estimated_call_count=analysis.estimated_call_count,
        cost_status=analysis.cost_status,
        estimated_cost=analysis.estimated_cost,
        currency=analysis.currency,
        execution_blockers=analysis.execution_blockers,
        created_by=payload.actor_id,
    )
    repository.add(snapshot)
    repository.flush()
    for entity_id in analysis.manifest["entity_version_ids"]:
        repository.add(SnapshotEntityVersion(snapshot_id=snapshot.id, entity_version_id=entity_id, role="shot_reference"))
    node_by_key: dict[str, DAGNode] = {}
    for contract_node in analysis.manifest["dag"]["nodes"]:
        node = DAGNode(snapshot_id=snapshot.id, **contract_node)
        repository.add(node)
        repository.flush()
        node_by_key[node.node_key] = node
    for contract_edge in analysis.manifest["dag"]["edges"]:
        repository.add(DependencyEdge(
            snapshot_id=snapshot.id,
            parent_node_id=node_by_key[contract_edge["parent_node_key"]].id,
            child_node_id=node_by_key[contract_edge["child_node_key"]].id,
            dependency_type=contract_edge["dependency_type"],
            input_slot=contract_edge["input_slot"],
        ))
    repository.add(ConfigurationReference(production_config_version_id=config.id, ref_type="snapshot", ref_id=snapshot.id))
    analysis.status = "confirmed"
    _save_receipt(session, project.id, payload.command_id, "production.snapshot.create", "production_snapshot", snapshot.id)
    _event(session, ProjectEvent(project_id=project.id, snapshot_id=snapshot.id, event_type="production.snapshot_prepared.v1", aggregate_type="production_snapshot", aggregate_id=snapshot.id, actor_type="user", actor_id=payload.actor_id, causation_id=payload.command_id, message="不可变生产快照已创建，等待成本核算", data={"snapshot_id": snapshot.id, "contract_hash": snapshot.contract_hash, "status": snapshot.status}))
    transition_project(
        session,
        project,
        ProjectStateTrigger.SNAPSHOT_PREPARED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"snapshot_id": snapshot.id},
    )
    session.commit()
    return _snapshot_dict(session, snapshot)


def lock_snapshot(
    session: Session,
    project: Project,
    snapshot_id: str,
    payload: LockProductionSnapshot,
) -> dict:
    repository = _production(session)
    receipt = _receipt(session, project.id, payload.command_id, "production.snapshot.lock")
    if receipt:
        row = repository.snapshot(receipt.result_id)
        if not row:
            raise ProductionConflictError("COMMAND_RESULT_MISSING", "快照锁定命令结果不存在。")
        return _snapshot_dict(session, row)
    snapshot = repository.snapshot(snapshot_id)
    if not snapshot or snapshot.project_id != project.id:
        raise ProductionNotFoundError("Production snapshot not found")
    if snapshot.status != "preparing":
        raise ProductionConflictError("SNAPSHOT_NOT_PREPARING", f"快照状态 {snapshot.status} 不能锁定。")
    if snapshot.contract_hash != payload.expected_contract_hash:
        raise ProductionConflictError("SNAPSHOT_CONTRACT_HASH_MISMATCH", "快照合同哈希不匹配，请刷新后重新确认。")
    if snapshot.cost_status != "estimated" or snapshot.estimated_cost is None or not snapshot.currency:
        raise ProductionConflictError("SNAPSHOT_COST_NOT_ESTIMATED", "快照尚未完成确定性成本估算。")
    expected = _money(payload.expected_estimated_cost)
    actual = _money(Decimal(str(snapshot.estimated_cost)))
    if expected != actual or payload.expected_currency != snapshot.currency:
        raise ProductionConflictError("SNAPSHOT_COST_MISMATCH", "确认的金额或币种与当前快照估算不一致。")
    if not payload.confirm_high_risk_cost:
        raise ProductionConflictError("HIGH_RISK_COST_CONFIRMATION_REQUIRED", "锁定生产快照必须明确确认预计费用。")
    pricing = repository.pricing_catalog(snapshot.pricing_catalog_version_id)
    if not pricing or pricing.status != "published":
        raise ProductionConflictError("PRICING_CATALOG_NOT_PUBLISHED", "价格目录已不可用于新快照锁定。")
    now = utc_now()
    if pricing.effective_from and now < _utc(pricing.effective_from):
        raise ProductionConflictError("PRICING_NOT_EFFECTIVE", "价格目录尚未生效。")
    if pricing.effective_to and now >= _utc(pricing.effective_to):
        raise ProductionConflictError("PRICING_EXPIRED", "价格目录已过有效期。")

    nodes = repository.snapshot_nodes(snapshot.id)
    priced_total = _money(sum(
        (Decimal(str(node.estimated_cost)) for node in nodes if node.workflow_slot_version_id),
        Decimal("0"),
    ))
    if priced_total != actual or any(
        node.workflow_slot_version_id and (node.estimated_cost is None or not node.pricing_rule_id)
        for node in nodes
    ):
        raise ProductionConflictError("SNAPSHOT_NODE_COST_MISMATCH", "DAG 节点成本明细与快照总额不一致。")

    for node in nodes:
        if not node.workflow_slot_version_id:
            continue
        workflow = repository.workflow(node.workflow_slot_version_id)
        provider = repository.provider(workflow.provider_config_version_id) if workflow else None
        if not workflow or not provider:
            raise ProductionConflictError("SNAPSHOT_PRICING_REFERENCE_MISSING", "成本事件无法解析精确工作流或供应商版本。")
        repository.add(CostEvent(
            project_id=project.id,
            snapshot_id=snapshot.id,
            provider=provider.provider_key,
            provider_operation=workflow.slot_key,
            kind="estimated",
            amount=node.estimated_cost,
            currency=snapshot.currency,
            status="confirmed",
        ))
    snapshot.status = "locked"
    snapshot.cost_status = "confirmed"
    snapshot.execution_blockers = []
    snapshot.locked_at = now
    _save_receipt(session, project.id, payload.command_id, "production.snapshot.lock", "production_snapshot", snapshot.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="production.snapshot_locked.v1",
        aggregate_type="production_snapshot",
        aggregate_id=snapshot.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="生产快照费用已确认并锁定",
        data={
            "snapshot_id": snapshot.id,
            "contract_hash": snapshot.contract_hash,
            "estimated_cost": snapshot.estimated_cost,
            "currency": snapshot.currency,
            "above_confirmation_threshold": actual >= _money(Decimal(str(pricing.confirmation_threshold))),
        },
    ))
    transition_project(
        session,
        project,
        ProjectStateTrigger.SNAPSHOT_LOCKED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"snapshot_id": snapshot.id},
    )
    session.commit()
    return _snapshot_dict(session, snapshot)


def activate_snapshot(
    session: Session,
    project: Project,
    snapshot_id: str,
    payload: ActivateProductionSnapshot,
) -> dict:
    repository = _production(session)
    receipt = _receipt(session, project.id, payload.command_id, "production.snapshot.activate")
    if receipt:
        row = repository.snapshot(receipt.result_id)
        if not row:
            raise ProductionConflictError("COMMAND_RESULT_MISSING", "Snapshot activation result no longer exists.")
        return _snapshot_dict(session, row)
    snapshot = repository.snapshot(snapshot_id)
    if not snapshot or snapshot.project_id != project.id:
        raise ProductionNotFoundError("Production snapshot not found")
    if snapshot.status != "locked" or snapshot.cost_status != "confirmed" or not snapshot.locked_at:
        raise ProductionConflictError("SNAPSHOT_NOT_LOCKED", "Only a locked snapshot with confirmed cost can be activated.")
    if snapshot.contract_hash != payload.expected_contract_hash:
        raise ProductionConflictError("SNAPSHOT_CONTRACT_HASH_MISMATCH", "Snapshot contract hash does not match.")
    if snapshot.execution_blockers:
        raise ProductionConflictError("SNAPSHOT_HAS_EXECUTION_BLOCKERS", "Snapshot still has execution blockers.")
    if project.active_snapshot_id and project.active_snapshot_id != snapshot.id:
        raise ProductionConflictError("PROJECT_HAS_ACTIVE_SNAPSHOT", "The project already has another active snapshot.")
    if repository.has_work_items(snapshot.id):
        raise ProductionConflictError("SNAPSHOT_ALREADY_SUBMITTED", "This snapshot already has execution work items.")

    now = utc_now()
    snapshot.status = "active"
    snapshot.activated_at = now
    project.active_snapshot_id = snapshot.id
    transition_project(
        session,
        project,
        ProjectStateTrigger.SNAPSHOT_ACTIVATED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"snapshot_id": snapshot.id},
    )
    _save_receipt(session, project.id, payload.command_id, "production.snapshot.activate", "production_snapshot", snapshot.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="production.snapshot_activated.v1",
        aggregate_type="production_snapshot",
        aggregate_id=snapshot.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Production snapshot activated; no work items were created.",
        data={"snapshot_id": snapshot.id, "contract_hash": snapshot.contract_hash},
    ))
    session.commit()
    return _snapshot_dict(session, snapshot)


def _execution_manifest(
    snapshot: ProductionSnapshot,
    node: DAGNode,
    workflow: WorkflowSlotVersion | None,
    provider: ProviderConfigVersion | None,
    video_spec: VideoSpecVersion | None,
    storage: StoragePolicyVersion | None,
) -> dict:
    duration_ms = node.input_contract.get("duration_ms")
    fps = video_spec.fps if video_spec else None
    frame_rule = video_spec.frame_count_rule if video_spec else None
    frame_count = (
        round(duration_ms * fps / 1000)
        if isinstance(duration_ms, int)
        and fps
        and isinstance(frame_rule, dict)
        and frame_rule.get("type") == "duration_times_fps"
        else None
    )
    return {
        "schema_version": "production-work-request.v3",
        "snapshot_id": snapshot.id,
        "contract_hash": snapshot.contract_hash,
        "dag_node_id": node.id,
        "node_key": node.node_key,
        "kind": node.kind,
        "input_contract": node.input_contract,
        "output_contract": node.output_contract,
        "workflow_slot_version_id": workflow.id if workflow else None,
        "provider_config_version_id": provider.id if provider else None,
        "provider": None if not provider else {
            "provider_key": provider.provider_key,
            "adapter_kind": provider.adapter_kind,
            "base_url": provider.base_url,
            "credential_ref": provider.credential_ref,
            "request_timeout_seconds": provider.request_timeout_seconds,
            "poll_interval_seconds": provider.poll_interval_seconds,
            "max_concurrency": provider.max_concurrency,
        },
        "provider_key": provider.provider_key if provider else "local",
        "adapter_kind": provider.adapter_kind if provider else "local",
        "workflow": None if not workflow else {
            "provider_workflow_id": workflow.provider_workflow_id,
            "provider_workflow_version": workflow.provider_workflow_version,
            "input_schema_version": workflow.input_schema_version,
            "output_schema_version": workflow.output_schema_version,
            "node_info_list": workflow.node_info_list,
        },
        "provider_workflow_id": workflow.provider_workflow_id if workflow else None,
        "video_spec": None if not video_spec else {
            "video_spec_version_id": video_spec.id,
            "width": video_spec.width,
            "height": video_spec.height,
            "aspect_ratio": video_spec.aspect_ratio,
            "fps": video_spec.fps,
            "frame_count_rule": video_spec.frame_count_rule,
            "frame_count": frame_count,
            "long_side": max(video_spec.width, video_spec.height),
            "container": video_spec.container,
            "video_codec": video_spec.video_codec,
            "pixel_format": video_spec.pixel_format,
            "safe_crop": video_spec.safe_crop,
        },
        "storage_policy": None if not storage else {
            "storage_policy_version_id": storage.id,
            "backend_kind": storage.backend_kind,
            "allowed_mime_types": storage.allowed_mime_types,
            "max_file_size_bytes": storage.max_file_size_bytes,
            "public_url_policy": storage.public_url_policy,
            "local_root_ref": storage.local_root_ref,
        },
    }


def submit_production(
    session: Session,
    project: Project,
    snapshot_id: str,
    payload: SubmitProduction,
) -> dict:
    repository = _production(session)
    receipt = _receipt(session, project.id, payload.command_id, "production.snapshot.submit")
    if receipt:
        row = repository.snapshot(receipt.result_id)
        if not row:
            raise ProductionConflictError("COMMAND_RESULT_MISSING", "Production submission result no longer exists.")
        return execution_view(session, project)
    snapshot = repository.snapshot(snapshot_id)
    if not snapshot or snapshot.project_id != project.id:
        raise ProductionNotFoundError("Production snapshot not found")
    if project.active_snapshot_id != snapshot.id or snapshot.status != "active" or not snapshot.activated_at:
        raise ProductionConflictError("SNAPSHOT_NOT_ACTIVE", "Only the project's active snapshot can be submitted.")
    if snapshot.contract_hash != payload.expected_contract_hash:
        raise ProductionConflictError("SNAPSHOT_CONTRACT_HASH_MISMATCH", "Snapshot contract hash does not match.")
    if snapshot.cost_status != "confirmed" or snapshot.estimated_cost is None or not snapshot.currency:
        raise ProductionConflictError("SNAPSHOT_COST_NOT_CONFIRMED", "Snapshot cost is not confirmed.")
    if (
        _money(payload.expected_estimated_cost) != _money(Decimal(str(snapshot.estimated_cost)))
        or payload.expected_currency != snapshot.currency
    ):
        raise ProductionConflictError("SNAPSHOT_COST_MISMATCH", "Submitted amount or currency does not match the locked snapshot.")
    if not payload.confirm_high_risk_submission:
        raise ProductionConflictError("HIGH_RISK_SUBMISSION_CONFIRMATION_REQUIRED", "Production submission requires explicit confirmation.")

    nodes = repository.snapshot_nodes(snapshot.id, ordered=True)
    actual_ids = [node.id for node in nodes]
    if len(payload.expected_dag_node_ids) != len(set(payload.expected_dag_node_ids)):
        raise ProductionConflictError("DAG_NODE_LIST_HAS_DUPLICATES", "Submitted DAG node list contains duplicates.")
    if set(payload.expected_dag_node_ids) != set(actual_ids) or len(payload.expected_dag_node_ids) != len(actual_ids):
        raise ProductionConflictError("DAG_NODE_LIST_MISMATCH", "Submitted DAG node list must exactly match the snapshot DAG.")
    if repository.has_work_items(snapshot.id):
        raise ProductionConflictError("SNAPSHOT_ALREADY_SUBMITTED", "This snapshot already has work items.")

    now = utc_now()
    video_spec = repository.component(VideoSpecVersion, snapshot.selection["video_spec_version_id"])
    storage_policies = repository.storage_policies(snapshot.production_config_version_id)
    if len(storage_policies) != 1:
        raise ProductionConflictError("STORAGE_POLICY_COUNT_INVALID", "The frozen configuration must contain exactly one storage policy.")
    storage = storage_policies[0]
    for node in nodes:
        workflow = repository.workflow(node.workflow_slot_version_id) if node.workflow_slot_version_id else None
        provider = repository.provider(workflow.provider_config_version_id) if workflow else None
        if node.workflow_slot_version_id and (not workflow or not provider):
            raise ProductionConflictError("EXECUTION_ROUTE_REFERENCE_MISSING", f"Node {node.node_key} has an unresolved execution route.")
        if provider and provider.adapter_kind == "runninghub" and snapshot.contract.get("schema_version") != "production-snapshot.v2":
            raise ProductionConflictError(
                "SNAPSHOT_SCHEMA_UNSUPPORTED",
                "RunningHub 严格执行只接受明确冻结生成输入的新快照。",
            )
        if node.kind == "generate_keyframe" and not _verify_frozen_reference_image(
            node.input_contract.get("reference_image")
        ):
            raise ProductionConflictError(
                "REFERENCE_IMAGE_CHANGED_AFTER_SNAPSHOT",
                f"节点 {node.node_key} 的冻结参考图已变化，不能提交生产。",
            )
        manifest = _execution_manifest(snapshot, node, workflow, provider, video_spec, storage)
        fingerprint = _hash(manifest)
        item = WorkItem(
            project_id=project.id,
            snapshot_id=snapshot.id,
            dag_node_id=node.id,
            kind=node.kind,
            payload=manifest,
            status="queued",
            priority=100,
            request_fingerprint=fingerprint,
            available_at=now,
        )
        repository.add(item)
        repository.flush()
        attempt = WorkAttempt(
            work_item_id=item.id,
            attempt_number=1,
            trigger="explicit_submission",
            provider=manifest["provider_key"],
            request_fingerprint=fingerprint,
            request_manifest=manifest,
            state="created",
        )
        repository.add(attempt)
        repository.flush()
        item.current_attempt_id = attempt.id

    snapshot.status = "submitted"
    transition_project(
        session,
        project,
        ProjectStateTrigger.PRODUCTION_SUBMITTED,
        actor_type="user",
        actor_id=payload.actor_id,
        event_data={"snapshot_id": snapshot.id, "dag_node_ids": actual_ids},
    )
    _save_receipt(session, project.id, payload.command_id, "production.snapshot.submit", "production_snapshot", snapshot.id)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="production.submitted.v1",
        aggregate_type="production_snapshot",
        aggregate_id=snapshot.id,
        actor_type="user",
        actor_id=payload.actor_id,
        causation_id=payload.command_id,
        message="Production DAG submitted after explicit confirmation.",
        data={"snapshot_id": snapshot.id, "contract_hash": snapshot.contract_hash, "dag_node_ids": actual_ids},
    ))
    session.commit()
    return execution_view(session, project)


def execution_view(session: Session, project: Project) -> dict:
    repository = _production(session)
    snapshot = repository.snapshot(project.active_snapshot_id) if project.active_snapshot_id else None
    items = [] if not snapshot else repository.work_items(snapshot.id)
    if snapshot:
        nodes = repository.snapshot_nodes(snapshot.id)
        edges = repository.snapshot_edges(snapshot.id)
        node_by_id = {node.id: node for node in nodes}
        item_by_node_id = {item.dag_node_id: item for item in items}
        indegree = {node.id: 0 for node in nodes}
        children: dict[str, list[str]] = {node.id: [] for node in nodes}
        for edge in edges:
            if edge.parent_node_id not in node_by_id or edge.child_node_id not in node_by_id:
                raise ProductionConflictError(
                    "DAG_EDGE_NODE_MISSING",
                    "A persisted dependency edge references a node outside the active snapshot.",
                )
            indegree[edge.child_node_id] += 1
            children[edge.parent_node_id].append(edge.child_node_id)
        ready = sorted(
            (node_id for node_id, count in indegree.items() if count == 0),
            key=lambda node_id: (node_by_id[node_id].node_key, node_id),
        )
        ordered_node_ids: list[str] = []
        while ready:
            node_id = ready.pop(0)
            ordered_node_ids.append(node_id)
            for child_id in sorted(children[node_id], key=lambda value: (node_by_id[value].node_key, value)):
                indegree[child_id] -= 1
                if indegree[child_id] == 0:
                    ready.append(child_id)
                    ready.sort(key=lambda value: (node_by_id[value].node_key, value))
        if len(ordered_node_ids) != len(nodes):
            raise ProductionConflictError("DAG_CYCLE_DETECTED", "The active snapshot dependency graph contains a cycle.")
        items = [item_by_node_id[node_id] for node_id in ordered_node_ids if node_id in item_by_node_id]
    else:
        node_by_id = {}
    attempts = repository.work_attempts([item.id for item in items])
    attempts_by_item: dict[str, list[dict]] = {}
    for attempt in attempts:
        attempts_by_item.setdefault(attempt.work_item_id, []).append({
            column.name: getattr(attempt, column.name) for column in attempt.__table__.columns
        })
    work_rows = []
    for item in items:
        node = node_by_id[item.dag_node_id]
        work_rows.append({
            "id": item.id,
            "project_id": item.project_id,
            "snapshot_id": item.snapshot_id,
            "dag_node_id": item.dag_node_id,
            "node_key": node.node_key,
            "kind": item.kind,
            "status": item.status,
            "error": item.error,
            "priority": item.priority,
            "request_fingerprint": item.request_fingerprint,
            "current_attempt_id": item.current_attempt_id,
            "available_at": item.available_at,
            "created_at": item.created_at,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
            "attempts": attempts_by_item.get(item.id, []),
        })
    blockers = []
    for item in items:
        if item.status != "blocked":
            continue
        attempts = attempts_by_item.get(item.id, [])
        latest_attempt = attempts[-1] if attempts else None
        blockers.append({
            "work_item_id": item.id,
            "node_key": node_by_id[item.dag_node_id].node_key,
            "error_code": latest_attempt["error_code"] if latest_attempt else None,
            "error": item.error,
        })
    return {
        "project_id": project.id,
        "project_status": project.status,
        "active_snapshot_id": project.active_snapshot_id,
        "snapshot": _snapshot_dict(session, snapshot) if snapshot else None,
        "work_items": work_rows,
        "blockers": blockers,
    }


def preparation_view(session: Session, project: Project) -> dict:
    repository = _production(session)
    active_plan = repository.active_plan(project.id)
    published_configs = repository.published_configurations()
    configs = published_configs[:1]
    choices = []
    for config in configs:
        videos = repository.video_specs(config.id)
        workflows = repository.workflows(config.id)
        pricing_catalogs = repository.pricing_catalogs(config.id)
        choices.append({
            "id": config.id,
            "config_key": config.config_key,
            "version_number": config.version_number,
            "display_name": config.display_name,
            "video_specs": [{"id": row.id, "key": row.spec_key, "display_name": row.display_name, "aspect_ratio": row.aspect_ratio, "width": row.width, "height": row.height, "fps": row.fps} for row in videos],
            "workflow_slots": [{"id": row.id, "key": row.slot_key, "display_name": row.display_name, "operation_kind": row.operation_kind, "supported_video_spec_ids": row.supported_video_spec_ids} for row in workflows],
            "pricing_catalogs": [{
                "id": row.id,
                "key": row.catalog_key,
                "display_name": row.display_name,
                "currency": row.currency,
                "confirmation_threshold": row.confirmation_threshold,
                "effective_from": row.effective_from,
                "effective_to": row.effective_to,
            } for row in pricing_catalogs],
        })
    analyses = repository.impact_history(project.id)
    snapshots = repository.snapshot_history(project.id)
    if not active_plan:
        next_action = {"code": "CONFIRM_PLAN", "label": "先确认方案", "incurs_production_cost": False}
    elif not choices:
        next_action = {"code": "PUBLISH_CONFIGURATION", "label": "发布生产配置", "incurs_production_cost": False}
    elif snapshots and snapshots[0].status == "preparing" and snapshots[0].cost_status == "estimated":
        next_action = {"code": "CONFIRM_PRODUCTION_COST", "label": "确认预计费用并锁定快照", "incurs_production_cost": False}
    elif snapshots and snapshots[0].status == "preparing":
        next_action = {"code": "CONFIGURE_PRICING", "label": "发布含价格目录的新配置并创建新快照", "incurs_production_cost": False}
    elif snapshots and snapshots[0].status == "locked":
        next_action = {"code": "ACTIVATE_SNAPSHOT", "label": "确认激活锁定快照", "incurs_production_cost": False}
    elif snapshots and snapshots[0].status == "active":
        next_action = {"code": "SUBMIT_PRODUCTION", "label": "确认完整 DAG 并提交生产", "incurs_production_cost": True}
    elif snapshots and snapshots[0].status == "submitted":
        next_action = {"code": "VIEW_PRODUCTION", "label": "查看生产执行", "incurs_production_cost": False}
    else:
        next_action = {"code": "ANALYZE_PRODUCTION_IMPACT", "label": "选择精确配置并分析生产影响", "incurs_production_cost": False}
    return {
        "project_id": project.id,
        "active_plan_id": active_plan.id if active_plan else None,
        "audio_mode": str(active_plan.creative_brief.get("audio_mode", "")) if active_plan else "",
        "published_configurations": choices,
        "analyses": [_impact_dict(item) for item in analyses],
        "snapshots": [_snapshot_dict(session, item) for item in snapshots],
        "next_action": next_action,
    }
