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
    link_production_entities_to_assets,
    load_asset_library,
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
DEFAULT_ASSET_LIBRARY_PATH = Path("my_workspace/my_asset_library/library.json")
PRODUCTION_TYPES = {"drama_story", "product_promo", "talking_avatar", "asset_only", "custom"}
FRAME_ROLE_SUFFIX = {
    "start": "start_frame",
    "first": "start_frame",
    "middle": "middle_frame",
    "mid": "middle_frame",
    "end": "end_frame",
    "last": "end_frame",
}


IMAGE_INTENT_ROUTES = {
    "generate_keyframe": ("04_keyframe", "keyframe"),
    "generate_three_frame_shot": ("04_keyframe", "keyframe"),
    "generate_cover_key_visual": ("03_style_cover_image", "cover_key_visual"),
    "repair_or_cutout_image": ("05_image_repair_cutout", "image_inpaint_fix"),
}
VIDEO_INTENT_ROUTES = {
    "generate_i2v_clip": ("06_i2v_first_frame", "i2v_first_frame"),
    "generate_three_frame_i2v_clip": ("06_i2v_first_middle_last_frame", "i2v_first_middle_last_frame"),
    "generate_broll_clip": ("10_broll_transition_video", "broll_scene_video"),
    "generate_talking_image": ("09_talking_image", "talking_image"),
    "enhance_video": ("11_video_enhance", "video_upscale"),
    "repair_video": ("12_video_inpaint_fix", "video_inpaint_fix"),
    "stylize_live_video": ("07_live_to_anime", "live_to_anime"),
    "transfer_motion": ("08_motion_transfer", "motion_transfer"),
}
WORKFLOW_ID_ALIASES = {
    "10_broll_transition": "10_broll_transition_video",
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
    asset_library_path: Path | None = None,
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
    entity_registry = link_production_entities_to_assets(
        load_production_entities(entity_path or DEFAULT_ENTITY_PATH),
        load_asset_library(asset_library_path or DEFAULT_ASSET_LIBRARY_PATH),
    )
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
        if intent_name == "no_image_required":
            notes.append(f"image intent {intent_id} declares no image generation is required; skipped image material jobs")
            continue
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
                    notes=notes,
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
            notes=notes,
        )
        _apply_animal_character_base_policy(item, intent, prompts, notes)
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


def _apply_animal_character_base_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    existing_items: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> None:
    if str(intent.get("intent") or "").strip() != "generate_base_asset":
        return
    if str(intent.get("asset_role") or "character").strip().lower() != "character":
        return
    character_text = " ".join(
        str(value or "")
        for value in (
            intent.get("character_id"),
            intent.get("asset_tag"),
            intent.get("prompt"),
            intent.get("description"),
            item.get("prompt"),
        )
    ).lower()
    is_animal = _looks_like_animal_character(character_text)
    if not is_animal:
        return
    if is_animal and _looks_like_turnaround_sheet(character_text):
        item["animal_character_reference_sheet"] = True
        item["prompt"] = _append_prompt_once(
            str(item.get("prompt") or ""),
            "动物角色设定图要求：保持四足动物解剖结构，不要人型骨架，不要人类站姿，不要拟人化成人体；在同一张图中呈现同一只动物的正面、侧面、背面/背部视角，毛色、耳朵、眼睛、体型和尾巴完全一致，干净白底，无文字水印。",
        )
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} kept on animal-safe character_base instead of humanoid turnaround")

    if not _looks_like_expression_sheet(character_text):
        return
    reference_job = _previous_character_reference_job(existing_items, str(item.get("character_id") or ""))
    if not reference_job:
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} has no previous character reference to bind for expression consistency")
        return

    reference_job_id = str(reference_job.get("job_id") or reference_job.get("id") or "").strip()
    if not reference_job_id:
        return
    item["workflow_id"] = "04_keyframe"
    item["workflow_mode"] = "img2img_style_keyframe"
    item["image_task_mode"] = "img2img_style_keyframe"
    item["mode"] = "img2img_style_keyframe"
    item["control_mode"] = "img2img_style"
    item["input_bindings"] = {
        **(item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}),
        "input_base_image": {"from_job": reference_job_id, "output": "output_final_image"},
    }
    item["depends_on"] = list(dict.fromkeys([*_string_list(item.get("depends_on")), reference_job_id]))
    item["input_reference_style"] = {"from_job": reference_job_id, "output": "output_final_image"}
    item["denoise"] = intent.get("denoise") or 0.38
    item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.72
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        "参考上一张角色设定图，必须保持同一只动物的毛色分布、耳朵形状、眼睛、鼻口、体型比例和尾巴一致；只改变表情和轻微动作，不改变物种，不变成人型。",
    )
    if notes is not None:
        notes.append(f"image intent {item.get('job_id')} routed to img2img_style_keyframe using {reference_job_id} for character consistency")


