from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_ENTITY_PATH = Path("my_workspace/my_production_entities/production_entities.json")
DEFAULT_ASSET_LIBRARY_PATH = Path("my_workspace/my_asset_library/library.json")
ENTITY_GROUPS = {
    "characters": "character_id",
    "styles": "style_id",
    "products": "product_id",
    "scenes": "scene_id",
}


def load_production_entities(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_ENTITY_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}
    return normalize_production_entities(data)


def write_production_entities(path: Path, registry: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_production_entities(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return normalized


def load_asset_library(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or DEFAULT_ASSET_LIBRARY_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except Exception:
        data = []
    return [dict(item) for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def link_production_entities_to_assets(
    registry: dict[str, Any],
    asset_library: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve entity asset IDs and infer entity references from library relations.

    The persisted entity registry may intentionally contain stable asset IDs.
    RunningHub needs real workspace paths, so the compiler uses this linked copy
    without rewriting the user's registry. Character turnarounds are identity
    references; they never become pose-control inputs implicitly.
    """

    linked = normalize_production_entities(registry)
    assets = [dict(item) for item in (asset_library or []) if isinstance(item, dict)]
    assets.sort(
        key=lambda item: (
            bool(item.get("approved")),
            float(item.get("updated_at") or item.get("created_at") or 0),
        ),
        reverse=True,
    )
    by_id: dict[str, dict[str, Any]] = {}
    for item in assets:
        for key in (item.get("asset_id"), item.get("id")):
            asset_id = str(key or "").strip()
            if asset_id:
                by_id[asset_id] = item

    def resolve(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        item = by_id.get(text)
        if item:
            return _asset_workspace_path(item)
        return text.replace("\\", "/")

    for group, id_key in ENTITY_GROUPS.items():
        values = linked.get(group) if isinstance(linked.get(group), dict) else {}
        for entity_id, entity in values.items():
            if not isinstance(entity, dict):
                continue
            for key in ("master_image", "style_reference", "product_master_image", "scene_reference", "expression_sheet"):
                if key in entity:
                    entity[key] = resolve(entity.get(key))
            for key in ("turnaround_images", "reference_assets", "approved_asset_ids"):
                raw = entity.get(key)
                if isinstance(raw, list):
                    entity[key] = list(dict.fromkeys(filter(None, (resolve(item) for item in raw))))

            matched = [item for item in assets if str(item.get(id_key) or "").strip() == str(entity_id)]
            matched_paths = [path for path in (_asset_workspace_path(item) for item in matched) if path]
            if matched_paths:
                current = entity.get("reference_assets") if isinstance(entity.get("reference_assets"), list) else []
                entity["reference_assets"] = list(dict.fromkeys([*current, *matched_paths]))

            if group == "characters":
                turnarounds = [
                    _asset_workspace_path(item)
                    for item in matched
                    if _asset_has_tag(item, {"character_turnaround", "turnaround", "three_view", "three_views"})
                ]
                current_turnarounds = entity.get("turnaround_images") if isinstance(entity.get("turnaround_images"), list) else []
                entity["turnaround_images"] = list(dict.fromkeys(filter(None, [*current_turnarounds, *turnarounds])))
                if not str(entity.get("master_image") or "").strip():
                    masters = [
                        _asset_workspace_path(item)
                        for item in matched
                        if _asset_has_tag(item, {"character_base", "character_master", "identity_reference"})
                    ]
                    candidates = [*masters, *entity["turnaround_images"], *matched_paths]
                    entity["master_image"] = next((value for value in candidates if value), "")
            elif group == "styles" and not str(entity.get("style_reference") or "").strip():
                entity["style_reference"] = next(iter(matched_paths), "")
            elif group == "products" and not str(entity.get("product_master_image") or "").strip():
                entity["product_master_image"] = next(iter(matched_paths), "")
            elif group == "scenes" and not str(entity.get("scene_reference") or "").strip():
                entity["scene_reference"] = next(iter(matched_paths), "")
    return linked


def normalize_production_entities(data: Any) -> dict[str, Any]:
    source = data if isinstance(data, dict) else {}
    try:
        schema_version = int(source.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "description": str(source.get("description") or "生产实体库"),
        "characters": {},
        "styles": {},
        "products": {},
        "scenes": {},
    }
    for group, id_key in ENTITY_GROUPS.items():
        normalized[group] = _normalize_entity_group(source.get(group), id_key)
    return normalized


def collect_entity_references(
    production_intents: dict[str, list[dict[str, Any]]],
    payloads: list[dict[str, Any]] | None = None,
) -> dict[str, set[str]]:
    refs = {
        "character_ids": set(),
        "style_ids": set(),
        "product_ids": set(),
        "scene_ids": set(),
    }
    for values in production_intents.values():
        for item in values:
            if not isinstance(item, dict):
                continue
            _add_ref(refs["character_ids"], item.get("character_id"))
            for character in item.get("characters") or []:
                if isinstance(character, dict):
                    _add_ref(refs["character_ids"], character.get("character_id"))
            _add_ref(refs["style_ids"], item.get("style_id"))
            _add_ref(refs["product_ids"], item.get("product_id"))
            _add_ref(refs["scene_ids"], item.get("scene_id"))
    for payload in payloads or []:
        if not isinstance(payload, dict):
            continue
        _add_ref(refs["character_ids"], payload.get("character_id"))
        _add_ref(refs["style_ids"], payload.get("style_id"))
        _add_ref(refs["product_ids"], payload.get("product_id"))
        _add_ref(refs["scene_ids"], payload.get("scene_id"))
        context = payload.get("global_context") if isinstance(payload.get("global_context"), dict) else {}
        for character in context.get("characters") or []:
            if isinstance(character, dict):
                _add_ref(refs["character_ids"], character.get("character_id"))
        style = context.get("style") if isinstance(context.get("style"), dict) else {}
        _add_ref(refs["style_ids"], style.get("style_id"))
    return refs


def enrich_global_context_with_entities(
    context: dict[str, Any],
    registry: dict[str, Any],
    references: dict[str, set[str]],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    enriched = json.loads(json.dumps(context or {}, ensure_ascii=False))
    resolved = {
        "characters": [],
        "styles": [],
        "products": [],
        "scenes": [],
    }
    notes: list[str] = []
    for character_id in sorted(references.get("character_ids") or []):
        entity = resolve_entity(registry, "characters", character_id)
        if entity:
            resolved["characters"].append(entity)
            _upsert_context_character(enriched, entity)
        else:
            notes.append(f"未找到角色实体：{character_id}")
    for style_id in sorted(references.get("style_ids") or []):
        entity = resolve_entity(registry, "styles", style_id)
        if entity:
            resolved["styles"].append(entity)
            _merge_style_context(enriched, entity)
        else:
            notes.append(f"未找到风格实体：{style_id}")
    for product_id in sorted(references.get("product_ids") or []):
        entity = resolve_entity(registry, "products", product_id)
        if entity:
            resolved["products"].append(entity)
        else:
            notes.append(f"未找到产品实体：{product_id}")
    for scene_id in sorted(references.get("scene_ids") or []):
        entity = resolve_entity(registry, "scenes", scene_id)
        if entity:
            resolved["scenes"].append(entity)
        elif scene_id:
            notes.append(f"未找到场景实体：{scene_id}")
    enriched["resolved_entities"] = resolved
    return enriched, resolved, notes


def resolve_entity(registry: dict[str, Any], group: str, entity_id: Any) -> dict[str, Any]:
    key = str(entity_id or "").strip()
    if not key:
        return {}
    values = registry.get(group) if isinstance(registry.get(group), dict) else {}
    entity = values.get(key) if isinstance(values.get(key), dict) else {}
    if entity:
        return json.loads(json.dumps(entity, ensure_ascii=False))
    lowered = key.lower()
    for item in values.values():
        if not isinstance(item, dict):
            continue
        aliases = [str(value).strip().lower() for value in item.get("aliases", []) if str(value).strip()] if isinstance(item.get("aliases"), list) else []
        if lowered in aliases:
            return json.loads(json.dumps(item, ensure_ascii=False))
    return {}


def entity_context_for_ids(
    resolved_entities: dict[str, Any],
    *,
    character_id: str = "",
    style_id: str = "",
    product_id: str = "",
    scene_id: str = "",
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    character = _first_by_id(resolved_entities.get("characters"), "character_id", character_id)
    style = _first_by_id(resolved_entities.get("styles"), "style_id", style_id)
    product = _first_by_id(resolved_entities.get("products"), "product_id", product_id)
    scene = _first_by_id(resolved_entities.get("scenes"), "scene_id", scene_id)
    if character:
        result["character"] = character
    if style:
        result["style"] = style
    if product:
        result["product"] = product
    if scene:
        result["scene"] = scene
    references = []
    for entity in (character, style, product, scene):
        references.extend(_entity_reference_assets(entity))
    if references:
        result["reference_assets"] = list(dict.fromkeys(references))
    constraints = []
    for entity in (character, style, product, scene):
        constraints.extend(_entity_constraints(entity))
    if constraints:
        result["constraints"] = constraints
    return result


def _normalize_entity_group(value: Any, id_key: str) -> dict[str, dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                data = dict(item)
                data.setdefault(id_key, str(key))
                items.append(data)
    elif isinstance(value, list):
        items = [dict(item) for item in value if isinstance(item, dict)]
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        entity_id = str(item.get(id_key) or item.get("id") or "").strip()
        if not entity_id:
            continue
        item[id_key] = entity_id
        item["id"] = entity_id
        item.setdefault("name", entity_id)
        item.setdefault("aliases", [])
        item.setdefault("reference_assets", [])
        item.setdefault("recommended_weight", "")
        item.setdefault("negative_constraints", [])
        result[entity_id] = item
    return result


def _add_ref(target: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if text:
        target.add(text)


def _upsert_context_character(context: dict[str, Any], entity: dict[str, Any]) -> None:
    characters = context.setdefault("characters", [])
    if not isinstance(characters, list):
        characters = []
        context["characters"] = characters
    character_id = str(entity.get("character_id") or "").strip()
    if not character_id:
        return
    item = next((entry for entry in characters if isinstance(entry, dict) and str(entry.get("character_id") or "") == character_id), None)
    payload = {
        "character_id": character_id,
        "name": entity.get("name") or character_id,
        "master_image": entity.get("master_image") or "",
        "turnaround_images": entity.get("turnaround_images") if isinstance(entity.get("turnaround_images"), list) else [],
        "expression_sheet": entity.get("expression_sheet") or "",
        "outfit_rules": entity.get("outfit_rules") if isinstance(entity.get("outfit_rules"), list) else [],
        "forbidden_changes": entity.get("forbidden_changes") if isinstance(entity.get("forbidden_changes"), list) else [],
        "recommended_weight": entity.get("recommended_weight") or "",
        "reference_assets": _entity_reference_assets(entity),
    }
    if item is None:
        characters.append(payload)
    else:
        item.update({key: value for key, value in payload.items() if value not in ("", [], None)})


def _merge_style_context(context: dict[str, Any], entity: dict[str, Any]) -> None:
    style = context.setdefault("style", {})
    if not isinstance(style, dict):
        style = {}
        context["style"] = style
    style.setdefault("style_id", entity.get("style_id") or "")
    style.setdefault("name", entity.get("name") or entity.get("style_id") or "")
    style.setdefault("style_reference", entity.get("style_reference") or "")
    style.setdefault("reference_asset", entity.get("style_reference") or "")
    style.setdefault("reference_assets", _entity_reference_assets(entity))
    style.setdefault("weight", entity.get("recommended_weight") or "")
    style.setdefault("color_rules", entity.get("color_rules") if isinstance(entity.get("color_rules"), list) else [])
    style.setdefault("camera_language", entity.get("camera_language") if isinstance(entity.get("camera_language"), list) else [])
    style.setdefault("negative_constraints", entity.get("negative_constraints") if isinstance(entity.get("negative_constraints"), list) else [])
    style.setdefault("applicable_workflows", entity.get("applicable_workflows") if isinstance(entity.get("applicable_workflows"), list) else [])


def _entity_reference_assets(entity: dict[str, Any]) -> list[str]:
    if not isinstance(entity, dict):
        return []
    values = []
    for key in (
        "master_image",
        "style_reference",
        "product_master_image",
        "scene_reference",
        "expression_sheet",
    ):
        value = str(entity.get(key) or "").strip()
        if value:
            values.append(value)
    for key in ("turnaround_images", "reference_assets", "approved_asset_ids"):
        raw = entity.get(key)
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))


def _entity_constraints(entity: dict[str, Any]) -> list[str]:
    if not isinstance(entity, dict):
        return []
    constraints: list[str] = []
    for key in (
        "outfit_rules",
        "forbidden_changes",
        "color_rules",
        "camera_language",
        "negative_constraints",
        "selling_points",
        "forbidden_regions",
        "display_angles",
        "lighting_rules",
        "background_constraints",
    ):
        raw = entity.get(key)
        if isinstance(raw, list):
            constraints.extend(str(item).strip() for item in raw if str(item).strip())
        elif isinstance(raw, str) and raw.strip():
            constraints.append(raw.strip())
    return list(dict.fromkeys(constraints))


def _asset_workspace_path(item: dict[str, Any]) -> str:
    value = str(item.get("file") or item.get("path") or "").strip().replace("\\", "/")
    if not value:
        return ""
    if value.startswith("my_workspace/") or ":/" in value or value.startswith("/"):
        return value
    return f"my_workspace/my_asset_library/{value.lstrip('/')}"


def _asset_has_tag(item: dict[str, Any], expected: set[str]) -> bool:
    raw = item.get("tags")
    tags = {str(value).strip().lower() for value in raw if str(value).strip()} if isinstance(raw, list) else set()
    return bool(tags & expected)


def _first_by_id(values: Any, key: str, entity_id: str) -> dict[str, Any]:
    if not entity_id or not isinstance(values, list):
        return {}
    for item in values:
        if isinstance(item, dict) and str(item.get(key) or item.get("id") or "") == entity_id:
            return item
    return {}
