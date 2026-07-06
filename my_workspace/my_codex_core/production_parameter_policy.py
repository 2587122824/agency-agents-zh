from __future__ import annotations

import json
from typing import Any


PARAMETER_POLICY_SCHEMA_VERSION = 1


def normalize_parameter_policy_context(
    context: dict[str, Any],
    *,
    route: dict[str, Any] | None = None,
    video_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach the task-level parameter inheritance and lock policy.

    Digital staff may describe intent, but this layer is the source of truth for
    values that should stay stable across the whole production chain.
    """

    normalized = json.loads(json.dumps(context if isinstance(context, dict) else {}, ensure_ascii=False))
    route = route if isinstance(route, dict) else {}
    video_config = video_config if isinstance(video_config, dict) else {}
    render = normalized.get("render") if isinstance(normalized.get("render"), dict) else {}
    aspect_ratio = _aspect_ratio_label(
        route.get("aspect_ratio")
        or video_config.get("aspect_ratio")
        or render.get("aspect_ratio")
        or "16:9"
    )
    working_width, working_height = _working_dimensions_for_aspect(aspect_ratio)
    delivery_width, delivery_height = _delivery_dimensions_for_aspect(aspect_ratio)
    configured_delivery_width = _positive_int(render.get("delivery_width"), delivery_width)
    configured_delivery_height = _positive_int(render.get("delivery_height"), delivery_height)
    if not _delivery_dimensions_match_aspect(
        configured_delivery_width,
        configured_delivery_height,
        aspect_ratio,
    ):
        configured_delivery_width, configured_delivery_height = delivery_width, delivery_height
    render.update(
        {
            "working_width": working_width,
            "working_height": working_height,
            "delivery_width": configured_delivery_width,
            "delivery_height": configured_delivery_height,
            "frame_rate": 24,
            "aspect_ratio": aspect_ratio,
            "locked": True,
            "lock_reason": "全链路视觉生成统一使用480p工作尺寸；最终交付尺寸由后期导出阶段负责。",
        }
    )
    normalized["render"] = render
    normalized.setdefault("characters", [])
    normalized.setdefault("style", {})
    existing_policy = normalized.get("parameter_policy") if isinstance(normalized.get("parameter_policy"), dict) else {}
    policy = _build_policy(normalized)
    if isinstance(existing_policy.get("overrides"), list):
        policy["overrides"] = existing_policy["overrides"]
    if isinstance(existing_policy.get("notes"), list):
        policy["notes"] = existing_policy["notes"]
    normalized["parameter_policy"] = policy
    return normalized


def apply_locked_parameters_to_intent(
    intent: dict[str, Any],
    *,
    global_context: dict[str, Any],
    intent_kind: str,
    intent_name: str,
    notes: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    locked = dict(intent if isinstance(intent, dict) else {})
    notes = notes if isinstance(notes, list) else []
    policy = global_context.get("parameter_policy") if isinstance(global_context.get("parameter_policy"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    overrides: list[dict[str, Any]] = []

    def force(field: str, value: Any, reason: str) -> None:
        if value in (None, ""):
            return
        old = locked.get(field)
        if old != value:
            overrides.append({"field": field, "from": old, "to": value, "reason": reason})
            locked[field] = value

    if (policy.get("locks") or {}).get("render", {}).get("enabled", True):
        force("width", _positive_int(render.get("working_width"), 848), "render_lock")
        force("height", _positive_int(render.get("working_height"), 480), "render_lock")
    style_id = str((global_context.get("style") or {}).get("style_id") or "").strip() if isinstance(global_context.get("style"), dict) else ""
    if style_id:
        force("style_id", style_id, "style_lock")
    character_id = _locked_character_id(global_context)
    if character_id:
        force("character_id", character_id, "character_identity_lock")
    elif locked.get("character_id") and _character_ids(global_context) and str(locked.get("character_id")) not in _character_ids(global_context):
        notes.append(f"{intent_kind} intent {locked.get('intent_id') or locked.get('id') or intent_name} uses unknown character_id={locked.get('character_id')}; kept for compatibility but not treated as a locked entity.")

    if intent_kind == "video":
        force("fps", 24, "frame_rate_lock")
        mode_text = f"{intent_name} {locked.get('workflow_mode') or ''} {locked.get('mode') or ''}".lower()
        is_locked_i2v_clip = (
            "three_frame" in mode_text
            or "first_middle_last" in mode_text
            or "i2v_first_frame" in mode_text
            or "first_frame" in mode_text
            or intent_name in {"generate_three_frame_i2v_clip", "generate_i2v_clip"}
        )
        if is_locked_i2v_clip:
            force("duration_seconds", 4, "first_middle_last_duration_lock")
            force("fps", 24, "first_middle_last_fps_lock")
            if "three_frame" in mode_text or "first_middle_last" in mode_text or intent_name == "generate_three_frame_i2v_clip":
                locked["control_mode"] = "first_middle_last_frame"

    if overrides:
        existing = locked.get("parameter_overrides") if isinstance(locked.get("parameter_overrides"), list) else []
        locked["parameter_overrides"] = [*existing, *overrides]
    return locked, overrides


def attach_parameter_lock_metadata(
    item: dict[str, Any],
    *,
    global_context: dict[str, Any],
    intent_kind: str,
    intent_name: str,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    policy = global_context.get("parameter_policy") if isinstance(global_context.get("parameter_policy"), dict) else {}
    locks = policy.get("locks") if isinstance(policy.get("locks"), dict) else {}
    item["parameter_locks"] = {
        "character_identity": locks.get("character_identity", {}),
        "style": locks.get("style", {}),
        "composition": locks.get("composition", {}),
        "render": {
            "enabled": True,
            "working_width": _positive_int(render.get("working_width"), 848),
            "working_height": _positive_int(render.get("working_height"), 480),
            "frame_rate": _positive_int(render.get("frame_rate"), 24),
        },
    }
    if intent_kind == "video" and (intent_name == "generate_three_frame_i2v_clip" or str(item.get("workflow_mode") or item.get("mode") or "").lower() == "first_middle_last_frame"):
        item["parameter_locks"]["first_middle_last_video"] = locks.get("first_middle_last_video", {})
    item["locked_fields"] = _locked_fields_for_item(intent_kind, intent_name)
    item["allowed_variations"] = ["motion", "camera_motion", "expression_micro_variation"]
    item["forbidden_variations"] = ["identity_change", "face_change", "hair_change", "outfit_change", "style_change", "working_resolution_change"]
    if overrides:
        item["parameter_overrides"] = [*(item.get("parameter_overrides") if isinstance(item.get("parameter_overrides"), list) else []), *overrides]
    return item


def apply_locked_parameters_to_payload(payload: dict[str, Any], *, job_type: str = "", mode: str = "") -> dict[str, Any]:
    global_context = payload.get("global_context") if isinstance(payload.get("global_context"), dict) else {}
    render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
    policy = global_context.get("parameter_policy") if isinstance(global_context.get("parameter_policy"), dict) else {}
    locks = policy.get("locks") if isinstance(policy.get("locks"), dict) else {}
    if locks.get("render", {}).get("enabled", True):
        payload["width"] = _positive_int(render.get("working_width"), 848)
        payload["height"] = _positive_int(render.get("working_height"), 480)
        payload["fps"] = _positive_int(render.get("frame_rate"), 24)
    if str(job_type or payload.get("workflow_item_type") or "").lower() == "video":
        payload["fps"] = _positive_int(render.get("frame_rate"), 24)
    mode_text = f"{mode} {payload.get('workflow_mode') or ''} {payload.get('control_mode') or ''}".lower()
    is_locked_i2v_clip = "first_middle_last" in mode_text or "three_frame" in mode_text or "i2v_first_frame" in mode_text or "first_frame" in mode_text
    if is_locked_i2v_clip:
        payload["duration"] = 4
        payload["fps"] = 24
        if "first_middle_last" in mode_text or "three_frame" in mode_text:
            payload["control_mode"] = "first_middle_last_frame"
    style = global_context.get("style") if isinstance(global_context.get("style"), dict) else {}
    if style.get("style_id"):
        payload["style_id"] = str(style.get("style_id") or "")
    character_id = _locked_character_id(global_context)
    if character_id:
        payload["character_id"] = character_id
    payload["global_style_weight"] = payload.get("global_style_weight") or style.get("weight") or ""
    payload["parameter_policy"] = policy
    return payload


def _build_policy(context: dict[str, Any]) -> dict[str, Any]:
    render = context.get("render") if isinstance(context.get("render"), dict) else {}
    characters = context.get("characters") if isinstance(context.get("characters"), list) else []
    style = context.get("style") if isinstance(context.get("style"), dict) else {}
    return {
        "schema_version": PARAMETER_POLICY_SCHEMA_VERSION,
        "inheritance_order": ["locked_global_context", "entity_defaults", "template_defaults", "node_intent_values"],
        "node_override_requires_explicit_unlock": True,
        "locks": {
            "character_identity": {
                "enabled": bool(characters),
                "scope": "task",
                "locked_fields": ["character_id", "face", "hair", "outfit", "body_proportion", "identity_reference_assets"],
                "allowed_variations": ["pose", "action", "expression_micro_variation"],
                "source": "production_entities.characters",
            },
            "style": {
                "enabled": bool(style.get("style_id") or style.get("reference_asset")),
                "scope": "task",
                "style_id": str(style.get("style_id") or ""),
                "locked_fields": ["style_id", "style_reference", "palette", "camera_language", "negative_constraints"],
                "source": "production_entities.styles",
            },
            "composition": {
                "enabled": True,
                "scope": "shot",
                "rule": "锁定镜头主体与构图，只允许动作、表情微变化和运镜变化。",
            },
            "render": {
                "enabled": True,
                "scope": "chain",
                "working_width": _positive_int(render.get("working_width"), 848),
                "working_height": _positive_int(render.get("working_height"), 480),
                "delivery_width": _positive_int(render.get("delivery_width"), 1920),
                "delivery_height": _positive_int(render.get("delivery_height"), 1080),
                "frame_rate": _positive_int(render.get("frame_rate"), 24),
            },
            "first_middle_last_video": {
                "enabled": True,
                "duration_seconds": 4,
                "fps": 24,
                "rule": "首中尾帧工作流固定4秒24fps，首/中/尾只作为动作连续性锚点。",
            },
        },
        "overrides": [],
    }


def _locked_fields_for_item(intent_kind: str, intent_name: str) -> list[str]:
    fields = ["character_id", "style_id", "width", "height"]
    if intent_kind == "video":
        fields.append("fps")
    if intent_name == "generate_three_frame_i2v_clip":
        fields.extend(["duration", "duration_seconds"])
    elif intent_name == "generate_i2v_clip":
        fields.extend(["duration", "duration_seconds"])
    return fields


def _locked_character_id(context: dict[str, Any]) -> str:
    ids = _character_ids(context)
    return ids[0] if len(ids) == 1 else ""


def _character_ids(context: dict[str, Any]) -> list[str]:
    characters = context.get("characters") if isinstance(context.get("characters"), list) else []
    ids = []
    for item in characters:
        if isinstance(item, dict):
            value = str(item.get("character_id") or "").strip()
            if value and value not in ids:
                ids.append(value)
    return ids


def _aspect_ratio_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("9:16", "portrait", "vertical", "竖屏")):
        return "9:16"
    if any(token in text for token in ("1:1", "square", "方屏")):
        return "1:1"
    return "16:9"


def _working_dimensions_for_aspect(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 480, 848
    if aspect_ratio == "1:1":
        return 480, 480
    return 848, 480


def _delivery_dimensions_for_aspect(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 1080, 1920
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1920, 1080


def _delivery_dimensions_match_aspect(width: int, height: int, aspect_ratio: str) -> bool:
    if aspect_ratio == "9:16":
        return height > width
    if aspect_ratio == "1:1":
        return width == height
    return width > height


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default
