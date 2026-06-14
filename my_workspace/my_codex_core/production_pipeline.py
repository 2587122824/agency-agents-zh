from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .cloud_image_adapter import CloudImageAdapter


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

    paths = _create_output_dirs(task_dir)
    image_step = _find_step(step_outputs, "06_")
    video_step = _find_step(step_outputs, "07_")
    image_content = image_step.get("content", "") if image_step else ""
    video_content = video_step.get("content", "") if video_step else ""

    image_prompt_path = paths["image_prompts"] / "storyboard_image_prompts.md"
    video_prompt_path = paths["video_prompts"] / "video_generation_prompts.md"
    voiceover_path = paths["audio"] / "voiceover.txt"
    subtitles_path = task_dir / "subtitles.srt"
    checklist_path = task_dir / "edit_checklist.md"
    production_note_path = task_dir / "auto_production.md"
    final_video_name = _safe_file_name(str(compose_config.get("final_video_name") or "final_video.mp4"))

    _write_text(image_prompt_path, image_content or "# 分镜生图提示词\n\n未找到 06_分镜生图设计师输出。\n")
    _write_text(video_prompt_path, video_content or "# 视频生成提示词\n\n未找到 07_视频生成执行员输出。\n")
    _write_text(voiceover_path, _extract_section(video_content, "TTS 配音稿") or "待从 07_视频生成执行员输出中整理配音稿。\n")
    _write_text(subtitles_path, _extract_srt(video_content) or _default_srt())
    _write_text(checklist_path, _build_edit_checklist(image_step, video_step, image_config, video_config, compose_config))

    manifest = {
        "schema_version": 1,
        "mode": mode,
        "status": "package_ready" if mode == "package_only" else "api_adapter_pending",
        "task_dir": str(task_dir),
        "image_generation": {
            "tool": image_config.get("tool") or "",
            "model": image_config.get("model") or "",
            "size": image_config.get("size") or "",
            "count_per_shot": image_config.get("count_per_shot") or "",
            "api_key_provided": bool(image_config.get("api_key_provided")),
            "base_url_provided": bool(image_config.get("base_url_provided")),
            "prompt_file": str(image_prompt_path),
            "output_dir": str(paths["generated_images"]),
            "adapter_status": "not_configured" if mode == "package_only" else "pending",
        },
        "video_generation": {
            "tool": video_config.get("tool") or "",
            "model": video_config.get("model") or "",
            "aspect_ratio": video_config.get("aspect_ratio") or "",
            "duration": video_config.get("duration") or "",
            "api_key_provided": bool(video_config.get("api_key_provided")),
            "base_url_provided": bool(video_config.get("base_url_provided")),
            "prompt_file": str(video_prompt_path),
            "output_dir": str(paths["video_clips"]),
            "adapter_status": "not_configured" if mode == "package_only" else "pending",
        },
        "composition": {
            "tool": compose_config.get("tool") or "ffmpeg",
            "target_file": str(task_dir / final_video_name),
            "subtitles_file": str(subtitles_path),
            "voiceover_file": str(voiceover_path),
            "adapter_status": "not_configured" if mode == "package_only" else "pending",
        },
        "files": {
            "image_prompts": str(image_prompt_path),
            "video_prompts": str(video_prompt_path),
            "voiceover": str(voiceover_path),
            "subtitles": str(subtitles_path),
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

    manifest_path = task_dir / "production_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_text(production_note_path, _build_production_note(manifest))
    manifest["files"]["manifest"] = str(manifest_path)
    manifest["files"]["note"] = str(production_note_path)
    return manifest


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


def _default_srt() -> str:
    return "1\n00:00:00,000 --> 00:00:03,000\n待从 07_视频生成执行员输出中整理字幕。\n"


def _build_edit_checklist(
    image_step: dict[str, str] | None,
    video_step: dict[str, str] | None,
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
            "## 3. 合成",
            f"- 合成工具：{compose_config.get('tool') or 'ffmpeg'}",
            "- 字幕：subtitles.srt",
            "- 配音：audio/voiceover.txt",
            "- 目标视频：final_video.mp4",
            "",
            "## 4. 当前限制",
            "- 当前版本生成自动生产资产包和 manifest，不直接调用第三方生图/生视频 API。",
            "- 后续接入具体平台时，适配器读取 production_manifest.json 执行。",
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
            "",
            "下一步接入真实平台 API 时，直接读取 `production_manifest.json` 中的配置、提示词文件和输出目录。",
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
