from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse


class CloudComfyUIAdapter:
    """Call a cloud ComfyUI final-production workflow and persist returned assets."""

    MAX_RESPONSE_BYTES = 4_000_000
    DOWNLOAD_TYPES = {"mp4", "mov", "webm", "m4v", "mp3", "wav", "aac", "png", "jpg", "jpeg", "webp"}

    def __init__(self, base_url: str, api_key: str, endpoint: str, progress_callback=None) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        self.progress_callback = progress_callback
        self._media_upload_cache: dict[str, str] = {}
        if not self.base_url:
            raise ValueError("ComfyUI base URL is required")
        if not self.api_key:
            raise ValueError("ComfyUI API key is required")
        if not self.endpoint:
            raise ValueError("ComfyUI workflow endpoint is required")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ComfyUI base URL must be an http/https URL")

    def _emit(self, message: str, **extra: Any) -> None:
        if not self.progress_callback:
            return
        event = {"event": "production_update", "stage": "comfyui", "message": message}
        event.update(extra)
        self.progress_callback(event)

    def run(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        provider = self._provider(compose_config)
        material_jobs = self._expand_material_jobs(comfyui_payload, compose_config)
        if material_jobs and self._uses_workflow_library(compose_config):
            return self._run_material_jobs(material_jobs, provider, compose_config, output_dir)
        if len(material_jobs) > 1 and self._as_bool(compose_config.get("loop_material_prompts"), default=True):
            return self._run_material_jobs(material_jobs, provider, compose_config, output_dir)
        if provider == "runninghub":
            return self._run_runninghub(comfyui_payload, compose_config, output_dir)
        return self._run_generic(comfyui_payload, compose_config, output_dir)

    def _provider(self, compose_config: dict[str, Any]) -> str:
        provider = str(compose_config.get("provider") or "").strip().lower()
        if provider:
            return provider
        base_url = self.base_url.lower()
        endpoint = self.endpoint.lower()
        if "runninghub" in base_url or endpoint.startswith(("/run/workflow/", "/run/ai-app/")):
            return "runninghub"
        tool = str(compose_config.get("tool") or "").strip().lower()
        return tool or "generic"

    def _run_material_jobs(
        self,
        material_jobs: list[dict[str, Any]],
        provider: str,
        compose_config: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        max_jobs = self._safe_int(compose_config.get("max_material_jobs"), default=50, minimum=1, maximum=50)
        selected_jobs = material_jobs[:max_jobs]
        job_results = []
        downloaded_files = []
        generated_reference_images: list[str] = []
        generated_reference_map: dict[str, str] = {}
        video_job_index = 0
        success_count = 0
        self._reference_search_dirs = [output_dir, output_dir.parent]
        self._emit(f"ComfyUI 素材批量任务开始：{len(selected_jobs)} 个", total_jobs=len(selected_jobs), completed_jobs=0)

        for index, job in enumerate(selected_jobs, start=1):
            job_name = str(job.get("name") or f"material_{index:02d}")
            job_type = str(job.get("type") or "material")
            job_dir = output_dir / f"material_{index:02d}_{self._safe_name(job_name)}"
            job_dir.mkdir(parents=True, exist_ok=True)
            job_config = self._compose_config_for_job(job, compose_config)
            if job_type in {"image", "video"}:
                job = dict(job)
                reference_image = str(job.get("reference_image") or "").strip()
                resolved_reference = self._resolve_reference_image(reference_image, generated_reference_map)
                if resolved_reference:
                    job["reference_image"] = self._reference_image_value(resolved_reference)
                    self._emit(
                        f"已把{job_type}素材 {index}/{len(selected_jobs)} 的参考图映射到本地生成图片",
                        total_jobs=len(selected_jobs),
                        completed_jobs=index - 1,
                        current_job=index,
                        job_index=index,
                        job_count=len(selected_jobs),
                        material_name=job_name,
                        material_type=job_type,
                        job_type=job_type,
                    )
                elif job_type == "video" and generated_reference_images:
                    paired_index = min(video_job_index, len(generated_reference_images) - 1)
                    job["reference_image"] = self._reference_image_value(generated_reference_images[paired_index])
                    self._emit(
                        f"已把第 {paired_index + 1} 张生图作为视频素材 {index}/{len(selected_jobs)} 的参考图",
                        total_jobs=len(selected_jobs),
                        completed_jobs=index - 1,
                        current_job=index,
                        job_index=index,
                        job_count=len(selected_jobs),
                        material_name=job_name,
                        material_type=job_type,
                        job_type=job_type,
                    )
                elif job_type == "image" and generated_reference_images:
                    previous_index = len(generated_reference_images) - 1
                    job["reference_image"] = self._reference_image_value(generated_reference_images[previous_index])
                    self._emit(
                        f"已把上一张生图作为生图素材 {index}/{len(selected_jobs)} 的参考图",
                        total_jobs=len(selected_jobs),
                        completed_jobs=index - 1,
                        current_job=index,
                        job_index=index,
                        job_count=len(selected_jobs),
                        material_name=job_name,
                        material_type=job_type,
                        job_type=job_type,
                    )
                else:
                    if reference_image:
                        self._emit(
                            f"忽略未解析的参考图：{reference_image}",
                            total_jobs=len(selected_jobs),
                            completed_jobs=index - 1,
                            current_job=index,
                            job_index=index,
                            job_count=len(selected_jobs),
                            material_name=job_name,
                            material_type=job_type,
                            job_type=job_type,
                        )
                    job["reference_image"] = ""
                if job_type == "video":
                    video_job_index += 1
            job_payload = self._payload_for_material_job(job["base_payload"], job, index)
            self._write_json(job_dir / "comfyui_payload.json", job_payload)
            self._emit(
                f"提交 ComfyUI 素材 {index}/{len(selected_jobs)}：{job_type}",
                total_jobs=len(selected_jobs),
                completed_jobs=index - 1,
                current_job=index,
                job_index=index,
                job_count=len(selected_jobs),
                material_name=job_name,
                material_type=job_type,
                job_type=job_type,
                endpoint=str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
            )
            try:
                if provider == "runninghub":
                    manifest = self._run_runninghub(job_payload, job_config, job_dir)
                else:
                    manifest = self._run_generic(job_payload, job_config, job_dir)
                job_downloaded = [str(path) for path in manifest.get("downloaded_files", [])]
                if manifest.get("status") in {"success", "downloaded", "submitted"}:
                    success_count += 1
                downloaded_files.extend(job_downloaded)
                if job_type == "image":
                    generated_reference_images.extend(
                        path
                        for path in job_downloaded
                        if Path(path).suffix.lower().lstrip(".") in {"png", "jpg", "jpeg", "webp"}
                    )
                    if job_downloaded:
                        first_image = next(
                            (
                                path
                                for path in job_downloaded
                                if Path(path).suffix.lower().lstrip(".") in {"png", "jpg", "jpeg", "webp"}
                            ),
                            "",
                        )
                        if first_image:
                            for key in self._reference_keys_for_job(job):
                                generated_reference_map[key] = first_image
                job_results.append(
                    {
                        "index": index,
                        "name": job_name,
                        "type": job_type,
                        "status": manifest.get("status", "unknown"),
                        "prompt": str(job.get("prompt") or "")[:500],
                        "workflow_preset_id": str(job_config.get("workflow_preset_id") or ""),
                        "workflow_preset_name": str(job_config.get("workflow_preset_name") or ""),
                        "endpoint": str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
                        "manifest_file": str(job_dir / "cloud_comfyui_manifest.json"),
                        "downloaded_files": job_downloaded,
                    }
                )
                self._emit(
                    f"ComfyUI 素材 {index}/{len(selected_jobs)} 完成：下载 {len(job_downloaded)} 个文件",
                    total_jobs=len(selected_jobs),
                    completed_jobs=index,
                    current_job=index,
                    job_index=index,
                    job_count=len(selected_jobs),
                    material_name=job_name,
                    material_type=job_type,
                    job_type=job_type,
                    job_status=manifest.get("status", "unknown"),
                    downloaded_count=len(job_downloaded),
                    output_file=job_downloaded[0] if job_downloaded else "",
                )
            except Exception as exc:
                error_manifest = {
                    "provider": provider,
                    "status": "failed",
                    "name": job_name,
                    "type": job_type,
                    "prompt": str(job.get("prompt") or "")[:2000],
                    "workflow_preset_id": str(job_config.get("workflow_preset_id") or ""),
                    "workflow_preset_name": str(job_config.get("workflow_preset_name") or ""),
                    "endpoint": str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
                    "error": str(exc),
                }
                self._write_json(job_dir / "cloud_comfyui_manifest.json", error_manifest)
                job_results.append(
                    {
                        "index": index,
                        "name": job_name,
                        "type": job_type,
                        "status": "failed",
                        "prompt": str(job.get("prompt") or "")[:500],
                        "workflow_preset_id": str(job_config.get("workflow_preset_id") or ""),
                        "workflow_preset_name": str(job_config.get("workflow_preset_name") or ""),
                        "endpoint": str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
                        "manifest_file": str(job_dir / "cloud_comfyui_manifest.json"),
                        "downloaded_files": [],
                        "error": str(exc),
                    }
                )
                self._emit(
                    f"ComfyUI 素材 {index}/{len(selected_jobs)} 失败：{exc}",
                    total_jobs=len(selected_jobs),
                    completed_jobs=index,
                    current_job=index,
                    job_index=index,
                    job_count=len(selected_jobs),
                    material_name=job_name,
                    material_type=job_type,
                    job_type=job_type,
                    job_status="failed",
                    error=str(exc),
                )

        failed_count = len(selected_jobs) - success_count
        if success_count == len(selected_jobs):
            status = "success"
        elif success_count > 0:
            status = "partial_success"
        else:
            status = "failed"
        manifest = {
            "provider": provider,
            "status": status,
            "looped": True,
            "job_count": len(selected_jobs),
            "source_job_count": len(material_jobs),
            "success_count": success_count,
            "failed_count": failed_count,
            "max_material_jobs": max_jobs,
            "endpoint": self.endpoint,
            "routing": "workflow_library_by_material_type" if self._uses_workflow_library(compose_config) else "selected_workflow",
            "downloaded_files": downloaded_files,
            "jobs": job_results,
            "note": "Each material prompt was submitted as a separate ComfyUI/RunningHub job.",
        }
        self._write_json(output_dir / "cloud_comfyui_manifest.json", manifest)
        self._emit(
            f"ComfyUI 素材批量完成：成功 {success_count} 个，失败 {failed_count} 个",
            total_jobs=len(selected_jobs),
            completed_jobs=len(selected_jobs),
            success_count=success_count,
            failed_count=failed_count,
            downloaded_count=len(downloaded_files),
            job_status=status,
        )
        if success_count == 0:
            raise ValueError("All ComfyUI material jobs failed")
        return manifest

    def _run_runninghub(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        payload = self._build_runninghub_payload(comfyui_payload, compose_config)
        endpoint = self._effective_endpoint(compose_config)
        self._emit(f"提交 RunningHub 请求：{endpoint}", endpoint=endpoint)
        submit_response = self._post_json(self._endpoint_url(endpoint), payload)
        submit_path = output_dir / "runninghub_comfyui_submit_response.json"
        self._write_json(submit_path, self._redact_response(submit_response))

        task_id = self._first_value(submit_response, ("taskId", "task_id"))
        if not task_id:
            raise ValueError("RunningHub ComfyUI workflow did not return taskId")
        self._emit(f"RunningHub 已返回任务 ID：{task_id}", endpoint=endpoint, task_id=task_id, remote_status=self._status(submit_response))

        query_url = urljoin(f"{self.base_url}/", "query")
        poll_interval = self._safe_int(compose_config.get("poll_interval_seconds"), default=10, minimum=2, maximum=60)
        timeout_seconds = self._safe_int(compose_config.get("poll_timeout_seconds"), default=3600, minimum=60, maximum=10800)
        deadline = time.time() + timeout_seconds
        query_response: dict[str, Any] = submit_response

        while time.time() < deadline:
            query_response = self._post_json(query_url, {"taskId": task_id})
            status = self._status(query_response)
            self._emit(f"RunningHub 任务 {task_id} 状态：{status or 'UNKNOWN'}", endpoint=endpoint, task_id=task_id, remote_status=status)
            if status == "SUCCESS":
                break
            if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
                break
            time.sleep(poll_interval)
        else:
            query_response = {
                "taskId": task_id,
                "status": "TIMEOUT",
                "errorMessage": f"RunningHub ComfyUI polling timed out after {timeout_seconds} seconds",
            }

        query_path = output_dir / "runninghub_comfyui_query_response.json"
        self._write_json(query_path, self._redact_response(query_response))

        status = self._status(query_response)
        results = self._results(query_response)
        downloaded = []
        result_items = []
        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or result.get("fileUrl") or result.get("download_url") or "").strip()
            output_type = str(result.get("outputType") or result.get("type") or "").strip().lower()
            if not output_type and url:
                output_type = Path(urlparse(url).path).suffix.lower().lstrip(".")
            item = {
                "nodeId": result.get("nodeId"),
                "outputType": output_type,
                "url": url,
                "text": result.get("text"),
            }
            if url and output_type in self.DOWNLOAD_TYPES:
                try:
                    path = self._download_file(url, output_dir, f"comfyui_result_{index:02d}", output_type)
                    item["downloaded_file"] = str(path)
                    downloaded.append(path)
                    self._emit(
                        f"RunningHub 结果下载成功：{path.name}",
                        endpoint=endpoint,
                        task_id=task_id,
                        remote_status=status,
                        output_type=output_type,
                        downloaded_file=str(path),
                        downloaded_count=len(downloaded),
                        url=url,
                    )
                except Exception as exc:
                    item["download_error"] = str(exc)
                    self._emit(
                        f"RunningHub 结果下载失败：{exc}",
                        endpoint=endpoint,
                        task_id=task_id,
                        remote_status=status,
                        output_type=output_type,
                        url=url,
                        error=str(exc),
                    )
            result_items.append(item)

        manifest = {
            "provider": "runninghub",
            "status": "success" if status == "SUCCESS" else "failed" if status != "TIMEOUT" else "timeout",
            "taskId": task_id,
            "endpoint": endpoint,
            "submit_response_file": str(submit_path),
            "query_response_file": str(query_path),
            "result_count": len(result_items),
            "downloaded_files": [str(path) for path in downloaded],
            "results": result_items,
            "note": "Result URLs may expire; downloaded files above are durable local copies.",
        }
        manifest_path = output_dir / "cloud_comfyui_manifest.json"
        self._write_json(manifest_path, manifest)
        if manifest["status"] != "success":
            message = self._first_value(query_response, ("errorMessage", "message")) or query_response.get("failedReason") or status
            raise ValueError(f"RunningHub ComfyUI workflow failed: {message}")
        return manifest

    def _run_generic(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        endpoint = self._effective_endpoint(compose_config)
        response = self._post_json(self._endpoint_url(endpoint), comfyui_payload)
        response_path = output_dir / "cloud_comfyui_response.json"
        self._write_json(response_path, self._redact_response(response))
        urls = self._find_download_urls(response)
        downloaded = [
            self._download_file(url, output_dir, f"comfyui_result_{index:02d}", "")
            for index, url in enumerate(urls, start=1)
        ]
        manifest = {
            "provider": str(compose_config.get("provider") or "generic"),
            "status": "submitted" if not downloaded else "downloaded",
            "endpoint": endpoint,
            "response_file": str(response_path),
            "download_urls": urls,
            "downloaded_files": [str(path) for path in downloaded],
        }
        self._write_json(output_dir / "cloud_comfyui_manifest.json", manifest)
        return manifest

    def _build_runninghub_payload(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any]) -> dict[str, Any]:
        node_info = self._parse_node_info_list(compose_config)
        replacements = {
            "{{payload}}": json.dumps(comfyui_payload, ensure_ascii=False),
            "{{negative_prompt}}": str(comfyui_payload.get("negative_prompt") or ""),
            "{{image_prompt}}": self._first_list_or_value(comfyui_payload, "image_prompts", "image_prompt"),
            "{{video_prompt}}": self._first_list_or_value(comfyui_payload, "video_prompts", "video_prompt"),
            "{{reference_image}}": self._first_reference_image(comfyui_payload),
            "{{has_reference_image}}": bool(self._first_reference_image(comfyui_payload)),
            "{{seed}}": str(comfyui_payload.get("seed") or ""),
            "{{width}}": str(comfyui_payload.get("width") or ""),
            "{{height}}": str(comfyui_payload.get("height") or ""),
            "{{task_type}}": str(comfyui_payload.get("task_type") or ""),
            "{{control_mode}}": str(comfyui_payload.get("control_mode") or ""),
            "{{duration}}": str(comfyui_payload.get("duration") or ""),
            "{{fps}}": str(comfyui_payload.get("fps") or ""),
            "{{denoise}}": str(comfyui_payload.get("denoise") or ""),
            "{{ipadapter_weight}}": str(comfyui_payload.get("ipadapter_weight") or ""),
            "{{reference_strength}}": str(comfyui_payload.get("reference_strength") or ""),
            "{{motion_strength}}": str(comfyui_payload.get("motion_strength") or ""),
            "{{pose_video}}": str(comfyui_payload.get("pose_video") or ""),
            "{{prompt}}": self._first_prompt(comfyui_payload),
        }
        if node_info:
            node_info = self._replace_placeholders(node_info, replacements)
            node_info = self._drop_empty_image_node_info(node_info)
        payload: dict[str, Any] = {
            "apiKey": self.api_key,
            "addMetadata": bool(compose_config.get("add_metadata", True)),
            "nodeInfoList": node_info,
            "instanceType": str(compose_config.get("instance_type") or "default").strip(),
            "usePersonalQueue": str(compose_config.get("use_personal_queue") or "false").strip().lower(),
        }
        app_id = self._app_id_from_endpoint(self._effective_endpoint(compose_config))
        if app_id:
            payload["webappId"] = app_id
        return payload

    @staticmethod
    def _drop_empty_image_node_info(node_info: list[Any]) -> list[Any]:
        cleaned: list[Any] = []
        for item in node_info:
            if (
                isinstance(item, dict)
                and str(item.get("fieldName") or "").strip().lower() == "image"
                and str(item.get("fieldValue") or "").strip() == ""
            ):
                continue
            cleaned.append(item)
        return cleaned

    def _expand_material_jobs(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any]) -> list[dict[str, Any]]:
        prompt_types = {"image", "video"} if self._uses_workflow_library(compose_config) else self._workflow_prompt_types(compose_config)
        jobs: list[dict[str, Any]] = []
        if "image" in prompt_types:
            jobs.extend(self._jobs_from_prompt_groups(comfyui_payload, "image_prompts", "image_prompt", "image"))
        if "video" in prompt_types:
            jobs.extend(self._jobs_from_prompt_groups(comfyui_payload, "video_prompts", "video_prompt", "video"))
        if not jobs:
            jobs.extend(self._jobs_from_prompt_groups(comfyui_payload, "image_prompts", "image_prompt", "image"))
            jobs.extend(self._jobs_from_prompt_groups(comfyui_payload, "video_prompts", "video_prompt", "video"))
        return jobs

    def _compose_config_for_job(self, job: dict[str, Any], compose_config: dict[str, Any]) -> dict[str, Any]:
        preset = self._workflow_library_preset_for_job(job, compose_config)
        if not preset:
            return compose_config
        job_config = dict(compose_config)
        preset_endpoint = str(preset.get("endpoint") or "").strip()
        if not self._is_usable_endpoint(preset_endpoint):
            preset_endpoint = ""
        job_config["workflow_endpoint"] = preset_endpoint or job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint
        job_config["node_info_list_json"] = str(preset.get("node_info_list_json") or preset.get("nodeInfoList") or "[]").strip() or "[]"
        job_config["poll_timeout_seconds"] = preset.get("poll_timeout_seconds") or preset.get("pollTimeout") or job_config.get("poll_timeout_seconds")
        job_config["workflow_preset_id"] = str(preset.get("id") or "").strip()
        job_config["workflow_preset_name"] = str(preset.get("name") or "").strip()
        job_config["workflow_preset_purpose"] = str(preset.get("purpose") or "").strip()
        return job_config

    @classmethod
    def _workflow_library_preset_for_job(cls, job: dict[str, Any], compose_config: dict[str, Any]) -> dict[str, Any] | None:
        library = compose_config.get("workflow_library")
        if not isinstance(library, list):
            return None
        configured = [item for item in library if cls._is_configured_library_item(item)]
        if not configured:
            return None
        job_type = str(job.get("type") or "").strip().lower()
        typed = [
            item
            for item in configured
            if cls._library_item_supports_material_type(item, job_type)
        ]
        if typed:
            configured = typed
        if job_type == "video":
            return cls._first_matching_preset(configured, ("all_in_one_video", "全能视频", "universal_video", "image_to_video", "ltx", "video", "broll", "视频", "图生视频", "生视频"))
        return cls._first_matching_preset(configured, ("all_in_one_image", "全能图片", "universal_image", "txt_img", "z_image", "image", "keyframe", "文生图", "生图", "关键帧", "配图"))

    @classmethod
    def _library_item_supports_material_type(cls, item: dict[str, Any], material_type: str) -> bool:
        raw_types = item.get("material_types") or item.get("materialTypes") or item.get("types")
        if isinstance(raw_types, str):
            types = {part.strip().lower() for part in raw_types.split(",") if part.strip()}
        elif isinstance(raw_types, list):
            types = {str(part).strip().lower() for part in raw_types if str(part).strip()}
        else:
            types = set()
        return bool(material_type and material_type in types)

    @classmethod
    def _is_configured_library_item(cls, item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        endpoint = str(item.get("endpoint") or item.get("workflow_endpoint") or "").strip()
        node_info = str(item.get("node_info_list_json") or item.get("nodeInfoList") or "").strip()
        return bool(cls._is_usable_endpoint(endpoint) and node_info and node_info != "[]")

    @classmethod
    def _first_matching_preset(cls, presets: list[dict[str, Any]], keywords: tuple[str, ...]) -> dict[str, Any] | None:
        lowered_keywords = tuple(keyword.lower() for keyword in keywords)
        for item in presets:
            text = " ".join(
                str(item.get(key) or "")
                for key in ("id", "name", "purpose")
            ).lower()
            if any(keyword in text for keyword in lowered_keywords):
                return item
        return presets[0] if presets else None

    @classmethod
    def _uses_workflow_library(cls, compose_config: dict[str, Any]) -> bool:
        library = compose_config.get("workflow_library")
        return isinstance(library, list) and any(cls._is_configured_library_item(item) for item in library)

    @staticmethod
    def _workflow_prompt_types(compose_config: dict[str, Any]) -> set[str]:
        preset_id = str(compose_config.get("workflow_preset_id") or "").strip().lower()
        preset_name = str(compose_config.get("workflow_preset_name") or "").strip().lower()
        text = f"{preset_id} {preset_name}"
        if any(key in text for key in ("03_reference", "reference_consistency", "04_broll", "broll")):
            return {"image", "video"}
        if any(key in text for key in ("all_in_one_video", "universal_video", "02_ltx_video", "image_to_video", "video", "全能视频", "图生视频", "视频")):
            return {"video"}
        if any(key in text for key in ("all_in_one_image", "universal_image", "01_image", "z_image", "txt_img", "image", "全能图片", "文生图", "生图", "素材")):
            return {"image"}
        return {"image", "video"}

    def _jobs_from_prompt_groups(
        self,
        payload: dict[str, Any],
        group_key: str,
        value_key: str,
        job_type: str,
    ) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        direct_value = payload.get(value_key)
        if isinstance(direct_value, str) and direct_value.strip():
            jobs.append(
                {
                    "type": job_type,
                    "name": value_key,
                    "prompt": direct_value.strip(),
                    "negative_prompt": str(payload.get("negative_prompt") or ""),
                    "reference_image": self._first_reference_image(payload),
                    "base_payload": payload,
                    "source": value_key,
                    "group": {},
                }
            )
        groups = payload.get(group_key)
        if not isinstance(groups, list):
            return jobs
        for group_index, group in enumerate(groups, start=1):
            if isinstance(group, str) and group.strip():
                jobs.append(
                    {
                        "type": job_type,
                        "name": f"{job_type}_{group_index:02d}",
                        "prompt": group.strip(),
                        "negative_prompt": str(payload.get("negative_prompt") or ""),
                        "reference_image": self._first_reference_image(payload),
                        "base_payload": payload,
                        "source": group_key,
                        "group": {},
                    }
                )
                continue
            if not isinstance(group, dict):
                continue
            prompts = group.get("prompts")
            if isinstance(prompts, dict):
                for name, item in prompts.items():
                    prompt_data = item if isinstance(item, dict) else {"positive": item}
                    prompt = self._prompt_from_item(prompt_data)
                    if not prompt:
                        continue
                    jobs.append(self._job_from_prompt_item(payload, group, prompt_data, str(name), job_type, group_key))
            elif isinstance(prompts, list):
                for item_index, item in enumerate(prompts, start=1):
                    prompt_data = item if isinstance(item, dict) else {"positive": item}
                    prompt = self._prompt_from_item(prompt_data)
                    if not prompt:
                        continue
                    name = str(
                        prompt_data.get("name")
                        or prompt_data.get("title")
                        or prompt_data.get("prompt_id")
                        or prompt_data.get("id")
                        or prompt_data.get("output_filename")
                        or f"{job_type}_{group_index:02d}_{item_index:02d}"
                    )
                    jobs.append(self._job_from_prompt_item(payload, group, prompt_data, name, job_type, group_key))
            else:
                prompt = self._prompt_from_item(group)
                if prompt:
                    name = str(
                        group.get("name")
                        or group.get("title")
                        or group.get("prompt_id")
                        or group.get("id")
                        or group.get("output_filename")
                        or group.get("slot")
                        or f"{job_type}_{group_index:02d}"
                    )
                    jobs.append(self._job_from_prompt_item(payload, group, group, name, job_type, group_key))
        return jobs

    @classmethod
    def _job_from_prompt_item(
        cls,
        base_payload: dict[str, Any],
        group: dict[str, Any],
        prompt_data: dict[str, Any],
        name: str,
        job_type: str,
        source: str,
    ) -> dict[str, Any]:
        prompt = cls._prompt_from_item(prompt_data)
        negative = str(
            prompt_data.get("negative")
            or prompt_data.get("negative_prompt")
            or group.get("negative_prompt")
            or base_payload.get("negative_prompt")
            or ""
        )
        reference_image = str(
            prompt_data.get("reference_image")
            or prompt_data.get("reference")
            or group.get("reference_image")
            or cls._first_reference_image(base_payload)
            or ""
        ).strip()
        return {
            "type": job_type,
            "name": name,
            "prompt": prompt,
            "negative_prompt": negative,
            "reference_image": reference_image,
            "seed": prompt_data.get("seed", group.get("seed", base_payload.get("seed", ""))),
            "width": prompt_data.get("width", group.get("width", base_payload.get("width", ""))),
            "height": prompt_data.get("height", group.get("height", base_payload.get("height", ""))),
            "base_payload": base_payload,
            "source": source,
            "group": group,
            "prompt_data": prompt_data,
        }

    @staticmethod
    def _prompt_from_item(item: dict[str, Any]) -> str:
        for key in ("positive", "prompt", "prompt_text", "text", "description", "content"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _resolve_reference_image(self, reference_image: str, generated_reference_map: dict[str, str]) -> str:
        text = str(reference_image or "").strip()
        if not text:
            return ""
        if text.startswith(("http://", "https://", "data:image/")):
            return text
        path = self._resolve_reference_path(text)
        if path:
            return str(path)
        keys = self._reference_lookup_keys(text)
        for key in keys:
            if key in generated_reference_map:
                return generated_reference_map[key]
        return ""

    def _resolve_reference_path(self, value: str) -> Path | None:
        text = str(value or "").strip()
        if not text or text.startswith(("http://", "https://", "data:image/")):
            return None
        direct = Path(text)
        if direct.is_file():
            return direct
        if direct.is_absolute():
            return None
        for base in getattr(self, "_reference_search_dirs", []) or []:
            candidate = (Path(base) / text).resolve()
            if candidate.is_file():
                return candidate
            name_candidate = (Path(base) / direct.name).resolve()
            if name_candidate.is_file():
                return name_candidate
        return None

    @classmethod
    def _reference_keys_for_job(cls, job: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        prompt_data = job.get("prompt_data") if isinstance(job.get("prompt_data"), dict) else {}
        group = job.get("group") if isinstance(job.get("group"), dict) else {}
        for value in (
            job.get("name"),
            prompt_data.get("prompt_id"),
            prompt_data.get("id"),
            prompt_data.get("name"),
            prompt_data.get("title"),
            prompt_data.get("output_filename"),
            group.get("prompt_id"),
            group.get("id"),
            group.get("name"),
            group.get("title"),
            group.get("output_filename"),
        ):
            keys.update(cls._reference_lookup_keys(value))
        return {key for key in keys if key}

    @staticmethod
    def _reference_lookup_keys(value: Any) -> set[str]:
        text = str(value or "").strip()
        if not text:
            return set()
        path = Path(text)
        stem = path.stem if path.suffix else text
        return {
            text,
            text.lower(),
            path.name,
            path.name.lower(),
            stem,
            stem.lower(),
        }

    @staticmethod
    def _payload_for_material_job(base_payload: dict[str, Any], job: dict[str, Any], index: int) -> dict[str, Any]:
        payload = json.loads(json.dumps(base_payload, ensure_ascii=False))
        prompt = str(job.get("prompt") or "").strip()
        negative = str(job.get("negative_prompt") or "").strip()
        reference_image = str(job.get("reference_image") or "").strip()
        job_type = str(job.get("type") or "material")
        prompt_data = job.get("prompt_data") if isinstance(job.get("prompt_data"), dict) else {}
        group = job.get("group") if isinstance(job.get("group"), dict) else {}
        payload["workflow_item_index"] = index
        payload["workflow_item_name"] = str(job.get("name") or f"material_{index:02d}")
        payload["workflow_item_type"] = job_type
        payload["task_type"] = str(
            prompt_data.get("task_type")
            or prompt_data.get("taskType")
            or group.get("task_type")
            or group.get("taskType")
            or (base_payload.get("video_task_type") if job_type == "video" else base_payload.get("image_task_type"))
            or ("img2video" if job_type == "video" and reference_image else "txt2video" if job_type == "video" else "img2img" if reference_image else "txt2img")
        )
        payload["control_mode"] = str(
            prompt_data.get("control_mode")
            or prompt_data.get("controlMode")
            or group.get("control_mode")
            or group.get("controlMode")
            or base_payload.get("control_mode")
            or ("first_frame" if job_type == "video" and reference_image else "reference_image" if reference_image else "none")
        )
        payload["prompt"] = prompt
        payload["negative_prompt"] = negative
        if job_type == "video":
            payload["video_prompt"] = prompt
            payload["video_prompts"] = [
                {
                    "slot": job.get("name"),
                    "prompts": {
                        str(job.get("name") or f"video_{index:02d}"): {
                            "positive": prompt,
                            "negative": negative,
                        }
                    },
                }
            ]
        else:
            payload["image_prompt"] = prompt
            payload["image_prompts"] = [
                {
                    "slot": job.get("name"),
                    "prompts": {
                        str(job.get("name") or f"image_{index:02d}"): {
                            "positive": prompt,
                            "negative": negative,
                        }
                    },
                }
            ]
        if reference_image:
            payload["reference_image"] = reference_image
        else:
            payload.pop("reference_image", None)
            payload.pop("reference_images", None)
        payload["has_reference_image"] = bool(reference_image)
        for key in ("seed", "width", "height", "duration", "fps", "denoise", "ipadapter_weight", "reference_strength", "motion_strength", "pose_video"):
            value = (
                prompt_data.get(key)
                if key in prompt_data
                else group.get(key)
                if key in group
                else job.get(key)
            )
            if value not in (None, ""):
                payload[key] = value
        return payload

    def _reference_image_value(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if text.startswith(("http://", "https://", "data:image/")):
            return text
        path = self._resolve_reference_path(text)
        if not path:
            return text
        if "runninghub" in self.base_url.lower():
            try:
                cache_key = str(path.resolve())
                if cache_key in self._media_upload_cache:
                    return self._media_upload_cache[cache_key]
                uploaded_url = self._upload_runninghub_media(path)
                self._media_upload_cache[cache_key] = uploaded_url
                return uploaded_url
            except Exception as exc:
                self._emit(f"RunningHub 参考图上传失败，回退 Base64：{exc}", error=str(exc))
        suffix = path.suffix.lower().lstrip(".")
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }.get(suffix, "image/png")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _upload_runninghub_media(self, path: Path) -> str:
        boundary = f"----agencyAgentsZh{int(time.time() * 1000)}"
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "application/octet-stream")
        file_bytes = path.read_bytes()
        head = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = head + file_bytes + tail
        url = urljoin(f"{self.base_url}/", "media/upload/binary")
        req = urllib_request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=120) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise ValueError(f"RunningHub media upload HTTP {exc.code}: {detail}") from exc
        parsed = json.loads(raw)
        data = parsed.get("data") if isinstance(parsed, dict) else {}
        url_value = ""
        if isinstance(data, dict):
            url_value = str(
                data.get("fileName")
                or data.get("file_name")
                or data.get("filename")
                or data.get("path")
                or data.get("filePath")
                or data.get("file_path")
                or data.get("download_url")
                or data.get("url")
                or ""
            ).strip()
        if not url_value and isinstance(parsed, dict):
            url_value = str(
                parsed.get("fileName")
                or parsed.get("file_name")
                or parsed.get("filename")
                or parsed.get("path")
                or parsed.get("filePath")
                or parsed.get("file_path")
                or parsed.get("download_url")
                or parsed.get("url")
                or ""
            ).strip()
        if not url_value:
            raise ValueError(f"RunningHub media upload did not return a usable file value: {raw[:300]}")
        self._emit(f"RunningHub 参考图上传成功：{path.name}", url=url_value, output_file=str(path))
        return url_value

    def _effective_endpoint(self, compose_config: dict[str, Any]) -> str:
        endpoint = str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or self.endpoint).strip()
        if self._is_usable_endpoint(endpoint):
            return endpoint
        fallback = str(self.endpoint or "").strip()
        return fallback if self._is_usable_endpoint(fallback) else endpoint

    def _endpoint_url(self, endpoint: str | None = None) -> str:
        endpoint = (endpoint or self.endpoint).strip()
        if not self._is_usable_endpoint(endpoint):
            raise ValueError("ComfyUI workflow endpoint is missing or still set to a placeholder such as /run/workflow/keep")
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return urljoin(f"{self.base_url}/", endpoint.lstrip("/"))

    @staticmethod
    def _is_usable_endpoint(endpoint: str) -> bool:
        value = str(endpoint or "").strip().lower().rstrip("/")
        if not value:
            return False
        placeholders = {"keep", "/keep", "/run/workflow/keep", "/run/ai-app/keep", "未配置", "none", "null"}
        return value not in placeholders

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=120) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise ValueError(f"ComfyUI workflow HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"ComfyUI workflow connection failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    @staticmethod
    def _app_id_from_endpoint(endpoint: str) -> str:
        match = re.search(r"/ai-app/([^/?#]+)", endpoint)
        return match.group(1) if match else ""

    @classmethod
    def _replace_placeholders(cls, value: Any, replacements: dict[str, Any]) -> Any:
        if isinstance(value, str):
            for key, replacement in replacements.items():
                if value == key:
                    return replacement
                value = value.replace(key, str(replacement).lower() if isinstance(replacement, bool) else str(replacement))
            return value
        if isinstance(value, list):
            return [cls._replace_placeholders(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: cls._replace_placeholders(item, replacements) for key, item in value.items()}
        return value

    @staticmethod
    def _parse_node_info_list(compose_config: dict[str, Any]) -> list[Any]:
        raw = compose_config.get("node_info_list")
        if isinstance(raw, list):
            return raw
        text = str(raw or compose_config.get("node_info_list_json") or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("ComfyUI nodeInfoList must be a JSON array")
        return data

    @classmethod
    def _find_download_urls(cls, data: Any) -> list[str]:
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if re.search(r"https?://\S+\.(?:mp4|mov|webm|m4v|mp3|wav|aac|png|jpe?g|webp)(?:\?\S*)?$", value, flags=re.IGNORECASE):
                    found.append(value)
                return
            if isinstance(value, dict):
                for child in value.values():
                    walk(child)
                return
            if isinstance(value, list):
                for child in value:
                    walk(child)

        walk(data)
        deduped = []
        for url in found:
            if url not in deduped:
                deduped.append(url)
        return deduped[:30]

    @staticmethod
    def _download_file(url: str, output_dir: Path, stem: str, output_type: str) -> Path:
        suffix = f".{output_type.lstrip('.')}" if output_type else Path(urlparse(url).path).suffix.lower()
        if suffix.lower().lstrip(".") not in CloudComfyUIAdapter.DOWNLOAD_TYPES:
            suffix = ".mp4"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-comfyui-adapter/1.0"})
        with urllib_request.urlopen(req, timeout=300) as response:
            target.write_bytes(response.read())
        return target

    @staticmethod
    def _first_prompt(payload: dict[str, Any]) -> str:
        for key in ("video_prompt", "image_prompt", "prompt"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:8000]
        prompts = payload.get("video_prompts") or payload.get("image_prompts")
        if isinstance(prompts, list) and prompts:
            return json.dumps(prompts, ensure_ascii=False)[:8000]
        return json.dumps(payload, ensure_ascii=False)[:8000]

    @staticmethod
    def _first_list_or_value(payload: dict[str, Any], list_key: str, value_key: str) -> str:
        value = payload.get(value_key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:8000]
        values = payload.get(list_key)
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, str):
                return first.strip()[:8000]
            return json.dumps(first, ensure_ascii=False)[:8000]
        return ""

    @staticmethod
    def _first_reference_image(payload: dict[str, Any]) -> str:
        value = payload.get("reference_image")
        if isinstance(value, str) and value.strip():
            return value.strip()
        values = payload.get("reference_images")
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict):
                for key in ("url", "path", "file", "image"):
                    item = first.get(key)
                    if isinstance(item, str) and item.strip():
                        return item.strip()
        return ""

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        return str(CloudComfyUIAdapter._first_value(data, ("status",)) or "").upper()

    @staticmethod
    def _results(data: dict[str, Any]) -> list[Any]:
        results = data.get("results")
        if isinstance(results, list):
            return results
        nested = data.get("data")
        if isinstance(nested, dict) and isinstance(nested.get("results"), list):
            return nested["results"]
        return []

    @staticmethod
    def _first_value(data: Any, keys: tuple[str, ...]) -> str:
        if isinstance(data, dict):
            for key in keys:
                value = data.get(key)
                if value not in (None, ""):
                    return str(value).strip()
            nested = data.get("data")
            if isinstance(nested, dict):
                return CloudComfyUIAdapter._first_value(nested, keys)
        return ""

    @staticmethod
    def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return min(max(parsed, minimum), maximum)

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "enabled", "启用", "是"}

    @staticmethod
    def _safe_name(value: str) -> str:
        text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE).strip("._")
        return (text or "material")[:80]

    @staticmethod
    def _redact_response(data: Any) -> Any:
        if isinstance(data, dict):
            return {key: ("***" if "key" in key.lower() else CloudComfyUIAdapter._redact_response(value)) for key, value in data.items()}
        if isinstance(data, list):
            return [CloudComfyUIAdapter._redact_response(value) for value in data]
        return data

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
