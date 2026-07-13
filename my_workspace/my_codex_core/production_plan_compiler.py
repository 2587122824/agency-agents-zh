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
VISUAL_STYLE_FAMILY_LABELS = {
    "live_action": "真人纪实/实拍",
    "3d_cartoon": "3D卡通动画",
    "flat_cartoon": "2D扁平卡通",
    "anime": "动画番剧",
    "product_render": "产品商业渲染",
    "infographic": "信息图/科普图解",
    "custom": "任务指定视觉风格",
}
UNIVERSAL_VISUAL_STYLE_POSITIVE = (
    "全片视觉一致性约束：所有角色、场景、道具、关键帧、封面和视频片段必须保持同一个美术风格、同一种媒介质感、"
    "同一套光线方向、色彩饱和度、镜头语言和画面颗粒；只允许根据剧情改变动作、表情、构图和局部道具。"
    "不要在同一任务内混用真人摄影、3D渲染、2D插画、扁平贴纸、漫画、产品棚拍等不同视觉体系。"
)
UNIVERSAL_VISUAL_STYLE_NEGATIVE = (
    "mixed visual styles, inconsistent art direction, inconsistent render medium, style drift, "
    "photorealistic and cartoon mixed together, 2D and 3D mixed together, live-action background with illustrated subject, "
    "different lighting style, different color palette, random material change, glossy wet skin unless requested, "
    "flat sticker mixed with realistic render, any visible text, readable text, Chinese characters, subtitles, captions, "
    "title text, logo, watermark, UI text, screen text, sign text, poster text, malformed text, gibberish text"
)
VISUAL_NO_TEXT_POSITIVE = "画面保持干净，不生成任何可读文字、标题、字幕、标签、UI、招牌字、水印或乱码；文字信息全部留给后期剪辑排版。"
VISUAL_NO_TEXT_NEGATIVE = (
    "visible text, readable text, Chinese text, English text, subtitles, captions, title, labels, logo, watermark, "
    "UI, screen text, sign text, poster text, random letters, malformed characters, gibberish"
)
LINKED_CHARACTER_VARIANT_DENOISE = 1
LINKED_CHARACTER_KEYFRAME_DENOISE = 1
SCENE_BASE_NO_CHARACTER_PROMPT = (
    "Background/location plate only: no protagonist, no recognizable foreground person, no character portrait, "
    "no posed person at the main subject position. If the scene explicitly needs a crowd, keep people as distant "
    "blurred anonymous silhouettes and do not borrow identity or style from any character reference."
)
SCENE_BASE_NO_CHARACTER_NEGATIVE = (
    "protagonist, main character, recognizable person, portrait, posed foreground person, cartoon office worker, "
    "character reference leakage, identity leakage"
)
SCENE_ASSET_ROLES = {"scene", "scene_base", "background", "bg", "environment", "location", "set"}
PROMPT_REFERENCE_ARTIFACT_PATTERNS = (
    r"\bopenapi/[A-Za-z0-9._/\-]+\.(?:png|jpe?g|webp|mp4|mov|webm)\b",
    r"\b(?:generated_images|video_clips|image_prompts|video_prompts|my_workspace)[\\/][^\s，。；;、]+",
    r"\b[A-Za-z]:[\\/][^\s，。；;、]+",
    r"\b(?:asset|source_asset|source_asset_id|library_asset|library_asset_id|job|shot|vid_clip|kf_shot)_[A-Za-z0-9_\-]+\b",
    r"\b[a-f0-9]{24,64}(?:_[A-Za-z0-9_\-]+)?\b",
)


def sanitize_generation_prompt(text: str) -> str:
    """Remove internal IDs, paths, and request-schema fragments from model prompts."""

    value = str(text or "")
    if not value:
        return ""
    value = re.sub(r"(?i)\"?\b(?:nodeId|fieldName|fieldValue|nodeInfoList|asset_id|source_asset_id|library_asset_id)\b\"?\s*[:：]\s*\"?[^,，。；;\n]+\"?", "", value)
    value = re.sub(
        r"参考已有(?:关键帧|素材|图片|图像|资产){0,3}\s*[\"'“”‘’]?\s*(?:asset_)?[A-Za-z0-9_\-]{12,64}\s*[\"'“”‘’]?\s*的(?:风格|画风|构图|视觉)",
        "参考关联素材的视觉风格",
        value,
    )
    value = re.sub(
        r"参考(?:素材库|关联)?(?:关键帧|素材|图片|图像|资产){0,3}\s*[\"'“”‘’]?\s*(?:asset_)?[A-Za-z0-9_\-]{24,64}\s*[\"'“”‘’]?",
        "参考关联素材",
        value,
    )
    for pattern in PROMPT_REFERENCE_ARTIFACT_PATTERNS:
        value = re.sub(pattern, "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s{2,}", " ", value)
    value = re.sub(r"\s*([，。；;、,.])\s*([，。；;、,.])+", r"\1", value)
    value = re.sub(r"\s+([，。；;、,.])", r"\1", value)
    value = re.sub(r"([，,；;、])\s*([。.])", r"\2", value)
    value = re.sub(r"参考已有(?:关键帧|素材|图片|图像|资产){0,3}\s*[\"'“”‘’\s]*的(?:风格|画风|构图|视觉)", "参考关联素材的视觉风格", value)
    return value.strip(" \t\r\n，,；;、")


def compile_production_plan(
    *,
    task_id: str,
    route_content: str = "",
    image_content: str = "",
    video_content: str = "",
    audio_content: str = "",
    package_content: str = "",
    source_content: str = "",
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
    source_payload = _json_object_from_text(source_content)

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
    entity_references = collect_entity_references(
        production_intents,
        [route_payload, image_payload, video_payload, audio_payload, package_payload, source_payload, existing_payload or {}],
    )
    entity_registry = link_production_entities_to_assets(
        load_production_entities(entity_path or DEFAULT_ENTITY_PATH),
        load_asset_library(asset_library_path or DEFAULT_ASSET_LIBRARY_PATH),
    )
    reference_assignments = _assign_linked_asset_roles(source_payload, entity_references)
    _merge_linked_asset_entities(entity_registry, source_payload, entity_references=entity_references)
    linked_style_notes = _merge_linked_style_reference_assets(global_context, source_payload)
    global_context, resolved_entities, entity_notes = enrich_global_context_with_entities(global_context, entity_registry, entity_references)
    transient_notes = _merge_generated_character_master_entities(
        global_context,
        resolved_entities,
        production_intents["image"],
    )
    global_context = normalize_parameter_policy_context(global_context, route=route, video_config=video_config or {})
    compat_payload = json.loads(json.dumps(existing_payload or {}, ensure_ascii=False))
    compile_notes: list[str] = [*entity_notes, *transient_notes, *linked_style_notes]
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
        image_prompts,
        image_jobs,
        compile_notes,
        parameter_overrides,
    )

    _prefer_compiled_compat_list(compat_payload, "image_prompts", image_prompts, image_payload.get("image_prompts"))
    _prefer_compiled_compat_list(compat_payload, "video_prompts", video_prompts, video_payload.get("video_prompts"))
    _repair_legacy_i2v_keyframe_dependencies(
        compat_payload,
        templates=templates,
        global_context=global_context,
        resolved_entities=resolved_entities,
        notes=compile_notes,
    )
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
    compat_payload["reference_assignments"] = reference_assignments
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
        "reference_assignments": reference_assignments,
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
        "style_id": str(route.get("style_id") or route.get("visual_style_id") or "").strip(),
        "visual_style": str(route.get("visual_style") or route.get("style") or "").strip(),
        "style_description": str(route.get("style_description") or route.get("visual_style_description") or "").strip(),
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
        "style": {
            "style_id": str(route.get("style_id") or "").strip(),
            "visual_style": str(route.get("visual_style") or "").strip(),
            "description": str(route.get("style_description") or "").strip(),
        },
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
        visual_style = str(payload.get("visual_style") or payload.get("style") or "").strip()
        if visual_style and isinstance(context.get("style"), dict):
            context["style"].setdefault("visual_style", visual_style)
        character_id = str(payload.get("character_id") or "").strip()
        if character_id:
            _upsert_character(context, {"character_id": character_id})
    _attach_visual_style_blueprint(context, route=route, payloads=payloads)
    return context


def _attach_visual_style_blueprint(
    context: dict[str, Any],
    *,
    route: dict[str, Any],
    payloads: list[dict[str, Any]],
) -> None:
    style = context.setdefault("style", {})
    if not isinstance(style, dict):
        style = {}
        context["style"] = style
    explicit_positive = str(style.get("positive_prompt") or style.get("style_prompt") or "").strip()
    explicit_negative = str(style.get("negative_prompt") or style.get("negative_constraints") or "").strip()
    if explicit_positive or explicit_negative:
        family = _infer_visual_style_family(route, payloads, style) or "custom"
        blueprint = {
            "style_family": family,
            "style_label": VISUAL_STYLE_FAMILY_LABELS.get(family, VISUAL_STYLE_FAMILY_LABELS["custom"]),
            "positive_prompt": explicit_positive or _build_universal_visual_style_positive(family),
            "negative_prompt": explicit_negative or UNIVERSAL_VISUAL_STYLE_NEGATIVE,
            "source": "explicit_global_context",
        }
        style["blueprint"] = blueprint
        if not str(style.get("style_id") or "").strip():
            style["style_id"] = "custom_locked_style"
        return

    family = _infer_visual_style_family(route, payloads, style) or "custom"
    blueprint_id = f"consistent_{family}"
    blueprint = {
        "id": blueprint_id,
        "style_family": family,
        "style_label": VISUAL_STYLE_FAMILY_LABELS.get(family, VISUAL_STYLE_FAMILY_LABELS["custom"]),
        "positive_prompt": _build_universal_visual_style_positive(family),
        "negative_prompt": UNIVERSAL_VISUAL_STYLE_NEGATIVE,
    }
    blueprint["source"] = "production_plan_compiler_inference"
    style["blueprint"] = blueprint
    if not str(style.get("style_id") or "").strip():
        style["style_id"] = blueprint_id
    if not str(style.get("style_family") or "").strip():
        style["style_family"] = family
    if not str(style.get("positive_prompt") or "").strip():
        style["positive_prompt"] = blueprint.get("positive_prompt") or ""
    if not str(style.get("negative_prompt") or "").strip():
        style["negative_prompt"] = blueprint.get("negative_prompt") or ""


def _build_universal_visual_style_positive(family: str) -> str:
    label = VISUAL_STYLE_FAMILY_LABELS.get(str(family or "").strip(), VISUAL_STYLE_FAMILY_LABELS["custom"])
    return f"本任务统一视觉风格锚点：{label}。{UNIVERSAL_VISUAL_STYLE_POSITIVE}"


