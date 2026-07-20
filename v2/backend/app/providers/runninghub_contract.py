from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


RUNNINGHUB_NODE_SOURCES = frozenset({
    "duration_ms",
    "reference_image.present",
    "reference_image.primary",
    "source_image",
    "seed",
    "shot.action",
    "shot.composition",
    "shot.duration_ms",
    "shot.face_visibility",
    "shot.subject_motion",
    "shot.negative_prompt",
    "shot.text_policy",
    "shot.visual_prompt",
    "video_spec.width",
    "video_spec.height",
    "video_spec.fps",
    "video_spec.long_side",
    "video_spec.frame_count",
})


def runninghub_source_is_supported(source: str) -> bool:
    if source in RUNNINGHUB_NODE_SOURCES:
        return True
    if not source.startswith("literal:"):
        return False
    try:
        json.loads(source[len("literal:"):])
    except json.JSONDecodeError:
        return False
    return True


def runninghub_workflow_contract_issues(
    operation_kind: str,
    bindings: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    rows = list(bindings or [])
    if not rows:
        return [{
            "code": "NODE_BINDING_LIST_EMPTY",
            "path": "node_info_list",
            "message": "RunningHub 工作流至少需要一个节点映射。",
        }]

    seen: set[tuple[str, str]] = set()
    source_image_count = 0
    reference_image_count = 0
    visual_prompt_count = 0
    for index, binding in enumerate(rows):
        if not isinstance(binding, dict):
            issues.append({
                "code": "NODE_BINDING_INVALID",
                "path": f"node_info_list.{index}",
                "message": "节点映射必须是结构化对象。",
            })
            continue
        required = ("node_id", "field_path", "value_source", "value_type", "required")
        missing = [key for key in required if key not in binding or binding[key] in (None, "")]
        if missing:
            issues.append({
                "code": "NODE_BINDING_INCOMPLETE",
                "path": f"node_info_list.{index}",
                "missing": missing,
            })
        identity = (str(binding.get("node_id", "")), str(binding.get("field_path", "")))
        if identity in seen:
            issues.append({"code": "NODE_BINDING_DUPLICATE", "path": f"node_info_list.{index}"})
        seen.add(identity)

        source = str(binding.get("value_source") or "")
        if source == "source_image":
            source_image_count += 1
        if source == "reference_image.primary":
            reference_image_count += 1
        if source == "shot.visual_prompt":
            visual_prompt_count += 1
        if not runninghub_source_is_supported(source):
            issues.append({
                "code": "RUNNINGHUB_NODE_SOURCE_UNSUPPORTED",
                "path": f"node_info_list.{index}.value_source",
                "value_source": source,
            })
        if source == "source_image" and operation_kind != "video_generation":
            issues.append({
                "code": "RUNNINGHUB_SOURCE_IMAGE_NOT_APPLICABLE",
                "path": f"node_info_list.{index}.value_source",
            })
        if source.startswith("reference_image.") and operation_kind != "image_generation":
            issues.append({
                "code": "RUNNINGHUB_REFERENCE_IMAGE_NOT_APPLICABLE",
                "path": f"node_info_list.{index}.value_source",
            })
        expected_type = {
            "source_image": "image",
            "reference_image.primary": "image",
            "reference_image.present": "boolean",
            "shot.visual_prompt": "string",
            "shot.negative_prompt": "string",
        }.get(source)
        if expected_type and binding.get("value_type") != expected_type:
            issues.append({
                "code": "RUNNINGHUB_NODE_SOURCE_TYPE_INVALID",
                "path": f"node_info_list.{index}.value_type",
                "value_source": source,
                "expected": expected_type,
            })

    if operation_kind == "video_generation" and source_image_count != 1:
        issues.append({
            "code": "RUNNINGHUB_I2V_SOURCE_IMAGE_COUNT_INVALID",
            "path": "node_info_list",
            "actual": source_image_count,
        })
    if reference_image_count > 1:
        issues.append({
            "code": "RUNNINGHUB_REFERENCE_IMAGE_COUNT_INVALID",
            "path": "node_info_list",
            "actual": reference_image_count,
        })
    if operation_kind == "image_generation" and visual_prompt_count != 1:
        issues.append({
            "code": "RUNNINGHUB_VISUAL_PROMPT_BINDING_COUNT_INVALID",
            "path": "node_info_list",
            "actual": visual_prompt_count,
        })
    return issues
