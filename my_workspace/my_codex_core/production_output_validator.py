from __future__ import annotations

import json
import re
from typing import Any


IMAGE_INTENTS = {
    "generate_base_asset",
    "generate_turnaround",
    "generate_keyframe",
    "generate_three_frame_shot",
    "generate_cover_key_visual",
    "repair_or_cutout_image",
    "no_image_required",
}
VIDEO_INTENTS = {
    "generate_i2v_clip",
    "generate_three_frame_i2v_clip",
    "generate_broll_clip",
    "generate_talking_image",
    "enhance_video",
    "repair_video",
}


def validate_production_output(
    step: dict[str, Any],
    content: str,
    requirement_lock: dict[str, Any],
    previous_outputs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    agent = str(step.get("agent") or "")
    issues: list[str] = []
    payloads = _json_objects(content)
    effective_lock = _effective_requirement_lock(requirement_lock, payloads, previous_outputs or [])
    duration = int(effective_lock.get("duration_seconds") or 0)
    expected_work = _expected_resolution(effective_lock, delivery=False)
    expected_delivery = _expected_resolution(effective_lock, delivery=True)

    if agent.startswith("03_"):
        _validate_script(content, duration, issues)
    elif agent.startswith("06_"):
        _validate_images(payloads, expected_work, issues)
    elif agent.startswith("20_"):
        _validate_audio(payloads, duration, issues, effective_lock, previous_outputs or [])
    elif agent.startswith("07_"):
        upstream_ids = _upstream_image_ids(previous_outputs or [])
        _validate_videos(payloads, expected_work, upstream_ids, issues, effective_lock)
    elif agent.startswith("22_"):
        _validate_package(payloads, duration, expected_delivery, issues)

    return {
        "passed": not issues,
        "agent": agent,
        "issues": issues,
        "issue_details": [
            {
                "source": _production_issue_source(agent, message),
                "code": "production_contract_violation",
                "message": message,
            }
            for message in issues
        ],
        "expected_work_resolution": f"{expected_work[0]}x{expected_work[1]}",
        "expected_delivery_resolution": f"{expected_delivery[0]}x{expected_delivery[1]}",
    }


def _validate_script(content: str, duration: int, issues: list[str]) -> None:
    if not duration:
        return
    voice_text = _extract_script_tts_text(content)
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", voice_text))
    max_cjk = int(duration * 5.0)
    if cjk_count > max_cjk:
        issues.append(f"TTS口播约 {cjk_count} 个汉字，无法在 {duration} 秒内自然读完；上限约 {max_cjk} 字")


def _extract_script_tts_text(content: str) -> str:
    """Return the final narration text intended for TTS from a 03 script output.

    Staff 03 commonly includes both a full timestamped script and a compact
    TTS plain-text section.  The duration gate must count the final TTS section,
    not the longest fenced block in the whole document, otherwise duplicated
    script text can be rejected even when the actual TTS payload fits.
    """
    tts_section = _section_after_heading_regex(content, r"TTS")
    if tts_section:
        text_blocks = re.findall(r"```(?:text|txt)?\s*(.*?)```", tts_section, flags=re.IGNORECASE | re.DOTALL)
        if text_blocks:
            return max(text_blocks, key=len).strip()
        return _strip_markdown_noise(tts_section).strip()
    text_blocks = re.findall(r"```text\s*(.*?)```", content, flags=re.IGNORECASE | re.DOTALL)
    return max(text_blocks, key=len).strip() if text_blocks else ""


def _validate_images(payloads: list[dict[str, Any]], expected: tuple[int, int], issues: list[str]) -> None:
    intent_payload = _payload_with(payloads, "production_intents", "image")
    compat_payload = _payload_with(payloads, "image_prompts")
    intents = _intent_group(intent_payload, "image")
    prompts = compat_payload.get("image_prompts") if isinstance(compat_payload.get("image_prompts"), list) else []
    if not intents:
        issues.append("缺少可解析的 production_intents.image JSON 数组")
    if not prompts:
        issues.append("缺少 image_prompts 兼容数组")
    if intents and prompts and intent_payload is not compat_payload:
        issues.append("production_intents.image 与 image_prompts 必须位于同一个 JSON 对象中")
    ids: set[str] = set()
    for index, item in enumerate(intents, 1):
        intent = str(item.get("intent") or "")
        intent_id = str(item.get("intent_id") or "")
        if intent not in IMAGE_INTENTS:
            issues.append(f"图片意图 {index} 的 intent 无效：{intent or '空'}")
        if not intent_id:
            issues.append(f"图片意图 {index} 缺少 intent_id")
        elif intent_id in ids:
            issues.append(f"图片 intent_id 重复：{intent_id}")
        ids.add(intent_id)
    for index, item in enumerate(prompts, 1):
        _validate_work_resolution(item, expected, f"image_prompts[{index}]", issues)


def _validate_audio(
    payloads: list[dict[str, Any]],
    duration: int,
    issues: list[str],
    requirement_lock: dict[str, Any],
    previous_outputs: list[dict[str, str]],
) -> None:
    payload = _payload_with(payloads, "production_intents", "audio")
    intents = _intent_group(payload, "audio")
    if not intents:
        issues.append("缺少可解析的 production_intents.audio JSON 数组")
        return
    voice = next((item for item in intents if item.get("intent") == "generate_voiceover"), None)
    subtitles = next((item for item in intents if item.get("intent") == "build_subtitles"), None)
    if not isinstance(voice, dict):
        issues.append("缺少 generate_voiceover 音频意图")
    elif _intent_disabled(voice):
        if not _requirement_disables_voiceover(requirement_lock):
            issues.append("generate_voiceover 已禁用，但原始需求未明确不需要配音/旁白")
    else:
        voice_text = str(voice.get("voice_text") or "")
        upstream_voice_text = _upstream_script_tts_text(previous_outputs)
        if not upstream_voice_text:
            issues.append("缺少上游 03_口播脚本师的 TTS 纯文本，无法验证旁白继承关系")
        elif _normalize_spoken_text(voice_text) != _normalize_spoken_text(upstream_voice_text):
            issues.append("旁白正文未逐字继承上游 03_口播脚本师的 TTS 纯文本")
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", voice_text))
        if duration and cjk_count > int(duration * 5.0):
            issues.append(f"旁白约 {cjk_count} 个汉字，无法在 {duration} 秒内自然读完")
        target_duration = _number(voice.get("target_duration_seconds"))
        if duration and target_duration > duration:
            issues.append(f"旁白目标时长 {target_duration:g} 秒超过成片 {duration} 秒")
    if not isinstance(subtitles, dict):
        issues.append("缺少 build_subtitles 字幕意图")
        return
    max_end = 0.0
    invalid_times: list[str] = []
    subtitle_segments = subtitles.get("segments")
    if not isinstance(subtitle_segments, list):
        # Employee 20's published contract and examples use
        # `subtitle_segments`; keep `segments` as the concise canonical alias.
        subtitle_segments = subtitles.get("subtitle_segments")
    if not isinstance(subtitle_segments, list):
        subtitle_segments = []
    for segment in subtitle_segments:
        if not isinstance(segment, dict):
            issues.append("subtitle segments contains a non-object item")
            continue
        for key, aliases in (
            ("start_time", ("start_time", "start", "start_timecode", "start_time_seconds", "start_seconds")),
            ("end_time", ("end_time", "end", "end_timecode", "end_time_seconds", "end_seconds")),
        ):
            raw, parsed = _segment_time_seconds(segment, aliases)
            if parsed is None:
                invalid_times.append(raw or "空")
            elif key == "end_time":
                max_end = max(max_end, parsed)
    audio_package = payload.get("audio_package") if isinstance(payload.get("audio_package"), dict) else {}
    if not audio_package:
        package_payload = _payload_with(payloads, "audio_package")
        audio_package = package_payload.get("audio_package") if isinstance(package_payload.get("audio_package"), dict) else {}
    if isinstance(voice, dict) and not _intent_disabled(voice):
        package_voice_text = str(audio_package.get("voiceover_text") or "")
        if _normalize_spoken_text(package_voice_text) != _normalize_spoken_text(str(voice.get("voice_text") or "")):
            issues.append("audio_package.voiceover_text 与 generate_voiceover.voice_text 不一致")
    srt = str(audio_package.get("subtitle_srt_draft") or "")
    srt = srt.replace("\\n", "\n").replace("\\r", "\r")
    if _intent_disabled(subtitles):
        if not (_requirement_disables_subtitles(requirement_lock) or _requirement_disables_voiceover(requirement_lock)):
            issues.append("build_subtitles 已禁用，但原始需求未明确不需要字幕")
        return
    if not subtitle_segments and not srt.strip():
        issues.append("build_subtitles.segments 为空，且 audio_package.subtitle_srt_draft 未提供")
    for raw in re.findall(r"-->\s*([^\n\r]+)", srt):
        parsed = _time_seconds(raw.strip())
        if parsed is None:
            invalid_times.append(raw.strip())
        else:
            max_end = max(max_end, parsed)
    if invalid_times:
        issues.append(f"字幕存在非法时间码：{', '.join(invalid_times[:3])}")
    if duration and max_end > duration + 0.25:
        issues.append(f"字幕结束于 {max_end:g} 秒，超过成片 {duration} 秒")
    if isinstance(voice, dict):
        voice_chars = len(re.findall(r"[\u4e00-\u9fff]", str(voice.get("voice_text") or "")))
        subtitle_text = "\n".join(
            str(item.get("text") or item.get("subtitle_text") or item.get("content") or item.get("line") or "")
            for item in subtitle_segments
            if isinstance(item, dict)
        )
        if srt.strip():
            subtitle_text = f"{subtitle_text}\n{_srt_dialogue_text(srt)}"
        subtitle_chars = len(re.findall(r"[\u4e00-\u9fff]", subtitle_text))
        if voice_chars and subtitle_chars < voice_chars * 0.9:
            issues.append("字幕文本覆盖不足，少于旁白正文的 90%")


def _upstream_script_tts_text(previous_outputs: list[dict[str, str]]) -> str:
    for output in previous_outputs:
        if not isinstance(output, dict) or not str(output.get("agent") or "").startswith("03_"):
            continue
        return _extract_script_tts_text(str(output.get("content") or ""))
    return ""


def _normalize_spoken_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _production_issue_source(agent: str, message: str) -> str:
    if str(agent or "").startswith("03_"):
        return "用户明确要求"
    if any(
        marker in str(message or "")
        for marker in (
            "上游 03_口播脚本师",
            "旁白正文未逐字继承",
            "audio_package.voiceover_text",
        )
    ):
        return "员工岗位输出契约"
    return "生产接口技术契约"


def _validate_videos(
    payloads: list[dict[str, Any]],
    expected: tuple[int, int],
    upstream_ids: set[str],
    issues: list[str],
    requirement_lock: dict[str, Any],
) -> None:
    intent_payload = _payload_with(payloads, "production_intents", "video")
    compat_payload = _payload_with(payloads, "video_prompts")
    intents = _intent_group(intent_payload, "video")
    prompts = compat_payload.get("video_prompts") if isinstance(compat_payload.get("video_prompts"), list) else []
    if _video_generation_disabled(intent_payload, compat_payload) and _requirement_disables_ai_video(requirement_lock):
        return
    if not intents:
        issues.append("缺少可解析的 production_intents.video JSON 数组")
    if not prompts:
        issues.append("缺少 video_prompts 兼容数组")
    if intents and prompts and intent_payload is not compat_payload:
        issues.append("production_intents.video 与 video_prompts 必须位于同一个 JSON 对象中")
    ids: set[str] = set()
    for index, item in enumerate(intents, 1):
        intent = str(item.get("intent") or "")
        intent_id = str(item.get("intent_id") or "")
        if intent not in VIDEO_INTENTS:
            issues.append(f"视频意图 {index} 的 intent 无效：{intent or '空'}")
        if not intent_id:
            issues.append(f"视频意图 {index} 缺少 intent_id")
        elif intent_id in ids:
            issues.append(f"视频 intent_id 重复：{intent_id}")
        ids.add(intent_id)
        if intent == "generate_three_frame_i2v_clip":
            if _number(item.get("duration"), item.get("duration_seconds")) != 4:
                issues.append(f"首中尾帧意图 {intent_id or index} 必须固定为 4 秒")
            if _number(item.get("fps")) != 24:
                issues.append(f"首中尾帧意图 {intent_id or index} 必须为 24fps")
            source_ids = _string_list(item.get("source_intent_ids"))
            refs = _reference_ids(item)
            if not source_ids and len(set(refs)) < 3:
                issues.append(f"首中尾帧意图 {intent_id or index} 必须引用一个三帧图片意图，或显式绑定 start/middle/end 三帧")
            for ref in [*source_ids, *refs]:
                if upstream_ids and ref not in upstream_ids:
                    issues.append(f"视频意图 {intent_id or index} 引用了不存在的上游图片：{ref}")
        elif intent in {"generate_i2v_clip", "generate_talking_image"}:
            for ref in _reference_ids(item):
                if upstream_ids and ref not in upstream_ids:
                    issues.append(f"视频意图 {intent_id or index} 引用了不存在的上游图片：{ref}")
    for index, item in enumerate(prompts, 1):
        label = f"video_prompts[{index}]"
        mode_text = " ".join(str(item.get(key) or "") for key in ("video_task_mode", "workflow_id", "workflow_mode", "control_mode")).lower()
        intent_text = " ".join(str(item.get(key) or "") for key in ("intent", "task_type", "capability", "asset_tag", "id", "job_id")).lower()
        is_postprocess = any(
            marker in f"{mode_text} {intent_text}"
            for marker in (
                "enhance",
                "upscale",
                "interpolation",
                "deflicker",
                "stabilize",
                "postprocess",
                "video_enhance",
                "video_upscale",
                "frame_interpolation",
            )
        )
        if not is_postprocess:
            _validate_work_resolution(item, expected, label, issues)
        if "first_last" in mode_text or "06b" in mode_text:
            issues.append(f"{label} 仍使用已禁用的首尾帧模式")
        if "first_middle_last" in mode_text or "06c" in mode_text or "three_frame" in mode_text:
            if _number(item.get("duration"), item.get("duration_seconds")) != 4:
                issues.append(f"{label} 的首中尾帧视频必须固定为 4 秒")
            if _number(item.get("fps")) != 24:
                issues.append(f"{label} 的首中尾帧视频必须为 24fps")
            refs = _reference_ids(item)
            if len(set(refs)) < 3:
                refs = _resolve_video_prompt_refs(item, index - 1, intents)
            source_ids = []
            if len(set(refs)) < 3:
                source_ids = _resolve_video_prompt_source_ids(item, index - 1, intents)
            if len(set(refs)) < 3 and not source_ids:
                issues.append(f"{label} 缺少 start/middle/end 三帧引用")
            for ref in [*refs, *source_ids]:
                if upstream_ids and ref not in upstream_ids:
                    issues.append(f"{label} 引用了不存在的上游图片：{ref}")


def _validate_package(
    payloads: list[dict[str, Any]],
    duration: int,
    expected_delivery: tuple[int, int],
    issues: list[str],
) -> None:
    payload = _payload_with(payloads, "production_intents", "package")
    intents = _intent_group(payload, "package")
    if not intents:
        issues.append("缺少可解析的 production_intents.package JSON 数组")
        return
    timeline_intent = next((item for item in intents if item.get("intent") == "build_edit_timeline"), None)
    detailed_timeline_valid = False
    if not isinstance(timeline_intent, dict):
        issues.append("缺少 build_edit_timeline 意图")
    else:
        timeline = timeline_intent.get("timeline") if isinstance(timeline_intent.get("timeline"), list) else []
        previous_end = 0.0
        timeline_issue_count_before = len(issues)
        for index, clip in enumerate(timeline, 1):
            start = _number(clip.get("start_seconds"))
            clip_duration = _number(clip.get("duration_seconds"))
            if index == 1 and abs(start) > 0.25:
                issues.append(f"剪辑时间轴必须从 0s 开始，实际从 {start:g}s 开始")
            if index > 1 and abs(start - previous_end) > 0.25:
                relation = "重叠" if start < previous_end else "空档"
                issues.append(f"剪辑时间轴第 {index} 项存在{relation}：应从 {previous_end:g}s 开始，实际 {start:g}s")
            previous_end = max(previous_end, start + clip_duration)
        if duration and abs(previous_end - duration) > 0.5:
            issues.append(f"详细剪辑时间轴结束于 {previous_end:g} 秒，不等于目标 {duration} 秒")
        detailed_timeline_valid = bool(timeline) and len(issues) == timeline_issue_count_before
    compact = payload.get("edit_timeline") if isinstance(payload.get("edit_timeline"), dict) else {}
    clips = compact.get("clips") if isinstance(compact.get("clips"), list) else []
    if clips and duration and not detailed_timeline_valid:
        total = sum(_number(item.get("duration_seconds")) for item in clips)
        if abs(total - duration) > 0.5 and not _compact_timeline_reaches_duration(compact, duration):
            issues.append(f"兼容剪辑时间轴合计 {total:g} 秒，不等于目标 {duration} 秒")
    delivery = payload.get("delivery_spec") if isinstance(payload.get("delivery_spec"), dict) else {}
    if not delivery:
        delivery = next(
            (
                item
                for item in intents
                if isinstance(item, dict)
                and str(item.get("intent") or "") in {"apply_delivery_spec", "delivery_spec"}
            ),
            {},
        )
    resolution = _resolution(delivery.get("resolution") or delivery.get("delivery_resolution"))
    if resolution != expected_delivery:
        issues.append(f"交付分辨率必须为 {expected_delivery[0]}x{expected_delivery[1]}，当前为 {resolution[0]}x{resolution[1]}")
    fps = _number(delivery.get("fps"))
    if fps != 24:
        issues.append(f"交付帧率必须继承全局 24fps，当前为 {fps:g}fps")
    missing = payload.get("missing_assets") if isinstance(payload.get("missing_assets"), list) else []
    if missing:
        issues.append(f"仍有 {len(missing)} 项缺失素材，不能声明生产就绪或无阻塞项")


def _compact_timeline_reaches_duration(compact: dict[str, Any], duration: int) -> bool:
    entries: list[Any] = []
    for key in ("clips", "transitions", "overlays"):
        values = compact.get(key)
        if isinstance(values, list):
            entries.extend(values)
    max_end = 0.0
    saw_timed_entry = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = _number(entry.get("start_seconds"), entry.get("start"))
        entry_duration = _number(entry.get("duration_seconds"), entry.get("duration"))
        end = _number(entry.get("end_seconds"), entry.get("end"))
        if entry_duration > 0:
            end = max(end, start + entry_duration)
        if end > 0:
            saw_timed_entry = True
            max_end = max(max_end, end)
    return saw_timed_entry and abs(max_end - duration) <= 0.5


def _json_objects(content: str) -> list[dict[str, Any]]:
    text = str(content or "").strip()
    blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if not blocks and text:
        blocks = [text]
    values: list[dict[str, Any]] = []
    for block in blocks:
        try:
            value = json.loads(_strip_json_comments(block))
        except Exception:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


def _effective_requirement_lock(
    requirement_lock: dict[str, Any],
    payloads: list[dict[str, Any]],
    previous_outputs: list[dict[str, str]],
) -> dict[str, Any]:
    lock = dict(requirement_lock if isinstance(requirement_lock, dict) else {})
    route_sources: list[dict[str, Any]] = []
    for output in previous_outputs:
        if isinstance(output, dict):
            route_sources.extend(_json_objects(str(output.get("content") or "")))
    route_sources.extend(payloads)
    for payload in route_sources:
        if not isinstance(payload, dict):
            continue
        for source_key, target_key in (
            ("aspect_ratio", "aspect_ratio"),
            ("target_aspect_ratio", "aspect_ratio"),
            ("target_platform", "target_platform"),
            ("platform", "target_platform"),
            ("duration_seconds", "duration_seconds"),
            ("target_duration_seconds", "duration_seconds"),
            ("production_type", "production_type"),
            ("quality_mode", "quality_mode"),
        ):
            value = payload.get(source_key)
            if value not in (None, "") and not lock.get(target_key):
                lock[target_key] = value
    return lock


def _strip_json_comments(value: str) -> str:
    text = re.sub(r"(?m)^\s*//.*$", "", str(value or ""))
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _payload_with(payloads: list[dict[str, Any]], key: str, group: str = "") -> dict[str, Any]:
    for payload in payloads:
        if key not in payload:
            continue
        if key == "production_intents" and group:
            intents = payload.get(key)
            if isinstance(intents, dict) and isinstance(intents.get(group), list):
                return payload
        else:
            return payload
    return {}


def _intent_group(payload: dict[str, Any], group: str) -> list[dict[str, Any]]:
    production = payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {}
    values = production.get(group) if isinstance(production.get(group), list) else []
    return [item for item in values if isinstance(item, dict)]


def _intent_disabled(intent: dict[str, Any]) -> bool:
    return intent.get("enabled") is False or str(intent.get("status") or "").strip().lower() in {"disabled", "skipped"}


def _video_generation_disabled(intent_payload: dict[str, Any], compat_payload: dict[str, Any]) -> bool:
    production = intent_payload.get("production_intents") if isinstance(intent_payload.get("production_intents"), dict) else {}
    if "video" not in production:
        return False
    video_intents = production.get("video")
    video_prompts = compat_payload.get("video_prompts")
    if isinstance(video_intents, list) and not video_intents and isinstance(video_prompts, list) and not video_prompts:
        return True
    if not isinstance(video_intents, list) or not video_intents:
        return False
    return all(_video_intent_disabled(item) for item in video_intents if isinstance(item, dict))


def _video_intent_disabled(intent: dict[str, Any]) -> bool:
    compatibility = intent.get("compatibility") if isinstance(intent.get("compatibility"), dict) else {}
    constraints = intent.get("constraints") if isinstance(intent.get("constraints"), dict) else {}
    return (
        _intent_disabled(intent)
        or str(intent.get("intent") or "") == "no_video_required"
        or compatibility.get("skip_execution") is True
        or constraints.get("skip_execution") is True
        or (_number(intent.get("duration"), intent.get("duration_seconds")) == 0 and "skip" in json.dumps(intent, ensure_ascii=False).lower())
    )


def _requirement_text(requirement_lock: dict[str, Any]) -> str:
    return " ".join(
        str(value or "")
        for value in (
            requirement_lock.get("original_requirement"),
            requirement_lock.get("production_type"),
            requirement_lock.get("quality_mode"),
            " ".join(str(item) for item in requirement_lock.get("explicit_constraints") or []),
        )
    )


def _requirement_disables_voiceover(requirement_lock: dict[str, Any]) -> bool:
    text = _requirement_text(requirement_lock)
    lowered = text.lower()
    return any(token in text for token in ("不需要配音", "无需配音", "不生成配音", "无配音", "不需要旁白", "无需旁白", "无旁白")) or any(
        token in lowered for token in ("no voice", "no voiceover", "no narration", "without voice", "without narration")
    )


def _requirement_disables_subtitles(requirement_lock: dict[str, Any]) -> bool:
    text = _requirement_text(requirement_lock)
    lowered = text.lower()
    return any(
        token in text
        for token in ("不需要字幕", "无需字幕", "不生成字幕", "无字幕", "文字标签后期叠加", "只验证图片素材", "本地图片轮播预览")
    ) or any(token in lowered for token in ("no subtitle", "no subtitles", "without subtitle", "without subtitles", "image-only"))


def _requirement_disables_ai_video(requirement_lock: dict[str, Any]) -> bool:
    text = _requirement_text(requirement_lock)
    lowered = text.lower()
    return any(
        token in text
        for token in ("不生成AI视频", "不生成 AI 视频", "不需要AI视频", "不需要 AI 视频", "无需AI视频", "无AI视频", "只验证图片素材", "本地图片轮播预览")
    ) or any(token in lowered for token in ("asset_only", "only image", "image-only", "no ai video", "without ai video"))


def _upstream_image_ids(previous_outputs: list[dict[str, str]]) -> set[str]:
    values: set[str] = set()
    for output in previous_outputs:
        if not str(output.get("agent") or "").startswith("06_"):
            continue
        payloads = _json_objects(output.get("content") or "")
        payload = _payload_with(payloads, "production_intents", "image")
        for item in _intent_group(payload, "image"):
            for key in ("intent_id", "asset_tag", "job_id"):
                if str(item.get(key) or "").strip():
                    values.add(str(item[key]).strip())
            if item.get("intent") == "generate_three_frame_shot" and str(item.get("intent_id") or "").strip():
                base = str(item["intent_id"]).strip()
                values.update({f"{base}_start_frame", f"{base}_middle_frame", f"{base}_end_frame"})
        compat = _payload_with(payloads, "image_prompts")
        for item in compat.get("image_prompts") if isinstance(compat.get("image_prompts"), list) else []:
            for key in ("intent_id", "asset_tag", "job_id", "id"):
                if str(item.get(key) or "").strip():
                    values.add(str(item[key]).strip())
    return values


def _reference_ids(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in (
        "reference_image",
        "start_frame_image",
        "middle_frame_image",
        "last_frame_image",
        "start_frame_intent_id",
        "middle_frame_intent_id",
        "end_frame_intent_id",
    ):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("from_intent") or value.get("from_job") or value.get("asset_id")
        if str(value or "").strip():
            values.append(str(value).strip())
    bindings = item.get("input_bindings") if isinstance(item.get("input_bindings"), dict) else {}
    for key in ("input_base_image", "input_middle_frame", "input_last_frame"):
        value = bindings.get(key)
        if isinstance(value, dict):
            value = value.get("from_intent") or value.get("from_job") or value.get("asset_id")
        if str(value or "").strip():
            values.append(str(value).strip())
    return list(dict.fromkeys(values))


def _resolve_video_prompt_refs(prompt_item: dict[str, Any], prompt_index: int, intents: list[dict[str, Any]]) -> list[str]:
    candidates = _matching_video_intents(prompt_item, prompt_index, intents)
    for candidate in candidates:
        refs = _reference_ids(candidate)
        if len(set(refs)) >= 3:
            return refs
    return []


def _resolve_video_prompt_source_ids(prompt_item: dict[str, Any], prompt_index: int, intents: list[dict[str, Any]]) -> list[str]:
    candidates = _matching_video_intents(prompt_item, prompt_index, intents)
    for candidate in candidates:
        source_ids = _string_list(candidate.get("source_intent_ids"))
        if source_ids:
            return source_ids
    return []


def _matching_video_intents(prompt_item: dict[str, Any], prompt_index: int, intents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prompt_keys = {
        str(prompt_item.get("job_id") or "").strip(),
        str(prompt_item.get("id") or "").strip(),
        str(prompt_item.get("asset_tag") or "").strip(),
    }
    prompt_keys = {value for value in prompt_keys if value}
    candidates: list[dict[str, Any]] = []
    for intent in intents:
        intent_keys = {
            str(intent.get("intent_id") or "").strip(),
            str(intent.get("job_id") or "").strip(),
            str(intent.get("id") or "").strip(),
            str(intent.get("asset_tag") or "").strip(),
        }
        intent_keys = {value for value in intent_keys if value}
        if prompt_keys and (
            prompt_keys & intent_keys
            or any(key.startswith(other) or other.startswith(key) for key in prompt_keys for other in intent_keys)
        ):
            candidates.append(intent)
    if 0 <= prompt_index < len(intents):
        indexed_intent = intents[prompt_index]
        if indexed_intent not in candidates:
            candidates.append(indexed_intent)
    return candidates


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _validate_work_resolution(item: dict[str, Any], expected: tuple[int, int], label: str, issues: list[str]) -> None:
    width = int(_number(item.get("width")))
    height = int(_number(item.get("height")))
    if not width or not height:
        issues.append(f"{label} 缺少明确的 width/height 工作尺寸")
    elif (width, height) != expected:
        issues.append(f"{label} 工作尺寸必须为 {expected[0]}x{expected[1]}，当前为 {width}x{height}")


def _expected_resolution(lock: dict[str, Any], delivery: bool) -> tuple[int, int]:
    constraints = " ".join(str(item) for item in lock.get("explicit_constraints") or [])
    original = str(lock.get("original_requirement") or "")
    aspect = str(lock.get("aspect_ratio") or lock.get("target_aspect_ratio") or "").strip()
    platform = str(lock.get("target_platform") or lock.get("platform") or "").strip()
    explicit_text = f"{original} {constraints}"
    explicit_lowered = explicit_text.lower()
    if "绔栧睆" in explicit_text or "9:16" in explicit_lowered or "portrait" in explicit_lowered:
        return (1080, 1920) if delivery else (480, 848)
    if "16:9" in explicit_lowered or "妯睆" in explicit_text or "landscape" in explicit_lowered:
        return (1920, 1080) if delivery else (848, 480)
    text = f"{original} {constraints} {aspect} {platform}"
    lowered = text.lower()
    if "16:9" in lowered or "横屏" in text or "landscape" in lowered:
        return (1920, 1080) if delivery else (848, 480)
    if "竖屏" in text or "9:16" in lowered or "portrait" in lowered:
        return (1080, 1920) if delivery else (480, 848)
    if any(token in text for token in ("短视频", "抖音", "快手", "小红书")):
        return (1080, 1920) if delivery else (480, 848)
    if "1:1" in lowered or "方形" in text or "square" in lowered:
        return (1080, 1080) if delivery else (480, 480)
    return (1920, 1080) if delivery else (848, 480)


def _resolution(value: Any) -> tuple[int, int]:
    match = re.search(r"(\d+)\s*[x×*]\s*(\d+)", str(value or ""), flags=re.IGNORECASE)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _number(*values: Any) -> float:
    for value in values:
        try:
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _time_seconds(value: str) -> float | None:
    text = str(value or "").strip().replace(",", ".")
    parts = text.split(":")
    if len(parts) not in {2, 3}:
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if any(number < 0 for number in numbers):
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _segment_time_seconds(segment: dict[str, Any], aliases: tuple[str, ...]) -> tuple[str, float | None]:
    for alias in aliases:
        if alias not in segment:
            continue
        value = segment.get(alias)
        if isinstance(value, (int, float)):
            parsed = float(value)
            return str(value), parsed if parsed >= 0 else None
        raw = str(value or "").strip()
        if not raw:
            return "", None
        parsed = _time_seconds(raw)
        if parsed is not None:
            return raw, parsed
        try:
            numeric = float(raw)
        except ValueError:
            return raw, None
        return raw, numeric if numeric >= 0 else None
    return "", None


def _srt_dialogue_text(srt: str) -> str:
    lines: list[str] = []
    for raw in str(srt or "").replace("\\n", "\n").replace("\\r", "\r").splitlines():
        line = raw.strip()
        if not line or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return "\n".join(lines)


def _section_after(content: str, heading: str) -> str:
    match = re.search(rf"^#+\s*.*{re.escape(heading)}.*$", content, flags=re.MULTILINE)
    if not match:
        return ""
    following = content[match.end() :]
    next_heading = re.search(r"^#+\s+", following, flags=re.MULTILINE)
    return following[: next_heading.start()] if next_heading else following


def _section_after_heading_regex(content: str, heading_pattern: str) -> str:
    match = re.search(rf"^#+\s*.*(?:{heading_pattern}).*$", content, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    following = content[match.end() :]
    next_heading = re.search(r"^#+\s+", following, flags=re.MULTILINE)
    return following[: next_heading.start()] if next_heading else following


def _strip_markdown_noise(text: str) -> str:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(("**字数统计", "**字數統計", "- ", "* ")):
            continue
        lines.append(re.sub(r"^[>#*\-\s]+", "", line).strip())
    return "\n".join(line for line in lines if line)