def _looks_like_animal_character(text: str) -> bool:
    return any(
        token in text
        for token in (
            "dog",
            "corgi",
            "cat",
            "rabbit",
            "fox",
            "bear",
            "panda",
            "puppy",
            "kitten",
            "animal",
            "狗",
            "柯基",
            "猫",
            "兔",
            "狐狸",
            "熊",
            "熊猫",
            "动物",
            "宠物",
            "尾巴",
            "爪",
        )
    )


def _looks_like_turnaround_sheet(text: str) -> bool:
    return any(token in text for token in ("turnaround", "three view", "three-view", "三视图", "正面", "侧面", "背面"))


def _looks_like_expression_sheet(text: str) -> bool:
    return any(token in text for token in ("emotion", "expression", "emotions", "expressions", "表情", "情绪", "表情图"))


def _append_prompt_once(prompt: str, addition: str) -> str:
    base = str(prompt or "").strip()
    extra = str(addition or "").strip()
    if not extra or extra in base:
        return base
    return f"{base} {extra}".strip()


def _previous_character_reference_job(items: list[dict[str, Any]], character_id: str) -> dict[str, Any] | None:
    target = str(character_id or "").strip()
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        if target and str(item.get("character_id") or "").strip() != target:
            continue
        mode = str(item.get("mode") or item.get("workflow_mode") or "").strip()
        job_id = str(item.get("job_id") or item.get("id") or "").strip().lower()
        if _looks_like_expression_sheet(job_id):
            continue
        if mode in {"character_base", "character_turnaround", "img2img_style_keyframe", "identity_keyframe"} or str(item.get("asset_tag") or "").strip().lower() in {"character", "character_base"}:
            return item
    return None


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
    video_job_ids: set[str] = set()
    for index, intent in enumerate(intents, 1):
        intent_name = str(intent.get("intent") or "").strip()
        intent_id = _safe_id(intent.get("intent_id") or intent.get("id") or f"video_intent_{index:03d}")
        contract = contracts.get(intent_name) if isinstance(contracts.get(intent_name), dict) else {}
        compatibility = intent.get("compatibility") if isinstance(intent.get("compatibility"), dict) else {}
        workflow_id, workflow_mode = _video_workflow_route(intent_name, intent, contract, compatibility)
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
            "video_task_mode": workflow_mode,
            "mode": workflow_mode,
            "workflow_id": workflow_id,
            "workflow_mode": workflow_mode,
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
            "source_video": str(intent.get("input_source_video") or intent.get("source_video") or intent.get("reference_video") or ""),
            "identity_image": str(intent.get("input_identity_image") or intent.get("identity_image") or ""),
            "pose_image": str(intent.get("input_pose_image") or intent.get("pose_image") or ""),
        }
        if _bool_or_default(
            intent.get("optional_when_unconfigured"),
            default=_bool_or_default(
                contract.get("optional_when_unconfigured"),
                default=intent_name in {"enhance_video", "generate_talking_image"},
            ),
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
        elif intent_name == "generate_i2v_clip":
            _bind_first_source_image(item, image_job_ids)
            if not _has_bound_first_frame(item):
                inferred = _bind_matching_keyframe_by_id(item, image_job_ids)
                if inferred:
                    notes.append(f"video intent {intent_id} inferred first frame from {inferred}")
                else:
                    workflow_id, workflow_mode = VIDEO_INTENT_ROUTES["generate_broll_clip"]
                    item["workflow_id"] = workflow_id
                    item["workflow_mode"] = workflow_mode
                    item["video_task_mode"] = workflow_mode
                    item["mode"] = workflow_mode
                    item["capability"] = "video_generate"
                    notes.append(
                        f"video intent {intent_id} downgraded to text-to-video because no source image/frame was available"
                    )
        elif intent_name in {"enhance_video", "repair_video", "stylize_live_video"}:
            _bind_first_source_video(item, video_job_ids)
        elif intent_name == "transfer_motion":
            _bind_first_source_video(item, video_job_ids)
            _bind_first_source_image(item, image_job_ids, slot="input_identity_image")
        elif intent_name == "generate_talking_image":
            item.setdefault("depends_on", [])
            if "local_tts" not in item["depends_on"]:
                item["depends_on"].append("local_tts")
            item["requires_audio"] = True
        prompts.append(item)
        jobs.append(dict(item))
        video_job_ids.add(intent_id)
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
    notes: list[str] | None = None,
) -> dict[str, Any]:
    intent_name = str(intent.get("intent") or "").strip()
    workflow_id, workflow_mode = _image_workflow_route(intent_name, intent, contract, compatibility)
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
        "image_task_mode": workflow_mode,
        "control_mode": str(intent.get("control_mode") or "none"),
        "mode": workflow_mode,
        "workflow_id": workflow_id,
        "workflow_mode": workflow_mode,
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
    character_references = _character_references_from_intent(intent, resolved_entities)
    if character_references:
        raw_characters = intent.get("characters") if isinstance(intent.get("characters"), list) else []
        if len(raw_characters) > 4 and notes is not None:
            notes.append(f"image intent {job_id} has {len(raw_characters)} character references; only the first 4 are passed to multi-character keyframe generation")
        item["character_references"] = character_references
        _merge_compat_list(item, "reference_images", [entry["identity_image"] for entry in character_references if entry.get("identity_image")])
        if len(character_references) > 4:
            item["character_references"] = character_references[:4]
    references = item.get("reference_images") if isinstance(item.get("reference_images"), list) else []
    first_reference = str(item.get("reference_image") or (references[0] if references and isinstance(references[0], str) else ""))
    pose_reference = str(intent.get("pose_layout_image") or intent.get("composition_reference") or intent.get("input_pose_image") or intent.get("pose_image") or "").strip()
    style_reference = str(
        intent.get("input_reference_style")
        or intent.get("reference_style")
        or intent.get("style_reference")
        or ""
    ).strip()
    style_reference_requested = (
        workflow_id == "04_keyframe"
        and bool(style_reference or first_reference)
        and str(intent.get("control_mode") or intent.get("controlMode") or "").strip().lower() in {"style_reference", "style_ipadapter", "ipadapter_style"}
    ) or (
        workflow_id == "04_keyframe"
        and bool(style_reference or first_reference)
        and str(intent.get("asset_role") or intent.get("reference_role") or "").strip().lower() in {"style", "style_reference"}
    )
    img2img_style_requested = (
        workflow_id == "04_keyframe"
        and bool(first_reference)
        and str(intent.get("control_mode") or intent.get("controlMode") or "").strip().lower() in {"img2img_style", "img2img_style_reference", "reference_image_style"}
    )
    if img2img_style_requested:
        workflow_mode = "img2img_style_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "img2img_style"
        item["input_base_image"] = first_reference
        item["input_reference_style"] = style_reference or first_reference
        item["denoise"] = intent.get("denoise") or 0.45
        item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.65
    elif style_reference_requested:
        workflow_mode = "style_reference_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "style_reference"
        item["input_reference_style"] = style_reference or first_reference
    usable_character_references = _references_with_identity_images(item.get("character_references"))
    if (img2img_style_requested or style_reference_requested):
        pass
    elif item.get("character_references") and not usable_character_references and workflow_id == "04_keyframe":
        item["character_references"] = []
        if notes is not None:
            notes.append(f"image intent {job_id} downgraded to text keyframe because character references had no identity images")
    elif workflow_id == "04_keyframe" and len(usable_character_references) > 1:
        item["character_references"] = usable_character_references
        workflow_mode = "multi_pose_identity_keyframe" if pose_reference else "multi_identity_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "multi_identity_pose_reference" if pose_reference else "multi_identity_reference"
        item["input_identity_image"] = item["character_references"][0].get("identity_image", "")
        if pose_reference:
            item["input_pose_image"] = pose_reference
    elif workflow_id == "04_keyframe" and len(usable_character_references) == 1:
        item["character_references"] = usable_character_references
        only_reference = item["character_references"][0].get("identity_image", "")
        if only_reference:
            workflow_mode = "pose_identity_keyframe" if pose_reference else "identity_keyframe"
            item["workflow_mode"] = workflow_mode
            item["image_task_mode"] = workflow_mode
            item["mode"] = workflow_mode
            item["control_mode"] = "identity_pose_reference" if pose_reference else "identity_reference"
            item["input_identity_image"] = only_reference
            if pose_reference:
                item["input_pose_image"] = pose_reference
    elif workflow_id == "04_keyframe" and first_reference:
        workflow_mode = "pose_identity_keyframe" if pose_reference else "identity_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "identity_pose_reference" if workflow_mode == "pose_identity_keyframe" else "identity_reference"
        item["input_identity_image"] = first_reference
        if workflow_mode == "pose_identity_keyframe":
            item["input_pose_image"] = pose_reference
    if workflow_id == "03_style_cover_image" and first_reference:
        item["input_reference_style"] = first_reference
    if _bool_or_default(
        intent.get("optional_when_unconfigured"),
        default=_bool_or_default(contract.get("optional_when_unconfigured"), default=intent_name == "generate_cover_key_visual"),
    ):
        item["optional_when_unconfigured"] = True
    return item


