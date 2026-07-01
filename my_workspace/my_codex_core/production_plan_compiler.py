from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .production_entities import (
    collect_entity_references,
    enrich_global_context_with_entities,
    entity_context_for_ids,
    load_production_entities,
)
from .production_parameter_policy import (
    apply_locked_parameters_to_intent,
    attach_parameter_lock_metadata,
    normalize_parameter_policy_context,
)


PLAN_SCHEMA_VERSION = 1
DEFAULT_TEMPLATE_PATH = Path("my_workspace/my_production_templates/production_templates.json")
DEFAULT_ENTITY_PATH = Path("my_workspace/my_production_entities/production_entities.json")
PRODUCTION_TYPES = {"drama_story", "product_promo", "talking_avatar", "asset_only", "custom"}
FRAME_ROLE_SUFFIX = {
    "start": "start_frame",
    "first": "start_frame",
    "middle": "middle_frame",
    "mid": "middle_frame",
    "end": "end_frame",
    "last": "end_frame",
}


def compile_production_plan(
    *,
    task_id: str,
    route_content: str = "",
    image_content: str = "",
    video_content: str = "",
    audio_content: str = "",
    package_content: str = "",
    existing_payload: dict[str, Any] | None = None,
    video_config: dict[str, Any] | None = None,
    voice_config: dict[str, Any] | None = None,
    template_path: Path | None = None,
    entity_path: Path | None = None,
) -> dict[str, Any]:
    """Compile digital-staff production intents into a conservative production plan.

    This is the first bridge between the new four-layer architecture and the
    existing production path. It does not replace the ComfyUI/FFmpeg scheduler;
    it produces a normalized `production_plan.json` plus a merged compatibility
    payload that still contains `image_prompts` and `video_prompts`.
    """

    templates = load_production_templates(template_path or DEFAULT_TEMPLATE_PATH)
    route_payload = _json_object_from_text(route_content)
    image_payload = _json_object_from_text(image_content)
    video_payload = _json_object_from_text(video_content)
    audio_payload = _json_object_from_text(audio_content)
    package_payload = _json_object_from_text(package_content)

    route = _route_from_payload(route_payload, route_content, video_config or {})
    production_type = str(route.get("production_type") or "custom").strip()
    if production_type not in PRODUCTION_TYPES:
        production_type = "custom"
        route["production_type"] = production_type
    selected_template = _template_for_type(templates, production_type)
    global_context = _global_context_from_sources(
        route=route,
        payloads=[image_payload, video_payload, audio_payload, package_payload, existing_payload or {}],
        templates=templates,
        video_config=video_config or {},
    )
    production_intents = {
        "image": _intent_list(image_payload, "image"),
        "video": _intent_list(video_payload, "video"),
        "audio": _intent_list(audio_payload, "audio"),
        "package": _intent_list(package_payload, "package"),
    }
    entity_registry = load_production_entities(entity_path or DEFAULT_ENTITY_PATH)
    entity_references = collect_entity_references(
        production_intents,
        [route_payload, image_payload, video_payload, audio_payload, package_payload, existing_payload or {}],
    )
    global_context, resolved_entities, entity_notes = enrich_global_context_with_entities(global_context, entity_registry, entity_references)
    global_context = normalize_parameter_policy_context(global_context, route=route, video_config=video_config or {})
    compat_payload = json.loads(json.dumps(existing_payload or {}, ensure_ascii=False))
    compile_notes: list[str] = [*entity_notes]
    parameter_overrides: list[dict[str, Any]] = []

    image_prompts, image_jobs = _compile_image_intents(
        production_intents["image"],
        templates,
        global_context,
        resolved_entities,
        compile_notes,
        parameter_overrides,
    )
    video_prompts, video_jobs = _compile_video_intents(
        production_intents["video"],
        templates,
        global_context,
        resolved_entities,
        image_jobs,
        compile_notes,
        parameter_overrides,
    )

    _prefer_compiled_compat_list(compat_payload, "image_prompts", image_prompts, image_payload.get("image_prompts"))
    _prefer_compiled_compat_list(compat_payload, "video_prompts", video_prompts, video_payload.get("video_prompts"))
    _merge_compat_list(compat_payload, "reference_images", image_payload.get("reference_images"))
    _merge_compat_list(compat_payload, "reference_images", video_payload.get("reference_images"))
    _merge_compat_list(compat_payload, "reference_images", route_payload.get("reference_images"))

    for key in ("negative_prompt", "seed", "width", "height", "duration", "fps", "aspect_ratio"):
        for payload in (video_payload, image_payload, route_payload):
            value = payload.get(key)
            if value not in (None, "", []):
                compat_payload.setdefault(key, value)

    compat_payload["production_type"] = production_type
    compat_payload["production_route"] = route
    compat_payload["production_intents"] = production_intents
    legacy_global_context = compat_payload.get("global_context") if isinstance(compat_payload.get("global_context"), dict) else {}
    compat_payload["global_context"] = _deep_merge_dict(legacy_global_context, global_context)
    render = compat_payload["global_context"].get("render") if isinstance(compat_payload["global_context"].get("render"), dict) else {}
    compat_payload["width"] = _positive_int(render.get("working_width"), 848)
    compat_payload["height"] = _positive_int(render.get("working_height"), 480)
    compat_payload["fps"] = _positive_int(render.get("frame_rate"), 24)
    if parameter_overrides:
        compat_payload["parameter_overrides"] = parameter_overrides
        policy = compat_payload["global_context"].get("parameter_policy") if isinstance(compat_payload["global_context"].get("parameter_policy"), dict) else {}
        policy["overrides"] = parameter_overrides
        compat_payload["global_context"]["parameter_policy"] = policy
    compat_payload["payload_source"] = "production_plan_compiler"
    compat_payload["notes"] = "由数字员工production_intents编译生成，并保留image_prompts/video_prompts兼容现有执行链路。"

    visual_jobs = [*_jobs_from_prompts(compat_payload.get("image_prompts"), "image"), *_jobs_from_prompts(compat_payload.get("video_prompts"), "video")]
    plan = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "task_id": str(task_id),
        "created_at": time.time(),
        "architecture_layers": [
            {
                "layer": 1,
                "name": "数字员工意图层",
                "status": "implemented",
                "contract": "01输出production_type，06/07/20/22输出production_intents并保留旧字段"
            },
            {
                "layer": 2,
                "name": "生产模板层",
                "status": "implemented",
                "contract": "production_type选择模板，模板声明默认阶段、意图和推荐工作流"
            },
            {
                "layer": 3,
                "name": "意图编译层",
                "status": "implemented",
                "contract": "production_intents编译为兼容payload、visual_jobs和后续production_graph输入"
            },
            {
                "layer": 4,
                "name": "执行调度适配层",
                "status": "wired_to_existing_pipeline",
                "contract": "继续复用production_graph、CloudComfyUIAdapter、本地TTS和FFmpeg封装"
            }
        ],
        "selected_template": {
            "production_type": production_type,
            "name": selected_template.get("name") or production_type,
            "description": selected_template.get("description") or "",
            "notes": selected_template.get("notes") or ""
        },
        "route": route,
        "global_context": global_context,
        "resolved_entities": resolved_entities,
        "production_intents": production_intents,
        "compiled_payload": compat_payload,
        "visual_jobs": visual_jobs,
        "audio_intents": production_intents["audio"],
        "package_intents": production_intents["package"],
        "compile_notes": compile_notes,
        "parameter_policy": compat_payload["global_context"].get("parameter_policy") if isinstance(compat_payload["global_context"].get("parameter_policy"), dict) else {},
        "parameter_overrides": parameter_overrides,
        "compatibility": {
            "image_prompts_count": len(compat_payload.get("image_prompts") if isinstance(compat_payload.get("image_prompts"), list) else []),
            "video_prompts_count": len(compat_payload.get("video_prompts") if isinstance(compat_payload.get("video_prompts"), list) else []),
            "legacy_fields_preserved": True,
            "compiler_is_authoritative_for_dag": bool(image_prompts or video_prompts)
        }
    }
    return plan


