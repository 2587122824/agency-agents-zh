from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .cloud_comfyui_adapter import CloudComfyUIAdapter
from .cloud_image_adapter import CloudImageAdapter
from .cloud_video_adapter import CloudVideoAdapter
from .comfy_mcp_adapter import ComfyMCPAdapter
from .local_ffmpeg_adapter import LocalFFmpegAdapter
from .local_tts_adapter import LocalTTSAdapter
from .production_graph import build_production_graph, normalize_global_context, write_json as write_graph_json
from .production_plan_compiler import compile_production_plan, write_production_plan
from .visual_provider_router import build_visual_provider_profile


DEFAULT_RUNNINGHUB_IMAGE_ENDPOINT = ""
DEFAULT_RUNNINGHUB_VIDEO_ENDPOINT = ""


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
    previous_manifest = _read_json_object(task_dir / "production_manifest.json")
    previous_production_plan = _read_json_object(task_dir / "production_plan.json")

    def emit(message: str, stage: str = "production", **extra: Any) -> None:
        if not progress_callback:
            return
        event = {"event": "production_update", "stage": stage, "message": message}
        event.update(extra)
        progress_callback(event)

    image_config = config.get("image_config") or {}
    video_config = config.get("video_config") or {}
    compose_config = dict(config.get("compose_config") or {})
    voice_config = config.get("voice_config") or {}
    quality_config = config.get("quality_config") or {}
    comfy_debug_gate = config.get("comfy_debug_gate") if isinstance(config.get("comfy_debug_gate"), dict) else {}
    manual_comfy_debug = mode == "comfy_full" and _as_bool(comfy_debug_gate.get("enabled"), default=False)
    manual_comfy_stage = str(comfy_debug_gate.get("stage") or "all").strip().lower() or "all"

    paths = _create_output_dirs(task_dir)
    route_step = _find_step(step_outputs, "01_")
    image_step = _find_step(step_outputs, "06_")
    video_step = _find_step(step_outputs, "07_")
    audio_step = _find_step(step_outputs, "20_")
    edit_step = _find_step(step_outputs, "22_")
    route_content = route_step.get("content", "") if route_step else ""
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
    production_plan_path = task_dir / "production_plan.json"
    production_graph_path = task_dir / "production_graph.json"
    edit_plan_path = task_dir / "final_edit_plan.md"
    checklist_path = task_dir / "edit_checklist.md"
    production_note_path = task_dir / "auto_production.md"
    final_video_name = _safe_file_name(str(compose_config.get("final_video_name") or "final_video.mp4"))
    _ensure_default_runninghub_workflow_endpoints(compose_config)
    visual_provider_profile = build_visual_provider_profile(compose_config)
    compose_config["visual_provider"] = visual_provider_profile["provider"]
    compose_config["visual_provider_profile"] = visual_provider_profile
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
    comfyui_payload = _load_comfyui_payload_with_fallback(comfyui_payload_path)
    production_plan = compile_production_plan(
        task_id=task_dir.name,
        route_content=route_content,
        image_content=image_content,
        video_content=video_content,
        audio_content=audio_content,
        package_content=edit_content,
        existing_payload=comfyui_payload,
        video_config=video_config,
        voice_config=voice_config,
    )
    compiled_payload = production_plan.get("compiled_payload") if isinstance(production_plan.get("compiled_payload"), dict) else {}
    if compiled_payload:
        comfyui_payload = compiled_payload
    global_context = normalize_global_context(comfyui_payload, video_config)
    comfyui_payload["global_context"] = global_context
    production_plan["global_context"] = global_context
    production_plan["compiled_payload"] = comfyui_payload
    write_production_plan(production_plan_path, production_plan)
    _write_text(comfyui_payload_path, json.dumps(comfyui_payload, ensure_ascii=False, indent=2) + "\n")
    packaging_jobs = _packaging_graph_jobs(comfyui_payload, voice_config, voice_text_quality.get("usable"))
    plan_visual_jobs = production_plan.get("visual_jobs") if isinstance(production_plan.get("visual_jobs"), list) else []
    reusable_visual_result = None
    if mode == "comfy_full" and not stop_after_comfyui:
        reusable_visual_result = _completed_visual_result_for_reuse(
            previous_manifest,
            previous_production_plan,
            plan_visual_jobs,
        )
    required_workflow_slots = _required_workflow_slots(plan_visual_jobs)
    configured_workflow_slots = _configured_workflow_slots(compose_config.get("workflow_library"))
    missing_workflow_slots = _missing_workflow_slots(required_workflow_slots, configured_workflow_slots)
    write_graph_json(production_graph_path, build_production_graph(task_dir.name, plan_visual_jobs, global_context, packaging_jobs))
    compose_config.update(
        {
            "production_graph_path": str(production_graph_path),
            "production_task_id": task_dir.name,
            "global_context": global_context,
            "packaging_jobs": packaging_jobs,
            "production_plan_visual_jobs": plan_visual_jobs,
        }
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
        "schema_version": 2,
        "mode": mode,
        "status": initial_status,
        "task_dir": str(task_dir),
        "production_plan": str(production_plan_path),
        "production_graph": str(production_graph_path),
        "architecture_layers": production_plan.get("architecture_layers") if isinstance(production_plan.get("architecture_layers"), list) else [],
        "selected_template": production_plan.get("selected_template") if isinstance(production_plan.get("selected_template"), dict) else {},
        "global_context": global_context,
        "production_nodes": [],
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
            "visual_provider": visual_provider_profile["provider"],
            "visual_provider_status": "pending" if visual_provider_profile["supported"] else "blocked",
            "visual_provider_reason": visual_provider_profile["reason"],
            "visual_provider_details": visual_provider_profile,
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
            "required_workflow_slots": required_workflow_slots,
            "configured_workflow_slots": configured_workflow_slots,
            "missing_workflow_slots": missing_workflow_slots,
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
            "production_plan": str(production_plan_path),
            "final_edit_plan": str(edit_plan_path),
            "edit_checklist": str(checklist_path),
            "production_graph": str(production_graph_path),
        },
    }
    for packaging_job in packaging_jobs:
        job_id = str(packaging_job.get("job_id") or "")
        initial_node_status = "success" if job_id == "subtitle_build" and subtitles_path.is_file() else "pending"
        _upsert_production_node(
            manifest,
            job_id,
            stage="08_audio_visual_packaging",
            mode=str(packaging_job.get("mode") or job_id),
            status=initial_node_status,
            depends_on=packaging_job.get("depends_on") or [],
            outputs=([str(subtitles_path)] if job_id == "subtitle_build" and subtitles_path.is_file() else []),
        )

    def apply_manual_comfy_debug_gate() -> dict[str, Any]:
        state_path = paths["comfyui"] / "manual_debug_state.json"
        state = _read_json_object(state_path)
        payload = _load_comfyui_payload_with_fallback(comfyui_payload_path)
        approval = _manual_comfy_debug_approval(payload, state, manual_comfy_stage, task_dir)
        composition = manifest.setdefault("composition", {})
        composition["manual_debug_enabled"] = True
        composition["manual_debug_stage"] = manual_comfy_stage
        composition["manual_debug_state_file"] = str(state_path)
        composition["manual_debug_total"] = approval["total"]
        composition["manual_debug_approved"] = approval["approved"]
        composition["manual_debug_completed"] = approval["complete"]
        composition["downloaded_files"] = approval["downloaded_files"]
        composition["comfyui_downloaded_files"] = approval["downloaded_files"]
        if approval["complete"]:
            composition["adapter_status"] = "success"
            composition["comfyui_adapter_status"] = "success"
            composition["adapter_manifest"] = str(state_path)
            composition["comfyui_adapter_manifest"] = str(state_path)
            manifest["status"] = f"comfyui_{manual_comfy_stage}_manual_approved" if manual_comfy_stage in {"image", "video"} else "comfyui_manual_approved"
            emit("ComfyUI 人工调试队列已全部确认，允许进入下一阶段", stage="comfyui", status="success")
        else:
            composition["adapter_status"] = "awaiting_confirmation"
            composition["comfyui_adapter_status"] = "awaiting_confirmation"
            composition["adapter_manifest"] = str(state_path)
            composition["comfyui_adapter_manifest"] = str(state_path)
            manifest["status"] = f"awaiting_comfyui_{manual_comfy_stage}_debug" if manual_comfy_stage in {"image", "video"} else "awaiting_comfyui_debug"
            emit(
                f"ComfyUI 人工调试队列等待确认：{manual_comfy_stage} {approval['approved']}/{approval['total']}",
                stage="comfyui",
                status="awaiting_confirmation",
            )
        return manifest

    def run_material_branch() -> tuple[str, dict[str, Any] | None]:
        if mode == "api_ready":
            emit("开始图片素材生成/匹配", stage="image")
            return "image", _run_image_adapter(image_content, image_config, paths["generated_images"])
        if mode == "comfy_full":
            if reusable_visual_result:
                emit(
                    f"复用素材闸门已完成的 ComfyUI 素材，共 {len(reusable_visual_result.get('downloaded_files') or [])} 个文件",
                    stage="comfyui",
                    status="cached",
                )
                return "comfyui", reusable_visual_result
            if manual_comfy_debug:
                state_path = paths["comfyui"] / "manual_debug_state.json"
                state = _read_json_object(state_path)
                payload = _load_comfyui_payload_with_fallback(comfyui_payload_path)
                approval = _manual_comfy_debug_approval(payload, state, "all", task_dir)
                if approval["complete"]:
                    emit("复用已确认的 ComfyUI 调试素材", stage="comfyui", status="success")
                    return "comfyui", {
                        "status": "success",
                        "manifest_file": str(state_path),
                        "downloaded_files": approval["downloaded_files"],
                        "quality_report": "",
                        "quality_score": 100,
                        "attempts": 1,
                    }
                return "comfyui", {
                    "status": "failed",
                    "manifest_file": str(state_path),
                    "downloaded_files": approval["downloaded_files"],
                    "error": "ComfyUI manual debug queue is not fully approved",
                }
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
            manifest["composition"]["visual_provider_status"] = comfyui_adapter_result["status"]
            manifest["composition"]["visual_provider_reason"] = comfyui_adapter_result.get("reason", "")
            if comfyui_adapter_result.get("missing_workflow_slots"):
                manifest["composition"]["missing_workflow_slots"] = comfyui_adapter_result.get("missing_workflow_slots", [])
            if comfyui_adapter_result.get("provider"):
                manifest["composition"]["visual_provider"] = comfyui_adapter_result.get("provider", manifest["composition"].get("visual_provider", ""))
            manifest["composition"]["quality_report"] = comfyui_adapter_result.get("quality_report", "")
            manifest["composition"]["quality_score"] = comfyui_adapter_result.get("quality_score", 0)
            manifest["composition"]["quality_attempts"] = comfyui_adapter_result.get("attempts", 1)
            manifest["composition"]["production_job_state"] = comfyui_adapter_result.get("job_state_file", "")
            manifest["artifacts"] = comfyui_adapter_result.get("artifacts", [])
            for node in comfyui_adapter_result.get("jobs", []):
                if not isinstance(node, dict):
                    continue
                _upsert_production_node(
                    manifest,
                    str(node.get("job_id") or node.get("name") or "material"),
                    stage="visual",
                    mode=str(node.get("mode") or ""),
                    status=str(node.get("status") or "unknown"),
                    depends_on=node.get("depends_on") or [],
                    outputs=node.get("downloaded_files") or [],
                    attempts=int(node.get("attempts") or 1),
                    cache_hit=bool(node.get("cache_hit")),
                    error=str(node.get("error") or ""),
                    optional_when_unconfigured=_as_bool(node.get("optional_when_unconfigured"), default=False),
                )
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
            _upsert_production_node(
                manifest,
                "local_tts",
                stage="08_audio_visual_packaging",
                mode="local_tts",
                status=str(tts_adapter_result.get("status") or "failed"),
                outputs=files,
                error=str(tts_adapter_result.get("error") or tts_adapter_result.get("reason") or ""),
            )
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
        if manual_comfy_debug:
            return _finalize_production_manifest(
                task_dir,
                apply_manual_comfy_debug_gate(),
                production_note_path,
                emit,
                "ComfyUI 人工调试门禁等待确认" if manifest.get("status") == "awaiting_comfyui_debug" else "ComfyUI 人工调试门禁完成",
            )
        material_kind, material_result = run_material_branch()
        if material_kind == "image":
            apply_image_result(material_result)
        elif material_kind == "comfyui":
            apply_comfyui_result(material_result)
        return _finalize_production_manifest(task_dir, manifest, production_note_path, emit, "ComfyUI 素材门禁完成")

    material_enabled = mode in {"api_ready", "comfy_full"}
    tts_enabled = _voice_config_tts_enabled(voice_config) and bool(voice_text_quality.get("usable"))
    talking_image_requires_audio = _payload_has_mode(comfyui_payload, "talking_image")
    if material_enabled and tts_enabled and talking_image_requires_audio:
        emit("检测到数字人口播：先生成最终 WAV，再执行口型工作流", stage="production")
        tts_kind, tts_result = run_tts_branch()
        if tts_kind == "tts":
            apply_tts_result(tts_result)
        voice_files = (tts_result or {}).get("downloaded_files") if isinstance(tts_result, dict) else []
        if voice_files:
            _inject_mode_audio_file(comfyui_payload_path, "talking_image", str(voice_files[0]))
            material_kind, material_result = run_material_branch()
            if material_kind == "image":
                apply_image_result(material_result)
            elif material_kind == "comfyui":
                apply_comfyui_result(material_result)
        else:
            manifest["status"] = "talking_image_audio_blocked"
            manifest["production_nodes"].append(
                {
                    "job_id": "talking_image",
                    "stage": "visual",
                    "status": "blocked",
                    "blocked_reason": "input_audio_file is missing because local TTS did not produce a WAV file",
                }
            )
    elif material_enabled and tts_enabled:
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

    bgm_result = _select_bgm_from_asset_library(task_dir, voice_config, comfyui_payload)
    manifest["audio"]["bgm_file"] = bgm_result.get("file", "")
    manifest["audio"]["bgm_asset_id"] = bgm_result.get("asset_id", "")
    manifest["audio"]["bgm_status"] = bgm_result.get("status", "skipped")
    _upsert_production_node(
        manifest,
        "bgm_select",
        stage="08_audio_visual_packaging",
        mode="bgm_select",
        status=str(bgm_result.get("status") or "skipped"),
        outputs=([bgm_result["file"]] if bgm_result.get("file") else []),
        error=str(bgm_result.get("reason") or ""),
    )

    emit("开始本地 FFmpeg 剪辑/预览合成", stage="ffmpeg")
    ffmpeg_depends_on = _ffmpeg_dependency_ids(manifest, tts_enabled)
    dependency_blockers = _packaging_dependency_blockers(manifest, tts_enabled, material_enabled)
    if dependency_blockers:
        blocked_reason = "; ".join(dependency_blockers)
        manifest["status"] = "ffmpeg_dependency_blocked"
        manifest["composition"]["adapter_status"] = "blocked"
        manifest["composition"]["local_ffmpeg_status"] = "blocked"
        manifest["composition"]["local_ffmpeg_blocked_reason"] = blocked_reason
        _upsert_production_node(
            manifest,
            "ffmpeg_compose",
            stage="08_audio_visual_packaging",
            mode="ffmpeg_compose",
            status="blocked",
            depends_on=ffmpeg_depends_on,
            outputs=[],
            error=blocked_reason,
        )
        _upsert_production_node(
            manifest,
            "format_export",
            stage="08_audio_visual_packaging",
            mode="format_export",
            status="blocked",
            depends_on=["ffmpeg_compose"],
            outputs=[],
            error="ffmpeg_compose is blocked by upstream production dependencies",
        )
        emit("FFmpeg composition blocked by upstream dependencies", stage="ffmpeg", status="blocked", reason=blocked_reason)
        return _finalize_production_manifest(task_dir, manifest, production_note_path, emit, "自动生成阶段完成")

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
        _upsert_production_node(
            manifest,
            "ffmpeg_compose",
            stage="08_audio_visual_packaging",
            mode="ffmpeg_compose",
            status=str(ffmpeg_adapter_result.get("status") or "failed"),
            depends_on=ffmpeg_depends_on,
            outputs=files,
            error=str(ffmpeg_adapter_result.get("error") or ffmpeg_adapter_result.get("reason") or ""),
        )
        export_status = "success" if ffmpeg_adapter_result.get("status") == "success" and files else "blocked"
        export_outputs = [*files, str(subtitles_path)] if files and subtitles_path.is_file() else files
        _upsert_production_node(
            manifest,
            "format_export",
            stage="08_audio_visual_packaging",
            mode="format_export",
            status=export_status,
            depends_on=["ffmpeg_compose"],
            outputs=export_outputs,
            error="" if export_status == "success" else "ffmpeg_compose did not produce a final MP4",
        )

    return _finalize_production_manifest(task_dir, manifest, production_note_path, emit, "自动生成阶段完成")


