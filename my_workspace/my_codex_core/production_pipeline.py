from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .cloud_comfyui_adapter import CloudComfyUIAdapter
from .cloud_image_adapter import CloudImageAdapter
from .cloud_video_adapter import CloudVideoAdapter
from .local_ffmpeg_adapter import LocalFFmpegAdapter
from .local_tts_adapter import LocalTTSAdapter


def run_auto_production(
    task_dir: Path,
    step_outputs: list[dict[str, str]],
    production_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    config = production_config or {}
    mode = str(config.get("mode") or "off").strip()
    if mode == "off":
        return None

    image_config = config.get("image_config") or {}
    video_config = config.get("video_config") or {}
    compose_config = config.get("compose_config") or {}
    voice_config = config.get("voice_config") or {}

    paths = _create_output_dirs(task_dir)
    image_step = _find_step(step_outputs, "06_")
    video_step = _find_step(step_outputs, "07_")
    audio_step = _find_step(step_outputs, "20_")
    compose_step = _find_step(step_outputs, "21_")
    edit_step = _find_step(step_outputs, "22_")
    image_content = image_step.get("content", "") if image_step else ""
    video_content = video_step.get("content", "") if video_step else ""
    audio_content = audio_step.get("content", "") if audio_step else ""
    compose_content = compose_step.get("content", "") if compose_step else ""
    edit_content = edit_step.get("content", "") if edit_step else ""

    image_prompt_path = paths["image_prompts"] / "storyboard_image_prompts.md"
    video_prompt_path = paths["video_prompts"] / "video_generation_prompts.md"
    audio_package_path = paths["audio"] / "audio_subtitle_package.md"
    voiceover_path = paths["audio"] / "voiceover.txt"
    subtitles_path = task_dir / "subtitles.srt"
    comfyui_plan_path = paths["comfyui"] / "comfyui_plan.md"
    comfyui_payload_path = paths["comfyui"] / "comfyui_payload.json"
    edit_plan_path = task_dir / "final_edit_plan.md"
    checklist_path = task_dir / "edit_checklist.md"
    production_note_path = task_dir / "auto_production.md"
    final_video_name = _safe_file_name(str(compose_config.get("final_video_name") or "final_video.mp4"))

    _write_text(image_prompt_path, image_content or "# 分镜生图提示词\n\n未找到 06_分镜生图设计师输出。\n")
    _write_text(video_prompt_path, video_content or "# 视频生成提示词\n\n未找到 07_视频生成执行员输出。\n")
    _write_text(audio_package_path, audio_content or "# 语音字幕制作包\n\n未找到 20_语音字幕包装师输出。\n")
    voice_text = _extract_section(audio_content, "TTS 配音稿") or "待从 20_语音字幕包装师输出中整理配音稿。\n"
    _write_text(voiceover_path, voice_text)
    _write_text(subtitles_path, _extract_srt(audio_content) or _default_srt())
    _write_text(comfyui_plan_path, compose_content or "# ComfyUI 素材生成编排方案\n\n未找到 21_ComfyUI素材编排师输出。\n")
    _write_text(edit_plan_path, edit_content or "# 剪辑成片执行方案\n\n未找到 22_剪辑成片执行师输出。\n")
    _write_text(comfyui_payload_path, _extract_json_block(compose_content) or _default_comfyui_payload(mode, final_video_name, video_config, voice_text))
    _write_text(checklist_path, _build_edit_checklist(image_step, video_step, audio_step, compose_step, edit_step, image_config, video_config, compose_config))

    if mode == "api_ready":
        initial_status = "api_adapter_pending"
    elif mode == "comfy_full":
        initial_status = "comfyui_package_ready"
    else:
        initial_status = "package_ready"

    manifest = {
        "schema_version": 1,
        "mode": mode,
        "status": initial_status,
        "task_dir": str(task_dir),
        "image_generation": {
            "tool": image_config.get("tool") or "",
            "positive_prompt": image_config.get("positive_prompt") or "",
            "model": image_config.get("model") or "",
            "size": image_config.get("size") or "",
            "count_per_shot": image_config.get("count_per_shot") or "",
            "seed": image_config.get("seed") or "",
            "guidance_scale": image_config.get("guidance_scale") or "",
            "steps": image_config.get("steps") or "",
            "denoise_strength": image_config.get("denoise_strength") or "",
            "sampler": image_config.get("sampler") or "",
            "control": image_config.get("control") or "",
            "api_key_provided": bool(image_config.get("api_key_provided")),
            "base_url_provided": bool(image_config.get("base_url_provided")),
            "prompt_file": str(image_prompt_path),
            "output_dir": str(paths["generated_images"]),
            "adapter_status": "pending" if mode == "api_ready" else "not_configured",
        },
        "video_generation": {
            "tool": video_config.get("tool") or "",
            "positive_prompt": video_config.get("positive_prompt") or "",
            "model": video_config.get("model") or "",
            "aspect_ratio": video_config.get("aspect_ratio") or "",
            "duration": video_config.get("duration") or "",
            "prompt_notes": video_config.get("prompt_notes") or "",
            "negative_prompt": video_config.get("negative_prompt") or "",
            "seed": video_config.get("seed") or "",
            "fps": video_config.get("fps") or "",
            "motion_strength": video_config.get("motion_strength") or "",
            "camera_motion": video_config.get("camera_motion") or "",
            "resolution": video_config.get("resolution") or "",
            "guidance_scale": video_config.get("guidance_scale") or "",
            "frames": video_config.get("frames") or "",
            "image_strength": video_config.get("image_strength") or "",
            "camera_path": video_config.get("camera_path") or "",
            "audio_notes": video_config.get("audio_notes") or "",
            "advanced_params": video_config.get("advanced_params") or "",
            "api_key_provided": bool(video_config.get("api_key_provided")),
            "base_url_provided": bool(video_config.get("base_url_provided")),
            "prompt_file": str(video_prompt_path),
            "output_dir": str(paths["video_clips"]),
            "adapter_status": "pending" if mode == "api_ready" else "not_configured",
        },
        "composition": {
            "tool": compose_config.get("tool") or "ffmpeg",
            "execution_mode": compose_config.get("execution_mode") or mode,
            "target_file": str(task_dir / final_video_name),
            "subtitles_file": str(subtitles_path),
            "voiceover_file": str(voiceover_path),
            "audio_package_file": str(audio_package_path),
            "comfyui_plan_file": str(comfyui_plan_path),
            "comfyui_payload_file": str(comfyui_payload_path),
            "final_edit_plan_file": str(edit_plan_path),
            "local_ffmpeg_manifest": "",
            "local_ffmpeg_command": "",
            "api_key_provided": bool(compose_config.get("api_key_provided")),
            "base_url_provided": bool(compose_config.get("base_url_provided")),
            "workflow_endpoint_provided": bool(str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip()),
            "node_mapping_provided": bool(str(compose_config.get("node_info_list_json") or "").strip() not in {"", "[]"}),
            "workflow_preset_id": str(compose_config.get("workflow_preset_id") or ""),
            "workflow_preset_name": str(compose_config.get("workflow_preset_name") or ""),
            "workflow_preset_purpose": str(compose_config.get("workflow_preset_purpose") or ""),
            "workflow_library": compose_config.get("workflow_library") if isinstance(compose_config.get("workflow_library"), list) else [],
            "adapter_status": "pending" if mode in {"api_ready", "comfy_full"} or str(compose_config.get("tool") or "").strip().lower() == "ffmpeg" else "not_configured",
        },
        "audio": {
            "provider": voice_config.get("provider") or "",
            "mode": voice_config.get("mode") or "off",
            "reference_audio_provided": bool(str(voice_config.get("reference_audio") or "").strip()),
            "reference_text_provided": bool(str(voice_config.get("reference_text") or "").strip()),
            "adapter_status": "pending" if str(voice_config.get("mode") or "").strip().lower() not in {"", "off"} else "not_configured",
            "voiceover_audio_file": "",
        },
        "files": {
            "image_prompts": str(image_prompt_path),
            "video_prompts": str(video_prompt_path),
            "audio_package": str(audio_package_path),
            "voiceover": str(voiceover_path),
            "subtitles": str(subtitles_path),
            "comfyui_plan": str(comfyui_plan_path),
            "comfyui_payload": str(comfyui_payload_path),
            "final_edit_plan": str(edit_plan_path),
            "edit_checklist": str(checklist_path),
        },
    }

    if mode == "api_ready":
        image_adapter_result = _run_image_adapter(image_content, image_config, paths["generated_images"])
        if image_adapter_result:
            manifest["image_generation"]["adapter_status"] = image_adapter_result["status"]
            manifest["image_generation"]["adapter_manifest"] = image_adapter_result.get("manifest_file", "")
            manifest["image_generation"]["downloaded_files"] = image_adapter_result.get("downloaded_files", [])
            if image_adapter_result["status"] == "success":
                manifest["status"] = "image_generated"
            elif image_adapter_result["status"] == "skipped":
                manifest["status"] = "api_adapter_skipped"
            else:
                manifest["status"] = "image_adapter_failed"
        video_adapter_result = _run_video_adapter(video_content, video_config, paths["video_clips"])
        if video_adapter_result:
            manifest["video_generation"]["adapter_status"] = video_adapter_result["status"]
            manifest["video_generation"]["adapter_manifest"] = video_adapter_result.get("manifest_file", "")
            manifest["video_generation"]["downloaded_files"] = video_adapter_result.get("downloaded_files", [])
            if video_adapter_result["status"] == "success":
                manifest["status"] = "video_generated"
            elif manifest["status"] == "api_adapter_pending" and video_adapter_result["status"] == "skipped":
                manifest["status"] = "api_adapter_skipped"
            elif video_adapter_result["status"] not in {"skipped", "success"}:
                manifest["status"] = "video_adapter_failed"
    if mode == "comfy_full":
        comfyui_adapter_result = _run_comfyui_adapter(comfyui_payload_path, compose_config, paths["comfyui"])
        if comfyui_adapter_result:
            manifest["composition"]["adapter_status"] = comfyui_adapter_result["status"]
            manifest["composition"]["adapter_manifest"] = comfyui_adapter_result.get("manifest_file", "")
            manifest["composition"]["downloaded_files"] = comfyui_adapter_result.get("downloaded_files", [])
            if comfyui_adapter_result["status"] == "success":
                manifest["status"] = "comfyui_generated"
            elif comfyui_adapter_result["status"] == "skipped":
                manifest["status"] = "comfyui_adapter_skipped"
            else:
                manifest["status"] = "comfyui_adapter_failed"

    tts_adapter_result = _run_local_tts_adapter(voice_text, voice_config, paths["audio"])
    if tts_adapter_result:
        manifest["audio"]["adapter_status"] = tts_adapter_result.get("status") or "failed"
        manifest["audio"]["adapter_manifest"] = str(paths["audio"] / "local_tts_manifest.json")
        files = tts_adapter_result.get("downloaded_files") or []
        if files:
            manifest["audio"]["voiceover_audio_file"] = str(files[0])
        if tts_adapter_result.get("status") == "success" and manifest["status"] in {"package_ready", "comfyui_package_ready"}:
            manifest["status"] = "audio_generated"
        elif tts_adapter_result.get("status") not in {"success", "skipped"}:
            manifest["status"] = "local_tts_failed"

    ffmpeg_adapter_result = _run_local_ffmpeg_adapter(task_dir, paths, compose_config, manifest)
    if ffmpeg_adapter_result:
        manifest["composition"]["adapter_status"] = ffmpeg_adapter_result.get("status") or "failed"
        manifest["composition"]["local_ffmpeg_manifest"] = str(task_dir / "local_ffmpeg_manifest.json")
        manifest["composition"]["local_ffmpeg_command"] = str(task_dir / "local_ffmpeg_command.txt")
        files = ffmpeg_adapter_result.get("downloaded_files") or []
        if files:
            manifest["composition"]["final_video_file"] = str(files[0])
            manifest["files"]["final_video"] = str(files[0])
        if ffmpeg_adapter_result.get("status") == "success":
            manifest["status"] = "final_video_generated"
        elif ffmpeg_adapter_result.get("status") == "skipped" and manifest["status"] in {"package_ready", "audio_generated", "comfyui_package_ready"}:
            manifest["status"] = "local_ffmpeg_skipped"
        elif ffmpeg_adapter_result.get("status") not in {"success", "skipped"}:
            manifest["status"] = "local_ffmpeg_failed"

    manifest_path = task_dir / "production_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text(production_note_path, _build_production_note(manifest))
    manifest["files"]["manifest"] = str(manifest_path)
    manifest["files"]["note"] = str(production_note_path)
    return manifest


def _run_local_tts_adapter(
    voice_text: str,
    voice_config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    mode = str(voice_config.get("mode") or "off").strip().lower()
    if mode in {"", "off"}:
        return {"status": "skipped", "reason": "local TTS is disabled"}
    try:
        workspace_root = Path(__file__).resolve().parents[1]
        return LocalTTSAdapter(workspace_root=workspace_root).run(
            voice_text=voice_text,
            voice_config=voice_config,
            output_dir=output_dir,
        )
    except Exception as exc:
        error_path = output_dir / "local_tts_error.json"
        error_path.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {"status": "failed", "error": str(exc), "manifest_file": str(error_path)}


def _run_local_ffmpeg_adapter(
    task_dir: Path,
    paths: dict[str, Path],
    compose_config: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    tool = str(compose_config.get("tool") or "").strip().lower()
    if tool not in {"", "ffmpeg"}:
        return None
    try:
        workspace_root = Path(__file__).resolve().parents[1]
        return LocalFFmpegAdapter(workspace_root=workspace_root).run(
            task_dir=task_dir,
            paths=paths,
            compose_config=compose_config,
            manifest=manifest,
        )
    except Exception as exc:
        error_path = task_dir / "local_ffmpeg_error.json"
        error_path.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"status": "failed", "error": str(exc), "manifest_file": str(error_path)}


def _run_comfyui_adapter(
    comfyui_payload_path: Path,
    compose_config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    tool = str(compose_config.get("tool") or "").strip().lower()
    if tool in {"", "manual", "ffmpeg", "jianying"}:
        return {"status": "skipped", "reason": "compose tool is not a cloud ComfyUI provider"}
    api_key = str(compose_config.get("api_key") or "").strip()
    base_url = str(compose_config.get("base_url") or "").strip()
    endpoint = str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip()
    if not api_key or not base_url or not endpoint:
        return {"status": "skipped", "reason": "ComfyUI API key, base URL, or workflow endpoint is missing"}

    try:
        comfyui_payload = json.loads(comfyui_payload_path.read_text(encoding="utf-8"))
        if not isinstance(comfyui_payload, dict):
            raise ValueError("comfyui_payload.json must contain a JSON object")
        manifest = CloudComfyUIAdapter(base_url=base_url, api_key=api_key, endpoint=endpoint).run(
            comfyui_payload=comfyui_payload,
            compose_config=compose_config,
            output_dir=output_dir,
        )
    except Exception as exc:
        error_path = output_dir / "cloud_comfyui_error.json"
        error_path.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"status": "failed", "error": str(exc), "manifest_file": str(error_path)}

    return {
        "status": manifest.get("status") or "success",
        "manifest_file": str(output_dir / "cloud_comfyui_manifest.json"),
        "downloaded_files": manifest.get("downloaded_files", []),
    }


def _run_video_adapter(
    video_content: str,
    video_config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    tool = str(video_config.get("tool") or "").strip().lower()
    if tool in {"", "prompt_only"}:
        return {"status": "skipped", "reason": "video tool is prompt_only"}
    api_key = str(video_config.get("api_key") or "").strip()
    base_url = str(video_config.get("base_url") or "").strip()
    endpoint = str(video_config.get("workflow_endpoint") or video_config.get("endpoint") or "").strip()
    if not api_key or not base_url or not endpoint:
        return {"status": "skipped", "reason": "video API key, base URL, or workflow endpoint is missing"}

    try:
        manifest = CloudVideoAdapter(base_url=base_url, api_key=api_key, endpoint=endpoint).run(
            prompt_text=video_content,
            video_config=video_config,
            output_dir=output_dir,
        )
    except Exception as exc:
        error_path = output_dir / "cloud_video_error.json"
        error_path.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"status": "failed", "error": str(exc), "manifest_file": str(error_path)}

    return {
        "status": manifest.get("status") or "success",
        "manifest_file": str(output_dir / "cloud_video_manifest.json"),
        "downloaded_files": manifest.get("downloaded_files", []),
    }


def _run_image_adapter(
    image_content: str,
    image_config: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    tool = str(image_config.get("tool") or "").strip().lower()
    if tool in {"", "prompt_only"}:
        return {"status": "skipped", "reason": "image tool is prompt_only"}
    api_key = str(image_config.get("api_key") or "").strip()
    base_url = str(image_config.get("base_url") or "").strip()
    endpoint = str(image_config.get("workflow_endpoint") or image_config.get("endpoint") or "").strip()
    if not api_key or not base_url or not endpoint:
        return {"status": "skipped", "reason": "image API key, base URL, or workflow endpoint is missing"}

    try:
        manifest = CloudImageAdapter(base_url=base_url, api_key=api_key, endpoint=endpoint).run(
            prompt_text=image_content,
            image_config=image_config,
            output_dir=output_dir,
        )
    except Exception as exc:
        error_path = output_dir / "cloud_image_error.json"
        error_path.write_text(
            json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"status": "failed", "error": str(exc), "manifest_file": str(error_path)}

    return {
        "status": manifest.get("status") or "success",
        "manifest_file": str(output_dir / "cloud_image_manifest.json"),
        "downloaded_files": manifest.get("downloaded_files", []),
    }


def _create_output_dirs(task_dir: Path) -> dict[str, Path]:
    paths = {
        "image_prompts": task_dir / "image_prompts",
        "generated_images": task_dir / "generated_images",
        "video_prompts": task_dir / "video_prompts",
        "video_clips": task_dir / "video_clips",
        "audio": task_dir / "audio",
        "comfyui": task_dir / "comfyui",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def _find_step(step_outputs: list[dict[str, str]], prefix: str) -> dict[str, str] | None:
    for item in step_outputs:
        if str(item.get("agent", "")).startswith(prefix):
            return item
    return None


def _extract_srt(content: str) -> str:
    match = re.search(r"```srt\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() + "\n" if match else ""


def _extract_section(content: str, heading: str) -> str:
    pattern = rf"#+\s*{re.escape(heading)}\s*(.*?)(?:\n#+\s|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"```(?:text)?\s*|\s*```", "", match.group(1)).strip()
    return text + "\n" if text else ""


def _extract_json_block(content: str) -> str:
    match = re.search(r"```json\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if not match:
        return ""
    raw = match.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw + "\n"
    return json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"


def _default_srt() -> str:
    return "1\n00:00:00,000 --> 00:00:03,000\n待从 20_语音字幕包装师输出中整理字幕。\n"


def _default_comfyui_payload(mode: str, final_video_name: str, video_config: dict[str, Any], voice_text: str = "") -> str:
    payload = {
        "execution_mode": mode,
        "image_prompts": [],
        "image_prompt": "",
        "video_prompts": [],
        "video_prompt": video_config.get("positive_prompt") or "",
        "reference_images": [],
        "reference_image": "",
        "negative_prompt": video_config.get("negative_prompt") or "",
        "voice_text": voice_text,
        "subtitle_srt": "",
        "subtitle_style": "",
        "bgm_style": "",
        "seed": video_config.get("seed") or "",
        "width": "",
        "height": "",
        "output": {
            "aspect_ratio": video_config.get("aspect_ratio") or "9:16",
            "duration": video_config.get("duration") or "30s",
            "file_name": final_video_name,
        },
        "nodeInfoList": [],
        "notes": "待根据 21_ComfyUI成片编排师输出和实际 ComfyUI 节点补全。",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _build_edit_checklist(
    image_step: dict[str, str] | None,
    video_step: dict[str, str] | None,
    audio_step: dict[str, str] | None,
    compose_step: dict[str, str] | None,
    edit_step: dict[str, str] | None,
    image_config: dict[str, Any],
    video_config: dict[str, Any],
    compose_config: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "# 自动生产执行清单",
            "",
            "## 1. 生图",
            f"- 工具：{image_config.get('tool') or '未指定'}",
            f"- 模型：{image_config.get('model') or '未指定'}",
            "- 提示词文件：image_prompts/storyboard_image_prompts.md",
            "- 输出目录：generated_images/",
            f"- 06 输出状态：{'已找到' if image_step else '未找到'}",
            "",
            "## 2. 生视频",
            f"- 工具：{video_config.get('tool') or '未指定'}",
            f"- 模型：{video_config.get('model') or '未指定'}",
            "- 提示词文件：video_prompts/video_generation_prompts.md",
            "- 输出目录：video_clips/",
            f"- 07 输出状态：{'已找到' if video_step else '未找到'}",
            "",
            "## 3. 语音字幕",
            "- 语音字幕包：audio/audio_subtitle_package.md",
            "- 配音：audio/voiceover.txt",
            "- 本地配音音频：audio/voiceover.wav（启用 VoxCPM2 后生成）",
            "- 字幕：subtitles.srt",
            f"- 20 输出状态：{'已找到' if audio_step else '未找到'}",
            "",
            "## 4. ComfyUI 素材编排",
            "- 素材编排方案：comfyui/comfyui_plan.md",
            "- 参数包：comfyui/comfyui_payload.json",
            f"- 21 输出状态：{'已找到' if compose_step else '未找到'}",
            "",
            "## 5. 剪辑成片",
            "- 剪辑方案：final_edit_plan.md",
            f"- 22 输出状态：{'已找到' if edit_step else '未找到'}",
            "- 原则：AI 图片和视频只作为素材片段，最终由剪辑工具成片。",
            "",
            "## 6. 合成",
            f"- 合成工具：{compose_config.get('tool') or 'ffmpeg'}",
            "- 字幕：subtitles.srt",
            "- 配音：audio/voiceover.txt",
            "- 目标视频：final_video.mp4",
            "",
            "## 7. 当前限制",
            "- 当前版本会生成自动生产资产包、语音字幕包、ComfyUI 素材编排方案、剪辑成片方案和 manifest。",
            "- 当合成工具为 ffmpeg 且本地可找到 ffmpeg.exe，并且已有视频片段、图片或配音音频时，会尝试生成 final_video.mp4。",
            "- 若缺少 FFmpeg 或素材不足，会在 local_ffmpeg_manifest.json 里记录 skipped 原因，不中断工作流。",
            "",
        ]
    )


def _build_production_note(manifest: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# 全自动生产框架输出",
            "",
            f"- 模式：{manifest['mode']}",
            f"- 状态：{manifest['status']}",
            f"- 生图提示词：{manifest['files']['image_prompts']}",
            f"- 视频提示词：{manifest['files']['video_prompts']}",
            f"- 字幕文件：{manifest['files']['subtitles']}",
            f"- 配音文本：{manifest['files']['voiceover']}",
            f"- 语音字幕包：{manifest['files']['audio_package']}",
            f"- ComfyUI 素材编排方案：{manifest['files']['comfyui_plan']}",
            f"- ComfyUI 参数包：{manifest['files']['comfyui_payload']}",
            f"- 剪辑成片方案：{manifest['files'].get('final_edit_plan', '')}",
            f"- 本地 FFmpeg：{manifest.get('composition', {}).get('adapter_status', '')}",
            f"- 目标视频：{manifest.get('files', {}).get('final_video') or manifest.get('composition', {}).get('target_file', '')}",
            "",
            "下一步可把素材放入 generated_images/、video_clips/ 或生成 audio/voiceover.wav 后，由本地 FFmpeg 适配器合成视频；云端平台仍读取 `production_manifest.json` 中的配置、提示词文件和输出目录。",
            "",
        ]
    )


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _safe_file_name(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-", ".") else "_" for ch in name).strip("._")
    if not safe:
        return "final_video.mp4"
    if "." not in safe:
        safe += ".mp4"
    return safe[:120]