def _references_with_identity_images(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        dict(entry)
        for entry in value
        if isinstance(entry, dict) and str(entry.get("identity_image") or "").strip()
    ]


def _character_references_from_intent(intent: dict[str, Any], resolved_entities: dict[str, Any]) -> list[dict[str, Any]]:
    raw_characters = intent.get("characters")
    characters: list[dict[str, Any]] = []
    if isinstance(raw_characters, list):
        characters = [dict(item) for item in raw_characters if isinstance(item, dict) and str(item.get("character_id") or "").strip()]
    if not characters:
        return []
    result: list[dict[str, Any]] = []
    for index, character in enumerate(characters[:4], 1):
        character_id = str(character.get("character_id") or "").strip()
        entity = _resolved_character(resolved_entities, character_id)
        identity_image = str(
            character.get("input_identity_image")
            or character.get("identity_image")
            or character.get("reference_image")
            or _first_identity_reference(entity)
            or ""
        ).strip()
        result.append(
            {
                "character_id": character_id,
                "identity_image": identity_image,
                "role_in_frame": str(character.get("role_in_frame") or character.get("role") or f"character_{index}").strip(),
                "position": str(character.get("position") or character.get("frame_position") or "").strip(),
                "identity_priority": str(character.get("identity_priority") or character.get("priority") or index).strip(),
            }
        )
    return result