def _infer_visual_style_family(
    route: dict[str, Any],
    payloads: list[dict[str, Any]],
    style: dict[str, Any],
) -> str:
    style_id = str(style.get("style_id") or route.get("style_id") or "").strip().lower()
    explicit_style = " ".join(
        str(value or "")
        for value in (
            style_id,
            style.get("visual_style"),
            style.get("description"),
            route.get("visual_style"),
            route.get("style_description"),
        )
    ).lower()
    text = f"{explicit_style} {_payload_style_text(payloads)}".lower()
    if _looks_like_live_action_context(text):
        return "live_action"
    if any(token in text for token in ("产品渲染", "商品渲染", "product render", "packshot", "棚拍", "电商主图")):
        return "product_render"
    if any(token in text for token in ("信息图", "科普图", "图解", "infographic", "diagram", "chart")):
        return "infographic"
    if any(token in text for token in ("二次元", "番剧", "anime", "manga")):
        return "anime"
    if any(token in text for token in ("扁平", "flat cartoon", "flat illustration", "2d cartoon", "二维卡通")):
        return "flat_cartoon"
    if any(token in text for token in ("3d卡通", "3d 卡通", "3d cartoon", "玩具质感", "toy render", "q版", "q 版")):
        return "3d_cartoon"
    if any(token in text for token in ("卡通", "cartoon", "动画", "animation")):
        return "3d_cartoon"
    return "custom"


def _payload_style_text(payloads: list[dict[str, Any]]) -> str:
    chunks: list[str] = []

    def visit(value: Any) -> None:
        if len(chunks) > 200:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key or "").lower()
                if key_text in {
                    "prompt",
                    "description",
                    "visual_description",
                    "style_id",
                    "visual_style",
                    "style_description",
                    "asset_role",
                    "asset_tag",
                    "character_id",
                    "scene_id",
                    "routing_reason",
                }:
                    chunks.append(str(item or ""))
                elif key_text in {"production_intents", "global_context", "style", "image", "video"}:
                    visit(item)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value[:80]:
                visit(item)

    for payload in payloads:
        visit(payload)
    return " ".join(chunks)


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
                prompt = sanitize_generation_prompt(frame.get("prompt") or frame.get("description") or intent.get("prompt") or "")
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
                _apply_linked_character_reference_policy(item, intent, notes)
                _apply_generated_character_reference_policy(item, intent, prompts, notes)
                _apply_generated_scene_reference_policy(item, intent, prompts, notes)
                _apply_linked_style_reference_policy(item, intent, global_context, notes)
                _apply_live_action_quality_policy(item, global_context=global_context, intent=intent)
                _apply_img2img_style_edit_prompt_policy(item, intent=intent, notes=notes)
                _apply_visual_style_policy(item, global_context=global_context)
                item["prompt"] = sanitize_generation_prompt(item.get("prompt") or "")
                prompts.append(item)
                jobs.append({"job_id": job_id, "intent_id": intent_id, "frame_role": role, **item})
            continue
        prompt = sanitize_generation_prompt(intent.get("prompt") or intent.get("description") or intent.get("visual_description") or "")
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
        _apply_character_base_policy(item, intent, prompts, notes)
        _apply_linked_character_reference_policy(item, intent, notes)
        _apply_generated_character_reference_policy(item, intent, prompts, notes)
        _apply_generated_scene_reference_policy(item, intent, prompts, notes)
        _apply_linked_style_reference_policy(item, intent, global_context, notes)
        attach_parameter_lock_metadata(
            item,
            global_context=global_context,
            intent_kind="image",
            intent_name=intent_name,
            overrides=overrides,
        )
        _apply_live_action_quality_policy(item, global_context=global_context, intent=intent)
        _apply_img2img_style_edit_prompt_policy(item, intent=intent, notes=notes)
        _apply_visual_style_policy(item, global_context=global_context)
        item["prompt"] = sanitize_generation_prompt(item.get("prompt") or "")
        prompts.append(item)
        jobs.append({"job_id": intent_id, "intent_id": intent_id, **item})
    return prompts, jobs


def _apply_character_base_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    existing_items: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> None:
    if str(intent.get("intent") or "").strip() != "generate_base_asset":
        return
    if str(intent.get("asset_role") or "character").strip().lower() != "character":
        return
    master_reference = _character_master_reference_from_item(item)
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
    if is_animal and _looks_like_turnaround_sheet(character_text):
        item["animal_character_reference_sheet"] = True
        item["prompt"] = _append_prompt_once(
            str(item.get("prompt") or ""),
            "动物角色设定图要求：保持四足动物解剖结构，不要人型骨架，不要人类站姿，不要拟人化成人体；在同一张图中呈现同一只动物的正面、侧面、背面/背部视角，毛色、耳朵、眼睛、体型和尾巴完全一致，干净白底，无文字水印。",
        )
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} kept on animal-safe character_base instead of humanoid turnaround")

    if master_reference and not _looks_like_turnaround_sheet(character_text):
        if _linked_master_base_asset_should_stay_text_to_image(character_text):
            item["prompt"] = _append_prompt_once(
                str(item.get("prompt") or ""),
                "Use the linked character image only as a loose identity description. Create a new character asset from this prompt; do not copy the reference image's original pose, outfit, background, or crop.",
            )
            if notes is not None:
                notes.append(f"image intent {item.get('job_id')} kept on character_base to avoid copying the linked master image composition")
            return
        _route_character_base_item_to_master_identity_keyframe(
            item,
            intent,
            master_reference,
            notes,
            reason="linked character master image",
        )
        return

    if not _looks_like_expression_sheet(character_text):
        return
    reference_job = _character_master_reference_job(existing_items, str(item.get("character_id") or ""))
    if not reference_job:
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} has no character master reference to bind for expression consistency")
        return

    reference_job_id = _route_character_asset_item_to_reference_job(
        item,
        intent,
        reference_job,
        prompt_suffix=(
            "鍙傝€冧笂涓€寮犺鑹茶瀹氬浘锛屽繀椤讳繚鎸佸悓涓€鍙姩鐗╃殑姣涜壊鍒嗗竷銆佽€虫湹褰㈢姸銆佺溂鐫涖€侀蓟鍙ｃ€佷綋鍨嬫瘮渚嬪拰灏惧反涓€鑷达紱鍙敼鍙樿〃鎯呭拰杞诲井鍔ㄤ綔锛屼笉鏀瑰彉鐗╃锛屼笉鍙樻垚浜哄瀷銆?"
            if is_animal
            else "鍙傝€冧笂涓€寮犺鑹茶瀹氬浘锛屽繀椤讳繚鎸佸悓涓€涓汉鐨勮劯鍨嬨€佸勾榫勬劅銆佷簲瀹樻瘮渚嬨€佸彂鍨嬨€佽偆鑹层€佽韩鏉愭瘮渚嬪拰鏈嶈涓€鑷达紱鍙敼鍙樿〃鎯呭拰杞诲井鍔ㄤ綔锛屼笉鎹㈣劯锛屼笉骞磋交鍖栵紝涓嶇（鐨紝涓嶆崲琛ｆ湇銆?"
        ),
    )
    if not reference_job_id:
        return
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        (
            "参考上一张角色设定图，必须保持同一只动物的毛色分布、耳朵形状、眼睛、鼻口、体型比例和尾巴一致；只改变表情和轻微动作，不改变物种，不变成人型。"
            if is_animal
            else "参考上一张角色设定图，必须保持同一个人的脸型、年龄感、五官比例、发型、肤色、身材比例和服装一致；只改变表情和轻微动作，不换脸，不年轻化，不磨皮，不换衣服。"
        ),
    )
    if notes is not None:
        notes.append(f"image intent {item.get('job_id')} routed to img2img_style_keyframe using character master {reference_job_id}")


def _character_master_reference_from_item(item: dict[str, Any]) -> str:
    entity_context = item.get("entity_context") if isinstance(item.get("entity_context"), dict) else {}
    character = entity_context.get("character") if isinstance(entity_context.get("character"), dict) else {}
    return _first_identity_reference(character)


def _merge_linked_asset_entities(
    registry: dict[str, Any],
    payload: dict[str, Any],
    *,
    entity_references: dict[str, set[str]] | None = None,
) -> None:
    linked_assets = payload.get("linked_assets") if isinstance(payload.get("linked_assets"), dict) else {}
    if not linked_assets:
        return
    characters = registry.setdefault("characters", {})
    if not isinstance(characters, dict):
        characters = {}
        registry["characters"] = characters
    scenes = registry.setdefault("scenes", {})
    if not isinstance(scenes, dict):
        scenes = {}
        registry["scenes"] = scenes
    referenced_character_ids = [
        str(value).strip()
        for value in sorted((entity_references or {}).get("character_ids") or [])
        if str(value).strip()
    ]
    for raw in linked_assets.get("characters") or []:
        if not isinstance(raw, dict):
            continue
        character_id = str(raw.get("character_id") or raw.get("id") or "").strip()
        if not character_id:
            continue
        _upsert_linked_character_entity(characters, raw, character_id=character_id)
        if entity_references is not None:
            entity_references.setdefault("character_ids", set()).add(character_id)
    for raw in linked_assets.get("assets") or []:
        if not isinstance(raw, dict) or not _linked_asset_looks_like_character(raw):
            continue
        character_id = str(raw.get("character_id") or "").strip()
        if not character_id and len(referenced_character_ids) == 1:
            character_id = referenced_character_ids[0]
        if not character_id:
            character_id = str(raw.get("asset_id") or raw.get("id") or "").strip()
        if not character_id:
            continue
        file_path = _linked_asset_file_path(raw)
        if not file_path:
            continue
        aliases = [str(raw.get("asset_id") or raw.get("id") or "").strip()]
        _upsert_linked_character_entity(
            characters,
            {
                **raw,
                "character_id": character_id,
                "master_image": file_path,
                "reference_assets": [file_path],
                "source_asset_id": str(raw.get("asset_id") or raw.get("id") or "linked_task_asset").strip(),
                "aliases": aliases,
            },
            character_id=character_id,
        )
        if entity_references is not None:
            entity_references.setdefault("character_ids", set()).add(character_id)
    for raw in linked_assets.get("scenes") or []:
        if not isinstance(raw, dict):
            continue
        scene_id = str(raw.get("scene_id") or raw.get("id") or "").strip()
        if not scene_id:
            continue
        current = scenes.get(scene_id) if isinstance(scenes.get(scene_id), dict) else {}
        reference_assets = list(current.get("reference_assets") or []) if isinstance(current.get("reference_assets"), list) else []
        reference_assets.extend(str(value).strip() for value in raw.get("reference_assets") or [] if str(value).strip())
        scene_master = str(raw.get("scene_master_image") or raw.get("scene_reference") or raw.get("reference_image") or "").strip()
        if scene_master:
            reference_assets.insert(0, scene_master)
        scenes[scene_id] = {
            **current,
            "scene_id": scene_id,
            "id": scene_id,
            "name": str(raw.get("name") or current.get("name") or scene_id).strip(),
            "scene_master_image": scene_master or str(current.get("scene_master_image") or "").strip(),
            "scene_reference": scene_master or str(current.get("scene_reference") or "").strip(),
            "scene_description": str(raw.get("scene_description") or current.get("scene_description") or "").strip(),
            "reference_assets": list(dict.fromkeys(reference_assets)),
            "source_asset_id": str(raw.get("source_asset_id") or current.get("source_asset_id") or "").strip(),
        }


