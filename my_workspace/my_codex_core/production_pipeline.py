from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
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
    progress_callback=None,
    stop_after_comfyui: bool = False,
) -> dict[str, Any] | None:
    config = production_config or {}
    mode = str(config.get("mode") or "off").strip()
    if mode == "off":
        return None

    def emit(message: str, stage: str = "production", **extra: Any) -> None:
        if not progress_callback:
            return
        event = {"event": "production_update", "stage": stage, "message": message}
        event.update(extra)
        progress_callback(event)

    image_config = config.get("image_config") or {}
    video_config = config.get("video_config") or {}
    compose_config = config.get("compose_config") or {}
    voice_config = config.get("voice_config") or {}
    quality_config = config.get("quality_config") or {}

    paths = _create_output_dirs(task_dir)
    image_step = _find_step(step_outputs, "06_")
    video_step = _find_step(step_outputs, "07_")
    audio_step = _find_step(step_outputs, "20_")
    edit_step = _find_step(step_outputs, "22_")
    image_content = image_step.get("content", "") if image_step else ""
    video_content = video_step.get("content", "") if video_step else ""
    audio_content = audio_step.get("content", "") if audio_step else ""
    compose_content = _combined_comfyui_plan(image_content, video_content)
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
    emit("正在整理制作包：提示词、配音文案、字幕、ComfyUI 参数和剪辑方案", stage="package")

    _write_text(image_prompt_path, image_content or "# 分镜生图提示词\n\n未找到 06_分镜生图设计师输出。\n")
    _write_text(video_prompt_path, video_content or "# 视频生成提示词\n\n未找到 07_视频生成执行员输出。\n")
    _write_text(audio_package_path, audio_content or "# 语音字幕制作包\n\n未找到 20_语音字幕包装师输出。\n")
    voice_text = _extract_voice_text(audio_content)
    voice_text_quality = _quality_check_voice_text(voice_text)
    if not voice_text_quality["usable"]:
        voice_text = "待从 20_语音字幕包装师输出中整理配音稿。\n"
    subtitle_srt = _extract_srt(audio_content)
    subtitle_srt_quality = _quality_check_srt(subtitle_srt)
    if not subtitle_srt_quality["usable"]:
        subtitle_srt = _srt_from_voice_text(voice_text) if voice_text_quality["usable"] else _default_srt()
        subtitle_srt_quality = _quality_check_srt(subtitle_srt)
    _write_text(voiceover_path, voice_text)
    _write_text(subtitles_path, subtitle_srt)
    _write_text(comfyui_plan_path, compose_content)
    _write_text(edit_plan_path, edit_content or "# 剪辑成片执行方案\n\n未找到 22_剪辑成片执行师输出。\n")
    comfyui_payload_text = _combined_comfyui_payload_text(image_content, video_content, mode, final_video_name, video_config)
    _write_text(
        comfyui_payload_path,
        _ensure_comfyui_payload_defaults(comfyui_payload_text, mode, final_video_name, video_config),
    )
    _write_text(checklist_path, _build_edit_checklist(image_step, video_step, audio_step, None, edit_step, image_config, video_config, compose_config))
    emit("制作包已生成，开始按自动生成配置调用工具", stage="package")

    if mode == "api_ready":
        initial_status = "image_adapter_pending"
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
            "adapter_status": "skipped" if mode == "api_ready" else "not_configured",
            "skip_reason": "api_ready mode is image-only; video generation is disabled." if mode == "api_ready" else "",
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
            "quality_gate_enabled": _as_bool(quality_config.get("enabled"), default=True),
            "quality_min_score": _safe_int(quality_config.get("min_score"), default=70, minimum=0, maximum=100),
            "quality_max_attempts": _safe_int(quality_config.get("max_attempts"), default=2, minimum=1, maximum=6),
            "quality_report": "",
        },
        "audio": {
            "provider": voice_config.get("provider") or "",
            "mode": voice_config.get("mode") or "off",
            "voice_preset": voice_config.get("voice_preset") or "",
            "voice_preset_name": voice_config.get("voice_preset_name") or "",
            "voice_text_status": voice_text_quality["status"],
            "voice_text_reason": voice_text_quality["reason"],
            "voice_text_chars": len(voice_text.strip()),
            "subtitle_status": subtitle_srt_quality["status"],
            "subtitle_reason": subtitle_srt_quality["reason"],
            "subtitle_entries": subtitle_srt_quality["entries"],
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

    def run_material_branch() -> tuple[str, dict[str, Any] | None]:
        if mode == "api_ready":
            emit("开始图片素材生成/匹配", stage="image")
            return "image", _run_image_adapter(image_content, image_config, paths["generated_images"])
        if mode == "comfy_full":
            emit("开始调用 ComfyUI 素材/预览工作流", stage="comfyui")
            return "comfyui", _run_comfyui_adapter_with_quality_gate(
                comfyui_payload_path,
                compose_config,
                quality_config,
                paths["comfyui"],
                progress_callback=progress_callback,
            )
        return "none", None

    def run_tts_branch() -> tuple[str, dict[str, Any] | None]:
        emit("开始本地配音处理", stage="tts")
        return "tts", _run_local_tts_adapter(voice_text, voice_config, paths["audio"])

    def apply_image_result(image_adapter_result: dict[str, Any] | None) -> None:
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
            emit(
                f"图片素材阶段结束：{image_adapter_result.get('status') or 'unknown'}",
                stage="image",
                status=image_adapter_result.get("status") or "",
                downloaded_count=len(image_adapter_result.get("downloaded_files") or []),
            )
        manifest["video_generation"]["adapter_status"] = "skipped"
        manifest["video_generation"]["skip_reason"] = "api_ready mode is image-only; video generation is disabled."

    def apply_comfyui_result(comfyui_adapter_result: dict[str, Any] | None) -> None:
        if comfyui_adapter_result:
            manifest["composition"]["adapter_status"] = comfyui_adapter_result["status"]
            manifest["composition"]["adapter_manifest"] = comfyui_adapter_result.get("manifest_file", "")
            manifest["composition"]["downloaded_files"] = comfyui_adapter_result.get("downloaded_files", [])
            manifest["composition"]["comfyui_adapter_status"] = comfyui_adapter_result["status"]
            manifest["composition"]["comfyui_adapter_manifest"] = comfyui_adapter_result.get("manifest_file", "")
            manifest["composition"]["comfyui_downloaded_files"] = comfyui_adapter_result.get("downloaded_files", [])
            manifest["composition"]["quality_report"] = comfyui_adapter_result.get("quality_report", "")
            manifest["composition"]["quality_score"] = comfyui_adapter_result.get("quality_score", 0)
            manifest["composition"]["quality_attempts"] = comfyui_adapter_result.get("attempts", 1)
            if comfyui_adapter_result["status"] == "success":
                manifest["status"] = "comfyui_generated"
            elif comfyui_adapter_result["status"] == "partial_success":
                manifest["status"] = "comfyui_partial_failed"
            elif comfyui_adapter_result["status"] == "skipped":
                manifest["status"] = "comfyui_adapter_skipped"
            else:
                manifest["status"] = "comfyui_adapter_failed"
            emit(
                f"ComfyUI 阶段结束：{comfyui_adapter_result.get('status') or 'unknown'}，下载 {len(comfyui_adapter_result.get('downloaded_files') or [])} 个素材",
                stage="comfyui",
                status=comfyui_adapter_result.get("status") or "",
                downloaded_count=len(comfyui_adapter_result.get("downloaded_files") or []),
                quality_score=comfyui_adapter_result.get("quality_score", 0),
            )

    def apply_tts_result(tts_adapter_result: dict[str, Any] | None) -> None:
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
            emit(
                f"本地配音阶段结束：{tts_adapter_result.get('status') or 'unknown'}",
                stage="tts",
                status=tts_adapter_result.get("status") or "",
            )

    if stop_after_comfyui:
        material_kind, material_result = run_material_branch()
        if material_kind == "image":
            apply_image_result(material_result)
        elif material_kind == "comfyui":
            apply_comfyui_result(material_result)
        return _finalize_production_manifest(task_dir, manifest, production_note_path, emit, "ComfyUI 素材门禁完成")

    material_enabled = mode in {"api_ready", "comfy_full"}
    tts_enabled = str(voice_config.get("mode") or "").strip().lower() not in {"", "off"}
    if material_enabled and tts_enabled:
        emit("并行启动素材生成/匹配与本地配音", stage="production")
        with ThreadPoolExecutor(max_workers=2) as executor:
            material_future = executor.submit(run_material_branch)
            tts_future = executor.submit(run_tts_branch)
            material_kind, material_result = material_future.result()
            tts_kind, tts_result = tts_future.result()
        if material_kind == "image":
            apply_image_result(material_result)
        elif material_kind == "comfyui":
            apply_comfyui_result(material_result)
        if tts_kind == "tts":
            apply_tts_result(tts_result)
    else:
        if material_enabled:
            material_kind, material_result = run_material_branch()
            if material_kind == "image":
                apply_image_result(material_result)
            elif material_kind == "comfyui":
                apply_comfyui_result(material_result)
        if tts_enabled:
            tts_kind, tts_result = run_tts_branch()
            if tts_kind == "tts":
                apply_tts_result(tts_result)

    emit("开始本地 FFmpeg 剪辑/预览合成", stage="ffmpeg")
    ffmpeg_adapter_result = _run_local_ffmpeg_adapter(task_dir, paths, compose_config, manifest)
    if ffmpeg_adapter_result:
        manifest["composition"]["adapter_status"] = ffmpeg_adapter_result.get("status") or "failed"
        manifest["composition"]["local_ffmpeg_status"] = ffmpeg_adapter_result.get("status") or "failed"
        manifest["composition"]["local_ffmpeg_manifest"] = str(task_dir / "local_ffmpeg_manifest.json")
        manifest["composition"]["local_ffmpeg_command"] = str(task_dir / "local_ffmpeg_command.txt")
        manifest["files"]["ffmpeg_manifest"] = str(task_dir / "local_ffmpeg_manifest.json")
        manifest["files"]["ffmpeg_command"] = str(task_dir / "local_ffmpeg_command.txt")
        if ffmpeg_adapter_result.get("timeline_file"):
            manifest["composition"]["local_ffmpeg_timeline"] = ffmpeg_adapter_result.get("timeline_file", "")
            manifest["files"]["ffmpeg_timeline"] = ffmpeg_adapter_result.get("timeline_file", "")
        if ffmpeg_adapter_result.get("edit_plan_file"):
            manifest["composition"]["local_ffmpeg_edit_plan"] = ffmpeg_adapter_result.get("edit_plan_file", "")
            manifest["files"]["ffmpeg_edit_plan"] = ffmpeg_adapter_result.get("edit_plan_file", "")
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
        emit(
            f"FFmpeg 阶段结束：{ffmpeg_adapter_result.get('status') or 'unknown'}",
            stage="ffmpeg",
            status=ffmpeg_adapter_result.get("status") or "",
            final_video=(files[0] if files else ""),
        )

    return _finalize_production_manifest(task_dir, manifest, production_note_path, emit, "自动生成阶段完成")


def _finalize_production_manifest(task_dir: Path, manifest: dict[str, Any], production_note_path: Path, emit, message: str) -> dict[str, Any]:
    manifest_path = task_dir / "production_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text(production_note_path, _build_production_note(manifest))
    manifest["files"]["manifest"] = str(manifest_path)
    manifest["files"]["note"] = str(production_note_path)
    emit(f"{message}：{manifest['status']}", stage="production", status=manifest["status"])
    return manifest


def retry_production_job(
    task_dir: Path,
    job: str,
    production_config: dict[str, Any] | None = None,
    progress_callback=None,
) -> dict[str, Any]:
    retry_job = str(job or "").strip().lower()
    if retry_job not in {"material", "tts", "ffmpeg"}:
        raise ValueError("job must be one of: material, tts, ffmpeg")

    manifest_path = task_dir / "production_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("production_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("production_manifest.json must contain a JSON object")

    config = production_config if isinstance(production_config, dict) else {}
    mode = _retry_mode(manifest, config)
    image_config = _retry_section_config(manifest, config, "image_config", "image_generation")
    voice_config = _retry_section_config(manifest, config, "voice_config", "audio")
    compose_config = _retry_section_config(manifest, config, "compose_config", "composition")
    quality_config = _retry_quality_config(manifest, config)
    paths = _create_output_dirs(task_dir)
    production_note_path = task_dir / "auto_production.md"

    manifest.setdefault("schema_version", 1)
    manifest["mode"] = mode
    manifest.setdefault("files", {})
    manifest.setdefault("image_generation", {})
    manifest.setdefault("video_generation", {})
    manifest.setdefault("composition", {})
    manifest.setdefault("audio", {})

    def emit(message: str, stage: str = "production", **extra: Any) -> None:
        if not progress_callback:
            return
        event = {"event": "production_update", "stage": stage, "message": message}
        event.update(extra)
        progress_callback(event)

    history_item: dict[str, Any] = {
        "job": retry_job,
        "status": "running",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "ended_at": "",
        "outputs": [],
        "error": "",
    }
    manifest.setdefault("production_job_history", []).append(history_item)

    try:
        if retry_job == "material":
            result = _retry_material_job(task_dir, paths, manifest, mode, image_config, compose_config, quality_config, emit, progress_callback)
        elif retry_job == "tts":
            result = _retry_tts_job(task_dir, paths, manifest, voice_config, emit)
        else:
            result = _retry_ffmpeg_job(task_dir, paths, manifest, compose_config, emit)

        history_item["status"] = str(result.get("status") or "unknown")
        history_item["outputs"] = [str(item) for item in (result.get("downloaded_files") or []) if item]
        history_item["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return _finalize_retry_manifest(task_dir, manifest, production_note_path, emit, f"production retry finished: {retry_job}")
    except Exception as exc:
        history_item["status"] = "failed"
        history_item["error"] = str(exc)
        history_item["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        manifest["status"] = f"{retry_job}_retry_failed"
        _finalize_retry_manifest(task_dir, manifest, production_note_path, emit, f"production retry failed: {retry_job}")
        raise


def _retry_material_job(
    task_dir: Path,
    paths: dict[str, Path],
    manifest: dict[str, Any],
    mode: str,
    image_config: dict[str, Any],
    compose_config: dict[str, Any],
    quality_config: dict[str, Any],
    emit,
    progress_callback=None,
) -> dict[str, Any]:
    if mode == "api_ready":
        prompt_path = paths["image_prompts"] / "storyboard_image_prompts.md"
        image_content = prompt_path.read_text(encoding="utf-8", errors="replace") if prompt_path.is_file() else ""
        emit("retrying image material generation", stage="image")
        result = _run_image_adapter(image_content, image_config, paths["generated_images"]) or {"status": "skipped"}
        image_generation = manifest.setdefault("image_generation", {})
        image_generation["adapter_status"] = result.get("status") or "failed"
        image_generation["adapter_manifest"] = result.get("manifest_file", "")
        image_generation["downloaded_files"] = result.get("downloaded_files", [])
        video_generation = manifest.setdefault("video_generation", {})
        video_generation["adapter_status"] = "skipped"
        video_generation["skip_reason"] = "api_ready mode is image-only; video generation is disabled."
        if result.get("status") == "success":
            manifest["status"] = "image_generated"
        elif result.get("status") == "skipped":
            manifest["status"] = "api_adapter_skipped"
        else:
            manifest["status"] = "image_adapter_failed"
        emit("image material retry finished", stage="image", status=result.get("status") or "")
        return result

    if mode == "comfy_full":
        comfyui_payload_path = paths["comfyui"] / "comfyui_payload.json"
        if not comfyui_payload_path.is_file():
            raise FileNotFoundError("comfyui/comfyui_payload.json")
        emit("retrying ComfyUI material generation", stage="comfyui")
        result = _run_comfyui_adapter_with_quality_gate(
            comfyui_payload_path,
            compose_config,
            quality_config,
            paths["comfyui"],
            progress_callback=progress_callback,
        ) or {"status": "skipped"}
        composition = manifest.setdefault("composition", {})
        composition["adapter_status"] = result.get("status") or "failed"
        composition["adapter_manifest"] = result.get("manifest_file", "")
        composition["downloaded_files"] = result.get("downloaded_files", [])
        composition["comfyui_adapter_status"] = result.get("status") or "failed"
        composition["comfyui_adapter_manifest"] = result.get("manifest_file", "")
        composition["comfyui_downloaded_files"] = result.get("downloaded_files", [])
        composition["quality_report"] = result.get("quality_report", "")
        composition["quality_score"] = result.get("quality_score", 0)
        composition["quality_attempts"] = result.get("attempts", 1)
        if result.get("status") == "success":
            manifest["status"] = "comfyui_generated"
        elif result.get("status") == "partial_success":
            manifest["status"] = "comfyui_partial_failed"
        elif result.get("status") == "skipped":
            manifest["status"] = "comfyui_adapter_skipped"
        else:
            manifest["status"] = "comfyui_adapter_failed"
        emit("ComfyUI material retry finished", stage="comfyui", status=result.get("status") or "")
        return result

    raise ValueError(f"Cannot retry material job when production mode is {mode!r}")


def _retry_tts_job(
    task_dir: Path,
    paths: dict[str, Path],
    manifest: dict[str, Any],
    voice_config: dict[str, Any],
    emit,
) -> dict[str, Any]:
    voiceover_path = paths["audio"] / "voiceover.txt"
    audio_package_path = paths["audio"] / "audio_subtitle_package.md"
    if voiceover_path.is_file():
        voice_text = voiceover_path.read_text(encoding="utf-8", errors="replace")
    elif audio_package_path.is_file():
        voice_text = _extract_voice_text(audio_package_path.read_text(encoding="utf-8", errors="replace"))
    else:
        raise FileNotFoundError("audio/voiceover.txt")
    if not voice_text.strip():
        raise ValueError("voiceover text is empty")

    emit("retrying local TTS", stage="tts")
    result = _run_local_tts_adapter(voice_text, voice_config, paths["audio"]) or {"status": "skipped"}
    audio = manifest.setdefault("audio", {})
    audio["adapter_status"] = result.get("status") or "failed"
    audio["adapter_manifest"] = str(paths["audio"] / "local_tts_manifest.json")
    files = result.get("downloaded_files") or []
    if files:
        audio["voiceover_audio_file"] = str(files[0])
    if result.get("status") == "success":
        manifest["status"] = "audio_generated"
    elif result.get("status") == "skipped":
        manifest["status"] = "local_tts_skipped"
    else:
        manifest["status"] = "local_tts_failed"
    emit("local TTS retry finished", stage="tts", status=result.get("status") or "")
    return result


def _retry_ffmpeg_job(
    task_dir: Path,
    paths: dict[str, Path],
    manifest: dict[str, Any],
    compose_config: dict[str, Any],
    emit,
) -> dict[str, Any]:
    emit("retrying local FFmpeg composition", stage="ffmpeg")
    result = _run_local_ffmpeg_adapter(task_dir, paths, compose_config, manifest) or {"status": "skipped"}
    composition = manifest.setdefault("composition", {})
    files_section = manifest.setdefault("files", {})
    composition["adapter_status"] = result.get("status") or "failed"
    composition["local_ffmpeg_status"] = result.get("status") or "failed"
    composition["local_ffmpeg_manifest"] = str(task_dir / "local_ffmpeg_manifest.json")
    composition["local_ffmpeg_command"] = str(task_dir / "local_ffmpeg_command.txt")
    files_section["ffmpeg_manifest"] = str(task_dir / "local_ffmpeg_manifest.json")
    files_section["ffmpeg_command"] = str(task_dir / "local_ffmpeg_command.txt")
    if result.get("timeline_file"):
        composition["local_ffmpeg_timeline"] = result.get("timeline_file", "")
        files_section["ffmpeg_timeline"] = result.get("timeline_file", "")
    if result.get("edit_plan_file"):
        composition["local_ffmpeg_edit_plan"] = result.get("edit_plan_file", "")
        files_section["ffmpeg_edit_plan"] = result.get("edit_plan_file", "")
    outputs = result.get("downloaded_files") or []
    if outputs:
        composition["final_video_file"] = str(outputs[0])
        files_section["final_video"] = str(outputs[0])
    if result.get("status") == "success":
        manifest["status"] = "final_video_generated"
    elif result.get("status") == "skipped":
        manifest["status"] = "local_ffmpeg_skipped"
    else:
        manifest["status"] = "local_ffmpeg_failed"
    emit("FFmpeg composition retry finished", stage="ffmpeg", status=result.get("status") or "")
    return result


def _finalize_retry_manifest(task_dir: Path, manifest: dict[str, Any], production_note_path: Path, emit, message: str) -> dict[str, Any]:
    manifest_path = task_dir / "production_manifest.json"
    files_section = manifest.setdefault("files", {})
    files_section["manifest"] = str(manifest_path)
    files_section["note"] = str(production_note_path)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text(production_note_path, _build_production_note(manifest))
    _update_run_summary_production_status(task_dir, manifest)
    emit(message, stage="production", status=manifest.get("status", ""))
    return manifest


def _update_run_summary_production_status(task_dir: Path, manifest: dict[str, Any]) -> None:
    summary_path = task_dir / "run_summary.json"
    if not summary_path.is_file():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return
    if not isinstance(summary, dict):
        return
    summary["production_status"] = manifest.get("status", "")
    summary["production_manifest"] = str(task_dir / "production_manifest.json")
    summary["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    if str(manifest.get("status") or "") == "final_video_generated":
        final_video = manifest.get("files", {}).get("final_video") if isinstance(manifest.get("files"), dict) else ""
        if final_video:
            summary["final_video"] = final_video
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _retry_mode(manifest: dict[str, Any], config: dict[str, Any]) -> str:
    configured = str(config.get("mode") or "").strip()
    if configured and configured != "off":
        return configured
    return str(manifest.get("mode") or configured or "off").strip()


def _retry_section_config(manifest: dict[str, Any], config: dict[str, Any], config_key: str, manifest_key: str) -> dict[str, Any]:
    base = manifest.get(manifest_key) if isinstance(manifest.get(manifest_key), dict) else {}
    override = config.get(config_key) if isinstance(config.get(config_key), dict) else {}
    merged = dict(base)
    merged.update({key: value for key, value in override.items() if value not in (None, "")})
    return merged


def _retry_quality_config(manifest: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    override = config.get("quality_config") if isinstance(config.get("quality_config"), dict) else {}
    composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
    base = {
        "enabled": composition.get("quality_gate_enabled", True),
        "min_score": composition.get("quality_min_score", 70),
        "max_attempts": composition.get("quality_max_attempts", 2),
    }
    base.update({key: value for key, value in override.items() if value not in (None, "")})
    return base


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
    progress_callback=None,
) -> dict[str, Any] | None:
    tool = str(compose_config.get("tool") or "").strip().lower()
    if tool in {"", "manual", "jianying"}:
        return {"status": "skipped", "reason": "compose tool is not a cloud ComfyUI provider"}
    api_key = str(compose_config.get("api_key") or "").strip()
    base_url = str(compose_config.get("base_url") or "").strip()
    endpoint = str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip()
    if not api_key or not base_url or not endpoint:
        return {"status": "skipped", "reason": "ComfyUI API key, base URL, or workflow endpoint is missing"}

    try:
        comfyui_payload = _load_comfyui_payload_with_fallback(comfyui_payload_path)
        if not isinstance(comfyui_payload, dict) or not comfyui_payload:
            raise ValueError("comfyui_payload.json must contain a JSON object with image_prompts or video_prompts")
        manifest = CloudComfyUIAdapter(base_url=base_url, api_key=api_key, endpoint=endpoint, progress_callback=progress_callback).run(
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


def _run_comfyui_adapter_with_quality_gate(
    comfyui_payload_path: Path,
    compose_config: dict[str, Any],
    quality_config: dict[str, Any],
    output_dir: Path,
    progress_callback=None,
) -> dict[str, Any] | None:
    enabled = _as_bool(quality_config.get("enabled"), default=True)
    max_attempts = _safe_int(quality_config.get("max_attempts"), default=2, minimum=1, maximum=6)
    min_score = _safe_int(quality_config.get("min_score"), default=70, minimum=0, maximum=100)
    min_file_size_kb = _safe_int(quality_config.get("min_file_size_kb"), default=64, minimum=1, maximum=200000)
    if not enabled:
        result = _run_comfyui_adapter(comfyui_payload_path, compose_config, output_dir, progress_callback=progress_callback)
        if result:
            score = _score_material_result(result, min_file_size_kb)
            result["quality_score"] = score["score"]
            result["quality_report"] = str(_write_quality_report(output_dir, [score], score, min_score, enabled=False))
            result["attempts"] = 1
        return result

    base_payload = _load_comfyui_payload_with_fallback(comfyui_payload_path)
    if not isinstance(base_payload, dict):
        base_payload = {}

    attempts: list[dict[str, Any]] = []
    best_result: dict[str, Any] | None = None
    best_score: dict[str, Any] = {"score": -1}
    for attempt in range(1, max_attempts + 1):
        if progress_callback:
            progress_callback(
                {
                    "event": "production_update",
                    "stage": "comfyui",
                    "message": f"ComfyUI 质量检查第 {attempt}/{max_attempts} 次尝试",
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }
            )
        attempt_dir = output_dir / f"attempt_{attempt:02d}"
        attempt_payload_path = attempt_dir / "comfyui_payload.json"
        attempt_payload = _payload_for_attempt(base_payload, attempt)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_payload_path.write_text(json.dumps(attempt_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = _run_comfyui_adapter(attempt_payload_path, compose_config, attempt_dir, progress_callback=progress_callback)
        score = _score_material_result(result or {}, min_file_size_kb)
        score["attempt"] = attempt
        score["payload_file"] = str(attempt_payload_path)
        attempts.append(score)
        if score["score"] > int(best_score.get("score", -1)):
            best_score = score
            best_result = result
        if result and result.get("status") == "skipped":
            break
        if result and result.get("status") == "success" and score["score"] >= min_score:
            break

    report_path = _write_quality_report(output_dir, attempts, best_score, min_score, enabled=True)
    if not best_result:
        return {
            "status": "failed",
            "quality_score": int(best_score.get("score", 0)),
            "quality_report": str(report_path),
            "attempts": len(attempts),
        }
    best_result = dict(best_result)
    best_result["quality_score"] = int(best_score.get("score", 0))
    best_result["quality_report"] = str(report_path)
    best_result["attempts"] = len(attempts)
    if best_result.get("status") in {"success", "partial_success"} and best_result["quality_score"] < min_score:
        best_result["status"] = "quality_failed"
    return best_result


def _payload_for_attempt(payload: dict[str, Any], attempt: int) -> dict[str, Any]:
    data = json.loads(json.dumps(payload, ensure_ascii=False))
    base_seed = str(data.get("seed") or "").strip()
    if attempt > 1:
        if base_seed.isdigit():
            data["seed"] = str(int(base_seed) + attempt - 1)
        elif not base_seed:
            data["seed"] = str(int(time.time()) + attempt)
        data["attempt_index"] = attempt
    return data


def _load_comfyui_payload_with_fallback(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return _salvage_comfyui_payload(text)


def _salvage_comfyui_payload(text: str) -> dict[str, Any]:
    image_section = _between_markers(text, '"image_prompts"', '"video_prompts"')
    video_section = _between_markers(text, '"video_prompts"', '"reference_images"')
    image_prompts = _salvage_prompt_items(image_section, default_model="Z-Image Turbo", include_duration=False)
    video_prompts = _salvage_prompt_items(video_section, default_model="LTX-Video 2.3", include_duration=True)
    if not image_prompts and not video_prompts:
        return {}
    payload: dict[str, Any] = {
        "execution_mode": "comfy_full",
        "image_prompts": image_prompts,
        "video_prompts": video_prompts,
        "reference_images": [],
        "output": {
            "aspect_ratio": "16:9",
            "output_directory": "output/comfyui_materials/",
            "file_naming_convention": "{type}_{id}.mp4 or .png",
        },
        "payload_recovered": True,
        "payload_recovery_note": "Original ComfyUI JSON was invalid; prompt items were recovered from text.",
    }
    return payload


def _between_markers(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    return text[start:end] if end > start else text[start:]


def _combined_comfyui_plan(image_content: str, video_content: str) -> str:
    return (
        "# ComfyUI 素材生成编排方案\n\n"
        "本方案由 06_分镜生图设计师和 07_视频生成执行员输出整合生成；项目已不再单独运行 21_ComfyUI素材编排师。\n\n"
        "## 1. 生图参数来源（06）\n\n"
        f"{image_content or '未找到 06_分镜生图设计师输出。'}\n\n"
        "## 2. 生视频参数来源（07）\n\n"
        f"{video_content or '未找到 07_视频生成执行员输出。'}\n"
    )


def _combined_comfyui_payload_text(
    image_content: str,
    video_content: str,
    mode: str,
    final_video_name: str,
    video_config: dict[str, Any],
) -> str:
    defaults = json.loads(_default_comfyui_payload(mode, final_video_name, video_config))
    image_payload = _json_object_from_first_block(image_content)
    video_payload = _json_object_from_first_block(video_content)
    for source in (image_payload, video_payload):
        if not source:
            continue
        for key in ("image_prompts", "video_prompts", "reference_images", "missing_or_inferred_prompts"):
            value = source.get(key)
            if isinstance(value, list):
                defaults.setdefault(key, [])
                defaults[key].extend(value)
        for key in ("image_prompt", "video_prompt", "reference_image", "negative_prompt", "seed", "width", "height"):
            value = source.get(key)
            if value not in (None, "", []):
                defaults[key] = value
        output = source.get("output")
        if isinstance(output, dict):
            defaults.setdefault("output", {}).update(output)
    defaults["payload_source"] = "merged_from_06_07"
    defaults["notes"] = "ComfyUI 参数包由 06 的 image_prompts 和 07 的 video_prompts 合并生成。"
    return json.dumps(defaults, ensure_ascii=False, indent=2) + "\n"


def _json_object_from_first_block(content: str) -> dict[str, Any]:
    block = _extract_json_block(content)
    if not block:
        return {}
    try:
        data = json.loads(block)
    except Exception:
        data = _salvage_comfyui_payload(block)
    return data if isinstance(data, dict) else {}


def _salvage_prompt_items(section: str, default_model: str, include_duration: bool) -> list[dict[str, Any]]:
    if not section:
        return []
    items: list[dict[str, Any]] = []
    pattern = re.compile(
        r'"(?:id|prompt_id)"\s*:\s*"(?P<id>[^"]+)"(?P<body>.*?)(?=\n\s*\{\s*\n\s*"(?:id|prompt_id)"|\n\s*\]\s*,|\Z)',
        re.DOTALL,
    )
    for match in pattern.finditer(section):
        body = match.group("body")
        prompt = _salvage_json_string_field(body, "prompt") or _salvage_json_string_field(body, "prompt_text")
        if not prompt:
            continue
        negative_prompt = _salvage_json_string_field(body, "negative_prompt")
        item: dict[str, Any] = {
            "id": match.group("id"),
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "aspect_ratio": _salvage_json_string_field(body, "aspect_ratio") or "16:9",
            "model": _salvage_json_string_field(body, "model") or default_model,
        }
        item_type = _salvage_json_string_field(body, "type")
        if item_type:
            item["type"] = item_type
        if include_duration:
            duration = _salvage_json_number_field(body, "duration")
            fps = _salvage_json_number_field(body, "fps")
            if duration:
                item["duration"] = duration
            if fps:
                item["fps"] = fps
        seed = _salvage_json_number_field(body, "seed")
        if seed:
            item["seed"] = seed
        items.append(item)
    return items


def _salvage_json_string_field(body: str, key: str) -> str:
    marker = f'"{key}"'
    start = body.find(marker)
    if start < 0:
        return ""
    colon = body.find(":", start + len(marker))
    if colon < 0:
        return ""
    first_quote = body.find('"', colon + 1)
    if first_quote < 0:
        return ""
    next_key = re.search(r'\n\s*"[A-Za-z_][A-Za-z0-9_]*"\s*:', body[first_quote + 1 :])
    if next_key:
        end_region = first_quote + 1 + next_key.start()
        comma = body.rfind(",", first_quote + 1, end_region)
        last_quote = body.rfind('"', first_quote + 1, comma if comma > first_quote else end_region)
    else:
        last_quote = body.rfind('"')
    if last_quote <= first_quote:
        return ""
    value = body[first_quote + 1 : last_quote]
    return value.replace('\\"', '"').strip()


def _salvage_json_number_field(body: str, key: str) -> int | float | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(-?\d+(?:\.\d+)?)', body)
    if not match:
        return None
    value = match.group(1)
    return float(value) if "." in value else int(value)


def _score_material_result(result: dict[str, Any], min_file_size_kb: int) -> dict[str, Any]:
    files = [Path(path) for path in result.get("downloaded_files", []) if path]
    existing = [path for path in files if path.exists()]
    total_size = sum(path.stat().st_size for path in existing)
    score = 0
    reasons = []
    if result.get("status") == "success":
        score += 45
        reasons.append("接口返回成功")
    elif result.get("status") == "partial_success":
        score += 35
        reasons.append("ComfyUI batch partially succeeded")
    else:
        reasons.append(f"接口状态：{result.get('status') or 'unknown'}")
    if existing:
        score += 30
        reasons.append(f"已下载 {len(existing)} 个素材文件")
    else:
        reasons.append("没有下载到素材文件")
    if total_size >= min_file_size_kb * 1024:
        score += 25
        reasons.append(f"素材总大小 {total_size} bytes 达到阈值")
    else:
        reasons.append(f"素材总大小 {total_size} bytes 低于阈值")
    return {
        "score": min(score, 100),
        "status": result.get("status") or "unknown",
        "downloaded_files": [str(path) for path in existing],
        "total_size_bytes": total_size,
        "reasons": reasons,
        "manifest_file": result.get("manifest_file", ""),
    }


def _write_quality_report(
    output_dir: Path,
    attempts: list[dict[str, Any]],
    best_score: dict[str, Any],
    min_score: int,
    enabled: bool,
) -> Path:
    report = {
        "enabled": enabled,
        "min_score": min_score,
        "best_score": int(best_score.get("score", 0)),
        "passed": int(best_score.get("score", 0)) >= min_score,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    path = output_dir / "auto_quality_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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


def _extract_voice_text(content: str) -> str:
    for heading in ("完整配音稿", "TTS 配音稿", "配音稿", "口播配音稿", "旁白稿"):
        text_block = _extract_fenced_block_after_heading(content, heading)
        if text_block:
            return _clean_voice_text(text_block)
    for heading in ("TTS 配音稿", "配音稿", "口播配音稿", "旁白稿"):
        section = _extract_section(content, heading)
        if section:
            return _clean_voice_text(section)
    for match in re.finditer(r"```(?:text)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE):
        block = match.group(1).strip()
        if len(block) > 200 and any(marker in block for marker in ("口播配音稿", "0s -", "我跟你说", "开头痛点")):
            return _clean_voice_text(block)
    for heading in ("完整配音稿", "TTS 配音稿", "配音稿", "口播稿", "旁白稿"):
        text_block = _extract_fenced_block_after_heading(content, heading)
        if text_block:
            return _clean_voice_text(text_block)
    for heading in ("TTS 配音稿", "配音稿", "口播稿", "旁白稿"):
        section = _extract_section(content, heading)
        if section:
            return _clean_voice_text(section)
    return ""


def _extract_section(content: str, heading: str) -> str:
    pattern = rf"#+\s*(?:\d+(?:\.\d+)*[\.、]?\s*)?{re.escape(heading)}\s*(.*?)(?:\n#+\s|\Z)"
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return ""
    text = re.sub(r"```(?:text)?\s*|\s*```", "", match.group(1)).strip()
    return text + "\n" if text else ""


def _extract_fenced_block_after_heading(content: str, heading: str) -> str:
    pattern = rf"#+\s*(?:\d+(?:\.\d+)*[\.、]?\s*)?{re.escape(heading)}(?:[^\n]*)\n.*?```(?:text)?\s*(.*?)```"
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() + "\n" if match else ""


def _clean_voice_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if line.startswith("|") or re.match(r"^[-:| ]{3,}$", line):
            continue
        if re.match(r"^#+\s+", line):
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned + "\n" if cleaned else ""


def _quality_check_voice_text(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    placeholder_patterns = ["待从", "后续", "自行完成", "按上述", "....", "..."]
    if not stripped:
        return {"usable": False, "status": "missing", "reason": "没有抽取到配音稿"}
    if len(stripped) < 200 and any(pattern in stripped for pattern in placeholder_patterns):
        return {"usable": False, "status": "placeholder", "reason": "配音稿包含占位说明"}
    if any(stripped.startswith(pattern) for pattern in placeholder_patterns):
        return {"usable": False, "status": "placeholder", "reason": "配音稿包含占位说明"}
    if len(stripped) < 80:
        return {"usable": False, "status": "too_short", "reason": "配音稿过短"}
    return {"usable": True, "status": "ok", "reason": ""}


def _quality_check_srt(srt: str) -> dict[str, Any]:
    stripped = (srt or "").strip()
    entries = len(re.findall(r"(?m)^\d+\s*$", stripped))
    placeholder_patterns = ["后续", "自行完成", "按上述", "....", "..."]
    if not stripped:
        return {"usable": False, "status": "missing", "reason": "没有抽取到 SRT", "entries": 0}
    if any(pattern in stripped for pattern in placeholder_patterns):
        return {"usable": False, "status": "placeholder", "reason": "SRT 包含占位说明", "entries": entries}
    if entries < 3:
        return {"usable": False, "status": "too_few_entries", "reason": "SRT 条目过少", "entries": entries}
    if "-->" not in stripped:
        return {"usable": False, "status": "invalid", "reason": "SRT 缺少时间轴", "entries": entries}
    return {"usable": True, "status": "ok", "reason": "", "entries": entries}


def _srt_from_voice_text(voice_text: str) -> str:
    chunks = _chunk_voice_text_for_srt(voice_text)
    if not chunks:
        return _default_srt()
    lines: list[str] = []
    current_ms = 0
    for index, chunk in enumerate(chunks, start=1):
        duration_ms = max(2200, min(7000, int(len(chunk) / 5 * 1000)))
        start = _format_srt_time(current_ms)
        end = _format_srt_time(current_ms + duration_ms)
        lines.extend([str(index), f"{start} --> {end}", chunk, ""])
        current_ms += duration_ms + 120
    return "\n".join(lines).strip() + "\n"


def _chunk_voice_text_for_srt(text: str, max_chars: int = 32) -> list[str]:
    source = re.sub(r"【.*?】", "", text)
    source = re.sub(r"\s+", " ", source).strip()
    if not source:
        return []
    parts = [part.strip() for part in re.split(r"([。！？!?；;])", source) if part.strip()]
    sentences: list[str] = []
    current = ""
    for part in parts:
        current += part
        if re.fullmatch(r"[。！？!?；;]", part):
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > max_chars:
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if sentence:
            chunks.append(sentence)
    return chunks


def _format_srt_time(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{sec:02d},{ms:03d}"


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


def _default_comfyui_payload(
    mode: str,
    final_video_name: str,
    video_config: dict[str, Any],
) -> str:
    payload = {
        "execution_mode": mode,
        "image_prompts": [],
        "image_prompt": "",
        "video_prompts": [],
        "video_prompt": video_config.get("positive_prompt") or "",
        "reference_images": [],
        "reference_image": "",
        "negative_prompt": video_config.get("negative_prompt") or "",
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
        "notes": "待根据 06/07 输出的 ComfyUI 参数包和实际 ComfyUI 节点补全。",
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _ensure_comfyui_payload_defaults(
    payload_text: str,
    mode: str,
    final_video_name: str,
    video_config: dict[str, Any],
) -> str:
    defaults = json.loads(_default_comfyui_payload(mode, final_video_name, video_config))
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = _salvage_comfyui_payload(payload_text)
        if not payload:
            payload = defaults
    if not isinstance(payload, dict):
        payload = defaults
    for key in ("negative_prompt", "reference_image", "seed"):
        if not str(payload.get(key) or "").strip():
            payload[key] = defaults.get(key, "")
    if not isinstance(payload.get("output"), dict):
        payload["output"] = defaults.get("output", {})
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
            f"- 06 生图参数包状态：{'已找到' if image_step else '未找到'}",
            f"- 07 生视频参数包状态：{'已找到' if video_step else '未找到'}",
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
            "- 当前版本会生成自动生产资产包、语音字幕包、由 06/07 合并得到的 ComfyUI 素材编排方案、剪辑成片方案和 manifest。",
            "- 当合成工具为 ffmpeg 且本地可找到 ffmpeg.exe，并且已有视频片段、图片或配音音频时，会尝试生成 final_video.mp4。",
            "- 若缺少 FFmpeg 或素材不足，会在 local_ffmpeg_manifest.json 里记录 skipped 原因，不中断工作流。",
            "- 启用 ComfyUI 全自动生成时，会生成 comfyui/auto_quality_report.json；不合格素材会按配置自动重试，保留最高分结果。",
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
            f"- 素材自动评审：{manifest.get('composition', {}).get('quality_score', '')} 分，尝试 {manifest.get('composition', {}).get('quality_attempts', '')} 次",
            f"- 评审报告：{manifest.get('composition', {}).get('quality_report', '')}",
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


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "启用"}