def _resolved_character(resolved_entities: dict[str, Any], character_id: str) -> dict[str, Any]:
    for item in resolved_entities.get("characters") or []:
        if isinstance(item, dict) and str(item.get("character_id") or "") == character_id:
            return item
    return {}


def _first_identity_reference(entity: dict[str, Any]) -> str:
    if not isinstance(entity, dict):
        return ""
    for key in ("master_image", "expression_sheet"):
        value = str(entity.get(key) or "").strip()
        if value:
            return value
    for key in ("turnaround_images", "reference_assets"):
        values = entity.get(key)
        if isinstance(values, list):
            first = next((str(value).strip() for value in values if str(value).strip()), "")
            if first:
                return first
    return ""


def _image_workflow_route(
    intent_name: str,
    intent: dict[str, Any],
    contract: dict[str, Any],
    compatibility: dict[str, Any],
) -> tuple[str, str]:
    """Resolve semantic image intent to the active debug-console workflow slot.

    Employee compatibility fields are legacy hints only.  Known production
    intents always use the compiler-owned route so stale staff JSON cannot
    point the DAG back to archived workflow IDs.
    """

    if intent_name == "generate_base_asset":
        role = str(intent.get("asset_role") or "character").strip().lower()
        if role in {"style", "style_reference"}:
            return "03_style_cover_image", "style_reference"
        if role == "product":
            return "01_base_asset_image", "product_base"
        if role == "scene":
            return "01_base_asset_image", "scene_base"
        return "01_base_asset_image", "character_base"
    if intent_name == "generate_turnaround":
        role = str(intent.get("asset_role") or "character").strip().lower()
        mode = "product_turnaround" if role == "product" else "character_turnaround"
        return "02_turnaround", mode
    if intent_name == "generate_cover_key_visual" and str(intent.get("asset_role") or "").strip().lower() in {"style", "style_reference"}:
        return "03_style_cover_image", "style_reference"
    if intent_name in IMAGE_INTENT_ROUTES:
        return IMAGE_INTENT_ROUTES[intent_name]
    return (
        str(contract.get("workflow_id") or compatibility.get("recommended_workflow_id") or ""),
        str(contract.get("workflow_mode") or compatibility.get("recommended_workflow_mode") or intent_name or "image"),
    )


def _video_workflow_route(
    intent_name: str,
    intent: dict[str, Any],
    contract: dict[str, Any],
    compatibility: dict[str, Any],
) -> tuple[str, str]:
    """Resolve semantic video intent to the active debug-console workflow slot."""

    if intent_name == "generate_broll_clip":
        requested_mode = str(
            intent.get("workflow_mode")
            or intent.get("broll_mode")
            or intent.get("video_task_mode")
            or compatibility.get("recommended_workflow_mode")
            or ""
        ).strip()
        if requested_mode in {"broll_scene_video", "empty_transition_video"}:
            return "10_broll_transition_video", requested_mode
    if intent_name in VIDEO_INTENT_ROUTES:
        return VIDEO_INTENT_ROUTES[intent_name]
    return (
        str(contract.get("workflow_id") or compatibility.get("recommended_workflow_id") or ""),
        str(contract.get("workflow_mode") or compatibility.get("recommended_workflow_mode") or intent_name or "video"),
    )


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


