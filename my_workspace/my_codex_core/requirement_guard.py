from __future__ import annotations

import json
import re
from typing import Any


GENERATED_CONTEXT_MARKERS = (
    "## 关联资产上下文",
    "## 可复用素材库",
    "## ComfyUI 素材/预览配置",
    "## 图片生成参数",
    "## 视频生成参数",
    "## 长期记忆",
    "## 本地知识库",
    "## 继承历史任务记忆",
    "## 参考图片",
)

def extract_original_requirement(user_input: str) -> str:
    text = str(user_input or "").strip()
    cut_at = len(text)
    for marker in GENERATED_CONTEXT_MARKERS:
        index = text.find(marker)
        if index >= 0:
            cut_at = min(cut_at, index)
    return text[:cut_at].strip()


def extract_generated_context(user_input: str, allowed_markers: tuple[str, ...]) -> str:
    text = str(user_input or "")
    sections: list[str] = []
    marker_positions = sorted(
        (index, marker)
        for marker in GENERATED_CONTEXT_MARKERS
        if (index := text.find(marker)) >= 0
    )
    allowed = set(allowed_markers)
    for position, (start, marker) in enumerate(marker_positions):
        if marker not in allowed:
            continue
        end = marker_positions[position + 1][0] if position + 1 < len(marker_positions) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)
    return "\n\n".join(sections)


def build_requirement_lock(user_input: str) -> dict[str, Any]:
    original = extract_original_requirement(user_input)
    topic_match = re.search(
        r"(?:主题|题目)\s*(?:是|为|[:：])\s*[“\"']?([^”\"'。；;\n]{2,120})",
        original,
        flags=re.IGNORECASE,
    )
    core_topic = (topic_match.group(1) if topic_match else original.splitlines()[0] if original else "").strip()
    english_topic_match = re.search(
        r"(?:theme|topic|subject)\s*[:：]\s*([^\n。；;.]{2,180})",
        original,
        flags=re.IGNORECASE,
    )
    if english_topic_match:
        core_topic = english_topic_match.group(1).strip()
    core_topic = _strip_delivery_suffix(core_topic)

    duration_seconds = 0
    minute_match = re.search(r"(\d+(?:\.\d+)?)\s*分钟", original)
    second_match = re.search(r"(\d+(?:\.\d+)?)\s*秒", original)
    if minute_match:
        duration_seconds = round(float(minute_match.group(1)) * 60)
    elif second_match:
        duration_seconds = round(float(second_match.group(1)))

    styles = [
        value
        for value in ("写实", "动漫", "二次元", "国风", "赛博朋克", "电影感", "纪录片", "口播")
        if value in original
    ]
    explicit_constraints: list[str] = []
    for pattern in (
        r"前\s*\d+\s*秒[^，。；;\n）)]*",
        r"后\s*\d+\s*秒[^，。；;\n）)]*",
        r"(?:横屏|竖屏|16:9|9:16|1:1)",
    ):
        explicit_constraints.extend(match.group(0).strip() for match in re.finditer(pattern, original))

    return {
        "schema_version": 1,
        "original_requirement": original,
        "core_topic": core_topic,
        "duration_seconds": duration_seconds,
        "styles": styles,
        "explicit_constraints": list(dict.fromkeys(item for item in explicit_constraints if item)),
        "confirmation_policy": {
            "auto_resolve": "仅允许不改变员工内容的技术解析；不得补写创意、默认值或生产方向",
            "human_required": "缺少当前步骤必需信息，或决定会改变用户明确要求与最终交付",
        },
    }


def requirement_lock_prompt(lock: dict[str, Any]) -> str:
    constraints = lock.get("explicit_constraints") or []
    duration = int(lock.get("duration_seconds") or 0)
    topic = str(lock.get("core_topic") or "").strip()
    lines = ["## 用户明确约束（机器提取，只读）"]
    if topic:
        lines.append(f"- core_topic: {topic}")
    if duration:
        lines.append(f"- target_duration_seconds: {duration}")
    if constraints:
        lines.append(f"- delivery_constraints: {'；'.join(str(item) for item in constraints)}")
    return "\n".join(lines)