def _completed_visual_result_for_reuse(
    previous_manifest: dict[str, Any],
    previous_production_plan: dict[str, Any],
    current_visual_jobs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not previous_manifest or not previous_production_plan or not current_visual_jobs:
        return None
    previous_visual_jobs = previous_production_plan.get("visual_jobs")
    if not isinstance(previous_visual_jobs, list):
        return None
    if _visual_jobs_fingerprint(previous_visual_jobs) != _visual_jobs_fingerprint(current_visual_jobs):
        return None

    composition = previous_manifest.get("composition")
    if not isinstance(composition, dict):
        return None
    if str(composition.get("comfyui_adapter_status") or "").strip().lower() != "success":
        return None

    previous_nodes = [
        node
        for node in (previous_manifest.get("production_nodes") or [])
        if isinstance(node, dict) and str(node.get("stage") or "") == "visual"
    ]
    nodes_by_id = {str(node.get("job_id") or ""): node for node in previous_nodes}
    artifact_outputs: dict[str, list[str]] = {}
    for artifact in previous_manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        producer_job_id = str(artifact.get("producer_job_id") or "").strip()
        path = str(artifact.get("path") or "").strip()
        if producer_job_id and path:
            artifact_outputs.setdefault(producer_job_id, []).append(path)
    allowed_statuses = {"success", "cached", "downloaded"}
    reusable_jobs: list[dict[str, Any]] = []
    downloaded_files: list[str] = []

    for job in current_visual_jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("job_id") or job.get("intent_id") or "").strip()
        if not job_id:
            return None
        previous_node = nodes_by_id.get(job_id)
        if not previous_node:
            return None
        status = str(previous_node.get("status") or "").strip().lower()
        optional = _as_bool(job.get("optional_when_unconfigured"), default=False)
        if status == "skipped" and optional:
            reusable_jobs.append(dict(previous_node))
            continue
        if status not in allowed_statuses:
            return None
        outputs = [
            str(path)
            for path in (
                previous_node.get("outputs")
                or previous_node.get("downloaded_files")
                or artifact_outputs.get(job_id)
                or []
            )
            if str(path).strip()
        ]
        if not outputs or any(not Path(path).is_file() for path in outputs):
            return None
        downloaded_files.extend(outputs)
        cached_node = dict(previous_node)
        cached_node["status"] = "cached"
        cached_node["cache_hit"] = True
        cached_node["outputs"] = outputs
        cached_node["downloaded_files"] = outputs
        reusable_jobs.append(cached_node)

    deduped_files = list(dict.fromkeys(downloaded_files))
    if not deduped_files:
        return None
    return {
        "status": "success",
        "provider": composition.get("visual_provider") or "runninghub",
        "reason": "reused completed material-gate outputs",
        "manifest_file": composition.get("comfyui_adapter_manifest") or composition.get("adapter_manifest") or "",
        "downloaded_files": deduped_files,
        "quality_report": composition.get("quality_report") or "",
        "quality_score": composition.get("quality_score") or 100,
        "attempts": composition.get("quality_attempts") or 1,
        "job_state_file": composition.get("production_job_state") or "",
        "jobs": reusable_jobs,
        "artifacts": previous_manifest.get("artifacts") if isinstance(previous_manifest.get("artifacts"), list) else [],
        "cache_hit": True,
    }