def load_production_templates(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_TEMPLATE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("global_defaults", {"render": {"working_width": 848, "working_height": 480, "delivery_width": 1920, "delivery_height": 1080, "frame_rate": 24, "aspect_ratio": "16:9"}})
    data.setdefault("workflow_contracts", {"image": {}, "video": {}, "audio": {}, "package": {}})
    data.setdefault("templates", {})
    return data


def write_production_plan(path: Path, plan: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _route_from_payload(payload: dict[str, Any], text: str, video_config: dict[str, Any]) -> dict[str, Any]:
    route = payload if isinstance(payload, dict) else {}
    production_type = str(route.get("production_type") or "").strip()
    if production_type not in PRODUCTION_TYPES:
        production_type = _infer_production_type(text)
    aspect_ratio = str(route.get("aspect_ratio") or video_config.get("aspect_ratio") or "16:9")
    return {
        "production_type": production_type,
        "target_platform": str(route.get("target_platform") or route.get("platform") or "未指定"),
        "aspect_ratio": aspect_ratio,
        "needs_voiceover": _bool_or_default(route.get("needs_voiceover"), default=True),
        "needs_final_video": _bool_or_default(route.get("needs_final_video"), default=production_type != "asset_only"),
        "quality_mode": str(route.get("quality_mode") or "standard"),
        "routing_reason": str(route.get("routing_reason") or "由production_plan_compiler根据01输出或关键词推断。"),
    }


def _infer_production_type(text: str) -> str:
    lowered = str(text or "").lower()
    if any(word in lowered for word in ("带货", "产品", "商品", "卖点", "转化", "product", "promo")):
        return "product_promo"
    if any(word in lowered for word in ("口播", "数字人", "虚拟主播", "talking", "avatar", "lip")):
        return "talking_avatar"
    if any(word in lowered for word in ("只要素材", "素材", "asset_only", "asset only")):
        return "asset_only"
    if any(word in lowered for word in ("漫剧", "剧情", "短剧", "角色", "分镜", "story", "drama")):
        return "drama_story"
    return "custom"


def _template_for_type(templates: dict[str, Any], production_type: str) -> dict[str, Any]:
    items = templates.get("templates") if isinstance(templates.get("templates"), dict) else {}
    template = items.get(production_type) if isinstance(items.get(production_type), dict) else {}
    if not template and production_type != "custom":
        template = items.get("custom") if isinstance(items.get("custom"), dict) else {}
    return template if isinstance(template, dict) else {}


def _global_context_from_sources(
    *,
    route: dict[str, Any],
    payloads: list[dict[str, Any]],
    templates: dict[str, Any],
    video_config: dict[str, Any],
) -> dict[str, Any]:
    render_defaults = ((templates.get("global_defaults") or {}).get("render") or {}) if isinstance(templates.get("global_defaults"), dict) else {}
    context: dict[str, Any] = {
        "characters": [],
        "style": {},
        "render": {
            "working_width": _positive_int(video_config.get("working_width") or video_config.get("width") or render_defaults.get("working_width"), 848),
            "working_height": _positive_int(video_config.get("working_height") or video_config.get("height") or render_defaults.get("working_height"), 480),
            "delivery_width": _positive_int(video_config.get("delivery_width") or render_defaults.get("delivery_width"), 1920),
            "delivery_height": _positive_int(video_config.get("delivery_height") or render_defaults.get("delivery_height"), 1080),
            "frame_rate": _positive_int(video_config.get("fps") or render_defaults.get("frame_rate"), 24),
            "aspect_ratio": str(route.get("aspect_ratio") or video_config.get("aspect_ratio") or render_defaults.get("aspect_ratio") or "16:9"),
        },
    }
    for payload in payloads:
        source = payload.get("global_context") if isinstance(payload.get("global_context"), dict) else {}
        if source:
            context = _deep_merge_dict(context, source)
        style_id = str(payload.get("style_id") or "").strip()
        if style_id:
            context.setdefault("style", {}).setdefault("style_id", style_id)
        character_id = str(payload.get("character_id") or "").strip()
        if character_id:
            _upsert_character(context, {"character_id": character_id})
    return context


def _intent_list(payload: dict[str, Any], group: str) -> list[dict[str, Any]]:
    production_intents = payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {}
    values = production_intents.get(group) if isinstance(production_intents, dict) else None
    if isinstance(values, list):
        return [dict(item) for item in values if isinstance(item, dict)]
    return []


def _compile_image_intents(
    intents: list[dict[str, Any]],
    templates: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    notes: list[str],
    parameter_overrides: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prompts: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    contracts = ((templates.get("workflow_contracts") or {}).get("image") or {}) if isinstance(templates.get("workflow_contracts"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    for index, intent in enumerate(intents, 1):
        intent_name = str(intent.get("intent") or "").strip()
        intent_id = _safe_id(intent.get("intent_id") or intent.get("id") or f"image_intent_{index:03d}")
        contract = contracts.get(intent_name) if isinstance(contracts.get(intent_name), dict) else {}
        compatibility = intent.get("compatibility") if isinstance(intent.get("compatibility"), dict) else {}
        locked_intent, overrides = apply_locked_parameters_to_intent(
            intent,
            global_context=global_context,
            intent_kind="image",
            intent_name=intent_name,
            notes=notes,
        )
        intent = locked_intent
        parameter_overrides.extend(_tag_overrides(overrides, intent_id, "image"))
        if intent_name == "generate_three_frame_shot":
            frame_set = intent.get("frame_set") if isinstance(intent.get("frame_set"), list) else []
            if not frame_set:
                frame_set = [
                    {"role": "start", "prompt": str(intent.get("prompt") or intent.get("description") or "")},
                    {"role": "middle", "prompt": str(intent.get("middle_prompt") or intent.get("prompt") or intent.get("description") or "")},
                    {"role": "end", "prompt": str(intent.get("end_prompt") or intent.get("prompt") or intent.get("description") or "")},
                ]
            for frame in frame_set:
                if not isinstance(frame, dict):
                    continue
                role = _frame_role(frame.get("role"))
                prompt = str(frame.get("prompt") or frame.get("description") or intent.get("prompt") or "").strip()
                if not prompt:
                    notes.append(f"image intent {intent_id}:{role} skipped because prompt is empty")
                    continue
                job_id = f"{intent_id}_{FRAME_ROLE_SUFFIX.get(role, role + '_frame')}"
                item = _image_prompt_item(
                    job_id=job_id,
                    prompt=prompt,
                    intent=intent,
                    contract=contract,
                    compatibility=compatibility,
                    render=render,
                    asset_tag=f"{intent_id}_{role}",
                    resolved_entities=resolved_entities,
                )
                attach_parameter_lock_metadata(
                    item,
                    global_context=global_context,
                    intent_kind="image",
                    intent_name=intent_name,
                    overrides=overrides,
                )
                item["frame_role"] = role
                prompts.append(item)
                jobs.append({"job_id": job_id, "intent_id": intent_id, "frame_role": role, **item})
            continue
        prompt = str(intent.get("prompt") or intent.get("description") or intent.get("visual_description") or "").strip()
        if not prompt:
            notes.append(f"image intent {intent_id} skipped because prompt is empty")
            continue
        item = _image_prompt_item(
            job_id=intent_id,
            prompt=prompt,
            intent=intent,
            contract=contract,
            compatibility=compatibility,
            render=render,
            asset_tag=str(intent.get("asset_role") or intent.get("asset_tag") or intent_name or intent_id),
            resolved_entities=resolved_entities,
        )
        attach_parameter_lock_metadata(
            item,
            global_context=global_context,
            intent_kind="image",
            intent_name=intent_name,
            overrides=overrides,
        )
        prompts.append(item)
        jobs.append({"job_id": intent_id, "intent_id": intent_id, **item})
    return prompts, jobs


def _compile_video_intents(
    intents: list[dict[str, Any]],
    templates: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    image_jobs: list[dict[str, Any]],
    notes: list[str],
    parameter_overrides: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prompts: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    contracts = ((templates.get("workflow_contracts") or {}).get("video") or {}) if isinstance(templates.get("workflow_contracts"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    image_job_ids = {str(job.get("job_id")) for job in image_jobs if job.get("job_id")}
    for index, intent in enumerate(intents, 1):
        intent_name = str(intent.get("intent") or "").strip()
        intent_id = _safe_id(intent.get("intent_id") or intent.get("id") or f"video_intent_{index:03d}")
        contract = contracts.get(intent_name) if isinstance(contracts.get(intent_name), dict) else {}
        compatibility = intent.get("compatibility") if isinstance(intent.get("compatibility"), dict) else {}
        locked_intent, overrides = apply_locked_parameters_to_intent(
            intent,
            global_context=global_context,
            intent_kind="video",
            intent_name=intent_name,
            notes=notes,
        )
        intent = locked_intent
        parameter_overrides.extend(_tag_overrides(overrides, intent_id, "video"))
        prompt = str(intent.get("prompt") or intent.get("motion_plan") or intent.get("description") or intent.get("edit_note") or "").strip()
        if not prompt and intent_name == "enhance_video":
            prompt = "对上游视频进行补帧、放大、稳定和画质增强。"
        if not prompt:
            notes.append(f"video intent {intent_id} skipped because prompt is empty")
            continue
        entity_context = entity_context_for_ids(
            resolved_entities,
            character_id=str(intent.get("character_id") or ""),
            style_id=str(intent.get("style_id") or ""),
            product_id=str(intent.get("product_id") or ""),
            scene_id=str(intent.get("scene_id") or intent.get("shot_id") or ""),
        )
        item = {
            "id": intent_id,
            "job_id": intent_id,
            "task_type": "video",
            "video_task_mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent_name),
            "mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent_name),
            "workflow_id": str(compatibility.get("recommended_workflow_id") or contract.get("workflow_id") or ""),
            "workflow_mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent_name),
            "capability": str(contract.get("capability") or "video_generate"),
            "prompt": prompt,
            "negative_prompt": str(intent.get("negative_prompt") or ""),
            "duration": _positive_int(intent.get("duration_seconds") or contract.get("duration_seconds"), 4),
            "fps": _positive_int(intent.get("fps") or contract.get("fps") or render.get("frame_rate"), 24),
            "width": _positive_int(intent.get("width") or render.get("working_width"), 848),
            "height": _positive_int(intent.get("height") or render.get("working_height"), 480),
            "character_id": str(intent.get("character_id") or ""),
            "style_id": str(intent.get("style_id") or ""),
            "product_id": str(intent.get("product_id") or ""),
            "scene_id": str(intent.get("scene_id") or intent.get("shot_id") or ""),
            "asset_tag": str(intent.get("asset_tag") or intent_name or intent_id),
            "source_intent_ids": _string_list(intent.get("source_intent_ids")),
            "depends_on": _string_list(intent.get("depends_on")),
            "input_bindings": intent.get("input_bindings") if isinstance(intent.get("input_bindings"), dict) else {},
        }
        if _as_bool(
            intent.get("optional_when_unconfigured"),
            default=_as_bool(contract.get("optional_when_unconfigured"), default=intent_name == "enhance_video"),
        ):
            item["optional_when_unconfigured"] = True
        if entity_context:
            item["entity_context"] = entity_context
            _merge_compat_list(item, "reference_images", entity_context.get("reference_assets"))
        attach_parameter_lock_metadata(
            item,
            global_context=global_context,
            intent_kind="video",
            intent_name=intent_name,
            overrides=overrides,
        )
        if intent_name == "generate_three_frame_i2v_clip":
            _bind_three_frames(item, image_job_ids)
        elif intent_name in {"generate_i2v_clip", "enhance_video", "repair_video"}:
            _bind_first_source_image(item, image_job_ids)
        elif intent_name == "generate_talking_image":
            item.setdefault("depends_on", [])
            if "local_tts" not in item["depends_on"]:
                item["depends_on"].append("local_tts")
            item["requires_audio"] = True
        prompts.append(item)
        jobs.append(dict(item))
    return prompts, jobs


def _image_prompt_item(
    *,
    job_id: str,
    prompt: str,
    intent: dict[str, Any],
    contract: dict[str, Any],
    compatibility: dict[str, Any],
    render: dict[str, Any],
    asset_tag: str,
    resolved_entities: dict[str, Any],
) -> dict[str, Any]:
    entity_context = entity_context_for_ids(
        resolved_entities,
        character_id=str(intent.get("character_id") or ""),
        style_id=str(intent.get("style_id") or ""),
        product_id=str(intent.get("product_id") or ""),
        scene_id=str(intent.get("scene_id") or intent.get("shot_id") or ""),
    )
    item = {
        "id": job_id,
        "job_id": job_id,
        "task_type": "image",
        "image_task_mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent.get("intent") or "image"),
        "control_mode": str(intent.get("control_mode") or "none"),
        "mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent.get("intent") or "image"),
        "workflow_id": str(compatibility.get("recommended_workflow_id") or contract.get("workflow_id") or ""),
        "workflow_mode": str(compatibility.get("recommended_workflow_mode") or contract.get("workflow_mode") or intent.get("intent") or "image"),
        "capability": str(contract.get("capability") or "image_generate"),
        "prompt": prompt,
        "negative_prompt": str(intent.get("negative_prompt") or ""),
        "width": _positive_int(intent.get("width") or render.get("working_width"), 848),
        "height": _positive_int(intent.get("height") or render.get("working_height"), 480),
        "character_id": str(intent.get("character_id") or ""),
        "style_id": str(intent.get("style_id") or ""),
        "product_id": str(intent.get("product_id") or ""),
        "scene_id": str(intent.get("scene_id") or intent.get("shot_id") or ""),
        "asset_tag": asset_tag,
        "depends_on": _string_list(intent.get("depends_on")),
        "input_bindings": intent.get("input_bindings") if isinstance(intent.get("input_bindings"), dict) else {},
        "reference_image": str(intent.get("reference_image") or intent.get("input_base_image") or ""),
        "reference_images": intent.get("reference_images") if isinstance(intent.get("reference_images"), list) else [],
    }
    if entity_context:
        item["entity_context"] = entity_context
        _merge_compat_list(item, "reference_images", entity_context.get("reference_assets"))
    return item


def _bind_three_frames(item: dict[str, Any], image_job_ids: set[str]) -> None:
    source_ids = _string_list(item.get("source_intent_ids"))
    if not source_ids:
        return
    base = _safe_id(source_ids[0])
    candidates = {
        "input_base_image": f"{base}_start_frame",
        "input_middle_frame": f"{base}_middle_frame",
        "input_last_frame": f"{base}_end_frame",
    }
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    for slot, job_id in candidates.items():
        if job_id in image_job_ids:
            bindings.setdefault(slot, {"from_job": job_id, "output": "output_final_image"})
            if job_id not in depends_on:
                depends_on.append(job_id)


def _bind_first_source_image(item: dict[str, Any], image_job_ids: set[str]) -> None:
    source_ids = _string_list(item.get("source_intent_ids"))
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    for source_id in source_ids:
        candidate = _safe_id(source_id)
        fallback = f"{candidate}_start_frame"
        job_id = candidate if candidate in image_job_ids else fallback if fallback in image_job_ids else ""
        if job_id:
            bindings.setdefault("input_base_image", {"from_job": job_id, "output": "output_final_image"})
            if job_id not in depends_on:
                depends_on.append(job_id)
            return


def _jobs_from_prompts(values: Any, job_type: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(values, 1):
        if isinstance(item, dict):
            jobs.append(
                {
                    "job_id": str(item.get("job_id") or item.get("id") or f"{job_type}_{index:03d}"),
                    "type": job_type,
                    "capability": str(item.get("capability") or ("video_generate" if job_type == "video" else "image_generate")),
                    "mode": str(item.get("workflow_mode") or item.get("mode") or ""),
                    "workflow_id": str(item.get("workflow_id") or ""),
                    "depends_on": _string_list(item.get("depends_on")),
                    "input_bindings": item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {},
                    "character_id": str(item.get("character_id") or ""),
                    "style_id": str(item.get("style_id") or ""),
                    "product_id": str(item.get("product_id") or ""),
                    "scene_id": str(item.get("scene_id") or ""),
                    "width": item.get("width") or "",
                    "height": item.get("height") or "",
                    "duration": item.get("duration") or item.get("duration_seconds") or "",
                    "fps": item.get("fps") or "",
                    "prompt": str(item.get("prompt") or "")[:500],
                    "optional_when_unconfigured": _as_bool(
                        item.get("optional_when_unconfigured"),
                        default=str(item.get("workflow_mode") or item.get("mode") or "").strip() == "enhance_video"
                        or str(item.get("capability") or "").strip() == "video_enhance",
                    ),
                    "parameter_locks": item.get("parameter_locks") if isinstance(item.get("parameter_locks"), dict) else {},
                    "locked_fields": item.get("locked_fields") if isinstance(item.get("locked_fields"), list) else [],
                }
            )
    return jobs


def _json_object_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "")
    candidates = []
    candidates.extend(match.group(1) for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE))
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _merge_compat_list(target: dict[str, Any], key: str, values: Any) -> None:
    if not isinstance(values, list) or not values:
        return
    current = target.get(key)
    if not isinstance(current, list):
        current = []
    seen = {_identity_for_item(item) for item in current}
    for item in values:
        identity = _identity_for_item(item)
        if identity and identity in seen:
            continue
        current.append(item)
        if identity:
            seen.add(identity)
    target[key] = current


def _prefer_compiled_compat_list(target: dict[str, Any], key: str, compiled_values: Any, legacy_values: Any) -> None:
    compiled_list = [item for item in compiled_values if isinstance(item, dict)] if isinstance(compiled_values, list) else []
    legacy_list = [item for item in legacy_values if isinstance(item, dict)] if isinstance(legacy_values, list) else []
    if compiled_list:
        if legacy_list:
            target[f"legacy_{key}"] = legacy_list
        target[key] = compiled_list
        return
    _merge_compat_list(target, key, legacy_list)


def _identity_for_item(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("job_id") or item.get("id") or item.get("prompt_id") or item.get("asset_tag") or "").strip()
    return str(item).strip()


def _tag_overrides(overrides: list[dict[str, Any]], intent_id: str, intent_kind: str) -> list[dict[str, Any]]:
    return [
        {
            "intent_id": intent_id,
            "intent_kind": intent_kind,
            **override,
        }
        for override in overrides
        if isinstance(override, dict)
    ]


def _deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, ensure_ascii=False))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge_dict(result[key], value)
        elif value not in (None, "", []):
            result[key] = value
    return result


def _upsert_character(context: dict[str, Any], item: dict[str, Any]) -> None:
    characters = context.setdefault("characters", [])
    if not isinstance(characters, list):
        context["characters"] = characters = []
    character_id = str(item.get("character_id") or "").strip()
    if not character_id:
        return
    existing = next((entry for entry in characters if isinstance(entry, dict) and str(entry.get("character_id") or "") == character_id), None)
    if existing is None:
        characters.append(item)
    else:
        existing.update({key: value for key, value in item.items() if value not in (None, "", [])})


def _bool_or_default(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "是", "需要"}:
        return True
    if text in {"false", "0", "no", "n", "否", "不需要"}:
        return False
    return default


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _safe_id(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", text)
    return text.strip("_") or "intent"


def _frame_role(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"first", "start", "首帧", "开始"}:
        return "start"
    if text in {"middle", "mid", "中帧", "中间"}:
        return "middle"
    if text in {"last", "end", "尾帧", "结束"}:
        return "end"
    return text or "frame"
