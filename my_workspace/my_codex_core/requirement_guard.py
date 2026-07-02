from __future__ import annotations

import re
from typing import Any


GENERATED_CONTEXT_MARKERS = (
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


def build_requirement_lock(user_input: str) -> dict[str, Any]:
    original = extract_original_requirement(user_input)
    topic_match = re.search(
        r"(?:主题|题目)\s*(?:是|为|[:：])\s*[“\"']?([^”\"'。；;\n]{2,120})",
        original,
        flags=re.IGNORECASE,
    )
    core_topic = (topic_match.group(1) if topic_match else original.splitlines()[0] if original else "").strip()
    core_topic = re.sub(r"[。；;]+$", "", core_topic).strip()

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
            "auto_resolve": "可合理推断且不改变主题、主体、合规边界或最终交付的缺省项",
            "human_required": "会改变主题、平台硬规格、品牌/产品、预算、人物身份、版权合规或最终交付的决定",
        },
    }


def requirement_lock_prompt(lock: dict[str, Any]) -> str:
    constraints = lock.get("explicit_constraints") or []
    styles = lock.get("styles") or []
    duration = int(lock.get("duration_seconds") or 0)
    lines = [
        "## 锁定需求（最高优先级，不得被上游输出、示例或素材库覆盖）",
        f"- 原始需求：{lock.get('original_requirement') or '未提供'}",
        f"- 核心主题：{lock.get('core_topic') or '未提取'}",
    ]
    if duration:
        lines.append(f"- 目标时长：{duration} 秒")
    if styles:
        lines.append(f"- 风格：{'、'.join(str(item) for item in styles)}")
    if constraints:
        lines.append(f"- 显式结构/画幅约束：{'；'.join(str(item) for item in constraints)}")
    lines.extend(
        [
            "- 禁止凭空替换主题，禁止引入原始需求中不存在的具体品牌、商品、人物或项目。",
            "- 如果上游员工输出与本锁定需求冲突，以本锁定需求为准并主动纠正。",
        ]
    )
    return "\n".join(lines)


def validate_requirement_alignment(lock: dict[str, Any], content: str, step_no: int) -> dict[str, Any]:
    output = str(content or "").strip()
    topic = str(lock.get("core_topic") or "").strip()
    original = str(lock.get("original_requirement") or "")
    issues: list[str] = []

    if not output:
        issues.append("模型输出为空")
    if topic and output:
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
        if missing_latin or (minimum_matches and len(matched_bigrams) < minimum_matches and not topic_covered_by_concepts):
            issues.append(f"输出未保持核心主题“{topic}”")

    if step_no <= 3:
        duration = int(lock.get("duration_seconds") or 0)
        if duration and not _mentions_duration(output, duration):
            issues.append(f"输出未体现锁定时长 {duration} 秒")
        for polarity in ("正面", "负面"):
            if polarity in original and polarity not in output:
                issues.append(f"输出遗漏原始结构约束“{polarity}”")

    original_has_commerce = bool(re.search(r"产品|商品|品牌|带货|推广|广告|手表|手机|鞋|服装", original))
    if not original_has_commerce and re.search(
        r"X[-‐‑–—]?Watch|智能手表|立即购买|下单购买|购买链接|革命性.{0,8}(?:产品|商品)",
        output,
        flags=re.IGNORECASE,
    ):
        issues.append("输出凭空引入了原始需求中不存在的商品/品牌叙事")

    pending_heading = re.search(r"^#{1,6}\s*待确认(?:信息|问题)?", output, flags=re.MULTILINE)
    if pending_heading and "暂无" not in output[pending_heading.start() : pending_heading.start() + 120] and not declares_human_confirmation(output):
        issues.append("输出使用了泛化的待确认项；应自动采用非阻塞默认值，或明确声明阻塞型人工确认")

    return {
        "passed": not issues,
        "step": int(step_no),
        "issues": issues,
        "core_topic": topic,
    }


def correction_prompt(prompt: str, lock: dict[str, Any], rejected_content: str, issues: list[str]) -> str:
    issue_text = "\n".join(f"- {item}" for item in issues)
    return f"""{prompt}

## 需求一致性自动纠偏
你上一次的输出已被系统拒绝，原因如下：
{issue_text}

请重新完成当前步骤。必须回到锁定需求，不得沿用下面这份跑题内容中的主题、品牌或商品：
<rejected_output>
{str(rejected_content or '')[:4000]}
</rejected_output>

{requirement_lock_prompt(lock)}
"""


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

    return False


def declares_human_confirmation(content: str) -> bool:
    text = str(content or "")
    explicit_values = [
        match.group(1).strip().lower()
        for match in re.finditer(
            r"human_confirmation_required\s*[:：]\s*`?\s*(true|false)\s*`?",
            text,
            flags=re.IGNORECASE,
        )
    ]
    if any(value == "true" for value in explicit_values):
        return True
    if any(value == "false" for value in explicit_values):
        return False
    return bool(
        re.search(r"human_confirmation_required\s*[:：]\s*true", text, flags=re.IGNORECASE)
        or re.search(r"^##\s*人工确认[（(]?阻塞[）)]?", text, flags=re.MULTILINE)
    )


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
    if _timeline_reaches_duration(text, duration_seconds):
        return True
    if _segment_durations_sum_to_target(text, duration_seconds):
        return True
    return False


def _timeline_reaches_duration(content: str, duration_seconds: int) -> bool:
    """Accept storyboard timelines such as 0-10s or 7.5-10s as proof of duration."""
    target = float(duration_seconds)
    for match in re.finditer(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|~|～|至|到)\s*(\d+(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds|秒)?",
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