def _bind_first_source_image(item: dict[str, Any], image_job_ids: set[str], *, slot: str = "input_base_image") -> None:
    source_ids = _string_list(item.get("source_intent_ids"))
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    for source_id in source_ids:
        candidate = _safe_id(source_id)
        fallback = f"{candidate}_start_frame"
        job_id = candidate if candidate in image_job_ids else fallback if fallback in image_job_ids else ""
        if job_id:
            bindings.setdefault(slot, {"from_job": job_id, "output": "output_final_image"})
            if job_id not in depends_on:
                depends_on.append(job_id)
            return


def _bind_first_source_video(item: dict[str, Any], video_job_ids: set[str]) -> None:
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    if str(item.get("source_video") or "").strip():
        bindings.setdefault("input_source_video", str(item["source_video"]))
        return
    for source_id in _string_list(item.get("source_intent_ids")):
        job_id = _safe_id(source_id)
        if job_id not in video_job_ids:
            continue
        bindings.setdefault("input_source_video", {"from_job": job_id, "output": "output_final_video"})
        if job_id not in depends_on:
            depends_on.append(job_id)
        return


def _bind_matching_keyframe_by_id(item: dict[str, Any], image_job_ids: set[str]) -> str:
    number = _trailing_number(item.get("job_id") or item.get("id") or "")
    if not number:
        return ""
    candidates = [
        f"kf_shot_{number}",
        f"keyframe_shot_{number}",
        f"shot_{number}_keyframe",
        f"shot_{number}_first_frame",
        f"clip_{number}_keyframe",
    ]
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    for job_id in candidates:
        if job_id not in image_job_ids:
            continue
        bindings.setdefault("input_base_image", {"from_job": job_id, "output": "output_final_image"})
        if job_id not in depends_on:
            depends_on.append(job_id)
        item.setdefault("source_intent_ids", [])
        if isinstance(item["source_intent_ids"], list) and job_id not in item["source_intent_ids"]:
            item["source_intent_ids"].append(job_id)
        return job_id
    return ""


def _trailing_number(value: Any) -> str:
    match = re.search(r"(\d+)$", str(value or "").strip())
    if not match:
        return ""
    return match.group(1).zfill(3)


def _has_bound_first_frame(item: dict[str, Any]) -> bool:
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    if any(key in bindings for key in ("input_base_image", "first_frame", "start_frame")):
        return True
    for key in ("reference_image", "first_frame_image", "image", "image_ref"):
        if str(item.get(key) or "").strip():
            return True
    return False


def _jobs_from_prompts(values: Any, job_type: str) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(values, 1):
        if isinstance(item, dict):
            if _skip_material_prompt_item(item):
                continue
            jobs.append(
                {
                    "job_id": str(item.get("job_id") or item.get("id") or f"{job_type}_{index:03d}"),
                    "type": job_type,
                    "capability": str(item.get("capability") or ("video_generate" if job_type == "video" else "image_generate")),
                    "mode": str(item.get("workflow_mode") or item.get("mode") or ""),
                    "workflow_id": _canonical_workflow_id(str(item.get("workflow_id") or "")),
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
                    "optional_when_unconfigured": _bool_or_default(
                        item.get("optional_when_unconfigured"),
                        default=str(item.get("workflow_mode") or item.get("mode") or "").strip() == "enhance_video"
                        or str(item.get("workflow_mode") or item.get("mode") or "").strip() == "talking_image"
                        or str(item.get("capability") or "").strip() == "video_enhance",
                    ),
                    "parameter_locks": item.get("parameter_locks") if isinstance(item.get("parameter_locks"), dict) else {},
                    "locked_fields": item.get("locked_fields") if isinstance(item.get("locked_fields"), list) else [],
                }
            )
    return jobs


def _skip_material_prompt_item(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(item.get(key) or "").strip().lower()
        for key in (
            "intent",
            "workflow_id",
            "workflow_mode",
            "mode",
            "image_task_mode",
            "asset_tag",
            "workflow_constraint",
            "control_mode",
        )
    )
    return any(
        marker in text
        for marker in (
            "no_image_required",
            "placeholder_no_image",
            "skip_image_generation",
            "placeholder",
            "workflow_id none",
        )
    )


def _canonical_workflow_id(workflow_id: str) -> str:
    value = str(workflow_id or "").strip()
    return WORKFLOW_ID_ALIASES.get(value, value)


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
