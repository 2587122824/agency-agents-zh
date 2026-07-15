from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldPolicy:
    field_key: str
    risk_level: str
    question: str
    options: tuple[dict[str, Any], ...] = ()
    allowed_sources: tuple[str, ...] = ("user", "user_confirmation", "declared_default")


FIELD_POLICIES = (
    FieldPolicy("core_topic", "high", "请明确这个项目的核心创作主题。"),
    FieldPolicy("duration_seconds", "medium", "请明确成片目标时长（秒）。"),
    FieldPolicy(
        "aspect_ratio",
        "medium",
        "请选择成片画幅。",
        ({"value": "9:16", "label": "竖屏 9:16"}, {"value": "16:9", "label": "横屏 16:9"}, {"value": "1:1", "label": "方形 1:1"}),
    ),
    FieldPolicy(
        "audio_mode",
        "high",
        "这个项目是否需要音频？",
        ({"value": "off", "label": "关闭音频"}, {"value": "voiceover", "label": "使用旁白"}),
    ),
)


def evaluate_requirement(fields: dict, field_sources: dict) -> list[dict]:
    missing: list[dict] = []
    for policy in FIELD_POLICIES:
        value = fields.get(policy.field_key)
        source_type = (field_sources.get(policy.field_key) or {}).get("type")
        if value is None or value == "" or source_type not in policy.allowed_sources:
            missing.append({
                "field_key": policy.field_key,
                "reason_code": "REQUIRED_FIELD_MISSING" if value is None or value == "" else "FIELD_SOURCE_NOT_ALLOWED",
                "question": policy.question,
                "options": list(policy.options),
                "risk_level": policy.risk_level,
            })
    return missing


def validate_clarification_value(field_key: str, value: Any) -> Any:
    policy = next((item for item in FIELD_POLICIES if item.field_key == field_key), None)
    if policy is None:
        raise ValueError("UNKNOWN_REQUIREMENT_FIELD")
    if policy.options and value not in {item["value"] for item in policy.options}:
        raise ValueError("VALUE_NOT_ALLOWED")
    if field_key == "duration_seconds":
        if isinstance(value, bool) or not isinstance(value, int) or not 5 <= value <= 3600:
            raise ValueError("VALUE_NOT_ALLOWED")
    if field_key == "core_topic" and (not isinstance(value, str) or not value.strip()):
        raise ValueError("VALUE_NOT_ALLOWED")
    return value.strip() if isinstance(value, str) else value