def _visual_jobs_fingerprint(jobs: list[dict[str, Any]]) -> str:
    normalized = json.dumps(jobs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    requested_job_id = str(job or "").strip()
    if not requested_job_id:
        raise ValueError("job or job_id is required")
    retry_job = requested_job_id.lower()

    manifest_path = task_dir / "production_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("production_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("production_manifest.json must contain a JSON object")

    config = production_config if isinstance(production_config, dict) else {}
    mode = _retry_mode(manifest, config, requested_job_id)
    image_config = _retry_section_config(manifest, config, "image_config", "image_generation")
    video_config = _retry_section_config(manifest, config, "video_config", "video_generation")
    voice_config = _retry_section_config(manifest, config, "voice_config", "audio")
    compose_config = _retry_section_config(manifest, config, "compose_config", "composition")
    compose_config = dict(compose_config)
    compose_config.update(
        {
            "production_graph_path": str(task_dir / "production_graph.json"),
            "production_task_id": task_dir.name,
            "global_context": manifest.get("global_context") if isinstance(manifest.get("global_context"), dict) else {},
            "packaging_jobs": _packaging_graph_jobs({}, voice_config),
        }
    )
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
    known_node_ids = {
        str(item.get("job_id") or "")
        for item in (manifest.get("production_nodes") or [])
        if isinstance(item, dict)
    }
    if retry_job not in {"material", "tts", "ffmpeg"}:
        if requested_job_id not in known_node_ids:
            raise ValueError(f"unknown production job_id: {requested_job_id}")
        if requested_job_id == "local_tts":
            retry_job = "tts"
        elif requested_job_id == "bgm_select":
            retry_job = "bgm"
        elif requested_job_id in {"ffmpeg_compose", "format_export", "subtitle_build"}:
            retry_job = "ffmpeg"
        else:
            retry_job = "material"
            compose_config["force_retry_job_id"] = requested_job_id
    if retry_job == "material":
        mode = _retry_mode(manifest, config, "material")

    def emit(message: str, stage: str = "production", **extra: Any) -> None:
        if not progress_callback:
            return
        event = {"event": "production_update", "stage": stage, "message": message}
        event.update(extra)
        progress_callback(event)

    history_item: dict[str, Any] = {
        "job": retry_job,
        "job_id": requested_job_id,
        "status": "running",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "ended_at": "",
        "outputs": [],
        "error": "",
    }
    manifest.setdefault("production_job_history", []).append(history_item)

    try:
        if retry_job == "material":
            _refresh_visual_plan_for_retry(
                task_dir,
                paths,
                manifest,
                compose_config,
                voice_config,
                video_config,
            )
            result = _retry_material_job(task_dir, paths, manifest, mode, image_config, compose_config, quality_config, emit, progress_callback)
        elif retry_job == "tts":
            result = _retry_tts_job(task_dir, paths, manifest, voice_config, emit)
        elif retry_job == "bgm":
            payload_path = paths["comfyui"] / "comfyui_payload.json"
            payload = _load_comfyui_payload_with_fallback(payload_path) if payload_path.is_file() else {}
            result = _select_bgm_from_asset_library(task_dir, voice_config, payload)
            manifest.setdefault("audio", {})["bgm_file"] = result.get("file", "")
            manifest["audio"]["bgm_asset_id"] = result.get("asset_id", "")
            manifest["audio"]["bgm_status"] = result.get("status", "skipped")
            result = {**result, "downloaded_files": ([result["file"]] if result.get("file") else [])}
        else:
            result = _retry_ffmpeg_job(task_dir, paths, manifest, compose_config, emit)

        if retry_job == "material" and isinstance(result.get("jobs"), list):
            for node in result["jobs"]:
                if not isinstance(node, dict):
                    continue
                _upsert_production_node(
                    manifest,
                    str(node.get("job_id") or node.get("name") or "material"),
                    stage="visual",
                    mode=str(node.get("mode") or ""),
                    status=str(node.get("status") or "unknown"),
                    depends_on=node.get("depends_on") or [],
                    outputs=node.get("downloaded_files") or [],
                    attempts=int(node.get("attempts") or 1),
                    cache_hit=bool(node.get("cache_hit")),
                    error=str(node.get("error") or ""),
                    optional_when_unconfigured=_as_bool(node.get("optional_when_unconfigured"), default=False),
                )
            selected_result = next(
                (node for node in result["jobs"] if isinstance(node, dict) and str(node.get("job_id") or "") == requested_job_id),
                None,
            )
        else:
            selected_result = None
        result_for_history = selected_result or result
        history_item["status"] = str(result_for_history.get("status") or "unknown")
        history_item["outputs"] = [str(item) for item in (result_for_history.get("downloaded_files") or []) if item]
        history_item["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if requested_job_id in known_node_ids:
            existing_node = next(
                (
                    item
                    for item in (manifest.get("production_nodes") or [])
                    if isinstance(item, dict) and str(item.get("job_id") or "") == requested_job_id
                ),
                {},
            )
            _upsert_production_node(
                manifest,
                requested_job_id,
                stage="08_audio_visual_packaging" if requested_job_id in {"local_tts", "subtitle_build", "bgm_select", "ffmpeg_compose", "format_export"} else "visual",
                mode=requested_job_id,
                status=history_item["status"],
                depends_on=result_for_history.get("depends_on") or existing_node.get("depends_on") or [],
                outputs=history_item["outputs"],
                error=str(result.get("error") or result.get("reason") or ""),
                optional_when_unconfigured=_as_bool(existing_node.get("optional_when_unconfigured"), default=False),
            )
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
        composition["visual_provider_status"] = result.get("status") or "failed"
        composition["visual_provider_reason"] = "" if result.get("status") == "success" else str(result.get("error") or result.get("reason") or "")
        if isinstance(compose_config.get("visual_provider_profile"), dict):
            composition["visual_provider_details"] = compose_config["visual_provider_profile"]
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
    nodes = [node for node in (manifest.get("production_nodes") or []) if isinstance(node, dict)]
    tts_enabled = _manifest_requires_tts_for_packaging(manifest)
    if not tts_enabled:
        _mark_optional_audio_packaging_skipped(
            manifest,
            "local_tts",
            "No usable voiceover text or generated WAV; composing a visual-only video.",
        )
        _mark_optional_audio_packaging_skipped(
            manifest,
            "bgm_select",
            "No reusable BGM audio asset selected; composing without BGM.",
        )
    material_enabled = any(str(node.get("stage") or "") == "visual" for node in nodes)
    if not material_enabled:
        material_enabled = any(
            str(value or "").strip() not in {"", "pending", "not_configured", "skipped"}
            for value in [
                (manifest.get("image_generation") or {}).get("adapter_status"),
                (manifest.get("composition") or {}).get("comfyui_adapter_status"),
            ]
        )
    ffmpeg_depends_on = _ffmpeg_dependency_ids(manifest, tts_enabled)
    dependency_blockers = _packaging_dependency_blockers(manifest, tts_enabled, material_enabled)
    if dependency_blockers:
        blocked_reason = "; ".join(dependency_blockers)
        composition = manifest.setdefault("composition", {})
        composition["adapter_status"] = "blocked"
        composition["local_ffmpeg_status"] = "blocked"
        composition["local_ffmpeg_blocked_reason"] = blocked_reason
        manifest["status"] = "ffmpeg_dependency_blocked"
        _upsert_production_node(
            manifest,
            "ffmpeg_compose",
            stage="08_audio_visual_packaging",
            mode="ffmpeg_compose",
            status="blocked",
            depends_on=ffmpeg_depends_on,
            outputs=[],
            error=blocked_reason,
        )
        _upsert_production_node(
            manifest,
            "format_export",
            stage="08_audio_visual_packaging",
            mode="format_export",
            status="blocked",
            depends_on=["ffmpeg_compose"],
            outputs=[],
            error="ffmpeg_compose is blocked by upstream production dependencies",
        )
        emit("FFmpeg composition retry blocked by upstream dependencies", stage="ffmpeg", status="blocked", reason=blocked_reason)
        return {"status": "blocked", "error": blocked_reason, "downloaded_files": [], "depends_on": ffmpeg_depends_on}

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
    _upsert_production_node(
        manifest,
        "ffmpeg_compose",
        stage="08_audio_visual_packaging",
        mode="ffmpeg_compose",
        status=str(result.get("status") or "failed"),
        depends_on=ffmpeg_depends_on,
        outputs=outputs,
        error=str(result.get("error") or result.get("reason") or ""),
    )
    export_status = "success" if result.get("status") == "success" and outputs else "blocked"
    subtitles_path = Path(str(manifest.get("files", {}).get("subtitles") or ""))
    if subtitles_path and not subtitles_path.is_absolute():
        subtitles_path = (task_dir / subtitles_path).resolve()
    export_outputs = [*outputs, str(subtitles_path)] if outputs and subtitles_path.is_file() else outputs
    _upsert_production_node(
        manifest,
        "format_export",
        stage="08_audio_visual_packaging",
        mode="format_export",
        status=export_status,
        depends_on=["ffmpeg_compose"],
        outputs=export_outputs,
        error="" if export_status == "success" else "ffmpeg_compose did not produce a final MP4",
    )
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
    if str(summary.get("status") or "").strip().lower() == "completed":
        summary.pop("error", None)
        summary.pop("traceback", None)
        summary.pop("failed_at", None)
    if str(manifest.get("status") or "") == "final_video_generated":
        final_video = manifest.get("files", {}).get("final_video") if isinstance(manifest.get("files"), dict) else ""
        if final_video:
            summary["final_video"] = final_video
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _retry_mode(manifest: dict[str, Any], config: dict[str, Any], requested_job: str = "") -> str:
    configured = str(config.get("mode") or "").strip()
    saved = str(manifest.get("mode") or "").strip()
    composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
    saved_execution = str(composition.get("execution_mode") or "").strip()
    if str(requested_job or "").strip().lower() == "material":
        material_modes = {"api_ready", "comfy_full"}
        if configured in material_modes:
            return configured
        if saved in material_modes:
            return saved
        if saved_execution in material_modes:
            return saved_execution
    if configured and configured != "off":
        return configured
    return str(saved or configured or "off").strip()


def _refresh_visual_plan_for_retry(
    task_dir: Path,
    paths: dict[str, Path],
    manifest: dict[str, Any],
    compose_config: dict[str, Any],
    voice_config: dict[str, Any],
    video_config: dict[str, Any],
) -> None:
    """Recompile visual jobs before retrying an existing task.

    Employee output is persistent, while compiler-owned workflow routing can
    evolve.  A material retry must therefore rebuild the plan/payload/graph
    instead of replaying an old payload containing archived workflow IDs.
    """

    step_outputs = _load_task_step_outputs(task_dir)
    route_step = _find_step(step_outputs, "01_")
    image_step = _find_step(step_outputs, "06_")
    video_step = _find_step(step_outputs, "07_")
    audio_step = _find_step(step_outputs, "20_")
    edit_step = _find_step(step_outputs, "22_")
    route_content = route_step.get("content", "") if route_step else ""
    image_content = image_step.get("content", "") if image_step else ""
    video_content = video_step.get("content", "") if video_step else ""
    audio_content = audio_step.get("content", "") if audio_step else ""
    edit_content = edit_step.get("content", "") if edit_step else ""
    if not image_content and not video_content:
        raise ValueError("cannot rebuild production plan: 06/07 employee outputs are missing")

    payload_path = paths["comfyui"] / "comfyui_payload.json"
    existing_payload = _load_comfyui_payload_with_fallback(payload_path) if payload_path.is_file() else {}
    production_plan = compile_production_plan(
        task_id=task_dir.name,
        route_content=route_content,
        image_content=image_content,
        video_content=video_content,
        audio_content=audio_content,
        package_content=edit_content,
        existing_payload=existing_payload,
        video_config=video_config,
        voice_config=voice_config,
    )
    compiled_payload = production_plan.get("compiled_payload") if isinstance(production_plan.get("compiled_payload"), dict) else {}
    payload = compiled_payload or existing_payload
    global_context = normalize_global_context(payload, video_config)
    payload["global_context"] = global_context
    production_plan["global_context"] = global_context
    production_plan["compiled_payload"] = payload

    plan_path = task_dir / "production_plan.json"
    graph_path = task_dir / "production_graph.json"
    write_production_plan(plan_path, production_plan)
    _write_text(payload_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    visual_jobs = production_plan.get("visual_jobs") if isinstance(production_plan.get("visual_jobs"), list) else []
    voice_text_quality = _quality_check_voice_text(_extract_voice_text(audio_content))
    packaging_jobs = _packaging_graph_jobs(payload, voice_config, voice_text_quality.get("usable"))
    write_graph_json(graph_path, build_production_graph(task_dir.name, visual_jobs, global_context, packaging_jobs))
    compose_config.update(
        {
            "production_graph_path": str(graph_path),
            "production_task_id": task_dir.name,
            "global_context": global_context,
            "packaging_jobs": packaging_jobs,
            "production_plan_visual_jobs": visual_jobs,
        }
    )

    required = _required_workflow_slots(visual_jobs)
    configured = _configured_workflow_slots(compose_config.get("workflow_library"))
    composition = manifest.setdefault("composition", {})
    composition["required_workflow_slots"] = required
    composition["configured_workflow_slots"] = configured
    composition["missing_workflow_slots"] = _missing_workflow_slots(required, configured)
    composition["workflow_library"] = compose_config.get("workflow_library") if isinstance(compose_config.get("workflow_library"), list) else []
    composition["comfyui_payload_file"] = str(payload_path)
    manifest["production_plan"] = str(plan_path)
    manifest["production_graph"] = str(graph_path)
    manifest["selected_template"] = production_plan.get("selected_template") if isinstance(production_plan.get("selected_template"), dict) else {}
    manifest["global_context"] = global_context
    files = manifest.setdefault("files", {})
    files["production_plan"] = str(plan_path)
    files["production_graph"] = str(graph_path)
    files["comfyui_payload"] = str(payload_path)


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
    _ensure_default_runninghub_workflow_endpoints(compose_config)
    provider_profile = build_visual_provider_profile(compose_config)
    provider = provider_profile["provider"]
    api_key = str(compose_config.get("api_key") or "").strip()
    base_url = str(compose_config.get("base_url") or "").strip()
    endpoint = str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip()
    required_workflow_slots = _required_workflow_slots(compose_config.get("production_plan_visual_jobs"))
    configured_workflow_slots = _configured_workflow_slots(compose_config.get("workflow_library"))
    missing_workflow_slots = _missing_workflow_slots(required_workflow_slots, configured_workflow_slots)
    has_workflow_library_config = bool(configured_workflow_slots)
    if provider == "comfy_mcp":
        return ComfyMCPAdapter(
            mcp_url=str(compose_config.get("comfy_mcp_url") or compose_config.get("mcp_url") or ""),
            api_key=api_key,
            progress_callback=progress_callback,
        ).run(_load_comfyui_payload_with_fallback(comfyui_payload_path), compose_config, output_dir)
    if provider == "local_comfyui":
        if not base_url:
            return {"status": "skipped", "reason": "local_comfyui provider requires a base URL"}
        if not endpoint:
            endpoint = "/prompt"
    tool = str(compose_config.get("tool") or "").strip().lower()
    if tool in {"", "manual", "jianying"} and provider != "runninghub":
        return {"status": "skipped", "reason": "compose tool is not a cloud ComfyUI provider"}
    if not api_key or not base_url or (not endpoint and not has_workflow_library_config):
        return {
            "status": "skipped",
            "reason": "ComfyUI/RunningHub 未配置：请先在 ComfyUI 调试台为对应子模式保存 endpoint 和 nodeInfoList",
            "missing_workflow_slots": missing_workflow_slots,
        }

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
        "provider": provider,
        "status": manifest.get("status") or "success",
        "manifest_file": str(output_dir / "cloud_comfyui_manifest.json"),
        "downloaded_files": manifest.get("downloaded_files", []),
        "jobs": manifest.get("jobs", []),
        "artifacts": manifest.get("artifacts", []),
        "job_state_file": manifest.get("job_state_file", ""),
        "production_graph": manifest.get("production_graph", ""),
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


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _manual_comfy_debug_approval(payload: dict[str, Any], state: dict[str, Any], stage: str = "all", task_dir: Path | None = None) -> dict[str, Any]:
    items = _manual_comfy_debug_items(payload, stage=stage)
    state_items = state.get("items") if isinstance(state.get("items"), dict) else {}
    approved = 0
    downloaded_files: list[str] = []
    for item in items:
        item_state = state_items.get(item["id"]) if isinstance(state_items.get(item["id"]), dict) else {}
        if item_state.get("status") == "approved":
            approved += 1
            for file in item_state.get("files") or []:
                if file:
                    downloaded_files.append(_manual_debug_file_path(file, task_dir))
    total = len(items)
    return {
        "total": total,
        "approved": approved,
        "complete": bool(total and approved >= total),
        "downloaded_files": downloaded_files,
    }


def _manual_debug_file_path(file: Any, task_dir: Path | None) -> str:
    path = Path(str(file))
    if path.is_absolute() or task_dir is None:
        return str(path)
    return str(task_dir / path)


def _manual_comfy_debug_items(payload: dict[str, Any], stage: str = "all") -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items: list[dict[str, Any]] = []
    stage = str(stage or "all").strip().lower()
    source_specs = []
    if stage in {"all", "image"}:
        source_specs.append(("image_prompts", "01_base_asset_image", "image"))
    if stage in {"all", "video"}:
        source_specs.append(("video_prompts", "06_i2v_first_frame", "video"))
    for source_key, default_workflow, item_stage in source_specs:
        values = payload.get(source_key)
        if not isinstance(values, list):
            continue
        for index, raw in enumerate(values, 1):
            entry = raw if isinstance(raw, dict) else {"prompt": str(raw)}
            workflow_id = str(entry.get("workflow_id") or entry.get("workflow") or default_workflow).strip()
            mode = str(
                entry.get("workflow_mode")
                or entry.get("image_task_mode")
                or entry.get("video_task_mode")
                or entry.get("task_type")
                or entry.get("asset_tag")
                or ""
            ).strip()
            item_id = str(entry.get("id") or entry.get("shot_id") or entry.get("scene_id") or f"{source_key}_{index:03d}").strip()
            items.append(
                {
                    "id": f"{workflow_id}:{mode or 'default'}:{item_id}",
                    "workflow_id": workflow_id,
                    "workflow_mode": mode,
                    "source": source_key,
                    "stage": item_stage,
                    "source_index": index,
                }
            )
    return items


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
        for key in ("image_prompt", "video_prompt", "reference_image", "negative_prompt", "seed", "width", "height", "task_type", "control_mode", "image_task_mode"):
            value = source.get(key)
            if value not in (None, "", []):
                defaults[key] = value
        global_context = source.get("global_context")
        if isinstance(global_context, dict):
            defaults.setdefault("global_context", {}).update(global_context)
        output = source.get("output")
        if isinstance(output, dict):
            defaults.setdefault("output", {}).update(output)
    _normalize_comfyui_canvas(defaults, video_config)
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


def _ensure_default_runninghub_workflow_endpoints(compose_config: dict[str, Any]) -> None:
    if not isinstance(compose_config, dict):
        return
    provider_hint = str(compose_config.get("visual_provider") or compose_config.get("provider") or "").strip().lower()
    base_url = str(compose_config.get("base_url") or "").strip().lower()
    endpoint = str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip()
    uses_runninghub = (
        provider_hint in {"", "runninghub", "rh", "cloud_runninghub"}
        or "runninghub" in base_url
        or endpoint.startswith(("/run/workflow/", "/run/ai-app/"))
    )
    if not uses_runninghub:
        return
    if not str(compose_config.get("base_url") or "").strip():
        compose_config["base_url"] = "https://www.runninghub.cn/openapi/v2"

    return


def _workflow_library_has_configured_slots(library: Any) -> bool:
    if not isinstance(library, list):
        return False
    for item in library:
        if not isinstance(item, dict):
            continue
        endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
        node_info = str(item.get("node_info_list_json") or item.get("nodeInfoList") or "").strip()
        if endpoint and node_info and node_info != "[]":
            return True
        mode_configs = item.get("mode_configs") or item.get("modeConfigs")
        if not isinstance(mode_configs, dict):
            continue
        for config in mode_configs.values():
            if not isinstance(config, dict):
                continue
            endpoint = str(config.get("endpoint") or config.get("workflow_endpoint") or "").strip()
            node_info = str(config.get("node_info_list_json") or config.get("nodeInfoList") or "").strip()
            if endpoint and node_info and node_info != "[]":
                return True
    return False


def _required_workflow_slots(jobs: Any) -> list[dict[str, str]]:
    if not isinstance(jobs, list):
        return []
    slots: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        workflow_id = str(job.get("workflow_id") or job.get("capability") or "").strip()
        mode = str(job.get("workflow_mode") or job.get("mode") or "").strip()
        material_type = str(job.get("type") or job.get("material_type") or "").strip()
        if _optional_workflow_slot(job, workflow_id, mode):
            continue
        if not workflow_id:
            continue
        key = (workflow_id, mode, material_type)
        if key in seen:
            continue
        seen.add(key)
        slots.append(
            {
                "workflow_id": workflow_id,
                "mode": mode,
                "material_type": material_type,
                "label": f"{workflow_id}{' / ' + mode if mode else ''}",
            }
        )
    return slots


def _configured_workflow_slots(library: Any) -> list[dict[str, str]]:
    if not isinstance(library, list):
        return []
    slots: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in library:
        if not isinstance(item, dict):
            continue
        workflow_id = str(item.get("id") or item.get("workflow_id") or "").strip()
        endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
        node_info = str(item.get("node_info_list_json") or item.get("nodeInfoList") or "").strip()
        if workflow_id and endpoint and node_info and node_info != "[]":
            key = (workflow_id, "")
            if key not in seen:
                seen.add(key)
                slots.append({"workflow_id": workflow_id, "mode": "", "label": workflow_id})
        mode_configs = item.get("mode_configs") or item.get("modeConfigs")
        if not isinstance(mode_configs, dict):
            continue
        for mode, config in mode_configs.items():
            if not isinstance(config, dict):
                continue
            endpoint = str(config.get("endpoint") or config.get("workflow_endpoint") or "").strip()
            node_info = str(config.get("node_info_list_json") or config.get("nodeInfoList") or "").strip()
            if workflow_id and str(mode).strip() and endpoint and node_info and node_info != "[]":
                key = (workflow_id, str(mode).strip())
                if key not in seen:
                    seen.add(key)
                    slots.append({"workflow_id": workflow_id, "mode": str(mode).strip(), "label": f"{workflow_id} / {str(mode).strip()}"})
    return slots


def _missing_workflow_slots(required: list[dict[str, str]], configured: list[dict[str, str]]) -> list[dict[str, str]]:
    configured_pairs = {(str(item.get("workflow_id") or ""), str(item.get("mode") or "")) for item in configured if isinstance(item, dict)}
    configured_ids = {workflow_id for workflow_id, mode in configured_pairs if workflow_id and not mode}
    missing: list[dict[str, str]] = []
    for slot in required:
        workflow_id = str(slot.get("workflow_id") or "")
        mode = str(slot.get("mode") or "")
        if (workflow_id, mode) in configured_pairs:
            continue
        if workflow_id in configured_ids and not mode:
            continue
        missing.append(slot)
    return missing


def _optional_workflow_slot(job: dict[str, Any], workflow_id: str = "", mode: str = "") -> bool:
    if _as_bool(job.get("optional_when_unconfigured"), default=False):
        return True
    text = " ".join(
        str(value or "").strip()
        for value in (
            workflow_id,
            mode,
            job.get("workflow_mode"),
            job.get("mode"),
            job.get("intent"),
            job.get("asset_tag"),
            job.get("capability"),
        )
    ).lower()
    return (
        "enhance_video" in text
        or "video_enhance" in text
        or "cover_key_visual" in text
        or "generate_cover_key_visual" in text
    )


def _ensure_library_item_runninghub_endpoint(item: dict[str, Any]) -> None:
    return
    endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
    if not endpoint:
        material_types = _string_set(item.get("material_types") or item.get("materialTypes") or item.get("types"))
        item_id = str(item.get("id") or "").strip().lower()
        text = " ".join(str(item.get(key) or "") for key in ("id", "name", "purpose")).lower()
        if "video" in material_types or item_id in {"all_in_one_video"} or "video" in text or "视频" in text:
            item["endpoint"] = DEFAULT_RUNNINGHUB_VIDEO_ENDPOINT
        elif "image" in material_types or item_id in {"all_in_one_image", "01_base_asset_image", "02_turnaround", "03_style_cover_image", "04_keyframe"}:
            item["endpoint"] = DEFAULT_RUNNINGHUB_IMAGE_ENDPOINT

    mode_configs = item.get("mode_configs") or item.get("modeConfigs")
    if isinstance(mode_configs, dict):
        parent_endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
        for config in mode_configs.values():
            if isinstance(config, dict) and not str(config.get("endpoint") or config.get("workflow_endpoint") or "").strip() and parent_endpoint:
                config["endpoint"] = parent_endpoint


def _first_library_endpoint_for_type(library: Any, material_type: str) -> str:
    if not isinstance(library, list):
        return ""
    target = str(material_type or "").strip().lower()
    for item in library:
        if not isinstance(item, dict):
            continue
        endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
        if endpoint and target in _string_set(item.get("material_types") or item.get("materialTypes") or item.get("types")):
            return endpoint
    return ""


def _string_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {part.strip().lower() for part in value.split(",") if part.strip()}
    if isinstance(value, list):
        return {str(part).strip().lower() for part in value if str(part).strip()}
    return set()


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


def _packaging_graph_jobs(
    payload: dict[str, Any],
    voice_config: dict[str, Any],
    voice_text_usable: bool | None = None,
) -> list[dict[str, Any]]:
    tts_enabled = _voice_config_tts_enabled(voice_config) and voice_text_usable is not False
    jobs: list[dict[str, Any]] = []
    if tts_enabled:
        jobs.append(
            {
                "job_id": "local_tts",
                "mode": "local_tts",
                "outputs": ["output_voiceover_audio"],
                "resource_class": "local_audio",
                "retry": {"max_attempts": 1, "retry_on": []},
            }
        )
    jobs.extend(
        [
            {
                "job_id": "subtitle_build",
                "mode": "subtitle_build",
                "outputs": ["output_subtitles"],
                "resource_class": "local",
            },
            {
                "job_id": "bgm_select",
                "mode": "bgm_select",
                "outputs": ["output_bgm_audio"],
                "resource_class": "local",
            },
            {
                "job_id": "ffmpeg_compose",
                "mode": "ffmpeg_compose",
                "depends_on": (["local_tts"] if tts_enabled else []) + ["subtitle_build", "bgm_select"],
                "outputs": ["output_final_video"],
                "resource_class": "local_ffmpeg",
                "depends_on_visual": True,
            },
            {
                "job_id": "format_export",
                "mode": "format_export",
                "depends_on": ["ffmpeg_compose"],
                "outputs": ["output_mp4", "output_subtitles_sidecar"],
                "resource_class": "local_ffmpeg",
            },
        ]
    )
    return jobs


def _upsert_production_node(
    manifest: dict[str, Any],
    job_id: str,
    *,
    stage: str,
    mode: str,
    status: str,
    depends_on: list[str] | None = None,
    outputs: list[str] | None = None,
    attempts: int = 1,
    cache_hit: bool = False,
    error: str = "",
    optional_when_unconfigured: bool = False,
) -> None:
    if not job_id:
        return
    nodes = manifest.setdefault("production_nodes", [])
    node = next((item for item in nodes if isinstance(item, dict) and item.get("job_id") == job_id), None)
    if node is None:
        node = {"job_id": job_id}
        nodes.append(node)
    node.update(
        {
            "stage": stage,
            "mode": mode,
            "status": status,
            "depends_on": [str(item) for item in (depends_on or []) if str(item)],
            "outputs": [str(item) for item in (outputs or []) if str(item)],
            "attempts": max(1, int(attempts or 1)),
            "cache_hit": bool(cache_hit),
            "optional_when_unconfigured": bool(optional_when_unconfigured),
            "error": error,
            "blocked_reason": error if status == "blocked" else "",
            "updated_at": time.time(),
        }
    )


def _ffmpeg_dependency_ids(manifest: dict[str, Any], tts_enabled: bool) -> list[str]:
    dependencies: list[str] = []
    for node in manifest.get("production_nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("stage") or "") == "visual":
            job_id = str(node.get("job_id") or "").strip()
            if job_id:
                dependencies.append(job_id)
    if tts_enabled:
        dependencies.append("local_tts")
    dependencies.extend(["subtitle_build", "bgm_select"])
    deduped: list[str] = []
    seen: set[str] = set()
    for item in dependencies:
        if item and item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _voice_config_tts_enabled(voice_config: dict[str, Any]) -> bool:
    return str((voice_config or {}).get("mode") or "").strip().lower() not in {"", "off"}


def _manifest_requires_tts_for_packaging(manifest: dict[str, Any]) -> bool:
    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    voiceover_audio = str(audio.get("voiceover_audio_file") or "").strip()
    if voiceover_audio:
        return True

    voice_text_status = str(audio.get("voice_text_status") or "").strip().lower()
    voice_text_usable = voice_text_status in {"ok", "usable", "success"}
    adapter_status = str(audio.get("adapter_status") or "").strip().lower()
    nodes = [node for node in (manifest.get("production_nodes") or []) if isinstance(node, dict)]
    tts_node = next((node for node in nodes if node.get("job_id") == "local_tts"), None)
    tts_node_status = str((tts_node or {}).get("status") or "").strip().lower()

    if adapter_status == "success" or tts_node_status == "success":
        return True
    if not voice_text_usable:
        return False
    if tts_node and tts_node_status not in {"", "skipped", "not_configured"}:
        return True
    return adapter_status not in {"", "off", "not_configured", "skipped"}


def _mark_optional_audio_packaging_skipped(manifest: dict[str, Any], job_id: str, reason: str) -> None:
    nodes = [node for node in (manifest.get("production_nodes") or []) if isinstance(node, dict)]
    node = next((item for item in nodes if str(item.get("job_id") or "") == job_id), None)
    current_status = str((node or {}).get("status") or "").strip().lower()
    if current_status in {"pending", "blocked", ""}:
        _upsert_production_node(
            manifest,
            job_id,
            stage="08_audio_visual_packaging",
            mode=job_id,
            status="skipped",
            depends_on=(node or {}).get("depends_on") or [],
            outputs=(node or {}).get("outputs") or [],
            error=reason,
        )
    if job_id == "local_tts":
        audio = manifest.setdefault("audio", {})
        if str(audio.get("adapter_status") or "").strip().lower() in {"", "pending"}:
            audio["adapter_status"] = "skipped"
            audio["skip_reason"] = reason
    elif job_id == "bgm_select":
        audio = manifest.setdefault("audio", {})
        if str(audio.get("bgm_status") or "").strip().lower() in {"", "pending"}:
            audio["bgm_status"] = "skipped"
            audio["bgm_reason"] = reason


def _packaging_dependency_blockers(manifest: dict[str, Any], tts_enabled: bool, material_enabled: bool) -> list[str]:
    ok_statuses = {"success", "cached", "downloaded", "submitted", "skipped", "not_configured"}
    blockers: list[str] = []
    nodes = [node for node in (manifest.get("production_nodes") or []) if isinstance(node, dict)]
    visual_nodes = [node for node in nodes if str(node.get("stage") or "") == "visual"]
    for node in visual_nodes:
        status = str(node.get("status") or "").strip()
        if status not in ok_statuses:
            job_id = str(node.get("job_id") or "visual")
            reason = str(node.get("blocked_reason") or node.get("error") or status or "not completed")
            blockers.append(f"{job_id}: {reason}")

    if material_enabled and not visual_nodes:
        image_status = str((manifest.get("image_generation") or {}).get("adapter_status") or "")
        comfy_status = str((manifest.get("composition") or {}).get("comfyui_adapter_status") or "")
        branch_status = comfy_status if comfy_status and comfy_status != "pending" else image_status
        if branch_status and branch_status not in ok_statuses:
            blockers.append(f"visual_material: {branch_status}")

    if tts_enabled:
        tts_node = next((node for node in nodes if node.get("job_id") == "local_tts"), None)
        tts_status = str((tts_node or {}).get("status") or (manifest.get("audio") or {}).get("adapter_status") or "")
        if tts_status not in ok_statuses:
            reason = str((tts_node or {}).get("blocked_reason") or (tts_node or {}).get("error") or tts_status or "not completed")
            blockers.append(f"local_tts: {reason}")
        elif not str((manifest.get("audio") or {}).get("voiceover_audio_file") or "").strip() and tts_status not in {"skipped", "not_configured"}:
            blockers.append("local_tts: voiceover WAV is missing")

    return blockers


def _select_bgm_from_asset_library(task_dir: Path, voice_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    workspace_root = task_dir.parent.parent
    library_root = workspace_root / "my_asset_library"
    library_path = library_root / "library.json"
    if not library_path.is_file():
        return {"status": "skipped", "reason": "asset library is unavailable", "file": "", "asset_id": ""}
    try:
        items = json.loads(library_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"status": "skipped", "reason": "asset library JSON is invalid", "file": "", "asset_id": ""}
    if not isinstance(items, list):
        return {"status": "skipped", "reason": "asset library contains no assets", "file": "", "asset_id": ""}
    output = payload.get("output") if isinstance(payload.get("output"), dict) else {}
    requested = str(voice_config.get("bgm_style") or output.get("bgm_style") or payload.get("bgm_style") or "").strip().lower()
    requested_tokens = {part for part in re.split(r"[\s,，/|]+", requested) if part}
    candidates: list[tuple[int, dict[str, Any], Path]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        relative = str(item.get("file") or "").strip()
        path = (library_root / relative).resolve()
        if path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"} or not path.is_file():
            continue
        tags = {str(tag).strip().lower() for tag in (item.get("tags") or []) if str(tag).strip()}
        text = " ".join([str(item.get("name") or ""), str(item.get("note") or ""), *tags]).lower()
        score = 10 if tags & {"bgm", "music", "音乐", "配乐"} else 0
        score += sum(3 for token in requested_tokens if token in text)
        candidates.append((score, item, path))
    if not candidates:
        return {"status": "skipped", "reason": "no reusable BGM audio asset was found", "file": "", "asset_id": ""}
    _, item, path = max(candidates, key=lambda entry: (entry[0], float(entry[1].get("updated_at") or 0)))
    return {"status": "success", "reason": "", "file": str(path), "asset_id": str(item.get("id") or "")}


def _payload_has_mode(payload: dict[str, Any], mode: str) -> bool:
    target = str(mode or "").strip()
    for key in ("image_prompts", "video_prompts"):
        values = payload.get(key) if isinstance(payload.get(key), list) else []
        for item in values:
            if not isinstance(item, dict):
                continue
            if str(item.get("mode") or item.get("workflow_mode") or item.get("video_task_mode") or "").strip() == target:
                return True
            prompts = item.get("prompts") if isinstance(item.get("prompts"), dict) else {}
            if any(
                isinstance(prompt, dict)
                and str(prompt.get("mode") or prompt.get("workflow_mode") or prompt.get("video_task_mode") or "").strip() == target
                for prompt in prompts.values()
            ):
                return True
    return False


def _inject_mode_audio_file(payload_path: Path, mode: str, audio_file: str) -> None:
    payload = _load_comfyui_payload_with_fallback(payload_path)
    for key in ("image_prompts", "video_prompts"):
        values = payload.get(key) if isinstance(payload.get(key), list) else []
        for item in values:
            if not isinstance(item, dict):
                continue
            item_mode = str(item.get("mode") or item.get("workflow_mode") or item.get("video_task_mode") or "").strip()
            if item_mode == mode:
                item["input_audio_file"] = audio_file
            prompts = item.get("prompts") if isinstance(item.get("prompts"), dict) else {}
            for prompt in prompts.values():
                if not isinstance(prompt, dict):
                    continue
                prompt_mode = str(prompt.get("mode") or prompt.get("workflow_mode") or prompt.get("video_task_mode") or "").strip()
                if prompt_mode == mode:
                    prompt["input_audio_file"] = audio_file
    payload["input_audio_file"] = audio_file
    _write_text(payload_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _find_step(step_outputs: list[dict[str, str]], prefix: str) -> dict[str, str] | None:
    for item in step_outputs:
        if str(item.get("agent", "")).startswith(prefix):
            return item
    return None


def _load_task_step_outputs(task_dir: Path) -> list[dict[str, str]]:
    outputs: list[dict[str, str]] = []
    for step_dir in sorted(task_dir.glob("step_*")):
        if not step_dir.is_dir():
            continue
        output_path = step_dir / "output.md"
        if not output_path.is_file():
            continue
        parts = step_dir.name.split("_", 2)
        agent = parts[2] if len(parts) == 3 else step_dir.name
        outputs.append(
            {
                "agent": agent,
                "content": output_path.read_text(encoding="utf-8", errors="replace"),
            }
        )
    return outputs


def _extract_srt(content: str) -> str:
    match = re.search(r"```srt\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
    payload = _json_object_from_first_block(content)
    audio_package = payload.get("audio_package") if isinstance(payload.get("audio_package"), dict) else {}
    draft = str(audio_package.get("subtitle_srt_draft") or "").strip()
    if draft:
        return draft + "\n"
    production_intents = payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {}
    audio_intents = production_intents.get("audio") if isinstance(production_intents.get("audio"), list) else []
    for intent in audio_intents:
        if not isinstance(intent, dict) or str(intent.get("intent") or "") != "build_subtitles":
            continue
        segments = intent.get("subtitle_segments")
        if not isinstance(segments, list):
            continue
        blocks: list[str] = []
        for index, segment in enumerate(segments, 1):
            if not isinstance(segment, dict):
                continue
            start = str(segment.get("start") or "").strip()
            end = str(segment.get("end") or "").strip()
            text = str(segment.get("text") or "").strip()
            if start and end and text:
                blocks.append(f"{segment.get('index') or index}\n{start} --> {end}\n{text}")
        if blocks:
            return "\n\n".join(blocks) + "\n"
    return ""


def _extract_voice_text(content: str) -> str:
    payload = _json_object_from_first_block(content)
    audio_package = payload.get("audio_package") if isinstance(payload.get("audio_package"), dict) else {}
    packaged_voice_text = str(audio_package.get("voiceover_text") or "").strip()
    if packaged_voice_text:
        return _clean_voice_text(packaged_voice_text)
    production_intents = payload.get("production_intents") if isinstance(payload.get("production_intents"), dict) else {}
    audio_intents = production_intents.get("audio") if isinstance(production_intents.get("audio"), list) else []
    for intent in audio_intents:
        if not isinstance(intent, dict) or str(intent.get("intent") or "") != "generate_voiceover":
            continue
        voice_text = str(intent.get("voice_text") or "").strip()
        if voice_text:
            return _clean_voice_text(voice_text)
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


def _normalize_comfyui_canvas(payload: dict[str, Any], video_config: dict[str, Any]) -> None:
    width, height, aspect_ratio = _target_canvas_from_video_config(video_config, payload)
    payload["width"] = width
    payload["height"] = height
    output = payload.setdefault("output", {})
    if isinstance(output, dict):
        output["aspect_ratio"] = aspect_ratio
        output["width"] = width
        output["height"] = height
    for key in ("image_prompts", "video_prompts"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item["width"] = width
                item["height"] = height


def _target_canvas_from_video_config(
    video_config: dict[str, Any],
    payload: dict[str, Any] | None,
) -> tuple[int, int, str]:
    resolution = str(video_config.get("resolution") or video_config.get("size") or "").strip()
    match = re.search(r"(\d{3,5})\s*[xX*×]\s*(\d{3,5})", resolution)
    if match:
        width = _safe_int(match.group(1), 1024, 256, 4096)
        height = _safe_int(match.group(2), 576, 256, 4096)
        aspect = _aspect_ratio_label(width, height)
        if aspect == "9:16":
            return 480, 848, "9:16"
        if aspect == "1:1":
            return 480, 480, "1:1"
        return 848, 480, "16:9"

    aspect_text = str(video_config.get("aspect_ratio") or "").strip().lower()
    if any(token in aspect_text for token in ("9:16", "portrait", "vertical", "竖屏")):
        return 480, 848, "9:16"
    if any(token in aspect_text for token in ("1:1", "square", "方屏")):
        return 480, 480, "1:1"
    if any(token in aspect_text for token in ("16:9", "landscape", "horizontal", "横屏")):
        return 848, 480, "16:9"

    inferred = _infer_canvas_from_prompt_items(payload or {})
    if inferred:
        width, height = inferred
        if width > height:
            return 848, 480, "16:9"
        if width < height:
            return 480, 848, "9:16"
        return 480, 480, "1:1"
    return 848, 480, "16:9"


def _infer_canvas_from_prompt_items(payload: dict[str, Any]) -> tuple[int, int] | None:
    votes: dict[str, int] = {"landscape": 0, "portrait": 0, "square": 0}
    for key in ("image_prompts", "video_prompts"):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            width = _safe_int(item.get("width"), 0, 0, 10000)
            height = _safe_int(item.get("height"), 0, 0, 10000)
            if width <= 0 or height <= 0:
                continue
            if width > height:
                votes["landscape"] += 1
            elif width < height:
                votes["portrait"] += 1
            else:
                votes["square"] += 1
    winner = max(votes, key=votes.get)
    if votes[winner] <= 0:
        return None
    if winner == "landscape":
        return 1024, 576
    if winner == "portrait":
        return 576, 1024
    return 1024, 1024


def _aspect_ratio_label(width: int, height: int) -> str:
    if width == height:
        return "1:1"
    return "16:9" if width > height else "9:16"


def _default_comfyui_payload(
    mode: str,
    final_video_name: str,
    video_config: dict[str, Any],
) -> str:
    width, height, aspect_ratio = _target_canvas_from_video_config(video_config, None)
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
        "width": width,
        "height": height,
        "global_context": {
            "characters": [],
            "style": {"style_id": "", "reference_asset": "", "weight": ""},
            "render": {
                "working_width": 848,
                "working_height": 480,
                "delivery_width": 1920,
                "delivery_height": 1080,
                "frame_rate": 24,
                "aspect_ratio": video_config.get("aspect_ratio") or aspect_ratio,
            },
        },
        "output": {
            "aspect_ratio": video_config.get("aspect_ratio") or aspect_ratio,
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
    _normalize_comfyui_canvas(payload, video_config)
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