def validate_requirement_alignment(
    lock: dict[str, Any],
    content: str,
    step_no: int,
    agent_id: str = "",
) -> dict[str, Any]:
    output = str(content or "").strip()
    topic = str(lock.get("core_topic") or "").strip()
    original = str(lock.get("original_requirement") or "")
    issues: list[str] = []
    issue_details: list[dict[str, str]] = []

    def add_issue(message: str, source: str, code: str) -> None:
        issues.append(message)
        issue_details.append({"source": source, "code": code, "message": message})

    if not output:
        add_issue("模型输出为空", "员工岗位输出契约", "empty_output")
    if topic and output and _agent_requires_topic_validation(agent_id, step_no):
        topic_covered_by_package = _production_package_preserves_topic(output, lock)
        if topic not in output and not topic_covered_by_package:
            add_issue(f"输出未保持核心主题“{topic}”", "用户明确要求", "core_topic_missing")

    if _agent_requires_duration_validation(agent_id, step_no):
        duration = int(lock.get("duration_seconds") or 0)
        if duration and not _mentions_duration(output, duration):
            add_issue(f"输出未体现锁定时长 {duration} 秒", "用户明确要求", "duration_missing")
    if _agent_requires_delivery_format_validation(agent_id, step_no):
        for constraint in lock.get("explicit_constraints") or []:
            if _is_delivery_format_constraint(str(constraint)) and not _mentions_delivery_format(output, str(constraint)):
                add_issue(
                    f"输出遗漏用户明确画幅约束“{constraint}”",
                    "用户明确要求",
                    "delivery_format_missing",
                )
    if _agent_requires_structure_validation(agent_id, step_no):
        for polarity in ("正面", "负面"):
            if polarity in original and polarity not in output:
                add_issue(f"输出遗漏原始结构约束“{polarity}”", "用户明确要求", "structure_missing")

    pending_heading = re.search(r"^#{1,6}\s*待确认(?:信息|问题)?", output, flags=re.MULTILINE)
    if pending_heading and "暂无" not in output[pending_heading.start() : pending_heading.start() + 120] and not declares_human_confirmation(output):
        add_issue(
            "输出使用了泛化的待确认项；需要明确声明是否阻塞执行",
            "员工岗位输出契约",
            "confirmation_state_ambiguous",
        )

    return {
        "passed": not issues,
        "step": int(step_no),
        "issues": issues,
        "issue_details": issue_details,
        "core_topic": topic,
    }


def _strip_delivery_suffix(value: str) -> str:
    text = re.sub(r"[。；;]+$", "", str(value or "")).strip()
    delivery_segment = re.compile(
        r"^(?:(?:横屏|竖屏|portrait|vertical|landscape|horizontal|16\s*:\s*9|9\s*:\s*16|1\s*:\s*1|"
        r"\d+(?:\.\d+)?\s*(?:分钟|秒|mins?|minutes?|secs?|seconds?)|长视频|短视频|成片|视频)\s*)+$",
        flags=re.IGNORECASE,
    )
    parts = [part.strip() for part in re.split(r"[，,；;|]", text) if part.strip()]
    while len(parts) > 1 and delivery_segment.fullmatch(parts[-1]):
        parts.pop()
    text = "，".join(parts).strip()
    trailing = re.compile(
        r"(?:[，,；;\s]*(?:横屏|竖屏|portrait|vertical|landscape|horizontal|16\s*:\s*9|9\s*:\s*16|1\s*:\s*1|"
        r"\d+(?:\.\d+)?\s*(?:分钟|秒|mins?|minutes?|secs?|seconds?)|长视频|短视频|成片))+$",
        flags=re.IGNORECASE,
    )
    cleaned = trailing.sub("", text).strip(" ，,；;")
    return cleaned or text


def _agent_requires_topic_validation(agent_id: str, step_no: int) -> bool:
    agent = str(agent_id or "").strip()
    if agent:
        return agent.startswith(("01_", "03_", "23_", "04_"))
    return int(step_no or 0) <= 4


