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
    lines = ["## 用户明确约束（机器提取，只读）"]
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
        latin_tokens = list(dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9_-]+", topic)))
        missing_latin = [token for token in latin_tokens if token.lower() not in output.lower()]
        compact_topic = re.sub(r"[^\u4e00-\u9fff]", "", topic)
        common = {"一个", "主题", "视频", "短片", "风格", "时代", "状态", "人类", "之后", "后的"}
        bigrams = list(
            dict.fromkeys(
                compact_topic[index : index + 2]
                for index in range(max(0, len(compact_topic) - 1))
                if compact_topic[index : index + 2] not in common
            )
        )
        matched_bigrams = [token for token in bigrams if token in output]
        minimum_matches = min(3, max(1, len(bigrams) // 6)) if bigrams else 0
        topic_covered_by_concepts = _topic_covered_by_salient_concepts(topic, output)
        if (
            not topic_covered_by_package
            and not topic_covered_by_concepts
            and (missing_latin or (minimum_matches and len(matched_bigrams) < minimum_matches))
        ):
            add_issue(f"输出未保持核心主题“{topic}”", "用户明确要求", "core_topic_missing")

    if _agent_requires_delivery_validation(agent_id, step_no):
        duration = int(lock.get("duration_seconds") or 0)
        if duration and not _mentions_duration(output, duration):
            add_issue(f"输出未体现锁定时长 {duration} 秒", "用户明确要求", "duration_missing")
        for constraint in lock.get("explicit_constraints") or []:
            if _is_delivery_format_constraint(str(constraint)) and not _mentions_delivery_format(output, str(constraint)):
                add_issue(
                    f"输出遗漏用户明确画幅约束“{constraint}”",
                    "用户明确要求",
                    "delivery_format_missing",
                )
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


def _agent_requires_delivery_validation(agent_id: str, step_no: int) -> bool:
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

        production = payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {}
        audio_intents = production.get("audio") if isinstance(production.get("audio"), list) else []
        if audio_intents:
            audio_text = _collect_audio_package_text(payload)
            if audio_text and _audio_package_text_preserves_topic(topic, audio_text, payload, lock):
                return True
    return False


def _collect_audio_package_text(payload: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            _collect_text_for_keys(
                payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {},
                {
                    "voice_text",
                    "subtitle_segments",
                    "text",
                    "description",
                    "mood_tags",
                    "mix_guidance",
                    "segments",
                    "sfx",
                },
            ),
            _collect_text_for_keys(
                payload.get("audio_package") if isinstance(payload.get("audio_package"), dict) else {},
                {
                    "voiceover_text",
                    "subtitle_srt_draft",
                    "bgm_keywords",
                    "voice",
                    "voice_style",
                },
            ),
        )
        if part
    )


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


def _audio_package_text_preserves_topic(topic: str, audio_text: str, payload: dict[str, Any], lock: dict[str, Any]) -> bool:
    if _topic_text_covers(topic, audio_text):
        return True
    if not _package_duration_matches(payload, lock):
        return False
    return _topic_covered_by_salient_concepts(topic, audio_text)


def _topic_text_covers(topic: str, text: str) -> bool:
    if not topic or not text:
        return False
    if topic in text:
        return True
    latin_tokens = list(dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9_-]+", topic)))
    if any(token.lower() not in text.lower() for token in latin_tokens):
        return False
    compact_topic = re.sub(r"[^\u4e00-\u9fff]", "", topic)
    common = {"一个", "主题", "视频", "短片", "风格", "竖屏", "横屏"}
    bigrams = [
        compact_topic[index : index + 2]
        for index in range(max(0, len(compact_topic) - 1))
        if compact_topic[index : index + 2] not in common
    ]
    if not bigrams:
        return bool(latin_tokens)
    matched = [token for token in dict.fromkeys(bigrams) if token in text]
    return len(matched) >= min(3, max(1, len(set(bigrams)) // 5))


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


def _topic_covered_by_salient_concepts(topic: str, output: str) -> bool:
    """Accept semantically faithful scripts that use paraphrases instead of exact topic bigrams."""
    compact_topic = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(topic or ""))
    text = str(output or "")
    if not compact_topic or not text:
        return False

    # The long-video guard previously required exact Chinese bigrams from the topic.
    # For themes such as “AI时代人类极大提高生产力后的生活状态”, a valid script often
    # says “效率提升 / 工作完成 / 时间归还 / 岗位消失 / 算法茧房” without repeating
    # “生产力” verbatim. Keep this narrow: require the AI anchor plus several
    # productivity/life-state concepts before treating the topic as covered.
    rebirth_2008_topic = (
        "打工" in topic
        and any(term in topic for term in ("错过", "风口", "平庸", "碌碌", "省吃俭用", "暴富"))
    )
    if rebirth_2008_topic:
        concept_families = (
            ("打工", "工薪", "上班", "打工人"),
            ("2008", "穿越", "重生", "回到"),
            ("逆袭", "翻盘", "机会", "商机", "风口", "房价", "互联网"),
        )
        if all(any(term in text for term in family) for family in concept_families):
            return True

    day_in_life_topic = (
        any(term in topic for term in ("一天", "一日", "日常", "vlog", "VLOG"))
        and any(term in topic for term in ("打工", "上班", "职场", "工作", "工薪"))
    )
    if day_in_life_topic:
        concept_families = (
            ("打工", "上班", "工作", "开会", "电脑", "回消息", "地铁", "通勤"),
            ("一天", "清晨", "闹钟", "下班", "回家", "日常", "重复"),
            ("我", "主角", "她", "他", "小美", "主人公"),
        )
        if all(any(term in text for term in family) for family in concept_families):
            return True

    topic_mentions_ai = bool(re.search(r"AI|人工智能", topic, flags=re.IGNORECASE))
    output_mentions_ai = bool(re.search(r"AI|人工智能", text, flags=re.IGNORECASE))
    productivity_topic = "生产力" in topic or ("生产" in topic and "提高" in topic)
    if topic_mentions_ai and productivity_topic and output_mentions_ai:
        concept_terms = (
            "生产力",
            "效率",
            "工作",
            "时间",
            "生活",
            "医疗",
            "教育",
            "岗位",
            "算法",
            "意义",
            "焦虑",
            "创造",
            "协作",
        )
        matched = [term for term in concept_terms if term in text]
        return len(matched) >= 3

    lower_topic = str(topic or "").lower()
    lower_text = text.lower()
    english_city_efficiency_quiet_topic = (
        "ai" in lower_topic
        and "city" in lower_topic
        and ("efficient" in lower_topic or "efficiency" in lower_topic)
        and ("quiet" in lower_topic or "silent" in lower_topic)
    )
    if english_city_efficiency_quiet_topic:
        concept_families = (
            (r"AI|artificial intelligence|\u4eba\u5de5\u667a\u80fd",),
            (r"city|urban|\u57ce\u5e02|\u90fd\u5e02",),
            (r"efficient|efficiency|\u9ad8\u6548|\u6548\u7387",),
            (r"quiet|silent|silence|\u5b89\u9759|\u5b81\u9759|\u9759\u8c27|\u65e0\u58f0|\u9759\u9ed8",),
        )
        if all(any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in family) for family in concept_families):
            return True

    english_ai_productivity_topic = "ai" in lower_topic and (
        "productivity" in lower_topic
        or "productive" in lower_topic
        or ("work" in lower_topic and ("improve" in lower_topic or "automation" in lower_topic))
    )
    if english_ai_productivity_topic and re.search(r"AI|artificial intelligence|人工智能", text, flags=re.IGNORECASE):
        concept_terms = (
            "productivity",
            "productive",
            "efficiency",
            "work",
            "routine",
            "automation",
            "time",
            "life",
            "worker",
            "organize",
            "labor",
            "leisure",
        )
        matched = [term for term in concept_terms if term in lower_text]
        if len(matched) >= 3:
            return True
        chinese_terms = (
            "生产力",
            "效率",
            "工作",
            "重复劳动",
            "自动",
            "时间",
            "生活",
            "清晨",
            "散步",
            "提前",
            "下班",
        )
        chinese_matched = [term for term in chinese_terms if term in text]
        return len(chinese_matched) >= 3

    return False


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
