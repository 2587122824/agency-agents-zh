from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageOps

from .production_graph import artifact_record, build_production_graph, read_json, stable_job_hash, write_json
from .production_parameter_policy import apply_locked_parameters_to_payload

WORKFLOW_ID_ALIASES = {
    "10_broll_transition": "10_broll_transition_video",
}


class CloudComfyUIAdapter:
    """Call a cloud ComfyUI final-production workflow and persist returned assets."""

    MAX_RESPONSE_BYTES = 4_000_000
    DOWNLOAD_TYPES = {"mp4", "mov", "webm", "m4v", "mp3", "wav", "aac", "png", "jpg", "jpeg", "webp"}
    NETWORK_RETRY_ATTEMPTS = 3

    def __init__(self, base_url: str, api_key: str, endpoint: str, progress_callback=None) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        self.progress_callback = progress_callback
        self._media_upload_cache: dict[str, str] = {}
        if not self.base_url:
            raise ValueError("ComfyUI base URL is required")
        parsed = urlparse(self.base_url)
        local_base = parsed.hostname in {"127.0.0.1", "localhost"} or self.base_url.lower().startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost"))
        if not self.api_key and not local_base:
            raise ValueError("ComfyUI API key is required")
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
            result = self._run_runninghub(comfyui_payload, compose_config, output_dir)
        else:
            result = self._run_generic(comfyui_payload, compose_config, output_dir)
        return self._maybe_append_turnaround_sheet(comfyui_payload, compose_config, output_dir, result)

    def _provider(self, compose_config: dict[str, Any]) -> str:
        provider = str(compose_config.get("visual_provider") or compose_config.get("provider") or "").strip().lower()
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
        selected_jobs = self._prepare_graph_jobs(material_jobs[:max_jobs])
        packaging_jobs = [dict(item) for item in (compose_config.get("packaging_jobs") or []) if isinstance(item, dict)]
        visual_job_ids = [str(item.get("job_id") or "") for item in selected_jobs]
        for item in packaging_jobs:
            if item.get("depends_on_visual"):
                item["depends_on"] = list(dict.fromkeys([*self._string_list(item.get("depends_on")), *visual_job_ids]))
        global_context = compose_config.get("global_context") if isinstance(compose_config.get("global_context"), dict) else {}
        global_context = self._enrich_global_context(global_context, selected_jobs)
        graph_jobs = compose_config.get("production_plan_visual_jobs") if isinstance(compose_config.get("production_plan_visual_jobs"), list) else selected_jobs
        graph = build_production_graph(
            str(compose_config.get("production_task_id") or output_dir.parent.name),
            graph_jobs,
            global_context,
            packaging_jobs,
        )
        graph_path = Path(str(compose_config.get("production_graph_path") or output_dir.parent / "production_graph.json"))
        write_json(graph_path, graph)
        state_path = output_dir / "production_job_state.json"
        job_state = read_json(state_path)
        job_state.setdefault("schema_version", 1)
        job_state.setdefault("task_id", graph.get("task_id") or output_dir.parent.name)
        job_state.setdefault("jobs", {})
        job_state.setdefault("artifacts", [])
        job_results = []
        downloaded_files = []
        generated_reference_images: list[str] = []
        generated_reference_map: dict[str, str] = {}
        video_job_index = 0
        success_count = 0
        skipped_count = 0
        self._reference_search_dirs = [output_dir, output_dir.parent]
        selected_jobs = self._topological_material_jobs(selected_jobs)
        self._emit(f"ComfyUI 素材批量任务开始：{len(selected_jobs)} 个", total_jobs=len(selected_jobs), completed_jobs=0)

        for index, job in enumerate(selected_jobs, start=1):
            job = dict(job)
            job_id = str(job.get("job_id") or f"material_{index:03d}")
            job_name = str(job.get("name") or f"material_{index:02d}")
            job_type = str(job.get("type") or "material")
            job_dir = output_dir / f"job_{self._safe_name(job_id)}"
            job_dir.mkdir(parents=True, exist_ok=True)
            try:
                job_config = self._compose_config_for_job(job, compose_config)
            except ValueError as exc:
                if not self._is_optional_when_unconfigured(job):
                    raise
                skipped_count += 1
                skipped = {
                    "index": index,
                    "job_id": job_id,
                    "name": job_name,
                    "type": job_type,
                    "status": "skipped",
                    "depends_on": self._string_list(job.get("depends_on")),
                    "cache_hit": False,
                    "skip_reason": str(exc),
                    "downloaded_files": [],
                }
                job_results.append(skipped)
                job_state["jobs"][job_id] = {**skipped, "updated_at": time.time()}
                write_json(state_path, job_state)
                self._write_json(job_dir / "cloud_comfyui_manifest.json", {"provider": provider, **skipped})
                self._emit(
                    f"跳过可选 ComfyUI 素材：{job_name}",
                    total_jobs=len(selected_jobs),
                    completed_jobs=index,
                    current_job=index,
                    job_index=index,
                    job_count=len(selected_jobs),
                    material_name=job_name,
                    material_type=job_type,
                    job_type=job_type,
                    job_status="skipped",
                    skip_reason=str(exc),
                )
                continue
            job, resolved_inputs, missing_inputs = self._apply_explicit_input_bindings(job, job_state)
            runninghub_resume_key = stable_job_hash(job, job_config, resolved_inputs)
            failed_dependencies = [
                dependency
                for dependency in self._string_list(job.get("depends_on"))
                if str((job_state.get("jobs") or {}).get(dependency, {}).get("status") or "") not in {"success", "cached", "downloaded", "submitted"}
                and dependency != "local_tts"
            ]
            if missing_inputs or failed_dependencies:
                reason = "; ".join(
                    [
                        *(f"missing input: {item}" for item in missing_inputs),
                        *(f"dependency not completed: {item}" for item in failed_dependencies),
                    ]
                )
                blocked = {
                    "index": index,
                    "job_id": job_id,
                    "name": job_name,
                    "type": job_type,
                    "status": "blocked",
                    "depends_on": self._string_list(job.get("depends_on")),
                    "cache_hit": False,
                    "error": reason,
                    "downloaded_files": [],
                }
                job_results.append(blocked)
                job_state["jobs"][job_id] = {**blocked, "updated_at": time.time()}
                write_json(state_path, job_state)
                continue
            explicit_graph_inputs = bool(job.get("depends_on") or job.get("input_bindings"))
            if job_type in {"image", "video"}:
                reference_images = self._reference_images(job)
                resolved_references = [self._resolve_reference_image(ref, generated_reference_map) or ref for ref in reference_images]
                resolved_references = [ref for ref in resolved_references if str(ref or "").strip()]
                if resolved_references:
                    uploaded_references = [self._reference_image_value(ref) for ref in resolved_references]
                    job["reference_images"] = uploaded_references
                    job["reference_image"] = uploaded_references[0]
                    if job.get("middle_frame_image") and len(uploaded_references) > 1:
                        job["middle_frame_image"] = uploaded_references[1]
                    if job.get("last_frame_image") and len(uploaded_references) > 1:
                        job["last_frame_image"] = uploaded_references[-1]
                    self._emit(
                        f"已为{job_type}素材 {index}/{len(selected_jobs)} 解析 {len(uploaded_references)} 张参考图",
                        total_jobs=len(selected_jobs),
                        completed_jobs=index - 1,
                        current_job=index,
                        job_index=index,
                        job_count=len(selected_jobs),
                        material_name=job_name,
                        material_type=job_type,
                        job_type=job_type,
                    )
                elif not explicit_graph_inputs and job_type == "video" and generated_reference_images:
                    paired_index = min(video_job_index, len(generated_reference_images) - 1)
                    job["reference_image"] = self._reference_image_value(generated_reference_images[paired_index])
                    job["reference_images"] = [job["reference_image"]]
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
                elif not explicit_graph_inputs and job_type == "image" and generated_reference_images:
                    previous_index = len(generated_reference_images) - 1
                    job["reference_image"] = self._reference_image_value(generated_reference_images[previous_index])
                    job["reference_images"] = [job["reference_image"]]
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
                    if reference_images:
                        self._emit(
                            f"忽略未解析的参考图：{', '.join(reference_images[:3])}",
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
                    job["reference_images"] = []
                if job_type == "video":
                    video_job_index += 1
            job_payload = self._payload_for_material_job(job["base_payload"], job, index)
            self._write_json(job_dir / "comfyui_payload.json", job_payload)
            input_hash = stable_job_hash(job_payload, job_config, resolved_inputs)
            job_config = dict(job_config)
            job_config["runninghub_resume_key"] = runninghub_resume_key
            cached_state = (job_state.get("jobs") or {}).get(job_id, {})
            cached_files = [str(path) for path in cached_state.get("downloaded_files", []) if Path(str(path)).is_file()]
            force_retry_ids = {
                str(value)
                for value in (compose_config.get("force_retry_job_ids") or [])
                if str(value)
            }
            force_retry = (
                str(compose_config.get("force_retry_job_id") or "") == job_id
                or job_id in force_retry_ids
            )
            current_endpoint = str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint)
            current_preset_id = str(job_config.get("workflow_preset_id") or "")
            cached_endpoint = str(cached_state.get("endpoint") or "")
            cached_preset_id = str(cached_state.get("workflow_preset_id") or "")
            cache_matches_route = (
                str(cached_state.get("type") or "") == job_type
                and bool(cached_preset_id)
                and cached_preset_id == current_preset_id
                and bool(cached_endpoint)
                and cached_endpoint == current_endpoint
            )
            reuse_existing_image_material = (
                self._as_bool(compose_config.get("reuse_existing_image_materials"), default=False)
                and job_type == "image"
                and str(cached_state.get("type") or "") == "image"
            )
            if (
                not force_retry
                and cached_state.get("status") in {"success", "cached", "downloaded", "submitted"}
                and (
                    (
                        cached_state.get("input_hash") == input_hash
                        and cache_matches_route
                    )
                    or reuse_existing_image_material
                )
                and cached_files
            ):
                cached_manifest = self._maybe_append_turnaround_sheet(
                    job_payload,
                    job_config,
                    job_dir,
                    {"status": "cached", "downloaded_files": cached_files},
                )
                cached_files = [str(path) for path in cached_manifest.get("downloaded_files", []) if Path(str(path)).is_file()]
                success_count += 1
                downloaded_files.extend(cached_files)
                if job_type == "image":
                    self._register_generated_images(job, cached_files, generated_reference_images, generated_reference_map)
                cached_result = {
                    "index": index,
                    "job_id": job_id,
                    "name": job_name,
                    "type": job_type,
                    "status": "cached",
                    "depends_on": self._string_list(job.get("depends_on")),
                    "cache_hit": True,
                    "attempts": int(cached_state.get("attempts") or 1),
                    "input_hash": input_hash,
                    "downloaded_files": cached_files,
                }
                job_results.append(cached_result)
                job_state["jobs"][job_id] = {**cached_state, **cached_result, "updated_at": time.time()}
                write_json(state_path, job_state)
                self._emit(
                    f"复用 ComfyUI 节点缓存：{job_name}",
                    job_id=job_id,
                    job_status="cached",
                    cache_hit=True,
                    completed_jobs=index,
                    total_jobs=len(selected_jobs),
                )
                continue
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
                manifest, attempts = self._run_job_with_retries(provider, job_payload, job_config, job_dir, job_id)
                job_downloaded = [str(path) for path in manifest.get("downloaded_files", [])]
                if manifest.get("status") in {"success", "downloaded", "submitted"}:
                    success_count += 1
                downloaded_files.extend(job_downloaded)
                if job_type == "image":
                    self._register_generated_images(job, job_downloaded, generated_reference_images, generated_reference_map)
                result_item = {
                        "index": index,
                        "job_id": job_id,
                        "name": job_name,
                        "type": job_type,
                        "status": manifest.get("status", "unknown"),
                        "depends_on": self._string_list(job.get("depends_on")),
                        "cache_hit": False,
                        "attempts": attempts,
                        "input_hash": input_hash,
                        "prompt": str(job.get("prompt") or "")[:500],
                        "workflow_preset_id": str(job_config.get("workflow_preset_id") or ""),
                        "workflow_preset_name": str(job_config.get("workflow_preset_name") or ""),
                        "endpoint": str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
                        "manifest_file": str(job_dir / "cloud_comfyui_manifest.json"),
                        "downloaded_files": job_downloaded,
                    }
                job_results.append(result_item)
                state_item = {**result_item, "updated_at": time.time(), "artifacts": []}
                for output_name, path in self._output_files_for_job(job, job_downloaded).items():
                    record = artifact_record(path, str(job_state.get("task_id") or ""), job_id, output_name, str(job.get("name") or ""))
                    state_item["artifacts"].append(record)
                    job_state["artifacts"].append(record)
                job_state["jobs"][job_id] = state_item
                write_json(state_path, job_state)
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
                failed_item = {
                        "index": index,
                        "job_id": job_id,
                        "name": job_name,
                        "type": job_type,
                        "status": "failed",
                        "depends_on": self._string_list(job.get("depends_on")),
                        "cache_hit": False,
                        "attempts": int((job_state.get("jobs") or {}).get(job_id, {}).get("attempts") or 1),
                        "input_hash": input_hash,
                        "prompt": str(job.get("prompt") or "")[:500],
                        "workflow_preset_id": str(job_config.get("workflow_preset_id") or ""),
                        "workflow_preset_name": str(job_config.get("workflow_preset_name") or ""),
                        "endpoint": str(job_config.get("workflow_endpoint") or job_config.get("endpoint") or self.endpoint),
                        "manifest_file": str(job_dir / "cloud_comfyui_manifest.json"),
                        "downloaded_files": [],
                        "error": str(exc),
                        "error_category": self._error_category(exc),
                    }
                job_results.append(failed_item)
                job_state["jobs"][job_id] = {**failed_item, "updated_at": time.time()}
                write_json(state_path, job_state)
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

        required_job_count = max(0, len(selected_jobs) - skipped_count)
        failed_count = required_job_count - success_count
        if required_job_count == 0:
            status = "skipped"
        elif success_count == required_job_count:
            status = "success"
        elif success_count > 0:
            status = "partial_success"
        else:
            status = "failed"
        manifest = {
            "provider": provider,
            "status": status,
            "looped": True,
            "job_count": required_job_count,
            "source_job_count": len(material_jobs),
            "skipped_count": skipped_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "max_material_jobs": max_jobs,
            "endpoint": self.endpoint,
            "routing": "workflow_library_by_material_type" if self._uses_workflow_library(compose_config) else "selected_workflow",
            "downloaded_files": downloaded_files,
            "jobs": job_results,
            "production_graph": str(graph_path),
            "job_state_file": str(state_path),
            "artifacts": job_state.get("artifacts", []),
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
        return manifest

    @staticmethod
    def _is_optional_when_unconfigured(job: dict[str, Any]) -> bool:
        if CloudComfyUIAdapter._as_bool(job.get("optional_when_unconfigured"), default=False):
            return True
        prompt_data = job.get("prompt_data") if isinstance(job.get("prompt_data"), dict) else {}
        text = " ".join(
            str(value or "").strip()
            for value in (
                job.get("workflow_id"),
                job.get("workflow_mode"),
                job.get("mode"),
                job.get("intent"),
                job.get("asset_tag"),
                job.get("capability"),
                prompt_data.get("workflow_mode"),
                prompt_data.get("capability"),
            )
        ).lower()
        return (
            "enhance_video" in text
            or "video_enhance" in text
            or "cover_key_visual" in text
            or "generate_cover_key_visual" in text
        )

    def _run_runninghub(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        comfyui_payload = self._prepare_runninghub_payload(comfyui_payload)
        payload = self._build_runninghub_payload(comfyui_payload, compose_config)
        endpoint = self._effective_endpoint(compose_config)
        request_path = output_dir / "runninghub_comfyui_request_payload.json"
        self._write_json(request_path, self._redact_response(payload))
        remote_state_path = output_dir / "runninghub_task_state.json"
        resume_key = str(compose_config.get("runninghub_resume_key") or "").strip()
        request_hash = hashlib.sha256(
            json.dumps(
                {"endpoint": endpoint, "resume_key": resume_key, "payload": ({} if resume_key else payload)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        remote_state = read_json(remote_state_path)
        saved_status = str(remote_state.get("status") or "").upper()
        saved_task_id = str(remote_state.get("task_id") or remote_state.get("taskId") or "").strip()
        resume_remote = bool(
            saved_task_id
            and remote_state.get("request_hash") == request_hash
            and saved_status not in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}
        )
        if resume_remote:
            submit_response = read_json(output_dir / "runninghub_comfyui_submit_response.json")
        else:
            self._emit(f"提交 RunningHub 请求：{endpoint}", endpoint=endpoint)
            submit_response = self._post_json(self._endpoint_url(endpoint), payload)
        if resume_remote and not submit_response:
            submit_response = {"taskId": saved_task_id, "status": saved_status or "RUNNING"}
        submit_path = output_dir / "runninghub_comfyui_submit_response.json"
        if not resume_remote:
            self._write_json(submit_path, self._redact_response(submit_response))

        task_id = self._first_value(submit_response, ("taskId", "task_id"))
        if not task_id:
            message = self._runninghub_error_message(submit_response) or "RunningHub ComfyUI workflow did not return taskId"
            raise ValueError(message)
        self._emit(f"RunningHub 已返回任务 ID：{task_id}", endpoint=endpoint, task_id=task_id, remote_status=self._status(submit_response))

        remote_state.update(
            {
                "schema_version": 1,
                "provider": "runninghub",
                "task_id": task_id,
                "endpoint": endpoint,
                "request_hash": request_hash,
                "status": saved_status if resume_remote else (self._status(submit_response) or "SUBMITTED"),
                "resumed": resume_remote,
                "submitted_at": remote_state.get("submitted_at") or time.time(),
                "updated_at": time.time(),
            }
        )
        self._write_json(remote_state_path, remote_state)
        if resume_remote:
            self._emit(
                "Resuming persisted RunningHub task",
                endpoint=endpoint,
                task_id=task_id,
                remote_status=saved_status or "RUNNING",
                resumed=True,
            )

        query_url = urljoin(f"{self.base_url}/", "query")
        poll_interval = self._safe_int(compose_config.get("poll_interval_seconds"), default=10, minimum=2, maximum=60)
        timeout_seconds = self._safe_int(compose_config.get("poll_timeout_seconds"), default=3600, minimum=60, maximum=10800)
        deadline = time.time() + timeout_seconds
        query_response: dict[str, Any] = submit_response

        while time.time() < deadline:
            try:
                query_response = self._post_json(query_url, {"taskId": task_id})
            except ValueError as exc:
                if not self._is_connection_error(exc):
                    raise
                query_response = {
                    "taskId": task_id,
                    "status": "RUNNING",
                    "transientError": str(exc),
                }
                self._emit(
                    f"RunningHub 查询暂时失败，继续等待：{exc}",
                    endpoint=endpoint,
                    task_id=task_id,
                    remote_status="RUNNING",
                    error=str(exc),
                )
                time.sleep(poll_interval)
                continue
            status = self._status(query_response)
            remote_state.update({"status": status or "UNKNOWN", "updated_at": time.time()})
            self._write_json(remote_state_path, remote_state)
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
            "request_payload_file": str(request_path),
            "submit_response_file": str(submit_path),
            "query_response_file": str(query_path),
            "remote_task_state_file": str(remote_state_path),
            "resumed_remote_task": resume_remote,
            "result_count": len(result_items),
            "downloaded_files": [str(path) for path in downloaded],
            "results": result_items,
            "note": "Result URLs may expire; downloaded files above are durable local copies.",
        }
        manifest_path = output_dir / "cloud_comfyui_manifest.json"
        self._write_json(manifest_path, manifest)
        remote_state.update(
            {
                "status": status or manifest["status"].upper(),
                "updated_at": time.time(),
                "downloaded_files": [str(path) for path in downloaded],
                "manifest_file": str(manifest_path),
            }
        )
        self._write_json(remote_state_path, remote_state)
        if manifest["status"] != "success":
            message = self._runninghub_error_message(query_response) or status
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
            "provider": str(compose_config.get("visual_provider") or compose_config.get("provider") or "generic"),
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
        seed_value = self._seed_value(comfyui_payload)
        global_context = comfyui_payload.get("global_context") if isinstance(comfyui_payload.get("global_context"), dict) else {}
        render_context = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
        style_context = global_context.get("style") if isinstance(global_context.get("style"), dict) else {}
        base_image = str(comfyui_payload.get("input_base_image") or self._first_reference_image(comfyui_payload) or "")
        identity_image = str(comfyui_payload.get("input_identity_image") or comfyui_payload.get("identity_image") or self._first_reference_image(comfyui_payload) or "")
        pose_image = str(comfyui_payload.get("input_pose_image") or comfyui_payload.get("pose_image") or "")
        source_video = str(comfyui_payload.get("input_source_video") or comfyui_payload.get("source_video") or "")
        middle_frame = str(comfyui_payload.get("input_middle_frame") or self._middle_frame_image(comfyui_payload) or "")
        last_frame = str(comfyui_payload.get("input_last_frame") or self._last_frame_image(comfyui_payload) or "")
        replacements = {
            "{{payload}}": json.dumps(comfyui_payload, ensure_ascii=False),
            "{{negative_prompt}}": str(comfyui_payload.get("negative_prompt") or ""),
            "{{image_prompt}}": self._first_list_or_value(comfyui_payload, "image_prompts", "image_prompt"),
            "{{video_prompt}}": self._first_list_or_value(comfyui_payload, "video_prompts", "video_prompt"),
            "{{reference_image}}": self._first_reference_image(comfyui_payload),
            "{{input_base_image}}": base_image,
            "{{input_identity_image}}": identity_image,
            "{{identity_image}}": identity_image,
            "{{input_pose_image}}": pose_image,
            "{{pose_image}}": pose_image,
            "{{input_source_video}}": source_video,
            "{{source_video}}": source_video,
            "{{has_reference_image}}": bool(self._first_reference_image(comfyui_payload)),
            "{{middle_frame_image}}": self._middle_frame_image(comfyui_payload),
            "{{input_middle_frame}}": middle_frame,
            "{{has_middle_frame_image}}": bool(self._middle_frame_image(comfyui_payload)),
            "{{last_frame_image}}": self._last_frame_image(comfyui_payload),
            "{{input_last_frame}}": last_frame,
            "{{input_mask_image}}": str(comfyui_payload.get("input_mask_image") or comfyui_payload.get("mask_image") or ""),
            "{{input_reference_style}}": str(comfyui_payload.get("input_reference_style") or comfyui_payload.get("reference_style") or style_context.get("reference_asset") or ""),
            "{{input_audio_file}}": str(comfyui_payload.get("input_audio_file") or comfyui_payload.get("audio_file") or ""),
            "{{has_last_frame_image}}": bool(self._last_frame_image(comfyui_payload)),
            "{{seed}}": seed_value,
            "{{job_id}}": str(comfyui_payload.get("job_id") or comfyui_payload.get("id") or comfyui_payload.get("name") or ""),
            "{{width}}": str(comfyui_payload.get("width") or ""),
            "{{height}}": str(comfyui_payload.get("height") or ""),
            "{{task_type}}": str(comfyui_payload.get("task_type") or ""),
            "{{image_task_mode}}": str(comfyui_payload.get("image_task_mode") or ""),
            "{{control_mode}}": str(comfyui_payload.get("control_mode") or ""),
            "{{duration}}": str(comfyui_payload.get("duration") or ""),
            "{{fps}}": str(comfyui_payload.get("fps") or ""),
            "{{steps}}": comfyui_payload.get("steps") or 10,
            "{{global_character_id}}": str(comfyui_payload.get("character_id") or ""),
            "{{product_id}}": str(comfyui_payload.get("product_id") or ""),
            "{{scene_id}}": str(comfyui_payload.get("scene_id") or ""),
            "{{entity_context}}": json.dumps(comfyui_payload.get("entity_context") if isinstance(comfyui_payload.get("entity_context"), dict) else {}, ensure_ascii=False),
            "{{global_style_weight}}": str(comfyui_payload.get("global_style_weight") or style_context.get("weight") or ""),
            "{{working_width}}": str(render_context.get("working_width") or comfyui_payload.get("width") or ""),
            "{{working_height}}": str(render_context.get("working_height") or comfyui_payload.get("height") or ""),
            "{{delivery_width}}": str(render_context.get("delivery_width") or comfyui_payload.get("delivery_width") or ""),
            "{{delivery_height}}": str(render_context.get("delivery_height") or comfyui_payload.get("delivery_height") or ""),
            "{{global_frame_rate}}": str(render_context.get("frame_rate") or comfyui_payload.get("fps") or ""),
            "{{camera_motion}}": str(comfyui_payload.get("camera_motion") or ""),
            "{{frame_count}}": self._frame_count(comfyui_payload),
            "{{middle_frame_index}}": self._middle_frame_index(comfyui_payload),
            "{{last_frame_index}}": self._last_frame_index(comfyui_payload),
            "{{ltx_guide_frame_count}}": self._ltx_guide_frame_count(comfyui_payload),
            "{{denoise}}": str(comfyui_payload.get("denoise") or ""),
            "{{ipadapter_weight}}": str(comfyui_payload.get("ipadapter_weight") or ""),
            "{{reference_strength}}": str(comfyui_payload.get("reference_strength") or ""),
            "{{motion_strength}}": str(comfyui_payload.get("motion_strength") or ""),
            "{{pose_video}}": str(comfyui_payload.get("pose_video") or ""),
            "{{prompt}}": self._first_prompt(comfyui_payload),
        }
        reference_images = self._reference_image_list_values(comfyui_payload)
        for index in range(1, 5):
            reference_value = reference_images[index - 1] if len(reference_images) >= index else ""
            replacements[f"{{{{reference_image_{index}}}}}"] = reference_value
            replacements[f"{{{{has_reference_image_{index}}}}}"] = bool(reference_value)
        if node_info:
            node_info = self._replace_placeholders(node_info, replacements)
            node_info = self._override_dimension_node_info(node_info, comfyui_payload)
            node_info = self._normalize_numeric_node_info(node_info, seed_value)
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

    @classmethod
    def _seed_value(cls, payload: dict[str, Any]) -> int:
        seed = cls._clean_int_value(payload.get("seed"), minimum=0)
        if seed is not None:
            return seed
        seed = cls._clean_int_value(payload.get("noise_seed"), minimum=0)
        if seed is not None:
            return seed
        return int(time.time() * 1000) % 2_147_483_647

    def _prepare_runninghub_payload(self, comfyui_payload: dict[str, Any]) -> dict[str, Any]:
        payload = json.loads(json.dumps(comfyui_payload, ensure_ascii=False))
        if not self._looks_like_video_payload(payload):
            return payload
        prompt = self._runninghub_safe_video_prompt(str(payload.get("prompt") or self._first_prompt(payload) or ""))
        negative = self._runninghub_safe_video_negative(str(payload.get("negative_prompt") or ""))
        payload["prompt"] = prompt
        payload["negative_prompt"] = negative
        payload["video_prompt"] = prompt
        prompts = payload.get("video_prompts")
        if isinstance(prompts, list):
            for group in prompts:
                self._rewrite_prompt_container(group, prompt, negative)
        return payload

    @staticmethod
    def _looks_like_video_payload(payload: dict[str, Any]) -> bool:
        if str(payload.get("video_prompt") or "").strip():
            return True
        if str(payload.get("video_task_mode") or "").strip():
            return True
        type_values = (
            payload.get("workflow_item_type"),
            payload.get("video_task_type"),
            payload.get("task_type"),
        )
        if payload.get("video_prompts"):
            return True
        return any(str(value or "").strip().lower() in {"video", "img2video", "txt2video", "first_last_frame_video", "first_middle_last_frame_video"} for value in type_values)

    @classmethod
    def _rewrite_prompt_container(cls, value: Any, prompt: str, negative: str) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                lowered = str(key).strip().lower()
                if lowered in {"positive", "prompt", "video_prompt", "text"} and isinstance(item, str):
                    value[key] = prompt
                elif lowered in {"negative", "negative_prompt"} and isinstance(item, str):
                    value[key] = negative
                else:
                    cls._rewrite_prompt_container(item, prompt, negative)
        elif isinstance(value, list):
            for item in value:
                cls._rewrite_prompt_container(item, prompt, negative)

    @classmethod
    def _runninghub_safe_video_prompt(cls, prompt: str) -> str:
        text = str(prompt or "").strip()
        text = re.sub(
            r"(?i)^safe non-graphic anime sci-fi video,\s*fully clothed subjects,.*?no hate content,\s*",
            "",
            text,
        )
        text = re.sub(
            r"(?i)^platform-safe non-graphic anime sci-fi video,\s*fully clothed subjects,\s*clean synthetic surfaces,\s*family-safe action tone,\s*",
            "",
            text,
        )
        replacements = (
            (r"(?i)extreme close-up macro shot of human nape and upper neck", "clinical sci-fi close-up of an external neural interface collar on a synthetic mannequin subject"),
            (r"(?i)close-up macro shot of human nape and upper neck", "clinical sci-fi close-up of an external neural interface collar"),
            (r"(?i)\bnape\b", "external collar area"),
            (r"(?i)\bupper neck\b", "external collar area"),
            (r"(?i)\bpale skin\b", "smooth synthetic surface"),
            (r"(?i)smooth synthetic surface separated by precise robotic robotic calibration arm", "external collar module adjusted by a robotic calibration arm"),
            (r"(?i)smooth synthetic surface separated by precise robotic calibration arm", "external collar module adjusted by a robotic calibration arm"),
            (r"(?i)skin separated by precise robotic surgical arm", "external collar module adjusted by a robotic calibration arm"),
            (r"(?i)\bskin separated\b", "external module opened"),
            (r"(?i)\bskin shows pain response\b", "subject remains calm and expressionless"),
            (r"(?i)\bpain response\b", "calm response"),
            (r"(?i)\bspinal area\b", "back-mounted interface panel"),
            (r"(?i)\bsurgical arm\b", "robotic calibration arm"),
            (r"(?i)\bsurgery\b", "clinical calibration"),
            (r"(?i)\bsurgical\b", "clinical"),
            (r"(?i)\binserted into\b", "attached onto"),
            (r"(?i)\bskin pores\b", "fine material detail"),
            (r"(?i)\bwound(s)?\b", "surface mark"),
            (r"(?i)\bblood\b", "red warning light"),
            (r"(?i)\bgore\b", "non-graphic detail"),
            (r"(?i)\bnude|nudity|erotic|sexual\b", "fully clothed non-sexual"),
            (r"(?i)\bmake-shift weapon\b", "improvised signal tool"),
            (r"(?i)\bweapon(s)?\b", "equipment"),
            (r"(?i)\bEMP disruptor\b", "blue signal device"),
            (r"(?i)\bEMP cannon\b", "blue signal projector"),
            (r"(?i)\brebels\b", "resistance team"),
            (r"(?i)\brebel\b", "resistance member"),
            (r"(?i)\bmilitary formation\b", "robot patrol formation"),
            (r"(?i)\bterrorism|terrorist(s)?\b", "public safety threat"),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text)
        text = re.sub(r"(?i)\brobot robot patrol formation\b", "robot patrol formation", text)
        text = re.sub(r"(?i)\bblue signal device device\b", "blue signal device", text)
        text = re.sub(r"(?i)\brobotic robotic calibration arm\b", "robotic calibration arm", text)
        text = re.sub(r"\s+", " ", text).strip(" ,")
        safety_prefix = (
            "platform-safe non-graphic video, fully clothed subjects, "
            "family-safe action tone, "
        )
        if not text.lower().startswith("platform-safe non-graphic"):
            text = safety_prefix + text
        return text

    @classmethod
    def _runninghub_safe_video_negative(cls, negative: str) -> str:
        base_terms = [
            "nudity",
            "sexual content",
            "erotic",
            "exposed skin",
            "wound",
            "blood",
            "gore",
            "surgery",
            "injury",
            "pain",
            "graphic violence",
            "weapon",
            "terrorism",
            "hate content",
            "offensive content",
            "unsafe content",
            "distorted body",
            "distorted face",
            "flicker",
            "low quality",
        ]
        existing = [part.strip() for part in str(negative or "").split(",") if part.strip()]
        seen = {part.lower() for part in existing}
        for term in base_terms:
            if term.lower() not in seen:
                existing.append(term)
                seen.add(term.lower())
        return ", ".join(existing)

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

    def _prepare_graph_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        seen: set[str] = set()
        aliases: dict[str, str] = {}
        for index, source in enumerate(jobs, 1):
            job = dict(source)
            base = self._safe_name(str(job.get("job_id") or job.get("name") or f"material_{index:03d}")) or f"material_{index:03d}"
            job_id = base
            suffix = 2
            while job_id in seen:
                job_id = f"{base}_{suffix}"
                suffix += 1
            seen.add(job_id)
            job["job_id"] = job_id
            prepared.append(job)
            for value in (job_id, job.get("name"), (job.get("prompt_data") or {}).get("id") if isinstance(job.get("prompt_data"), dict) else ""):
                for key in self._reference_lookup_keys(value):
                    aliases[key] = job_id
        for job in prepared:
            dependencies = self._string_list(job.get("depends_on"))
            bindings = dict(job.get("input_bindings") or {})
            reference = str(job.get("reference_image") or "").strip()
            if reference and "input_base_image" not in bindings:
                upstream = next((aliases[key] for key in self._reference_lookup_keys(reference) if key in aliases and aliases[key] != job["job_id"]), "")
                if upstream:
                    bindings["input_base_image"] = {"from_job": upstream, "output": "output_final_image"}
                    dependencies.append(upstream)
            if str(job.get("mode") or "") == "talking_image" and "local_tts" not in dependencies:
                dependencies.append("local_tts")
            job["depends_on"] = list(dict.fromkeys(dependencies))
            job["input_bindings"] = bindings
        return prepared

    @staticmethod
    def _enrich_global_context(context: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
        enriched = json.loads(json.dumps(context, ensure_ascii=False))
        characters = enriched.get("characters") if isinstance(enriched.get("characters"), list) else []
        by_id = {str(item.get("character_id") or ""): item for item in characters if isinstance(item, dict) and item.get("character_id")}
        style = enriched.get("style") if isinstance(enriched.get("style"), dict) else {}
        for job in jobs:
            character_id = str(job.get("character_id") or "").strip()
            if character_id:
                item = by_id.setdefault(character_id, {"character_id": character_id, "reference_assets": [], "recommended_weight": ""})
                for value in (job.get("reference_image"), *(job.get("reference_images") or [])):
                    text = str(value or "").strip()
                    if text and text not in item["reference_assets"]:
                        item["reference_assets"].append(text)
            style_id = str(job.get("style_id") or "").strip()
            if style_id and not style.get("style_id"):
                style["style_id"] = style_id
        enriched["characters"] = list(by_id.values())
        enriched["style"] = style
        return enriched

    def _topological_material_jobs(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {str(job.get("job_id")): job for job in jobs}
        ordered: list[dict[str, Any]] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(job_id: str) -> None:
            if job_id in visited:
                return
            if job_id in visiting:
                raise ValueError(f"production graph contains a dependency cycle at {job_id}")
            visiting.add(job_id)
            for dependency in self._string_list(by_id[job_id].get("depends_on")):
                if dependency in by_id:
                    visit(dependency)
            visiting.remove(job_id)
            visited.add(job_id)
            ordered.append(by_id[job_id])

        for item_id in by_id:
            visit(item_id)
        return ordered

    def _apply_explicit_input_bindings(self, job: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], list[str]]:
        bindings = job.get("input_bindings") if isinstance(job.get("input_bindings"), dict) else {}
        resolved: dict[str, str] = {}
        missing: list[str] = []
        field_map = {
            "input_base_image": "reference_image",
            "input_identity_image": "identity_image",
            "input_pose_image": "pose_image",
            "input_source_video": "source_video",
            "input_middle_frame": "middle_frame_image",
            "input_last_frame": "last_frame_image",
            "input_mask_image": "mask_image",
            "input_reference_style": "reference_style",
            "input_audio_file": "audio_file",
        }
        state_jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        for slot, spec in bindings.items():
            value = ""
            if isinstance(spec, str):
                value = spec
            elif isinstance(spec, dict):
                upstream = str(spec.get("from_job") or "")
                output_name = str(spec.get("output") or "")
                upstream_state = state_jobs.get(upstream) if isinstance(state_jobs.get(upstream), dict) else {}
                artifacts = upstream_state.get("artifacts") if isinstance(upstream_state.get("artifacts"), list) else []
                match = next((item for item in artifacts if isinstance(item, dict) and (not output_name or item.get("output_name") == output_name)), None)
                value = str((match or {}).get("path") or "")
            if value and Path(value).is_file():
                resolved[str(slot)] = value
                target = field_map.get(str(slot), str(slot))
                job[target] = value
                if target == "reference_image":
                    job["reference_images"] = [value]
            elif isinstance(spec, dict) and spec.get("required", True):
                missing.append(str(slot))
        return job, resolved, missing

    def _run_job_with_retries(
        self,
        provider: str,
        payload: dict[str, Any],
        config: dict[str, Any],
        output_dir: Path,
        job_id: str,
    ) -> tuple[dict[str, Any], int]:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                result = self._run_runninghub(payload, config, output_dir) if provider == "runninghub" else self._run_generic(payload, config, output_dir)
                result = self._maybe_append_turnaround_sheet(payload, config, output_dir, result)
                if not result.get("downloaded_files") and result.get("status") not in {"submitted"}:
                    raise ValueError("ComfyUI download failed: no output files returned")
                return result, attempt
            except Exception as exc:
                last_error = exc
                category = self._error_category(exc)
                if category not in {"network", "timeout", "provider_busy", "download"} or attempt >= 3:
                    raise
                self._emit(
                    f"生产节点 {job_id} 暂时失败，准备重试 {attempt}/3：{exc}",
                    job_id=job_id,
                    retry_attempt=attempt,
                    error_category=category,
                )
                time.sleep(min(8, 2 ** (attempt - 1)))
        raise last_error or ValueError(f"production job failed: {job_id}")

    @staticmethod
    def _error_category(exc: BaseException) -> str:
        text = str(exc).lower()
        if any(marker in text for marker in ("out of memory", "cuda oom", "显存")):
            return "resource_oom"
        if any(marker in text for marker in ("timeout", "timed out")):
            return "timeout"
        if any(marker in text for marker in ("429", "busy", "queue full", "temporarily unavailable")):
            return "provider_busy"
        if any(marker in text for marker in ("download", "no output files")):
            return "download"
        if any(marker in text for marker in ("connection", "urlerror", "ssl", "network")):
            return "network"
        if any(marker in text for marker in ("missing input", "required", "nodeinfo", "invalid")):
            return "configuration"
        return "execution"

    def _register_generated_images(
        self,
        job: dict[str, Any],
        files: list[str],
        generated_images: list[str],
        generated_map: dict[str, str],
    ) -> None:
        images = [path for path in files if Path(path).suffix.lower().lstrip(".") in {"png", "jpg", "jpeg", "webp"}]
        generated_images.extend(path for path in images if path not in generated_images)
        if images:
            for key in self._reference_keys_for_job(job) | self._reference_lookup_keys(job.get("job_id")):
                generated_map[key] = images[0]

    @staticmethod
    def _output_files_for_job(job: dict[str, Any], files: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        image = next((path for path in files if Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}), "")
        video = next((path for path in files if Path(path).suffix.lower() in {".mp4", ".mov", ".webm", ".m4v"}), "")
        if image:
            result["output_final_image"] = image
            if str(job.get("mode") or "") == "background_remove":
                result["output_mask_alpha"] = image
        if video:
            result["output_final_video"] = video
        return result

    def _maybe_append_turnaround_sheet(
        self,
        payload: dict[str, Any],
        compose_config: dict[str, Any],
        output_dir: Path,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        if not self._is_turnaround_request(payload, compose_config):
            return manifest
        images = [
            Path(str(path))
            for path in (manifest.get("downloaded_files") or [])
            if Path(str(path)).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and Path(str(path)).is_file()
        ]
        if len(images) < 2:
            return manifest
        stem_source = (
            payload.get("job_id")
            or payload.get("asset_tag")
            or payload.get("workflow_mode")
            or payload.get("task_type")
            or "turnaround"
        )
        sheet_path = output_dir / f"{self._safe_name(str(stem_source))}_turnaround_sheet.png"
        created = self._create_turnaround_sheet(images[:4], sheet_path, payload)
        if not created:
            return manifest
        downloaded = [str(sheet_path), *[str(path) for path in (manifest.get("downloaded_files") or []) if str(path) != str(sheet_path)]]
        updated = dict(manifest)
        updated["downloaded_files"] = downloaded
        updated["turnaround_sheet"] = {
            "file": str(sheet_path),
            "source_files": [str(path) for path in images[:4]],
            "layout": created,
            "note": "Auto-stitched multi-view turnaround sheet for identity/keyframe reference input.",
        }
        manifest_path = output_dir / "cloud_comfyui_manifest.json"
        if manifest_path.is_file():
            existing = read_json(manifest_path)
            if existing:
                existing.update(updated)
                self._write_json(manifest_path, existing)
        return updated

    @classmethod
    def _is_turnaround_request(cls, payload: dict[str, Any], compose_config: dict[str, Any]) -> bool:
        values = []
        for source in (payload, compose_config):
            for key in (
                "workflow_id",
                "workflow_mode",
                "image_task_mode",
                "task_type",
                "image_task_type",
                "asset_tag",
                "control_mode",
                "workflow_preset_id",
                "workflow_preset_name",
            ):
                values.append(str(source.get(key) or ""))
        text = " ".join(values).lower()
        return any(marker in text for marker in ("turnaround", "three_view", "three-views", "multi_view", "三视", "四视"))

    @classmethod
    def _create_turnaround_sheet(cls, image_paths: list[Path], output_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
        opened: list[Image.Image] = []
        try:
            for path in image_paths:
                with Image.open(path) as image:
                    opened.append(ImageOps.exif_transpose(image).convert("RGBA"))
            if len(opened) < 2:
                return {}
            layout = cls._turnaround_sheet_layout(opened, payload)
            sheet_width = int(layout["width"])
            sheet_height = int(layout["height"])
            slots = layout["slots"]
            sheet = Image.new("RGBA", (sheet_width, sheet_height), (250, 250, 247, 255))
            for index, image in enumerate(opened):
                if index >= len(slots):
                    break
                slot = slots[index]
                box = (
                    int(slot["x"] * sheet_width),
                    int(slot["y"] * sheet_height),
                    max(1, int(slot["w"] * sheet_width)),
                    max(1, int(slot["h"] * sheet_height)),
                )
                fitted = ImageOps.contain(image, (box[2], box[3]), method=Image.Resampling.LANCZOS)
                tile = Image.new("RGBA", (box[2], box[3]), (255, 255, 255, 255))
                offset = ((box[2] - fitted.width) // 2, (box[3] - fitted.height) // 2)
                tile.alpha_composite(fitted, offset)
                sheet.alpha_composite(tile, (box[0], box[1]))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sheet.convert("RGB").save(output_path, "PNG", optimize=True)
            return {
                "strategy": str(layout["strategy"]),
                "width": sheet_width,
                "height": sheet_height,
                "slots": slots,
            }
        finally:
            for image in opened:
                image.close()

    @classmethod
    def _turnaround_sheet_layout(cls, images: list[Image.Image], payload: dict[str, Any]) -> dict[str, Any]:
        count = min(4, len(images))
        if count <= 1:
            return {"strategy": "single", "width": images[0].width, "height": images[0].height, "slots": [{"x": 0, "y": 0, "w": 1, "h": 1, "role": "main_front"}]}
        ratio = cls._turnaround_target_ratio(payload, images)
        portrait = ratio < 1.0
        max_long_side = 4096
        source_long = max(max(image.width, image.height) for image in images[:count])
        if portrait:
            sheet_height = min(max_long_side, max(848, source_long * 2))
            sheet_width = max(1, int(sheet_height * ratio))
            strategy = "portrait_priority_quadrants"
            slots = [
                {"x": 0.025, "y": 0.025, "w": 0.560, "h": 0.470, "role": "main_front"},
                {"x": 0.615, "y": 0.025, "w": 0.360, "h": 0.355, "role": "back_view"},
                {"x": 0.025, "y": 0.525, "w": 0.560, "h": 0.450, "role": "side_view"},
                {"x": 0.615, "y": 0.525, "w": 0.360, "h": 0.285, "role": "detail_or_material"},
            ]
        else:
            sheet_width = min(max_long_side, max(1280, source_long * 3))
            sheet_height = max(1, int(sheet_width / ratio))
            strategy = "landscape_left_main_right_stack"
            slots = [
                {"x": 0.025, "y": 0.025, "w": 0.405, "h": 0.950, "role": "main_front"},
                {"x": 0.465, "y": 0.025, "w": 0.510, "h": 0.295, "role": "back_view"},
                {"x": 0.465, "y": 0.352, "w": 0.510, "h": 0.295, "role": "left_side_view"},
                {"x": 0.465, "y": 0.680, "w": 0.510, "h": 0.295, "role": "right_side_view"},
            ]
        return {
            "strategy": strategy,
            "width": sheet_width,
            "height": sheet_height,
            "slots": slots[:count],
        }

    @staticmethod
    def _turnaround_target_ratio(payload: dict[str, Any], images: list[Image.Image]) -> float:
        try:
            target_width = float(payload.get("width") or payload.get("delivery_width") or 0)
            target_height = float(payload.get("height") or payload.get("delivery_height") or 0)
        except (TypeError, ValueError):
            target_width = target_height = 0
        if target_width > 0 and target_height > 0:
            return max(0.2, min(5.0, target_width / target_height))
        return sum((image.width / max(1, image.height)) for image in images) / max(1, len(images))

    def _load_cached_artifacts(self, state: dict[str, Any], images: list[str], generated_map: dict[str, str]) -> None:
        jobs = state.get("jobs") if isinstance(state.get("jobs"), dict) else {}
        for job_id, item in jobs.items():
            if not isinstance(item, dict) or item.get("status") not in {"success", "cached", "downloaded", "submitted"}:
                continue
            files = [str(path) for path in item.get("downloaded_files", []) if Path(str(path)).is_file()]
            cached_job = {"job_id": job_id, "name": item.get("name") or job_id}
            self._register_generated_images(cached_job, files, images, generated_map)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if isinstance(value, list):
            return [str(part).strip() for part in value if str(part).strip()]
        return []

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
        if (
            self._is_optional_when_unconfigured(job)
            and self._uses_workflow_library(compose_config)
            and not self._has_exact_workflow_library_config_for_job(job, compose_config)
        ):
            workflow_id = self._canonical_workflow_id(job.get("workflow_id") or (job.get("prompt_data") or {}).get("workflow_id") or "")
            workflow_mode = str(job.get("mode") or (job.get("prompt_data") or {}).get("workflow_mode") or "").strip()
            target = " / ".join(part for part in [workflow_id, workflow_mode] if part) or str(job.get("name") or job.get("job_id") or "当前可选节点")
            raise ValueError(f"可选 ComfyUI 后处理未配置，已跳过：{target}")
        preset = self._workflow_library_preset_for_job(job, compose_config)
        if not preset:
            if self._uses_workflow_library(compose_config):
                workflow_id = self._canonical_workflow_id(job.get("workflow_id") or (job.get("prompt_data") or {}).get("workflow_id") or "")
                workflow_mode = str(job.get("mode") or (job.get("prompt_data") or {}).get("workflow_mode") or "").strip()
                target = " / ".join(part for part in [workflow_id, workflow_mode] if part) or str(job.get("name") or job.get("job_id") or "当前生产节点")
                raise ValueError(f"ComfyUI 调试台未配置：找不到生产节点对应槽位 {target}，请先在调试台保存 endpoint 和 nodeInfoList")
            return compose_config
        job_config = dict(compose_config)
        preset_endpoint = str(preset.get("endpoint") or "").strip()
        if not self._is_usable_endpoint(preset_endpoint):
            preset_endpoint = ""
        node_info = str(preset.get("node_info_list_json") or preset.get("nodeInfoList") or "[]").strip() or "[]"
        node_info = self._repair_known_runninghub_node_info(
            node_info,
            endpoint=preset_endpoint,
            workflow_id=str(preset.get("id") or "").strip(),
            workflow_mode=str(preset.get("_matched_mode") or "").strip(),
        )
        if not preset_endpoint:
            target = " / ".join(part for part in [str(preset.get("id") or "").strip(), str(preset.get("_matched_mode") or "").strip()] if part) or str(job.get("name") or job.get("job_id") or "当前生产节点")
            raise ValueError(f"ComfyUI 调试台未配置 endpoint：{target}")
        if node_info in {"", "[]"}:
            target = " / ".join(part for part in [str(preset.get("id") or "").strip(), str(preset.get("_matched_mode") or "").strip()] if part) or str(job.get("name") or job.get("job_id") or "当前生产节点")
            raise ValueError(f"ComfyUI 调试台未配置 nodeInfoList：{target}")
        job_config["workflow_endpoint"] = preset_endpoint
        job_config["node_info_list_json"] = node_info
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
        workflow_id = cls._canonical_workflow_id(job.get("workflow_id") or (job.get("prompt_data") or {}).get("workflow_id") or "")
        workflow_mode = str(job.get("mode") or (job.get("prompt_data") or {}).get("workflow_mode") or "").strip()
        if workflow_id:
            exact = next((item for item in library if isinstance(item, dict) and cls._canonical_workflow_id(item.get("id") or "") == workflow_id), None)
            if exact:
                return cls._library_item_with_mode_config(exact, workflow_mode)
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

    @staticmethod
    def _canonical_workflow_id(workflow_id: Any) -> str:
        value = str(workflow_id or "").strip()
        return WORKFLOW_ID_ALIASES.get(value, value)

    @staticmethod
    def _library_item_with_mode_config(item: dict[str, Any], mode: str) -> dict[str, Any]:
        merged = dict(item)
        mode_configs = item.get("mode_configs") or item.get("modeConfigs")
        config = mode_configs.get(mode) if isinstance(mode_configs, dict) and isinstance(mode_configs.get(mode), dict) else None
        if config:
            merged["endpoint"] = config.get("endpoint") or ""
            merged["node_info_list_json"] = config.get("node_info_list_json") or config.get("nodeInfoList") or "[]"
            merged["poll_timeout_seconds"] = config.get("poll_timeout_seconds") or config.get("pollTimeout") or merged.get("poll_timeout_seconds")
            merged["_matched_mode"] = mode
        return merged

    @classmethod
    def _repair_known_runninghub_node_info(
        cls,
        node_info_json: str,
        *,
        endpoint: str = "",
        workflow_id: str = "",
        workflow_mode: str = "",
    ) -> str:
        """Repair legacy debug-console nodeInfoList rows before submission.

        Older local presets for the current RunningHub z_image/keyframe slots
        used node ids from a previous workflow export. RunningHub rejects those
        rows with NODE_INFO_MISMATCH before a task is created. Keep the repair
        deliberately narrow: only touch known stale prompt/negative/size rows
        that have already been observed failing against the user's configured
        workflow endpoints.
        """

        text = str(node_info_json or "").strip()
        if not text or text == "[]":
            return text or "[]"
        try:
            rows = json.loads(text)
        except Exception:
            return text
        if not isinstance(rows, list):
            return text

        endpoint_text = str(endpoint or "").strip()
        workflow_key = str(workflow_id or "").strip()
        mode_key = str(workflow_mode or "").strip()
        stale_node_ids = {str(row.get("nodeId") or "") for row in rows if isinstance(row, dict)}
        is_three_frame_ltx = (
            endpoint_text.endswith("/2071735603636563970")
            or workflow_key in {"06_i2v_first_middle_last_frame_ltx_2_3", "06_i2v_first_middle_last_frame"}
            or "first_middle_last" in mode_key.lower()
            or "three_frame" in mode_key.lower()
        )

        # The current z_image_turbo RunningHub workflow uses node 63 for the
        # text prompt and node 64 for latent size. Some migrated scene modes
        # still point to old nodes 10/11/20/40; replace those with the working
        # image mapping used by the successfully generated base assets.
        if (
            endpoint_text.endswith("/2067423263386591234")
            and {"10", "11", "20", "40"}.intersection(stale_node_ids)
        ):
            return json.dumps(cls._z_image_turbo_text_to_image_node_info(), ensure_ascii=False, indent=2)

        # Keyframe/style presets copied a non-existent negative prompt node 60.
        # Removing it preserves prompt/size/seed/output-prefix updates while
        # avoiding RunningHub NODE_INFO_MISMATCH.
        repaired = []
        changed = False
        for row in rows:
            if isinstance(row, dict) and str(row.get("nodeId") or "") == "60" and str(row.get("fieldName") or "").lower() == "text":
                changed = True
                continue
            if (
                isinstance(row, dict)
                and str(row.get("nodeId") or "") in {"177", "220"}
                and str(row.get("fieldName") or "").lower() == "text"
                and "young anime protagonist" in str(row.get("fieldValue") or "").lower()
                and "countdown timer" in str(row.get("fieldValue") or "").lower()
                and (
                    endpoint_text.endswith("/2071735603636563970")
                    or endpoint_text.endswith("/2067423263386591234")
                    or workflow_key in {"02_ltx_video_2_3", "06_i2v_first_frame", "06_i2v_first_middle_last_frame_ltx_2_3"}
                    or "i2v" in mode_key.lower()
                    or "ltx" in workflow_key.lower()
                )
            ):
                row = dict(row)
                row["fieldValue"] = "{{prompt}}"
                changed = True
            if is_three_frame_ltx and isinstance(row, dict):
                node_id = str(row.get("nodeId") or "")
                field_name = str(row.get("fieldName") or "")
                if node_id == "448" and field_name == "image" and str(row.get("fieldValue") or "") == "{{reference_image}}":
                    row = dict(row)
                    row["fieldValue"] = "{{input_middle_frame}}"
                    changed = True
                elif node_id == "449" and field_name == "image" and str(row.get("fieldValue") or "") == "{{reference_image}}":
                    row = dict(row)
                    row["fieldValue"] = "{{input_last_frame}}"
                    changed = True
                elif node_id == "422" and field_name == "value" and "{{prompt}}" not in str(row.get("fieldValue") or ""):
                    row = dict(row)
                    row["fieldValue"] = "{{prompt}}"
                    changed = True
                elif node_id == "426" and field_name == "preset_prompt":
                    row = dict(row)
                    row["fieldValue"] = "Describe this image in detail."
                    changed = True
                elif node_id == "412" and field_name == "value" and str(row.get("fieldValue") or "") != "{{fps}}":
                    row = dict(row)
                    row["fieldValue"] = "{{fps}}"
                    changed = True
                elif node_id == "413" and field_name == "frame_rate" and str(row.get("fieldValue") or "") != "{{fps}}":
                    row = dict(row)
                    row["fieldValue"] = "{{fps}}"
                    changed = True
            repaired.append(row)
        if changed and (
            endpoint_text.endswith("/2069402773254397953")
            or endpoint_text.endswith("/2067423263386591234")
            or endpoint_text.endswith("/2071735603636563970")
            or workflow_key in {"03_style_cover_image", "04_keyframe"}
            or workflow_key in {"02_ltx_video_2_3", "06_i2v_first_frame", "06_i2v_first_middle_last_frame_ltx_2_3"}
            or mode_key in {"style_reference", "cover_key_visual", "keyframe"}
            or "i2v" in mode_key.lower()
            or "ltx" in workflow_key.lower()
            or is_three_frame_ltx
        ):
            return json.dumps(repaired, ensure_ascii=False, indent=2)
        return text

    @staticmethod
    def _z_image_turbo_text_to_image_node_info() -> list[dict[str, Any]]:
        return [
            {"nodeId": "63", "fieldName": "text", "fieldValue": "{{prompt}}"},
            {"nodeId": "64", "fieldName": "width", "fieldValue": "{{width}}"},
            {"nodeId": "64", "fieldName": "height", "fieldValue": "{{height}}"},
            {"nodeId": "64", "fieldName": "batch_size", "fieldValue": 1},
            {"nodeId": "66", "fieldName": "seed", "fieldValue": "{{seed}}"},
            {"nodeId": "66", "fieldName": "steps", "fieldValue": 12},
            {"nodeId": "66", "fieldName": "cfg", "fieldValue": 1.5},
            {"nodeId": "66", "fieldName": "denoise", "fieldValue": 1},
            {"nodeId": "9", "fieldName": "filename_prefix", "fieldValue": "z_image_turbo_keyframe"},
            {"nodeId": "58", "fieldName": "clip_name", "fieldValue": "qwen_3_4b.safetensors"},
            {"nodeId": "58", "fieldName": "type", "fieldValue": "lumina2"},
            {"nodeId": "58", "fieldName": "device", "fieldValue": "default"},
            {"nodeId": "59", "fieldName": "vae_name", "fieldValue": "z_image_turbo-vae.safetensors"},
            {"nodeId": "62", "fieldName": "unet_name", "fieldValue": "z_image_turbo_bf16.safetensors"},
            {"nodeId": "62", "fieldName": "weight_dtype", "fieldValue": "default"},
            {"nodeId": "65", "fieldName": "shift", "fieldValue": 3},
            {"nodeId": "66", "fieldName": "sampler_name", "fieldValue": "res_multistep"},
            {"nodeId": "66", "fieldName": "scheduler", "fieldValue": "simple"},
        ]

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
        if cls._is_usable_endpoint(endpoint) and node_info and node_info != "[]":
            return True
        mode_configs = item.get("mode_configs") or item.get("modeConfigs")
        if isinstance(mode_configs, dict):
            return any(
                isinstance(config, dict)
                and cls._is_usable_endpoint(str(config.get("endpoint") or ""))
                and str(config.get("node_info_list_json") or config.get("nodeInfoList") or "").strip() not in {"", "[]"}
                for config in mode_configs.values()
            )
        return False

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
    def _has_exact_workflow_library_config_for_job(cls, job: dict[str, Any], compose_config: dict[str, Any]) -> bool:
        library = compose_config.get("workflow_library")
        if not isinstance(library, list):
            return False
        workflow_id = str(job.get("workflow_id") or (job.get("prompt_data") or {}).get("workflow_id") or "").strip()
        workflow_mode = str(job.get("mode") or job.get("workflow_mode") or (job.get("prompt_data") or {}).get("workflow_mode") or "").strip()
        if not workflow_id:
            return False
        item = next((entry for entry in library if isinstance(entry, dict) and str(entry.get("id") or "").strip() == workflow_id), None)
        if not item:
            return False
        if workflow_mode:
            mode_configs = item.get("mode_configs") or item.get("modeConfigs")
            config = mode_configs.get(workflow_mode) if isinstance(mode_configs, dict) and isinstance(mode_configs.get(workflow_mode), dict) else None
            if not config:
                return False
            endpoint = str(config.get("endpoint") or config.get("workflow_endpoint") or "").strip()
            node_info = str(config.get("node_info_list_json") or config.get("nodeInfoList") or "").strip()
            return cls._is_usable_endpoint(endpoint) and node_info not in {"", "[]"}
        return cls._is_configured_library_item(item)

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
                    "reference_images": self._reference_images(payload),
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
                        "reference_images": self._reference_images(payload),
                        "base_payload": payload,
                        "source": group_key,
                        "group": {},
                    }
                )
                continue
            if not isinstance(group, dict):
                continue
            if self._skip_material_prompt_group(group):
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

    @staticmethod
    def _skip_material_prompt_group(group: dict[str, Any]) -> bool:
        text = " ".join(
            str(group.get(key) or "").strip().lower()
            for key in (
                "intent",
                "workflow_id",
                "workflow_mode",
                "mode",
                "image_task_mode",
                "asset_tag",
                "workflow_constraint",
                "control_mode",
            )
        )
        return any(
            marker in text
            for marker in (
                "no_image_required",
                "placeholder_no_image",
                "skip_image_generation",
                "placeholder",
            )
        )

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
        last_frame_image = str(
            prompt_data.get("last_frame_image")
            or prompt_data.get("end_frame_image")
            or group.get("last_frame_image")
            or ""
        ).strip()
        middle_frame_image = str(
            prompt_data.get("middle_frame_image")
            or prompt_data.get("mid_frame_image")
            or prompt_data.get("middle_frame")
            or group.get("middle_frame_image")
            or group.get("mid_frame_image")
            or ""
        ).strip()
        mask_image = str(prompt_data.get("input_mask_image") or prompt_data.get("mask_image") or group.get("input_mask_image") or group.get("mask_image") or "").strip()
        reference_style = str(prompt_data.get("input_reference_style") or prompt_data.get("reference_style") or group.get("input_reference_style") or group.get("reference_style") or "").strip()
        audio_file = str(prompt_data.get("input_audio_file") or prompt_data.get("audio_file") or group.get("input_audio_file") or group.get("audio_file") or base_payload.get("input_audio_file") or "").strip()
        reference_images = cls._reference_images(prompt_data) or cls._reference_images(group) or cls._reference_images(base_payload)
        if reference_image and reference_image not in reference_images:
            reference_images = [reference_image, *reference_images]
        if middle_frame_image and middle_frame_image not in reference_images:
            reference_images.append(middle_frame_image)
        if last_frame_image and last_frame_image not in reference_images:
            reference_images.append(last_frame_image)
        return {
            "type": job_type,
            "name": name,
            "job_id": str(prompt_data.get("job_id") or group.get("job_id") or prompt_data.get("id") or name).strip(),
            "capability": str(prompt_data.get("capability") or group.get("capability") or "").strip(),
            "mode": str(prompt_data.get("mode") or prompt_data.get("workflow_mode") or group.get("mode") or group.get("workflow_mode") or "").strip(),
            "workflow_id": str(prompt_data.get("workflow_id") or group.get("workflow_id") or "").strip(),
            "optional_when_unconfigured": cls._as_bool(
                prompt_data.get("optional_when_unconfigured", group.get("optional_when_unconfigured", False)),
                default=False,
            ),
            "depends_on": cls._string_list(prompt_data.get("depends_on") or group.get("depends_on")),
            "input_bindings": prompt_data.get("input_bindings") if isinstance(prompt_data.get("input_bindings"), dict) else group.get("input_bindings") if isinstance(group.get("input_bindings"), dict) else {},
            "character_id": str(prompt_data.get("character_id") or group.get("character_id") or "").strip(),
            "style_id": str(prompt_data.get("style_id") or group.get("style_id") or "").strip(),
            "product_id": str(prompt_data.get("product_id") or group.get("product_id") or "").strip(),
            "scene_id": str(prompt_data.get("scene_id") or group.get("scene_id") or "").strip(),
            "entity_context": prompt_data.get("entity_context") if isinstance(prompt_data.get("entity_context"), dict) else group.get("entity_context") if isinstance(group.get("entity_context"), dict) else {},
            "prompt": prompt,
            "negative_prompt": negative,
            "reference_image": reference_image,
            "middle_frame_image": middle_frame_image,
            "last_frame_image": last_frame_image,
            "mask_image": mask_image,
            "reference_style": reference_style,
            "audio_file": audio_file,
            "reference_images": reference_images,
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
        candidates = [text]
        normalized = text.replace("\\", "/").lstrip("/")
        if normalized.startswith("comfyui_manual_debug/"):
            candidates.append("comfyui/manual_debug/" + normalized[len("comfyui_manual_debug/") :])
        if normalized.startswith("comfyui/manual_debug/"):
            candidates.append("comfyui_manual_debug/" + normalized[len("comfyui/manual_debug/") :])
        for base in getattr(self, "_reference_search_dirs", []) or []:
            for candidate_text in candidates:
                candidate = (Path(base) / candidate_text).resolve()
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

    def _payload_for_material_job(self, base_payload: dict[str, Any], job: dict[str, Any], index: int) -> dict[str, Any]:
        payload = json.loads(json.dumps(base_payload, ensure_ascii=False))
        prompt = str(job.get("prompt") or "").strip()
        negative = str(job.get("negative_prompt") or "").strip()
        reference_image = str(job.get("reference_image") or "").strip()
        middle_frame_image = str(job.get("middle_frame_image") or "").strip()
        last_frame_image = str(job.get("last_frame_image") or "").strip()
        job_type = str(job.get("type") or "material")
        prompt_data = job.get("prompt_data") if isinstance(job.get("prompt_data"), dict) else {}
        group = job.get("group") if isinstance(job.get("group"), dict) else {}
        payload["workflow_item_index"] = index
        payload["job_id"] = str(job.get("job_id") or f"material_{index:03d}")
        payload["capability"] = str(job.get("capability") or "")
        payload["workflow_mode"] = str(job.get("mode") or "")
        payload["character_id"] = str(job.get("character_id") or "")
        payload["style_id"] = str(job.get("style_id") or "")
        payload["product_id"] = str(job.get("product_id") or "")
        payload["scene_id"] = str(job.get("scene_id") or "")
        if isinstance(job.get("entity_context"), dict):
            payload["entity_context"] = job["entity_context"]
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
        if job_type == "video":
            prompt = self._runninghub_safe_video_prompt(prompt)
            negative = self._runninghub_safe_video_negative(negative)
        elif str(job.get("mode") or "").strip().lower() == "style_reference":
            prompt = self._safe_style_reference_prompt(prompt)
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
        reference_images = self._reference_image_list_values(job)
        if reference_images:
            payload["reference_images"] = reference_images
            if reference_image:
                payload["reference_image"] = reference_image
            else:
                payload["reference_image"] = reference_images[0]
            if last_frame_image:
                payload["last_frame_image"] = last_frame_image
            if middle_frame_image:
                payload["middle_frame_image"] = middle_frame_image
            elif len(reference_images) > 2:
                payload["middle_frame_image"] = reference_images[1]
            if not payload.get("last_frame_image") and len(reference_images) > 1:
                payload["last_frame_image"] = reference_images[-1]
        elif reference_image:
            payload["reference_image"] = reference_image
            if middle_frame_image:
                payload["middle_frame_image"] = middle_frame_image
            if last_frame_image:
                payload["last_frame_image"] = last_frame_image
            payload.pop("reference_images", None)
        else:
            payload.pop("reference_image", None)
            payload.pop("middle_frame_image", None)
            payload.pop("last_frame_image", None)
            payload.pop("reference_images", None)
        payload["has_reference_image"] = bool(payload.get("reference_image"))
        payload["has_middle_frame_image"] = bool(payload.get("middle_frame_image"))
        payload["has_last_frame_image"] = bool(payload.get("last_frame_image"))
        for semantic_key, job_key in (
            ("input_base_image", "reference_image"),
            ("input_identity_image", "identity_image"),
            ("input_pose_image", "pose_image"),
            ("input_source_video", "source_video"),
            ("input_middle_frame", "middle_frame_image"),
            ("input_last_frame", "last_frame_image"),
            ("input_mask_image", "mask_image"),
            ("input_reference_style", "reference_style"),
            ("input_audio_file", "audio_file"),
        ):
            value = str(job.get(semantic_key) or job.get(job_key) or payload.get(semantic_key) or "").strip()
            if value:
                payload[semantic_key] = self._reference_media_value(value) if semantic_key in {"input_audio_file", "input_source_video"} else value
        for key in ("seed", "width", "height", "duration", "fps", "frame_count", "frames", "denoise", "ipadapter_weight", "reference_strength", "motion_strength", "camera_motion", "camera_path", "pose_video", "image_task_mode"):
            value = (
                prompt_data.get(key)
                if key in prompt_data
                else group.get(key)
                if key in group
                else job.get(key)
            )
            if value not in (None, ""):
                payload[key] = value
        global_context = payload.get("global_context") if isinstance(payload.get("global_context"), dict) else {}
        render = global_context.get("render") if isinstance(global_context.get("render"), dict) else {}
        style = global_context.get("style") if isinstance(global_context.get("style"), dict) else {}
        payload["width"] = payload.get("width") or render.get("working_width") or 848
        payload["height"] = payload.get("height") or render.get("working_height") or 480
        payload["fps"] = payload.get("fps") or render.get("frame_rate") or 24
        payload["delivery_width"] = render.get("delivery_width") or 1920
        payload["delivery_height"] = render.get("delivery_height") or 1080
        payload["global_style_weight"] = payload.get("global_style_weight") or style.get("weight") or ""
        apply_locked_parameters_to_payload(payload, job_type=job_type, mode=str(job.get("mode") or ""))
        return payload

    @staticmethod
    def _safe_style_reference_prompt(prompt: str) -> str:
        """Keep style boards focused on environment instead of human appearance.

        Style-reference images do not need people. Removing incidental skin/beauty
        clauses both makes the reference cleaner and avoids false content-audit hits.
        """
        clauses = [item.strip() for item in re.split(r"(?<=[。！？!?])", str(prompt or "")) if item.strip()]
        person_markers = ("人物", "人像", "面部", "磨皮", "美颜", "portrait", "face", "body")
        kept = []
        for clause in clauses:
            lowered = clause.lower()
            if any(marker in lowered for marker in person_markers):
                continue
            cleaned_clause = re.sub(r"(?:自然)?肤色|皮肤(?:质感|纹理)?|natural skin(?: tone)?|skin tone", "", clause, flags=re.IGNORECASE)
            cleaned_clause = re.sub(r"[，,]{2,}", "，", cleaned_clause).strip("，, ")
            cleaned_clause = re.sub(r"[，,]+([。！？!?])", r"\1", cleaned_clause)
            if cleaned_clause:
                kept.append(cleaned_clause)
        cleaned = "".join(kept).strip()
        suffix = "空景室内，无人物、无人像、无文字、无UI、无数字。"
        return f"{cleaned}{suffix}" if cleaned else suffix

    def _reference_media_value(self, value: str) -> str:
        text = str(value or "").strip()
        path = self._resolve_reference_path(text)
        if not path or "runninghub" not in self.base_url.lower():
            return text
        cache_key = str(path.resolve())
        if cache_key not in self._media_upload_cache:
            self._media_upload_cache[cache_key] = self._upload_runninghub_media(path)
        return self._media_upload_cache[cache_key]

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
                **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            raw = self._read_request(req, timeout=120, label="RunningHub media upload", max_bytes=self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
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
                **(
                    {
                        "Authorization": f"Bearer {self.api_key}",
                        "X-API-Key": self.api_key,
                    }
                    if self.api_key
                    else {}
                ),
            },
            method="POST",
        )
        try:
            raw = self._read_request(req, timeout=120, label="ComfyUI workflow request", max_bytes=self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise ValueError(f"ComfyUI workflow HTTP {exc.code}: {detail}") from exc
        except (urllib_error.URLError, TimeoutError, OSError) as exc:
            raise ValueError(f"ComfyUI workflow connection failed: {self._connection_error_message(exc)}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _read_request(self, req: urllib_request.Request, timeout: int, label: str, max_bytes: int | None = None) -> bytes:
        last_exc: BaseException | None = None
        for attempt in range(1, self.NETWORK_RETRY_ATTEMPTS + 1):
            try:
                with urllib_request.urlopen(req, timeout=timeout) as response:
                    return response.read(max_bytes) if max_bytes else response.read()
            except urllib_error.HTTPError:
                raise
            except (urllib_error.URLError, TimeoutError, OSError) as exc:
                last_exc = exc
                if attempt >= self.NETWORK_RETRY_ATTEMPTS:
                    break
                self._emit(
                    f"{label} 网络连接暂时失败，重试 {attempt}/{self.NETWORK_RETRY_ATTEMPTS - 1}：{self._connection_error_message(exc)}",
                    error=self._connection_error_message(exc),
                    retry_attempt=attempt,
                )
                time.sleep(min(8, attempt * 2))
        if last_exc:
            raise last_exc
        raise ValueError(f"{label} failed without response")

    @staticmethod
    def _connection_error_message(exc: BaseException) -> str:
        reason = getattr(exc, "reason", None)
        return str(reason or exc)

    @classmethod
    def _is_connection_error(cls, exc: BaseException) -> bool:
        message = str(exc).lower()
        return "connection failed" in message or "timed out" in message or "timeout" in message or "ssl" in message

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

    @classmethod
    def _override_dimension_node_info(cls, node_info: list[Any], payload: dict[str, Any]) -> list[Any]:
        width = cls._clean_dimension_value(payload.get("width"))
        height = cls._clean_dimension_value(payload.get("height"))
        if width is None and height is None:
            return node_info

        def replace_item(item: Any) -> Any:
            if isinstance(item, list):
                return [replace_item(child) for child in item]
            if not isinstance(item, dict):
                return item
            updated = {key: replace_item(value) for key, value in item.items()}
            field_name = str(updated.get("fieldName") or updated.get("field_name") or "").strip().lower()
            if field_name == "width" and width is not None:
                updated["fieldValue"] = width
            elif field_name == "height" and height is not None:
                updated["fieldValue"] = height
            return updated

        return [replace_item(item) for item in node_info]

    @classmethod
    def _normalize_numeric_node_info(cls, node_info: list[Any], seed_value: int) -> list[Any]:
        integer_fields = {
            "seed",
            "noise_seed",
            "width",
            "height",
            "length",
            "frames",
            "frame_count",
            "frames_number",
            "batch_size",
            "steps",
            "frame_rate",
            "fps",
            "value",
        }
        integer_fields_allow_negative = {"frame_idx", "frame_index"}

        def replace_item(item: Any) -> Any:
            if isinstance(item, list):
                return [replace_item(child) for child in item]
            if not isinstance(item, dict):
                return item
            updated = {key: replace_item(value) for key, value in item.items()}
            field_name = str(updated.get("fieldName") or updated.get("field_name") or "").strip().lower()
            if field_name in {"seed", "noise_seed"}:
                updated["fieldValue"] = seed_value
            elif field_name in integer_fields_allow_negative or field_name.startswith("frame_idx_"):
                number = cls._clean_int_value(updated.get("fieldValue"), minimum=-1)
                if number is not None:
                    updated["fieldValue"] = number
            elif field_name in integer_fields:
                number = cls._clean_int_value(updated.get("fieldValue"), minimum=1)
                if number is not None:
                    updated["fieldValue"] = number
            return updated

        return [replace_item(item) for item in node_info]

    @staticmethod
    def _clean_dimension_value(value: Any) -> int | str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            number = int(float(text))
        except ValueError:
            return text
        return number if number > 0 else None

    @staticmethod
    def _clean_int_value(value: Any, minimum: int = 0) -> int | None:
        text = str(value or "").strip()
        if not text or text in {"\\", "/", "None", "null", "undefined"}:
            return None
        try:
            number = int(float(text))
        except ValueError:
            return None
        return number if number >= minimum else None

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

    def _download_file(self, url: str, output_dir: Path, stem: str, output_type: str) -> Path:
        suffix = f".{output_type.lstrip('.')}" if output_type else Path(urlparse(url).path).suffix.lower()
        if suffix.lower().lstrip(".") not in CloudComfyUIAdapter.DOWNLOAD_TYPES:
            suffix = ".mp4"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-comfyui-adapter/1.0"})
        target.write_bytes(self._read_request(req, timeout=300, label="RunningHub result download"))
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
        values = CloudComfyUIAdapter._reference_images(payload)
        if values:
            return values[0]
        return ""

    @staticmethod
    def _last_frame_image(payload: dict[str, Any]) -> str:
        value = payload.get("last_frame_image") or payload.get("end_frame_image")
        if isinstance(value, str) and value.strip():
            return value.strip()
        values = CloudComfyUIAdapter._reference_image_list_values(payload)
        if len(values) > 2:
            return values[-1]
        if len(values) > 1:
            return values[1]
        return ""

    @staticmethod
    def _middle_frame_image(payload: dict[str, Any]) -> str:
        value = payload.get("middle_frame_image") or payload.get("mid_frame_image") or payload.get("middle_keyframe")
        if isinstance(value, str) and value.strip():
            return value.strip()
        values = CloudComfyUIAdapter._reference_image_list_values(payload)
        if len(values) > 2:
            return values[1]
        return ""

    @staticmethod
    def _frame_count(payload: dict[str, Any]) -> str:
        explicit = payload.get("frame_count") or payload.get("frames")
        if explicit not in (None, "", []):
            return str(explicit)
        try:
            duration = float(str(payload.get("duration") or "").strip())
            fps = float(str(payload.get("fps") or "").strip())
        except ValueError:
            return ""
        if duration <= 0 or fps <= 0:
            return ""
        return str(max(1, int(round(duration * fps)) + 1))

    @staticmethod
    def _ltx_guide_frame_count(payload: dict[str, Any]) -> str:
        value = CloudComfyUIAdapter._frame_count(payload)
        if not value:
            return ""
        try:
            target = int(float(value))
        except ValueError:
            return value
        # LTX guide nodes add latent context frames. For the first/last-frame
        # guide workflow, use a shorter internal length so the exported video
        # lands closer to the requested duration.
        internal = max(9, target - 16)
        return str(internal)

    @staticmethod
    def _middle_frame_index(payload: dict[str, Any]) -> str:
        value = CloudComfyUIAdapter._frame_count(payload)
        if not value:
            return ""
        try:
            total = int(float(value))
        except ValueError:
            return ""
        return str(max(1, total // 2))

    @staticmethod
    def _last_frame_index(payload: dict[str, Any]) -> str:
        value = CloudComfyUIAdapter._frame_count(payload)
        if not value:
            return "-1"
        try:
            total = int(float(value))
        except ValueError:
            return "-1"
        return str(max(0, total - 1))

    @staticmethod
    def _reference_images(payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload, dict):
            return []
        result: list[str] = []
        value = payload.get("reference_image")
        if isinstance(value, str) and value.strip():
            parts = [part.strip() for part in re.split("[,\uFF0C;\uFF1B\n]+", value) if part.strip()]
            result.extend(parts or [value.strip()])
        last_frame = payload.get("last_frame_image") or payload.get("end_frame_image")
        if isinstance(last_frame, str) and last_frame.strip():
            result.append(last_frame.strip())
        middle_frame = payload.get("middle_frame_image") or payload.get("mid_frame_image") or payload.get("middle_keyframe")
        if isinstance(middle_frame, str) and middle_frame.strip():
            insert_at = 1 if result else 0
            result.insert(insert_at, middle_frame.strip())
        result.extend(CloudComfyUIAdapter._reference_image_list_values(payload))
        deduped: list[str] = []
        seen = set()
        for item in result:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _reference_image_list_values(payload: dict[str, Any]) -> list[str]:
        if not isinstance(payload, dict):
            return []
        result: list[str] = []
        values = payload.get("reference_images")
        if isinstance(values, list):
            for item in values:
                candidate = ""
                if isinstance(item, str):
                    candidate = item.strip()
                elif isinstance(item, dict):
                    for key in ("url", "path", "file", "image", "reference_image"):
                        raw_value = item.get(key)
                        if isinstance(raw_value, str) and raw_value.strip():
                            candidate = raw_value.strip()
                            break
                if candidate:
                    result.append(candidate)
        deduped: list[str] = []
        seen = set()
        for item in result:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        return str(CloudComfyUIAdapter._first_value(data, ("status",)) or "").upper()

    @classmethod
    def _runninghub_error_message(cls, data: Any) -> str:
        if not isinstance(data, dict):
            return ""
        message = cls._first_value(data, ("errorMessage", "message", "msg", "failedReason"))
        code = cls._first_value(data, ("errorCode", "code"))
        failed_reason = data.get("failedReason")
        if isinstance(failed_reason, dict) and failed_reason:
            detail = json.dumps(failed_reason, ensure_ascii=False)
            message = f"{message}; failedReason={detail}" if message else f"failedReason={detail}"
        if code and message:
            return f"RunningHub error {code}: {message}"
        if message:
            return f"RunningHub error: {message}"
        nested = data.get("data")
        if isinstance(nested, dict):
            return cls._runninghub_error_message(nested)
        return ""

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