def _agent_requires_duration_validation(agent_id: str, step_no: int) -> bool:
    agent = str(agent_id or "").strip()
    if agent:
        return agent.startswith(("01_", "03_", "23_"))
    return int(step_no or 0) <= 3


def _agent_requires_delivery_format_validation(agent_id: str, step_no: int) -> bool:
    agent = str(agent_id or "").strip()
    if agent:
        return agent.startswith(("01_", "23_"))
    return int(step_no or 0) in {1, 3}


def _agent_requires_structure_validation(agent_id: str, step_no: int) -> bool:
    agent = str(agent_id or "").strip()
    if agent:
        return agent.startswith(("01_", "03_", "23_"))
    return int(step_no or 0) <= 3


def _production_package_preserves_topic(content: str, lock: dict[str, Any]) -> bool:
    """Accept structured packages that preserve the locked task without verbatim prose."""
    topic = str(lock.get("core_topic") or "").strip()
    if not topic:
        return True
    for payload in _json_objects(content):
        anchor_text = _collect_text_for_keys(
            payload,
            {
                "requirement_anchor",
                "topic_anchor",
                "task_anchor",
                "core_topic",
                "locked_topic",
                "original_requirement",
                "delivery_requirement",
                "aspect_ratio",
            },
        )
        if anchor_text and _topic_text_covers(topic, anchor_text):
            return True
    return False


def _collect_text_for_keys(value: Any, keys: set[str]) -> str:
    parts: list[str] = []
    normalized_keys = {key.lower() for key in keys}

    def visit(item: Any, current_key: str = "") -> None:
        key_matches = current_key.lower() in normalized_keys
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, str(key))
            return
        if isinstance(item, list):
            for child in item:
                visit(child, current_key)
            return
        if key_matches and item not in (None, ""):
            parts.append(str(item))

    visit(value)
    return " ".join(parts)


def _topic_text_covers(topic: str, text: str) -> bool:
    return bool(topic and text and topic in text)


def _package_duration_matches(payload: dict[str, Any], lock: dict[str, Any]) -> bool:
    duration = int(lock.get("duration_seconds") or 0)
    if not duration:
        return True
    numeric_values = _collect_numeric_values_for_keys(
        payload,
        {
            "duration",
            "duration_seconds",
            "target_duration_seconds",
            "total_duration_seconds",
        },
    )
    return any(abs(value - duration) <= max(2.0, duration * 0.08) for value in numeric_values)


def _collect_numeric_values_for_keys(value: Any, keys: set[str]) -> list[float]:
    values: list[float] = []
    normalized_keys = {key.lower() for key in keys}

    def visit(item: Any, current_key: str = "") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, str(key))
            return
        if isinstance(item, list):
            for child in item:
                visit(child, current_key)
            return
        if current_key.lower() not in normalized_keys:
            return
        try:
            values.append(float(item))
        except (TypeError, ValueError):
            return

    visit(value)
    return values