def _assign_linked_asset_roles(payload: dict[str, Any], entity_references: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Classify task-local references before style policy can consume a person as style."""

    linked_assets = payload.get("linked_assets") if isinstance(payload.get("linked_assets"), dict) else {}
    raw_assets = [item for item in linked_assets.get("assets") or [] if isinstance(item, dict)]
    character_ids = [str(value).strip() for value in sorted(entity_references.get("character_ids") or []) if str(value).strip()]
    assignments: list[dict[str, Any]] = []
    next_character = 0
    assigned_characters: set[str] = set()
    for index, raw in enumerate(raw_assets, 1):
        asset_id = str(raw.get("asset_id") or raw.get("id") or f"linked_asset_{index}").strip()
        role = "style_reference"
        confidence = "high"
        character_id = str(raw.get("character_id") or "").strip()
        if character_id or _linked_asset_looks_like_character(raw):
            role = "identity_reference"
            character_id = character_id or (character_ids[0] if len(character_ids) == 1 else asset_id)
        elif _linked_asset_should_be_single_character_identity(raw, character_ids):
            role = "identity_reference"
            confidence = "low"
            character_id = character_ids[0]
        elif _linked_asset_looks_like_scene(raw):
            role = "scene_reference"
        elif character_ids and not _linked_asset_looks_like_style_reference(raw):
            role = "identity_reference"
            confidence = "low"
            if not character_id:
                available = [value for value in character_ids if value not in assigned_characters]
                character_id = available[0] if available else character_ids[min(next_character, len(character_ids) - 1)]
                next_character += 1
        elif len(character_ids) == 1 and _linked_asset_looks_like_style_reference(raw):
            # A lone generic keyframe selected for a single protagonist is an identity
            # source unless it is explicitly a style/scene asset.
            tags = {str(value).strip().lower() for value in raw.get("tags") or [] if str(value).strip()}
            if not tags.intersection({"style", "style_reference", "cover", "background", "scene", "environment"}):
                role = "identity_reference"
                confidence = "low"
                character_id = character_ids[0]
        if role == "identity_reference":
            if character_id in assigned_characters:
                role = "auxiliary_reference"
            else:
                assigned_characters.add(character_id)
                raw["character_id"] = character_id
                raw["reference_role"] = role
                raw["identity_anchor"] = True
        else:
            raw["reference_role"] = role
        assignments.append(
            {
                "asset_id": asset_id,
                "selection_rank": int(raw.get("selection_rank") or index),
                "role": role,
                "confidence": confidence,
                "character_id": character_id if role == "identity_reference" else "",
                "file": _linked_asset_file_path(raw),
                "sha256": str(raw.get("snapshot_sha256") or raw.get("source_sha256") or ""),
            }
        )
    return assignments


def _merge_linked_style_reference_assets(context: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    linked_assets = payload.get("linked_assets") if isinstance(payload.get("linked_assets"), dict) else {}
    if not linked_assets:
        return []
    style = context.setdefault("style", {})
    if not isinstance(style, dict):
        style = {}
        context["style"] = style
    reference_assets = style.get("reference_assets") if isinstance(style.get("reference_assets"), list) else []
    linked_references = style.get("linked_reference_assets") if isinstance(style.get("linked_reference_assets"), list) else []
    notes: list[str] = []
    seen = {
        str(value.get("file") if isinstance(value, dict) else value or "").strip().replace("\\", "/")
        for value in linked_references
    }
    seen.update(str(value or "").strip().replace("\\", "/") for value in reference_assets if str(value or "").strip())
    for raw in linked_assets.get("assets") or []:
        if not isinstance(raw, dict) or not _linked_asset_looks_like_style_reference(raw):
            continue
        file_path = _linked_asset_file_path(raw)
        if not file_path or file_path in seen:
            continue
        seen.add(file_path)
        reference_assets.insert(0, file_path)
        linked_references.append(
            {
                "asset_id": str(raw.get("asset_id") or raw.get("id") or "").strip(),
                "name": str(raw.get("name") or "").strip(),
                "file": file_path,
                "tags": [str(tag).strip() for tag in raw.get("tags") or [] if str(tag).strip()],
                "source": "linked_assets.assets",
            }
        )
        notes.append(f"linked reference asset {file_path} registered as input_reference_style")
    if not linked_references:
        return notes
    style["reference_assets"] = list(dict.fromkeys(value for value in reference_assets if str(value or "").strip()))
    style["linked_reference_assets"] = linked_references
    first_reference = str(style["reference_assets"][0] or "").strip() if style["reference_assets"] else ""
    if first_reference:
        if not str(style.get("reference_asset") or "").strip():
            style["reference_asset"] = first_reference
        if not str(style.get("style_reference") or "").strip():
            style["style_reference"] = first_reference
    return notes


def _upsert_linked_character_entity(characters: dict[str, Any], raw: dict[str, Any], *, character_id: str) -> None:
    current = characters.get(character_id) if isinstance(characters.get(character_id), dict) else {}
    reference_assets = list(current.get("reference_assets") or []) if isinstance(current.get("reference_assets"), list) else []
    reference_assets.extend(_linked_asset_file_path({"file": value}) for value in raw.get("reference_assets") or [] if str(value).strip())
    master_image = _linked_asset_file_path(
        {
            "file": raw.get("master_image")
            or raw.get("reference_image")
            or raw.get("file")
            or raw.get("source_file")
            or ""
        }
    )
    if master_image:
        reference_assets.insert(0, master_image)
    aliases = list(current.get("aliases") or []) if isinstance(current.get("aliases"), list) else []
    aliases.extend(str(value).strip() for value in raw.get("aliases") or [] if str(value).strip())
    for value in (raw.get("asset_id"), raw.get("id")):
        text = str(value or "").strip()
        if text and text != character_id:
            aliases.append(text)
    characters[character_id] = {
        **current,
        "character_id": character_id,
        "id": character_id,
        "name": str(raw.get("name") or current.get("name") or character_id).strip(),
        "master_image": master_image or str(current.get("master_image") or "").strip(),
        "reference_assets": list(dict.fromkeys(value for value in reference_assets if value)),
        "aliases": list(dict.fromkeys(value for value in aliases if value)),
        "source_asset_id": str(raw.get("source_asset_id") or current.get("source_asset_id") or ("linked_task_asset" if master_image else "")).strip(),
    }


def _linked_asset_looks_like_character(raw: dict[str, Any]) -> bool:
    if str(raw.get("reference_role") or "").strip().lower() == "identity_reference":
        return True
    tags = {str(tag or "").strip().lower() for tag in raw.get("tags") or [] if str(tag).strip()}
    if tags.intersection({"character", "character_base", "character_master", "character_turnaround", "identity_reference", "turnaround", "three_view", "three_views"}):
        return True
    file_path = str(raw.get("file") or raw.get("source_file") or "").replace("\\", "/").lower()
    return any(part in file_path for part in ("/01_character_base/", "/04_character_turnaround/", "character_base", "character_turnaround"))


def _linked_asset_should_be_single_character_identity(raw: dict[str, Any], character_ids: list[str]) -> bool:
    if len(character_ids) != 1:
        return False
    if str(raw.get("character_id") or "").strip():
        return False
    role = str(raw.get("reference_role") or "").strip().lower()
    if role in {"scene_reference", "style_reference", "auxiliary_reference"}:
        return False
    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    if kind and kind not in {"image", "reference", "asset", "keyframe"}:
        return False
    if str(raw.get("scene_id") or raw.get("product_id") or "").strip():
        return False
    tags = {str(tag or "").strip().lower() for tag in raw.get("tags") or [] if str(tag).strip()}
    if tags.intersection({"style", "style_reference", "cover", "background", "environment", "location", "scene_base", "scene_reference"}):
        return False
    return _linked_asset_name_looks_like_person_reference(raw)


def _linked_asset_name_looks_like_person_reference(raw: dict[str, Any]) -> bool:
    name = str(raw.get("name") or raw.get("title") or raw.get("label") or "").strip()
    if not name:
        return False
    normalized = re.sub(r"[\s_\-]+", "", name).lower()
    if not normalized or re.fullmatch(r"[a-f0-9]{12,64}", normalized):
        return False
    scene_terms = (
        "scene",
        "background",
        "environment",
        "location",
        "room",
        "street",
        "track",
        "field",
        "场景",
        "背景",
        "环境",
        "地点",
        "房间",
        "卧室",
        "客厅",
        "办公室",
        "街",
        "跑道",
        "操场",
        "田径场",
        "天空",
        "夕阳",
        "清晨",
        "黄昏",
    )
    if any(term in normalized for term in scene_terms):
        return False
    if any(term in normalized for term in ("character", "人物", "角色", "主角", "形象", "头像", "人像", "portrait")):
        return True
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", name)
    if 1 < len(cjk_chars) <= 4:
        return True
    if re.fullmatch(r"[a-z][a-z0-9]{1,24}", normalized):
        return True
    return False


def _linked_asset_looks_like_style_reference(raw: dict[str, Any]) -> bool:
    if str(raw.get("reference_role") or "").strip().lower() in {"identity_reference", "auxiliary_reference"}:
        return False
    if _linked_asset_looks_like_character(raw) or _linked_asset_looks_like_scene(raw):
        return False
    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    if kind and kind not in {"image", "reference", "asset", "keyframe", "style"}:
        return False
    tags = {str(tag or "").strip().lower() for tag in raw.get("tags") or [] if str(tag).strip()}
    if tags.intersection(
        {
            "style",
            "style_reference",
            "reference",
            "keyframe",
            "cover",
            "cover_key_visual",
            "i2v_first_frame",
            "i2v_first_last_frame",
            "i2v_first_middle_last_frame",
            "image_reference",
        }
    ):
        return True
    file_path = str(raw.get("file") or raw.get("source_file") or raw.get("path") or "").replace("\\", "/").lower()
    return any(part in file_path for part in ("/06_style_reference/", "/07_keyframe/", "/08_cover_key_visual/", "style_reference", "keyframe", "cover_key_visual"))


def _linked_asset_looks_like_scene(raw: dict[str, Any]) -> bool:
    tags = {str(tag or "").strip().lower() for tag in raw.get("tags") or [] if str(tag).strip()}
    if tags.intersection({"scene", "scene_base", "scene_reference", "background", "environment", "location"}):
        return True
    file_path = str(raw.get("file") or raw.get("source_file") or raw.get("path") or "").replace("\\", "/").lower()
    return any(part in file_path for part in ("/02_scene_base/", "/03_background/", "scene_base", "scene_reference", "background"))


def _linked_asset_file_path(raw: dict[str, Any]) -> str:
    value = str(raw.get("file") or raw.get("source_file") or raw.get("path") or raw.get("source_path") or "").strip().replace("\\", "/")
    if not value:
        return ""
    if value.startswith("my_workspace/") or re.match(r"^[A-Za-z]:/", value) or value.startswith("/"):
        return value
    if re.match(r"^\d{2}_[A-Za-z0-9_]+/", value):
        return f"my_workspace/my_asset_library/{value}"
    return value


def _merge_generated_character_master_entities(
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    image_intents: list[dict[str, Any]],
) -> list[str]:
    """Expose first generated character base assets as task-local identity anchors."""

    notes: list[str] = []
    generated_masters: dict[str, dict[str, Any]] = {}
    for index, intent in enumerate(image_intents, 1):
        if not isinstance(intent, dict):
            continue
        if str(intent.get("intent") or "").strip() != "generate_base_asset":
            continue
        if str(intent.get("asset_role") or "character").strip().lower() != "character":
            continue
        character_id = str(intent.get("character_id") or "").strip()
        if not character_id or character_id in generated_masters:
            continue
        job_id = _safe_id(intent.get("intent_id") or intent.get("id") or f"image_intent_{index:03d}")
        generated_masters[character_id] = {
            "character_id": character_id,
            "id": character_id,
            "name": str(intent.get("name") or intent.get("character_name") or character_id).strip(),
            "generated_master_job_id": job_id,
            "master_image_binding": {"from_job": job_id, "output": "output_final_image"},
            "reference_assets": [],
            "source_asset_id": "generated_task_master",
        }
    if not generated_masters:
        return notes

    characters = resolved_entities.setdefault("characters", [])
    if not isinstance(characters, list):
        characters = []
        resolved_entities["characters"] = characters
    for character_id, generated in generated_masters.items():
        existing = next(
            (
                entry
                for entry in characters
                if isinstance(entry, dict) and str(entry.get("character_id") or "") == character_id
            ),
            None,
        )
        if existing and _first_identity_reference(existing):
            _upsert_character(global_context, existing)
            continue
        if existing:
            existing.update({key: value for key, value in generated.items() if value not in (None, "", [])})
            entity = existing
        else:
            entity = dict(generated)
            characters.append(entity)
        _upsert_character(global_context, entity)
        notes.append(
            f"角色实体 {character_id} 使用本任务生成的 {generated['generated_master_job_id']} 作为临时master identity"
        )
    return notes


def _apply_linked_character_reference_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    notes: list[str] | None = None,
) -> None:
    if not str(item.get("character_id") or intent.get("character_id") or "").strip():
        return
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    master_reference = _linked_character_reference_from_intent_or_item(intent, item)
    if not master_reference:
        return
    if item.get("input_base_image") or bindings.get("input_base_image"):
        if str(item.get("input_identity_image") or "").strip() == master_reference:
            item["identity_anchor"] = {"source": "external_identity_anchor", "file": master_reference}
        return
    workflow_id = str(item.get("workflow_id") or "").strip()
    workflow_mode = str(item.get("workflow_mode") or item.get("mode") or "").strip()
    if workflow_id == "01_base_asset_image" and workflow_mode == "character_base":
        _route_character_base_item_to_master_identity_keyframe(
            item,
            intent,
            master_reference,
            notes,
            reason="linked external identity anchor",
        )
        item["identity_anchor"] = {"source": "external_identity_anchor", "file": master_reference}
        return
    if workflow_id != "04_keyframe":
        return
    item["workflow_mode"] = "identity_keyframe"
    item["image_task_mode"] = "identity_keyframe"
    item["mode"] = "identity_keyframe"
    item["control_mode"] = "identity_reference"
    item["input_identity_image"] = master_reference
    item["input_base_image"] = master_reference
    item["reference_image"] = master_reference
    item["identity_anchor"] = {"source": "external_identity_anchor", "file": master_reference}
    scene_reference = _linked_scene_reference_from_intent_or_item(intent, item)
    if scene_reference:
        item["workflow_mode"] = "identity_scene_keyframe"
        item["image_task_mode"] = "identity_scene_keyframe"
        item["mode"] = "identity_scene_keyframe"
        item["control_mode"] = "identity_scene_reference"
        item["input_scene_image"] = scene_reference
        item["scene_reference_image"] = scene_reference
        _merge_compat_list(item, "reference_images", [scene_reference])
    item["denoise"] = intent.get("denoise") or item.get("denoise") or LINKED_CHARACTER_KEYFRAME_DENOISE
    item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or item.get("ipadapter_weight") or 0.72
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        "参考关联角色母版图，必须保持同一张脸、同一年龄感、同一发型、肤色、五官比例、身材比例和服装主特征；只改变当前镜头要求的表情、动作和轻微状态，不随机换人。",
    )
    if notes is not None:
        routed_mode = item.get("workflow_mode") or "identity_keyframe"
        notes.append(f"image intent {item.get('job_id')} bound to linked character master image as {routed_mode}")


def _linked_character_reference_from_intent_or_item(intent: dict[str, Any], item: dict[str, Any]) -> str:
    entity_usage = intent.get("entity_usage") if isinstance(intent.get("entity_usage"), dict) else {}
    for key in (
        "character_reference_image",
        "character_master_image",
        "input_identity_image",
        "identity_image",
        "master_image",
    ):
        value = str(entity_usage.get(key) or intent.get(key) or "").strip()
        if value:
            return value
    entity_context = item.get("entity_context") if isinstance(item.get("entity_context"), dict) else {}
    character = entity_context.get("character") if isinstance(entity_context.get("character"), dict) else {}
    if str(character.get("source_asset_id") or "").strip():
        return _first_identity_reference(character)
    return ""


def _linked_scene_reference_from_intent_or_item(intent: dict[str, Any], item: dict[str, Any]) -> str:
    entity_usage = intent.get("entity_usage") if isinstance(intent.get("entity_usage"), dict) else {}
    for key in (
        "scene_reference_image",
        "scene_master_image",
        "scene_reference",
        "input_scene_image",
    ):
        value = str(entity_usage.get(key) or intent.get(key) or "").strip()
        if value:
            return value
    entity_context = item.get("entity_context") if isinstance(item.get("entity_context"), dict) else {}
    scene = entity_context.get("scene") if isinstance(entity_context.get("scene"), dict) else {}
    for key in ("scene_master_image", "scene_reference", "reference_asset"):
        value = str(scene.get(key) or "").strip()
        if value:
            return value
    references = scene.get("reference_assets")
    if isinstance(references, list):
        return next((str(value).strip() for value in references if str(value).strip()), "")
    return ""


def _apply_linked_style_reference_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    global_context: dict[str, Any],
    notes: list[str] | None = None,
) -> None:
    reference = _linked_style_reference_from_context(global_context)
    if not reference:
        return
    if str(item.get("input_reference_style") or "").strip():
        return
    workflow_id = str(item.get("workflow_id") or "").strip()
    workflow_mode = str(item.get("workflow_mode") or item.get("mode") or "").strip()
    asset_role = str(intent.get("asset_role") or item.get("asset_tag") or "").strip().lower()
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}

    if (
        workflow_id == "01_base_asset_image"
        and workflow_mode == "character_base"
        and not _item_has_identity_reference(item)
        and not bindings.get("input_base_image")
    ):
        item["workflow_id"] = "04_keyframe"
        item["workflow_mode"] = "img2img_style_keyframe"
        item["image_task_mode"] = "img2img_style_keyframe"
        item["mode"] = "img2img_style_keyframe"
        item["control_mode"] = "img2img_style"
        item["input_base_image"] = reference
        item["input_reference_style"] = reference
        item["reference_image"] = reference
        item["denoise"] = intent.get("denoise") or item.get("denoise") or 1
        item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or item.get("ipadapter_weight") or 0.65
        _merge_compat_list(item, "reference_images", [reference])
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} uses linked reference asset as img2img_style base")
        return

    if workflow_id == "03_style_cover_image" and workflow_mode == "cover_key_visual":
        item["input_reference_style"] = reference
        item["control_mode"] = "style_reference"
        _merge_compat_list(item, "reference_images", [reference])
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} uses linked reference asset as cover style reference")
        return

    if workflow_id != "04_keyframe":
        return

    if workflow_mode == "keyframe" and not _item_has_identity_reference(item) and not item.get("input_scene_image"):
        item["workflow_mode"] = "style_reference_keyframe"
        item["image_task_mode"] = "style_reference_keyframe"
        item["mode"] = "style_reference_keyframe"
        item["control_mode"] = "style_reference"
        item["input_reference_style"] = reference
        _merge_compat_list(item, "reference_images", [reference])
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} routed to style_reference_keyframe from linked reference asset")
        return

    if workflow_mode in {"style_reference_keyframe", "img2img_style_keyframe"} or asset_role in {"style", "style_reference", "cover_key_visual"}:
        item["input_reference_style"] = reference
        _merge_compat_list(item, "reference_images", [reference])
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} uses linked reference asset as input_reference_style")


def _linked_style_reference_from_context(global_context: dict[str, Any]) -> str:
    style = global_context.get("style") if isinstance(global_context.get("style"), dict) else {}
    linked = style.get("linked_reference_assets")
    if isinstance(linked, list):
        for entry in linked:
            if isinstance(entry, dict):
                value = str(entry.get("file") or entry.get("path") or "").strip()
            else:
                value = str(entry or "").strip()
            if value:
                return value
    for key in ("input_reference_style", "style_reference", "reference_asset"):
        value = str(style.get(key) or "").strip()
        if value:
            return value
    references = style.get("reference_assets")
    if isinstance(references, list):
        return next((str(value).strip() for value in references if str(value).strip()), "")
    return ""


def _item_has_identity_reference(item: dict[str, Any]) -> bool:
    if str(item.get("input_identity_image") or "").strip():
        return True
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    if bindings.get("input_identity_image") or bindings.get("input_base_image"):
        return True
    if _references_with_identity_sources(item.get("character_references")):
        return True
    control_mode = str(item.get("control_mode") or "").strip().lower()
    mode = str(item.get("workflow_mode") or item.get("mode") or "").strip().lower()
    return control_mode.startswith("identity") or mode in {
        "identity_keyframe",
        "identity_scene_keyframe",
        "pose_identity_keyframe",
        "multi_identity_keyframe",
        "multi_pose_identity_keyframe",
    }


def _route_character_base_item_to_master_identity_keyframe(
    item: dict[str, Any],
    intent: dict[str, Any],
    master_reference: str,
    notes: list[str] | None = None,
    *,
    reason: str,
) -> None:
    item["workflow_id"] = "04_keyframe"
    item["workflow_mode"] = "identity_keyframe"
    item["image_task_mode"] = "identity_keyframe"
    item["mode"] = "identity_keyframe"
    item["control_mode"] = "identity_reference"
    item["input_identity_image"] = master_reference
    item.pop("input_base_image", None)
    item.pop("reference_image", None)
    _merge_compat_list(item, "reference_images", [master_reference])
    item["denoise"] = intent.get("denoise") or LINKED_CHARACTER_VARIANT_DENOISE
    item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.58
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        "参考关联角色母版图，必须保持同一张脸、同一年龄感、同一发型、肤色、五官比例、身材比例和服装主特征；只改变当前任务要求的表情、动作或轻微状态，不随机换人。",
    )
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        "Use the linked character image only as an identity anchor. Recompose the requested pose, outfit, expression, framing, and background from the prompt; do not copy the reference image's original seated pose, school uniform, background, or crop.",
    )
    if notes is not None:
        notes.append(f"image intent {item.get('job_id')} routed to identity_keyframe from {reason}")


def _route_character_asset_item_to_reference_job(
    item: dict[str, Any],
    intent: dict[str, Any],
    reference_job: dict[str, Any],
    *,
    prompt_suffix: str,
) -> str:
    reference_job_id = str(reference_job.get("job_id") or reference_job.get("id") or "").strip()
    if not reference_job_id:
        return ""
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
    item["denoise"] = intent.get("denoise") or 1
    item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.72
    return reference_job_id


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
    value = str(text or "").lower()
    if any(
        token in value
        for token in (
            "turnaround",
            "three view",
            "three-view",
            "three views",
            "model sheet",
            "三视图",
            "三面图",
            "多视角",
            "正侧背",
            "涓夎",
        )
    ):
        return True
    front = any(token in value for token in ("正面", "姝ｉ潰", "front view", "front-facing"))
    side = any(token in value for token in ("侧面", "側面", "渚ч潰", "side view", "profile view"))
    back = any(token in value for token in ("背面", "背部", "鑳岄潰", "back view", "rear view"))
    return front and side and back


def _looks_like_expression_sheet(text: str) -> bool:
    return any(token in text for token in ("emotion", "expression", "emotions", "expressions", "表情", "情绪", "表情图"))


def _linked_master_base_asset_should_stay_text_to_image(text: str) -> bool:
    normalized = str(text or "").lower()
    if _looks_like_expression_sheet(normalized):
        return False
    return any(
        token in normalized
        for token in (
            "fullbody",
            "full body",
            "portrait",
            "outfit",
            "costume",
            "wardrobe",
            "全身",
            "半身",
            "面部特写",
            "脸部特写",
            "母版",
            "服装",
            "套装",
            "站姿",
        )
    )


def _append_prompt_once(prompt: str, addition: str) -> str:
    base = str(prompt or "").strip()
    extra = str(addition or "").strip()
    if not extra or extra in base:
        return base
    return f"{base} {extra}".strip()


def _apply_img2img_style_edit_prompt_policy(
    item: dict[str, Any],
    *,
    intent: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> None:
    mode = str(item.get("workflow_mode") or item.get("mode") or item.get("image_task_mode") or "").strip()
    if mode not in {"img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe"}:
        return
    intent_name = str((intent or {}).get("intent") or "").strip()
    if intent_name == "generate_base_asset":
        return
    original = str(item.get("prompt") or "").strip()
    if not original:
        return
    edited = _concise_img2img_style_edit_prompt(original)
    if edited and edited != original:
        item["production_prompt_before_img2img_edit"] = original
        item["prompt"] = edited
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} uses concise img2img edit prompt for Qwen Image Edit")
    negative = str(item.get("negative_prompt") or "").strip()
    if negative and _looks_like_generic_safety_negative(negative):
        item["production_negative_before_img2img_edit"] = negative
        item["negative_prompt"] = ""


def _concise_img2img_style_edit_prompt(prompt: str) -> str:
    text = str(prompt or "").strip()
    text = re.sub(r"(?i)\bplatform-safe\s+non-graphic\s+video,\s*fully\s+clothed\s+subjects,\s*family-safe\s+action\s+tone,\s*", "", text)
    text = re.sub(r"\uff08\s*(?:character_id|scene_id)\s*:[^)]*\uff09", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\(\s*(?:character_id|scene_id)\s*:[^)]*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?:\u7ad6\u5c4f\s*)?9:16[^\u3002\uff1b;,.]*", "", text)
    text = re.sub(r"\u5de5\u4f5c\u5c3a\u5bf8\s*\d+\s*x\s*\d+[^\u3002\uff1b;,.]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\u53c2\u8003(?:\u5173\u8054|\u4e0a\u4e00\u5f20)?\u89d2\u8272[\s\S]*?(?:\u4e0d\u968f\u673a\u6362\u4eba|\u4e0d\u751f\u6210\u53e6\u4e00\u4e2a\u4eba|\u4e0d\u6362\u8863\u670d|\u4e0d\u53d8\u6210\u4eba\u578b)\u3002?", "", text)
    text = re.sub(r"\u670d\u88c5\u3001\u53d1\u578b\u3001\u4e94\u5b98[^\u3002\uff1b;,.]*[\u3002\uff1b;,.]?", "", text)
    text = re.sub(r"\u80cc\u666f\u4e3a\u5173\u8054\u573a\u666f\u56fa\u5b9a\u89c6\u89d2[\u3002\uff1b;,.]?", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,.;\u3002\uff0c\uff1b")
    if not text:
        return str(prompt or "").strip()
    if not re.search(r"^\s*(?:\u8ba9|\u5c06|\u628a|\u57fa\u4e8e)", text):
        text = "\u8ba9\u56fe\u4e2d\u4eba\u7269" + re.sub(r"^\s*\u56fe\u4e2d\u4eba\u7269", "", text).strip()
    return text


def _looks_like_generic_safety_negative(negative: str) -> bool:
    value = str(negative or "").lower()
    safety_tokens = (
        "nudity",
        "sexual content",
        "erotic",
        "gore",
        "unsafe content",
        "graphic violence",
    )
    quality_tokens = (
        "distorted body",
        "distorted face",
        "low quality",
        "flicker",
    )
    return any(token in value for token in safety_tokens) and any(token in value for token in quality_tokens)


def _apply_live_action_quality_policy(
    item: dict[str, Any],
    *,
    global_context: dict[str, Any],
    intent: dict[str, Any] | None = None,
) -> None:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("prompt"),
            item.get("style_id"),
            item.get("asset_tag"),
            (intent or {}).get("prompt") if isinstance(intent, dict) else "",
            (intent or {}).get("description") if isinstance(intent, dict) else "",
            (global_context.get("style") or {}).get("style_id") if isinstance(global_context.get("style"), dict) else "",
            (global_context.get("style") or {}).get("description") if isinstance(global_context.get("style"), dict) else "",
        )
    ).lower()
    if not _looks_like_live_action_context(text) or not _looks_like_retro_period_context(text):
        return
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        (
            "真人纪实质感：自然皮肤纹理、普通市井人物、真实街拍/手持摄影光线、2008年前后中国城市生活细节；"
            "避免棚拍海报感、网红精修脸、夸张皮衣硬照、时尚大片姿势和过度锐化塑料质感。"
            "画面中如需招牌/报纸/广告，只用模糊不可读背景文字，不生成可读乱码。"
        ),
    )
    item["negative_prompt"] = _append_prompt_once(
        str(item.get("negative_prompt") or ""),
        (
            "AI generated look, glossy poster, studio fashion shoot, celebrity portrait, beauty retouching, plastic skin, "
            "random different face, inconsistent age, inconsistent protagonist, gibberish text, malformed Chinese text, readable fake signs"
        ),
    )


def _apply_visual_style_policy(item: dict[str, Any], *, global_context: dict[str, Any]) -> None:
    style = global_context.get("style") if isinstance(global_context.get("style"), dict) else {}
    blueprint = style.get("blueprint") if isinstance(style.get("blueprint"), dict) else {}
    positive = str(
        blueprint.get("positive_prompt")
        or style.get("positive_prompt")
        or style.get("style_prompt")
        or ""
    ).strip()
    negative = str(
        blueprint.get("negative_prompt")
        or style.get("negative_prompt")
        or style.get("negative_constraints")
        or ""
    ).strip()
    if not positive and not negative:
        return
    if positive:
        item["prompt"] = _append_prompt_once(str(item.get("prompt") or ""), positive)
    mode = str(item.get("workflow_mode") or item.get("mode") or item.get("image_task_mode") or "").strip()
    reference_edit_modes = {"img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe", "pose_identity_keyframe"}
    if negative and mode not in reference_edit_modes:
        item["negative_prompt"] = _append_prompt_once(str(item.get("negative_prompt") or ""), negative)
    item["visual_style_blueprint"] = {
        "id": str(blueprint.get("id") or style.get("style_id") or "").strip(),
        "style_family": str(blueprint.get("style_family") or style.get("style_family") or "").strip(),
        "source": str(blueprint.get("source") or "").strip(),
        "positive_prompt": positive,
        "negative_prompt": negative,
    }
    _apply_no_text_visual_policy(item)


def _apply_no_text_visual_policy(item: dict[str, Any]) -> None:
    item["prompt"] = _remove_text_generation_cues(str(item.get("prompt") or ""))
    item["prompt"] = _append_prompt_once(str(item.get("prompt") or ""), VISUAL_NO_TEXT_POSITIVE)
    mode = str(item.get("workflow_mode") or item.get("mode") or item.get("image_task_mode") or "").strip()
    reference_edit_modes = {"img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe", "pose_identity_keyframe"}
    if mode not in reference_edit_modes:
        item["negative_prompt"] = _append_prompt_once(str(item.get("negative_prompt") or ""), VISUAL_NO_TEXT_NEGATIVE)


def _remove_title_layout_generation_cues(prompt: str) -> str:
    text = str(prompt or "")
    text = re.sub(
        r"(?i)(?:\u6784\u56fe\u65f6)?\s*(?:\u4e0a|\u4e0b|\u5de6|\u53f3)?\s*(?:\d+\s*/\s*\d+|\u4e09\u5206\u4e4b\u4e00|third)"
        r"[^\u3002\uff1b;,.]*?(?:\u7528\u4e8e|\u65b9\u4fbf|\u53ef\u4f9b|\u9884\u7559|\u7559\u7ed9|for)"
        r"[^\u3002\uff1b;,.]*?(?:\u6807\u9898|\u526f\u6807\u9898|\u5b57\u5e55|\u6587\u6848|\u6587\u5b57|title|subtitle|caption|copy|text)"
        r"[^\u3002\uff1b;,.]*[\u3002\uff1b;,.]?",
        " upper third clean empty space. ",
        text,
    )
    text = re.sub(
        r"(?i)(?:\u6807\u9898|\u526f\u6807\u9898|\u5b57\u5e55|\u6587\u6848|\u6587\u5b57|title|subtitle|caption|copy|text)"
        r"[^\u3002\uff1b;,.]{0,16}(?:\u7559\u767d|\u9884\u7559|\u533a\u57df|area|space)"
        r"[^\u3002\uff1b;,.]*[\u3002\uff1b;,.]?",
        " clean empty space. ",
        text,
    )
    text = re.sub(
        r"(?i)(?:\u7559\u767d|\u9884\u7559)[^\u3002\uff1b;,.]{0,24}"
        r"(?:\u6807\u9898|\u526f\u6807\u9898|\u5b57\u5e55|\u6587\u6848|\u6587\u5b57|title|subtitle|caption|copy|text)"
        r"[^\u3002\uff1b;,.]*[\u3002\uff1b;,.]?",
        " clean empty space. ",
        text,
    )
    return re.sub(r"\s{2,}", " ", text).strip()


def _remove_text_generation_cues(prompt: str) -> str:
    text = _remove_title_layout_generation_cues(str(prompt or ""))
    text = re.sub(r"画面内文字仅在明确要求时生成[；;，,。]?.*?(?:后期排版|排版)[。；;]?", "", text)
    text = re.sub(r"(?:封面)?(?:主标题|副标题|标题|字幕|文字标签|文案)[:：][^。；;\\n]*[。；;]?", "", text)
    text = re.sub(r"(?i)(?:title|subtitle|caption|text label)\\s*[:：][^.。；;\\n]*[.。；;]?", "", text)
    text = re.sub(r"\\s{2,}", " ", text)
    return text.strip(" ，,。；;")


def _looks_like_live_action_context(text: str) -> bool:
    value = str(text or "").lower()
    return any(
        token in value
        for token in (
            "真人",
            "live action",
            "live-action",
            "realistic",
            "photoreal",
            "电影级",
            "复古",
            "年代",
            "市井",
            "2008",
            "街拍",
            "纪实",
        )
    )


def _looks_like_retro_period_context(text: str) -> bool:
    value = str(text or "").lower()
    return any(
        token in value
        for token in (
            "2008",
            "复古",
            "年代",
            "怀旧",
            "老式",
            "旧式",
            "旧时代",
            "千禧",
            "vintage",
            "retro",
            "period",
        )
    )


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
        if mode in {"character_base", "character_turnaround", "img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe"} or str(item.get("asset_tag") or "").strip().lower() in {"character", "character_base"}:
            return item
    return None


def _is_character_asset_variant_role(asset_role: str) -> bool:
    role = str(asset_role or "").strip().lower()
    if not role:
        return True
    if role in {
        "character",
        "character_base",
        "character_variant",
        "variant",
        "expression",
        "expression_sheet",
        "emotion",
        "emotion_sheet",
        "state",
        "pose",
    }:
        return True
    return role.startswith(("character_", "expression_", "emotion_", "state_"))


def _apply_generated_character_reference_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    existing_items: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> None:
    prompt_text = " ".join(
        str(value or "")
        for value in (
            item.get("job_id"),
            item.get("prompt"),
            intent.get("prompt"),
            intent.get("description"),
            intent.get("visual_description"),
        )
    )
    character_id = str(item.get("character_id") or intent.get("character_id") or "").strip()
    if not character_id and _looks_like_main_character_prompt(prompt_text):
        character_id = _single_previous_character_id(existing_items)
        if character_id:
            item["character_id"] = character_id
    if not character_id:
        return

    intent_name = str(intent.get("intent") or "").strip()
    reference_job = _character_master_reference_job(existing_items, character_id)
    if not reference_job:
        reference_job = _referenced_previous_character_job_from_prompt(existing_items, prompt_text)
    if not reference_job:
        return
    reference_job_id = str(reference_job.get("job_id") or reference_job.get("id") or "").strip()
    if not reference_job_id:
        return

    workflow_id = str(item.get("workflow_id") or "").strip()
    workflow_mode = str(item.get("workflow_mode") or item.get("mode") or "").strip()
    asset_role = str(intent.get("asset_role") or item.get("asset_tag") or "").strip().lower()

    if (
        intent_name == "generate_base_asset"
        and _is_character_asset_variant_role(asset_role)
        and workflow_mode == "character_base"
        and not item.get("input_bindings")
    ):
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
        item["denoise"] = intent.get("denoise") or 1
        item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.72
        item["prompt"] = _append_prompt_once(
            str(item.get("prompt") or ""),
            "参考上一张角色母版，必须保持同一个人的脸型、五官比例、年龄感、发型、肤色和身材比例一致；可以根据剧情阶段改变服装、精神状态和环境，但不换脸，不年轻化，不磨皮，不生成另一个人。",
        )
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} bound to character master reference {reference_job_id}")
        return

    if workflow_id == "04_keyframe" and workflow_mode == "keyframe" and not item.get("input_identity_image"):
        item["workflow_mode"] = "identity_keyframe"
        item["image_task_mode"] = "identity_keyframe"
        item["mode"] = "identity_keyframe"
        item["control_mode"] = "identity_reference"
        item["input_bindings"] = {
            **(item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}),
            "input_identity_image": {"from_job": reference_job_id, "output": "output_final_image"},
            "input_base_image": {"from_job": reference_job_id, "output": "output_final_image"},
        }
        item["depends_on"] = list(dict.fromkeys([*_string_list(item.get("depends_on")), reference_job_id]))
        item["denoise"] = intent.get("denoise") or 1
        item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.72
        item["prompt"] = _append_prompt_once(
            str(item.get("prompt") or ""),
            "主角必须参考角色母版，保持同一张脸、同一年龄感、同一发型和身材比例；只改变场景、动作、表情和服装阶段，不随机换人。",
        )
        if notes is not None:
            notes.append(f"image intent {item.get('job_id')} inferred identity reference from {reference_job_id}")


def _looks_like_main_character_prompt(text: str) -> bool:
    value = str(text or "").lower()
    return any(token in value for token in ("主角", "主人公", "同一主角", "protagonist", "main character", "same protagonist"))


def _single_previous_character_id(items: list[dict[str, Any]]) -> str:
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        character_id = str(item.get("character_id") or "").strip()
        if not character_id:
            continue
        mode = str(item.get("mode") or item.get("workflow_mode") or "").strip()
        if mode not in {"character_base", "character_turnaround", "img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe"}:
            continue
        if character_id not in ids:
            ids.append(character_id)
    if len(ids) == 1:
        return ids[0]
    if ids and all(_looks_like_main_character_id(value) for value in ids):
        return ids[0]
    return ""


def _character_master_reference_job(items: list[dict[str, Any]], character_id: str) -> dict[str, Any] | None:
    target = str(character_id or "").strip()
    for item in items:
        if not isinstance(item, dict):
            continue
        if target and str(item.get("character_id") or "").strip() != target:
            continue
        mode = str(item.get("mode") or item.get("workflow_mode") or "").strip()
        if mode in {"character_base", "character_turnaround", "img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe"}:
            return item
    return None


def _apply_generated_scene_reference_policy(
    item: dict[str, Any],
    intent: dict[str, Any],
    existing_items: list[dict[str, Any]],
    notes: list[str] | None = None,
) -> None:
    if str(item.get("workflow_id") or "").strip() != "04_keyframe":
        return
    mode = str(item.get("workflow_mode") or item.get("mode") or "").strip()
    if mode not in {"keyframe", "identity_keyframe", "identity_scene_keyframe"}:
        return
    if item.get("input_scene_image") or item.get("scene_reference_image"):
        return
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    if bindings.get("input_scene_image"):
        return
    if not _item_has_identity_reference(item):
        if notes is not None:
            notes.append(
                f"scene-only image intent {item.get('job_id')} keeps its keyframe route; "
                "identity_scene_keyframe requires an identity binding"
            )
        return
    scene_job = _generated_scene_reference_job(existing_items, item, intent)
    if not scene_job:
        return
    scene_job_id = str(scene_job.get("job_id") or scene_job.get("id") or "").strip()
    if not scene_job_id:
        return
    item["workflow_mode"] = "identity_scene_keyframe"
    item["image_task_mode"] = "identity_scene_keyframe"
    item["mode"] = "identity_scene_keyframe"
    item["control_mode"] = "identity_scene_reference"
    bindings = dict(bindings)
    bindings.setdefault("input_scene_image", {"from_job": scene_job_id, "output": "output_final_image"})
    item["input_bindings"] = bindings
    item["depends_on"] = list(dict.fromkeys([*_string_list(item.get("depends_on")), scene_job_id]))
    item["scene_reference_binding"] = {"from_job": scene_job_id, "output": "output_final_image"}
    item["prompt"] = _append_prompt_once(
        str(item.get("prompt") or ""),
        "Keep the linked scene/platform layout from the scene reference image; preserve the same room, platform height, floor, wall and camera-space continuity while changing only the character action.",
    )
    if notes is not None:
        notes.append(f"image intent {item.get('job_id')} bound to generated scene reference {scene_job_id}")


def _generated_scene_reference_job(
    items: list[dict[str, Any]],
    item: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any] | None:
    scene_items = [candidate for candidate in items if _is_scene_reference_item(candidate)]
    if not scene_items:
        return None
    item_scene_id = str(item.get("scene_id") or intent.get("scene_id") or intent.get("shot_id") or "").strip()
    if item_scene_id:
        exact = _preferred_scene_reference_candidate(
            [
                candidate
                for candidate in scene_items
                if str(candidate.get("scene_id") or "").strip() == item_scene_id
            ]
        )
        if exact:
            return exact
        normalized_scene = _normalized_scene_key(item_scene_id)
        if normalized_scene:
            fuzzy = _preferred_scene_reference_candidate(
                [
                    candidate
                    for candidate in scene_items
                    if normalized_scene and normalized_scene == _normalized_scene_key(candidate.get("scene_id"))
                ]
            )
            if fuzzy:
                return fuzzy
    if not _scene_token_fallback_allowed(item, intent):
        return None
    target_tokens = _scene_match_tokens(item, intent)
    if target_tokens:
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for index, candidate in enumerate(scene_items):
            candidate_tokens = _scene_match_tokens(candidate, {})
            score = len(target_tokens.intersection(candidate_tokens))
            if score:
                scored.append((score, _scene_reference_priority(candidate), index, candidate))
        if scored:
            scored.sort(key=lambda value: (value[0], value[1], value[2]), reverse=True)
            return scored[0][3]
    return None


def _preferred_scene_reference_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(enumerate(candidates), key=lambda value: (_scene_reference_priority(value[1]), -value[0]))[1]


def _scene_reference_priority(item: dict[str, Any]) -> int:
    workflow_mode = str(item.get("workflow_mode") or item.get("mode") or item.get("image_task_mode") or "").strip()
    asset_tag = str(item.get("asset_tag") or item.get("asset_role") or "").strip().lower()
    job_id = str(item.get("job_id") or item.get("id") or "").strip().lower()
    if workflow_mode == "scene_base" and (asset_tag in {"scene", "scene_base"} or job_id.startswith(("base_scene", "asset_scene", "scene_"))):
        return 40
    if workflow_mode == "scene_base":
        return 30
    if asset_tag in {"scene", "scene_base"} or job_id.startswith(("base_scene", "asset_scene", "scene_")):
        return 20
    if asset_tag in {"background", "bg", "environment", "location"} or job_id.startswith("base_bg"):
        return 10
    return 0


def _is_scene_reference_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    workflow_mode = str(item.get("workflow_mode") or item.get("mode") or item.get("image_task_mode") or "").strip()
    asset_tag = str(item.get("asset_tag") or item.get("asset_role") or "").strip().lower()
    job_id = str(item.get("job_id") or item.get("id") or "").strip().lower()
    return (
        workflow_mode == "scene_base"
        or asset_tag in SCENE_ASSET_ROLES
        or job_id.startswith(("asset_scene", "base_scene", "base_bg", "scene_"))
    )


def _normalized_scene_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    replacements = {
        "base": "low",
        "low": "low",
        "30": "low",
        "30cm": "low",
        "medium": "mid",
        "middle": "mid",
        "mid": "mid",
        "60": "mid",
        "60cm": "mid",
        "high": "high",
        "1m": "high",
        "100": "high",
        "100cm": "high",
    }
    for token, normalized in replacements.items():
        if re.search(rf"(^|[_\-\s]){re.escape(token)}($|[_\-\s])", text) or token in text:
            return normalized
    return text


def _scene_match_tokens(item: dict[str, Any], intent: dict[str, Any]) -> set[str]:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("job_id"),
            item.get("id"),
            item.get("scene_id"),
            item.get("asset_tag"),
            item.get("prompt"),
            intent.get("scene_id"),
            intent.get("shot_id"),
            intent.get("prompt"),
            intent.get("description"),
        )
    ).lower()
    tokens: set[str] = set()
    if any(token in text for token in ("30cm", "30 cm", "30厘米", "低高度", "low", "base", "platform_low")):
        tokens.add("low")
    if any(token in text for token in ("60cm", "60 cm", "60厘米", "中等高度", "medium", "middle", "mid", "platform_mid")):
        tokens.add("mid")
    if any(token in text for token in ("1m", "1 m", "100cm", "100 cm", "一米", "1米", "较高", "高高度", "high", "platform_high")):
        tokens.add("high")
    return tokens


def _scene_token_fallback_allowed(item: dict[str, Any], intent: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            item.get("job_id"),
            item.get("id"),
            item.get("asset_tag"),
            item.get("prompt"),
            intent.get("prompt"),
            intent.get("description"),
        )
    ).lower()
    return any(
        token in text
        for token in (
            "platform",
            "跳跃平台",
            "平台上",
            "平台",
            "jump",
            "landing",
            "land",
            "stands on",
        )
    )


def _referenced_previous_character_job_from_prompt(items: list[dict[str, Any]], prompt: str) -> dict[str, Any] | None:
    text = str(prompt or "")
    for item in reversed(items):
        if not isinstance(item, dict):
            continue
        character_id = str(item.get("character_id") or "").strip()
        if not character_id or character_id not in text:
            continue
        mode = str(item.get("mode") or item.get("workflow_mode") or "").strip()
        if mode in {"character_base", "character_turnaround", "img2img_style_keyframe", "identity_keyframe", "identity_scene_keyframe"}:
            return item
    return None


def _looks_like_main_character_id(value: str) -> bool:
    text = str(value or "").strip().lower()
    return bool(text) and any(token in text for token in ("main", "protagonist", "hero", "主角", "主人公", "char_main"))


def _compile_video_intents(
    intents: list[dict[str, Any]],
    templates: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    image_prompts: list[dict[str, Any]],
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
        if _skip_video_intent(intent):
            notes.append(f"video intent {intent_id} skipped because it is explicitly disabled")
            continue
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
        prompt = sanitize_generation_prompt(intent.get("prompt") or intent.get("motion_plan") or intent.get("description") or intent.get("edit_note") or "")
        broll_promoted_to_i2v = intent_name == "generate_broll_clip" and _video_intent_has_visible_character(intent, prompt)
        broll_removed_character_terms: list[str] = []
        if intent_name == "generate_broll_clip" and not broll_promoted_to_i2v:
            prompt, broll_removed_character_terms = _sanitize_broll_character_prompt(
                prompt,
                intent=intent,
                global_context=global_context,
                resolved_entities=resolved_entities,
            )
        if not prompt and intent_name == "enhance_video":
            prompt = "对上游视频进行补帧、放大、稳定和画质增强。"
        if not prompt:
            notes.append(f"video intent {intent_id} skipped because prompt is empty")
            continue
        effective_intent_name = "generate_i2v_clip" if broll_promoted_to_i2v else intent_name
        if broll_promoted_to_i2v:
            workflow_id, workflow_mode = VIDEO_INTENT_ROUTES["generate_i2v_clip"]
            notes.append(
                f"video intent {intent_id} promoted from B-roll to image-to-video because it contains a visible character action"
            )
        entity_context = entity_context_for_ids(
            resolved_entities,
            character_id="" if effective_intent_name == "generate_broll_clip" else str(intent.get("character_id") or ""),
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
            "character_id": "" if effective_intent_name == "generate_broll_clip" else str(intent.get("character_id") or ""),
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
        if effective_intent_name == "generate_broll_clip":
            _apply_broll_no_character_policy(item, broll_removed_character_terms, notes)
        attach_parameter_lock_metadata(
            item,
            global_context=global_context,
            intent_kind="video",
            intent_name=effective_intent_name,
            overrides=overrides,
        )
        if intent_name == "generate_three_frame_i2v_clip":
            _bind_three_frames(item, image_job_ids)
        elif effective_intent_name == "generate_i2v_clip":
            _bind_first_source_image(item, image_job_ids)
            if not _has_bound_first_frame(item, image_job_ids):
                inferred = _bind_matching_keyframe_by_id(item, image_job_ids)
                if inferred:
                    notes.append(f"video intent {intent_id} inferred first frame from {inferred}")
                else:
                    generated = _ensure_video_keyframe_dependency(
                        item,
                        intent,
                        templates=templates,
                        global_context=global_context,
                        resolved_entities=resolved_entities,
                        image_prompts=image_prompts,
                        image_jobs=image_jobs,
                        image_job_ids=image_job_ids,
                        notes=notes,
                        promoted_from_broll=broll_promoted_to_i2v,
                    )
                    notes.append(
                        f"video intent {intent_id} generated first-frame keyframe dependency {generated} instead of using text-to-video"
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
        _apply_live_action_quality_policy(item, global_context=global_context, intent=intent)
        _apply_visual_style_policy(item, global_context=global_context)
        item["prompt"] = sanitize_generation_prompt(item.get("prompt") or "")
        prompts.append(item)
        jobs.append(dict(item))
        video_job_ids.add(intent_id)
    return prompts, jobs


def _video_intent_has_visible_character(intent: dict[str, Any], prompt: str) -> bool:
    if str(intent.get("character_id") or "").strip():
        return True
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    if _bool_or_default(constraints.get("identity_lock"), default=False):
        return True
    text = " ".join(
        str(value or "")
        for value in (
            prompt,
            intent.get("motion_plan"),
            intent.get("description"),
            intent.get("shot_description"),
        )
    )
    if _looks_like_main_character_prompt(text):
        return True
    value = text.lower()
    return any(
        token in value
        for token in (
            "主角",
            "主人公",
            "同一张脸",
            "同一个人",
            "出镜",
            "露面",
            "protagonist",
            "main character",
            "same face",
            "same person",
        )
    )


def _ensure_video_keyframe_dependency(
    item: dict[str, Any],
    intent: dict[str, Any],
    *,
    templates: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    image_prompts: list[dict[str, Any]],
    image_jobs: list[dict[str, Any]],
    image_job_ids: set[str],
    notes: list[str],
    promoted_from_broll: bool = False,
) -> str:
    video_id = _safe_id(item.get("job_id") or item.get("id") or "")
    bound_source = _first_frame_binding_source(item)
    keyframe_id = _safe_id(
        intent.get("keyframe_intent_id")
        or intent.get("first_frame_intent_id")
        or (bound_source if bound_source and bound_source not in image_job_ids else "")
        or f"{video_id}_keyframe"
    )
    if not keyframe_id:
        keyframe_id = f"video_keyframe_{len(image_jobs) + 1:03d}"
    bindings = item.setdefault("input_bindings", {})
    depends_on = item.setdefault("depends_on", [])
    if keyframe_id in image_job_ids:
        bindings.setdefault("input_base_image", {"from_job": keyframe_id, "output": "output_final_image"})
        if keyframe_id not in depends_on:
            depends_on.append(keyframe_id)
        return keyframe_id

    image_contracts = ((templates.get("workflow_contracts") or {}).get("image") or {}) if isinstance(templates.get("workflow_contracts"), dict) else {}
    contract = image_contracts.get("generate_keyframe") if isinstance(image_contracts.get("generate_keyframe"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    keyframe_intent = {
        "intent": "generate_keyframe",
        "intent_id": keyframe_id,
        "prompt": str(item.get("prompt") or intent.get("prompt") or intent.get("motion_plan") or "").strip(),
        "negative_prompt": str(item.get("negative_prompt") or intent.get("negative_prompt") or ""),
        "character_id": str(item.get("character_id") or intent.get("character_id") or ""),
        "style_id": str(item.get("style_id") or intent.get("style_id") or ""),
        "product_id": str(item.get("product_id") or intent.get("product_id") or ""),
        "scene_id": str(item.get("scene_id") or intent.get("scene_id") or intent.get("shot_id") or ""),
        "asset_tag": f"{video_id}_first_frame",
    }
    if promoted_from_broll:
        keyframe_intent["source_note"] = "promoted_from_character_broll"
    keyframe_item = _image_prompt_item(
        job_id=keyframe_id,
        prompt=str(keyframe_intent["prompt"]),
        intent=keyframe_intent,
        contract=contract,
        compatibility={},
        render=render,
        asset_tag=str(keyframe_intent["asset_tag"]),
        resolved_entities=resolved_entities,
        notes=notes,
    )
    _apply_generated_character_reference_policy(keyframe_item, keyframe_intent, image_prompts, notes)
    _apply_linked_style_reference_policy(keyframe_item, keyframe_intent, global_context, notes)
    _apply_live_action_quality_policy(keyframe_item, global_context=global_context, intent=keyframe_intent)
    _apply_img2img_style_edit_prompt_policy(keyframe_item, intent=keyframe_intent, notes=notes)
    _apply_visual_style_policy(keyframe_item, global_context=global_context)
    image_prompts.append(keyframe_item)
    image_jobs.append({"job_id": keyframe_id, "intent_id": keyframe_id, **keyframe_item})
    image_job_ids.add(keyframe_id)
    bindings.setdefault("input_base_image", {"from_job": keyframe_id, "output": "output_final_image"})
    if keyframe_id not in depends_on:
        depends_on.append(keyframe_id)
    if keyframe_id not in item.setdefault("source_intent_ids", []):
        item["source_intent_ids"].append(keyframe_id)
    return keyframe_id


def _sanitize_broll_character_prompt(
    prompt: str,
    *,
    intent: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
) -> tuple[str, list[str]]:
    text = str(prompt or "").strip()
    character_terms = _broll_character_terms(intent, global_context, resolved_entities)
    removed: list[str] = []
    for term in character_terms:
        if not term or not re.search(re.escape(term), text, flags=re.IGNORECASE):
            continue
        text = re.sub(re.escape(term), "", text, flags=re.IGNORECASE)
        removed.append(term)
    text = re.sub(r"\s{2,}", " ", text).strip(" ，,。.;；、")
    text = _append_prompt_once(
        text,
        "B-roll空镜要求：只拍摄环境、道具、光影、天气、建筑或氛围细节，不出现主角，不出现任何可识别角色，不生成新人物或新动物角色。",
    )
    return text, removed


def _broll_character_terms(
    intent: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
) -> list[str]:
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if len(text) >= 2 and text not in terms:
            terms.append(text)

    add(intent.get("character_id"))
    for character in intent.get("characters") or []:
        if isinstance(character, dict):
            add(character.get("character_id"))
            add(character.get("name"))
            for alias in character.get("aliases") or []:
                add(alias)

    for source in (
        global_context.get("characters") if isinstance(global_context.get("characters"), list) else [],
        resolved_entities.get("characters") if isinstance(resolved_entities.get("characters"), list) else [],
    ):
        for character in source:
            if not isinstance(character, dict):
                continue
            add(character.get("character_id"))
            add(character.get("name"))
            for alias in character.get("aliases") or []:
                add(alias)

    return sorted(terms, key=len, reverse=True)


def _apply_broll_no_character_policy(
    item: dict[str, Any],
    removed_character_terms: list[str],
    notes: list[str] | None = None,
) -> None:
    item["character_id"] = ""
    item["no_visible_characters"] = True
    item["broll_policy"] = "environment_only"
    item.pop("identity_image", None)
    if removed_character_terms:
        item["removed_character_terms"] = removed_character_terms
        if notes is not None:
            notes.append(
                f"video intent {item.get('job_id')} sanitized B-roll character terms: {', '.join(removed_character_terms)}"
            )


def _apply_scene_base_no_character_policy(item: dict[str, Any], notes: list[str] | None = None) -> None:
    item["character_id"] = ""
    item["scene_base_policy"] = "background_plate_no_character_reference"
    item["prompt"] = _append_prompt_once(str(item.get("prompt") or ""), SCENE_BASE_NO_CHARACTER_PROMPT)
    item["negative_prompt"] = _append_prompt_once(str(item.get("negative_prompt") or ""), SCENE_BASE_NO_CHARACTER_NEGATIVE)
    item["reference_images"] = []
    for key in (
        "input_identity_image",
        "input_base_image",
        "identity_image",
        "character_references",
    ):
        item.pop(key, None)
    if notes is not None:
        notes.append(f"image intent {item.get('job_id')} isolated scene base from character references")


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
    scene_base_item = workflow_mode == "scene_base" or str(intent.get("asset_role") or "").strip().lower() in SCENE_ASSET_ROLES
    entity_context = entity_context_for_ids(
        resolved_entities,
        character_id="" if scene_base_item else str(intent.get("character_id") or ""),
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
        "character_id": "" if scene_base_item else str(intent.get("character_id") or ""),
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
    if scene_base_item:
        _apply_scene_base_no_character_policy(item, notes)
    character_references = [] if scene_base_item else _character_references_from_intent(intent, resolved_entities)
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
    scene_reference = _linked_scene_reference_from_intent_or_item(intent, item)
    if scene_reference:
        item["input_scene_image"] = scene_reference
        item["scene_reference_image"] = scene_reference
        _merge_compat_list(item, "reference_images", [scene_reference])
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
        item["denoise"] = intent.get("denoise") or 1
        item["ipadapter_weight"] = intent.get("ipadapter_weight") or intent.get("reference_strength") or 0.65
    elif style_reference_requested:
        workflow_mode = "style_reference_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "style_reference"
        item["input_reference_style"] = style_reference or first_reference
    usable_character_references = _references_with_identity_sources(item.get("character_references"))
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
        _apply_character_reference_dependency_bindings(item)
        if pose_reference:
            item["input_pose_image"] = pose_reference
    elif workflow_id == "04_keyframe" and len(usable_character_references) == 1:
        item["character_references"] = usable_character_references
        only_reference = item["character_references"][0].get("identity_image", "")
        only_binding = item["character_references"][0].get("identity_binding") if isinstance(item["character_references"][0].get("identity_binding"), dict) else {}
        if only_reference or only_binding:
            workflow_mode = "pose_identity_keyframe" if pose_reference else "identity_keyframe"
            if scene_reference and not pose_reference:
                workflow_mode = "identity_scene_keyframe"
            item["workflow_mode"] = workflow_mode
            item["image_task_mode"] = workflow_mode
            item["mode"] = workflow_mode
            item["control_mode"] = "identity_scene_reference" if workflow_mode == "identity_scene_keyframe" else ("identity_pose_reference" if pose_reference else "identity_reference")
            if only_reference:
                item["input_identity_image"] = only_reference
            elif only_binding:
                bindings = item.setdefault("input_bindings", {})
                bindings.setdefault("input_identity_image", dict(only_binding))
                bindings.setdefault("input_base_image", dict(only_binding))
            _apply_character_reference_dependency_bindings(item)
            if pose_reference:
                item["input_pose_image"] = pose_reference
    elif workflow_id == "04_keyframe" and first_reference:
        workflow_mode = "pose_identity_keyframe" if pose_reference else "identity_keyframe"
        if scene_reference and not pose_reference:
            workflow_mode = "identity_scene_keyframe"
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "identity_scene_reference" if workflow_mode == "identity_scene_keyframe" else ("identity_pose_reference" if workflow_mode == "pose_identity_keyframe" else "identity_reference")
        item["input_identity_image"] = first_reference
        if workflow_mode == "pose_identity_keyframe":
            item["input_pose_image"] = pose_reference
    if (
        workflow_id == "03_style_cover_image"
        and workflow_mode == "cover_key_visual"
        and str(item.get("character_id") or "").strip()
        and first_reference
    ):
        workflow_id = "04_keyframe"
        workflow_mode = "identity_scene_keyframe" if scene_reference else "identity_keyframe"
        item["workflow_id"] = workflow_id
        item["workflow_mode"] = workflow_mode
        item["image_task_mode"] = workflow_mode
        item["mode"] = workflow_mode
        item["control_mode"] = "identity_scene_reference" if scene_reference else "identity_reference"
        item["input_identity_image"] = first_reference
        item.pop("input_reference_style", None)
        if notes is not None:
            notes.append(f"image intent {job_id} routed cover key visual to {workflow_mode} to preserve character identity")
    if workflow_id == "03_style_cover_image" and first_reference:
        item["input_reference_style"] = first_reference
    if _bool_or_default(
        intent.get("optional_when_unconfigured"),
        default=_bool_or_default(contract.get("optional_when_unconfigured"), default=intent_name == "generate_cover_key_visual"),
    ):
        item["optional_when_unconfigured"] = True
    return item


def _references_with_identity_sources(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        dict(entry)
        for entry in value
        if isinstance(entry, dict)
        and (
            str(entry.get("identity_image") or "").strip()
            or isinstance(entry.get("identity_binding"), dict)
        )
    ]


def _apply_character_reference_dependency_bindings(item: dict[str, Any]) -> None:
    references = item.get("character_references") if isinstance(item.get("character_references"), list) else []
    if not references:
        return
    depends_on = item.setdefault("depends_on", [])
    if not isinstance(depends_on, list):
        depends_on = _string_list(depends_on)
        item["depends_on"] = depends_on
    for reference in references:
        if not isinstance(reference, dict):
            continue
        binding = reference.get("identity_binding") if isinstance(reference.get("identity_binding"), dict) else {}
        upstream = str(binding.get("from_job") or "").strip()
        if upstream and upstream not in depends_on:
            depends_on.append(upstream)


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
        identity_binding = entity.get("master_image_binding") if isinstance(entity.get("master_image_binding"), dict) else {}
        reference = {
            "character_id": character_id,
            "identity_image": identity_image,
            "role_in_frame": str(character.get("role_in_frame") or character.get("role") or f"character_{index}").strip(),
            "position": str(character.get("position") or character.get("frame_position") or "").strip(),
            "identity_priority": str(character.get("identity_priority") or character.get("priority") or index).strip(),
        }
        if not identity_image and identity_binding:
            reference["identity_binding"] = dict(identity_binding)
        result.append(
            reference
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
        if role in SCENE_ASSET_ROLES:
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


def _first_frame_binding_source(item: dict[str, Any]) -> str:
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    for key in ("input_base_image", "first_frame", "start_frame"):
        binding = bindings.get(key)
        if isinstance(binding, dict):
            source = str(binding.get("from_job") or "").strip()
            if source:
                return source
        elif str(binding or "").strip():
            return str(binding).strip()
    return ""


def _has_bound_first_frame(item: dict[str, Any], image_job_ids: set[str] | None = None) -> bool:
    source = _first_frame_binding_source(item)
    if source:
        if image_job_ids is None:
            return True
        return source in image_job_ids or Path(source).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    for key in ("reference_image", "first_frame_image", "image", "image_ref"):
        if str(item.get(key) or "").strip():
            return True
    return False


def _repair_legacy_i2v_keyframe_dependencies(
    payload: dict[str, Any],
    *,
    templates: dict[str, Any],
    global_context: dict[str, Any],
    resolved_entities: dict[str, Any],
    notes: list[str],
) -> None:
    image_prompts = payload.get("image_prompts")
    video_prompts = payload.get("video_prompts")
    if not isinstance(image_prompts, list) or not isinstance(video_prompts, list):
        return
    image_job_ids = {
        str(item.get("job_id") or item.get("id") or "").strip()
        for item in image_prompts
        if isinstance(item, dict) and str(item.get("job_id") or item.get("id") or "").strip()
    }
    image_contracts = ((templates.get("workflow_contracts") or {}).get("image") or {}) if isinstance(templates.get("workflow_contracts"), dict) else {}
    contract = image_contracts.get("generate_keyframe") if isinstance(image_contracts.get("generate_keyframe"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}

    for item in video_prompts:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("workflow_mode") or item.get("mode") or item.get("video_task_mode") or "").lower()
        if "i2v" not in mode:
            continue
        source = _safe_id(_first_frame_binding_source(item))
        if not source or source in image_job_ids:
            continue
        intent = {
            "intent": "generate_keyframe",
            "intent_id": source,
            "prompt": str(item.get("prompt") or item.get("motion_plan") or "").strip(),
            "negative_prompt": str(item.get("negative_prompt") or ""),
            "character_id": str(item.get("character_id") or ""),
            "style_id": str(item.get("style_id") or ""),
            "product_id": str(item.get("product_id") or ""),
            "scene_id": str(item.get("scene_id") or ""),
            "asset_tag": f"{str(item.get('job_id') or item.get('id') or source)}_first_frame",
        }
        keyframe_item = _image_prompt_item(
            job_id=source,
            prompt=str(intent["prompt"]),
            intent=intent,
            contract=contract,
            compatibility={},
            render=render,
            asset_tag=str(intent["asset_tag"]),
            resolved_entities=resolved_entities,
            notes=notes,
        )
        _apply_generated_character_reference_policy(keyframe_item, intent, image_prompts, notes)
        _apply_linked_style_reference_policy(keyframe_item, intent, global_context, notes)
        _apply_live_action_quality_policy(keyframe_item, global_context=global_context, intent=intent)
        _apply_img2img_style_edit_prompt_policy(keyframe_item, intent=intent, notes=notes)
        image_prompts.append(keyframe_item)
        image_job_ids.add(source)
        notes.append(f"legacy i2v video prompt {item.get('job_id') or item.get('id') or ''} restored missing first-frame keyframe {source}")


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
    compatibility = item.get("compatibility") if isinstance(item.get("compatibility"), dict) else {}
    constraints = item.get("constraints") if isinstance(item.get("constraints"), dict) else {}
    if item.get("enabled") is False or compatibility.get("skip_execution") is True or constraints.get("skip_execution") is True:
        return True
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
            "prompt",
            "motion_plan",
        )
    )
    return any(
        marker in text
        for marker in (
            "no_image_required",
            "placeholder_no_image",
            "skip_image_generation",
            "skip_video_generation",
            "skip_execution",
            "placeholder",
            "不应执行",
            "不生成ai视频",
            "workflow_id none",
        )
    )


def _skip_video_intent(intent: dict[str, Any]) -> bool:
    compatibility = intent.get("compatibility") if isinstance(intent.get("compatibility"), dict) else {}
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    text = json.dumps(intent, ensure_ascii=False).lower()
    return (
        intent.get("enabled") is False
        or str(intent.get("status") or "").strip().lower() in {"disabled", "skipped"}
        or compatibility.get("skip_execution") is True
        or constraints.get("skip_execution") is True
        or str(intent.get("intent") or "") == "no_video_required"
        or ("skip" in text and _positive_int(intent.get("duration") or intent.get("duration_seconds"), 0) == 0)
        or "不生成ai视频" in text
        or "不生成 ai 视频" in text
        or "不应执行" in text
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
            value = json.loads(_strip_json_comments(candidate))
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _strip_json_comments(value: str) -> str:
    text = re.sub(r"(?m)^\s*//.*$", "", str(value or ""))
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


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
    if key in {"image_prompts", "video_prompts"}:
        compiled_list = [item for item in compiled_list if not _skip_material_prompt_item(item)]
        legacy_list = [item for item in legacy_list if not _skip_material_prompt_item(item)]
    if compiled_list:
        if legacy_list:
            target[f"legacy_{key}"] = legacy_list
        target[key] = compiled_list
        return
    if key in {"image_prompts", "video_prompts"}:
        target[key] = []
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