def _json_objects(content: str) -> list[dict[str, Any]]:
    text = str(content or "").strip()
    candidates = re.findall(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if not candidates and text:
        candidates = [text]
    values: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            value = json.loads(_strip_json_comments(candidate))
        except Exception:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _strip_json_comments(value: str) -> str:
    text = re.sub(r"(?m)^\s*//.*$", "", str(value or ""))
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def declares_human_confirmation(content: str) -> bool:
    text = str(content or "")
    explicit_values = [
        match.group(1).strip().lower()
        for match in re.finditer(
            r"[\"'`]?human_confirmation_required[\"'`]?\s*[:：]\s*[\"'`]?\s*(true|false)\s*[\"'`]?",
            text,
            flags=re.IGNORECASE,
        )
    ]
    if any(value == "true" for value in explicit_values):
        return True
    if any(value == "false" for value in explicit_values):
        return False
    if re.search(
        r"[\"'`]?human_confirmation_required[\"'`]?\s*[:：]\s*[\"'`]?\s*true",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    heading_match = re.search(r"^##\s*人工确认[（(]?阻塞[）)]?.*$", text, flags=re.MULTILINE)
    if not heading_match:
        return False
    next_heading = re.search(r"^##\s+", text[heading_match.end() :], flags=re.MULTILINE)
    section = text[heading_match.end() : heading_match.end() + next_heading.start()] if next_heading else text[heading_match.end() :]
    normalized_section = section.strip().lower()
    normalized_section = re.sub(r"^[。．.、\s>*\-•·]+", "", normalized_section)
    non_blocking_prefixes = ("无", "暂无", "没有", "无需", "不需要", "否", "none", "no", "false", "n/a", "not required")
    if normalized_section.startswith(non_blocking_prefixes):
        return False
    return bool(normalized_section)


def _mentions_duration(content: str, duration_seconds: int) -> bool:
    text = str(content or "")
    if not duration_seconds:
        return True
    escaped = re.escape(str(duration_seconds))
    if re.search(rf"(?<!\d){escaped}\s*(?:s|sec|secs|second|seconds|秒)(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
        return True
    if duration_seconds % 60 == 0:
        minutes = duration_seconds // 60
        if re.search(rf"(?<!\d){re.escape(str(minutes))}\s*(?:min|mins|minute|minutes|分钟)(?![A-Za-z0-9])", text, flags=re.IGNORECASE):
            return True
    for payload in _json_objects(text):
        if _package_duration_matches(payload, {"duration_seconds": duration_seconds}):
            return True
    if _timeline_reaches_duration(text, duration_seconds):
        return True
    if _segment_durations_sum_to_target(text, duration_seconds):
        return True
    return False


def _timeline_reaches_duration(content: str, duration_seconds: int) -> bool:
    """Accept second ranges and MM:SS/HH:MM:SS storyboard timelines."""
    target = float(duration_seconds)
    timestamp = r"(?:\d{1,3}:){1,2}\d{1,2}(?:[.,]\d{1,3})?"
    range_separator = r"[-‐‑‒–—―~～至到]"
    for match in re.finditer(rf"(?<!\d)({timestamp})\s*{range_separator}\s*({timestamp})(?!\d)", content):
        end = _timestamp_seconds(match.group(2))
        if end is not None and abs(end - target) <= 0.25:
            return True
    for match in re.finditer(
        rf"(?<!\d)(\d+(?:\.\d+)?)\s*{range_separator}\s*(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?",
        content,
        flags=re.IGNORECASE,
    ):
        try:
            start = float(match.group(1))
            end = float(match.group(2))
        except ValueError:
            continue
        if start <= target <= end + 0.25 or abs(end - target) <= 0.25:
            return True
    return False


def _timestamp_seconds(value: str) -> float | None:
    parts = re.split(r":", str(value or "").strip())
    if len(parts) not in {2, 3}:
        return None
    try:
        numbers = [float(part.replace(",", ".")) for part in parts]
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def _is_delivery_format_constraint(value: str) -> bool:
    return str(value or "").strip().lower() in {"竖屏", "横屏", "16:9", "9:16", "1:1"}


def _mentions_delivery_format(content: str, constraint: str) -> bool:
    text = str(content or "").lower()
    value = str(constraint or "").strip().lower()
    if value in {"竖屏", "9:16"}:
        return "竖屏" in text or "9:16" in text or "vertical" in text
    if value in {"横屏", "16:9"}:
        return "横屏" in text or "16:9" in text or "horizontal" in text or "landscape" in text
    if value == "1:1":
        return "1:1" in text or "方形" in text or "square" in text
    return True


def _segment_durations_sum_to_target(content: str, duration_seconds: int) -> bool:
    """Accept split-shot tables like four 2.5s shots for a 10-second task."""
    values: list[float] = []
    for match in re.finditer(
        r"(?<![\d-])(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)(?![A-Za-z0-9])",
        content,
        flags=re.IGNORECASE,
    ):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if 0 < value <= max(duration_seconds, 1):
            values.append(value)
    if not values:
        return False
    total = sum(values)
    return abs(total - float(duration_seconds)) <= 0.5
