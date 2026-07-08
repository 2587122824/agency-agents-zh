from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable


FAILED_STATUS_MARKERS = (
    "failed",
    "error",
    "blocked",
    "timeout",
    "quality_failed",
    "dependency_blocked",
)


COMFY_WORKFLOW_SLOT_LABELS: dict[tuple[str, str], str] = {
    ("01_base_asset_image", "character_base"): "01 基础资产 / 角色基础图",
    ("01_base_asset_image", "product_base"): "01 基础资产 / 产品基础图",
    ("01_base_asset_image", "scene_base"): "01 基础资产 / 场景基础图",
    ("02_turnaround", "character_turnaround"): "01 基础资产 / 角色三视图",
    ("02_turnaround", "product_turnaround"): "01 基础资产 / 产品三视图",
    ("03_style_cover_image", "style_reference"): "01 基础资产 / 风格参考图",
    ("03_style_cover_image", "cover_key_visual"): "01 基础资产 / 封面关键视觉",
    ("04_keyframe", "keyframe"): "02 分镜关键帧 / 关键帧",
    ("04_keyframe", "style_reference_keyframe"): "02 分镜关键帧 / 风格参考关键帧",
    ("04_keyframe", "img2img_style_keyframe"): "02 分镜关键帧 / 图生图风格关键帧",
    ("04_keyframe", "identity_keyframe"): "02 分镜关键帧 / 身份一致关键帧",
    ("04_keyframe", "identity_scene_keyframe"): "02 分镜关键帧 / 人物+场景一致关键帧",
    ("05_image_repair_cutout", "image_inpaint_fix"): "03 图片处理 / 局部修复",
    ("05_image_repair_cutout", "background_remove"): "03 图片处理 / 抠图去背景",
    ("06_i2v_first_frame", "i2v_first_frame"): "04 视频生成 / 首帧图生视频",
    ("06_i2v_first_middle_last_frame", "i2v_first_middle_last_frame"): "04 视频生成 / 首中尾帧图生视频",
    ("10_broll_transition_video", "broll_scene_video"): "04 视频生成 / B-roll 场景视频",
    ("10_broll_transition_video", "empty_transition_video"): "04 视频生成 / 空镜转场视频",
    ("09_talking_image", "talking_image"): "06 数字人口播 / 图片口播",
    ("11_video_enhance", "video_upscale"): "07 视频后期 / 视频放大",
    ("11_video_enhance", "frame_interpolation"): "07 视频后期 / 补帧",
    ("11_video_enhance", "video_deflicker_stabilize"): "07 视频后期 / 去闪烁稳定",
    ("12_video_inpaint_fix", "video_inpaint_fix"): "07 视频后期 / 视频局部修复",
}


class TaskStateCenter:
    """Unified read model for task progress and production state.

    The UI should read this object instead of independently inferring status
    from run_summary, production_manifest, manual debug state, and task files.
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        *,
        task_dir: Path,
        task_name: str,
        summary: dict[str, Any] | None,
        files: list[str],
        assets: dict[str, Any] | None = None,
        active_job: dict[str, Any] | None = None,
        comfy_debug_loader: Callable[[Path], dict[str, Any]] | None = None,
        runtime_comfy_config: dict[str, Any] | None = None,
    ) -> None:
        self.task_dir = task_dir
        self.task_name = task_name
        self.summary = summary if isinstance(summary, dict) else {}
        self.files = files
        self.assets = assets if isinstance(assets, dict) else {}
        self.active_job = active_job if isinstance(active_job, dict) else None
        self.comfy_debug_loader = comfy_debug_loader
        self.runtime_comfy_config = runtime_comfy_config if isinstance(runtime_comfy_config, dict) else {}

    def build(self) -> dict[str, Any]:
        comfy_debug = self._comfy_debug()
        state = self._task_state(comfy_debug)
        steps = self._steps()
        production = self._production()
        workflow = self._workflow(steps, state)
        manual_debug = self._manual_debug(comfy_debug)
        blockers = self._blockers(workflow, production, manual_debug)
        if state in {"cancelled", "completed"}:
            manual_debug["allowed_actions"] = []
            blockers = []
        allowed_actions = self._allowed_actions(state, blockers, production, manual_debug)
        next_action = self._next_action(state, workflow, production, manual_debug, blockers, allowed_actions)
        diagnostics = self._diagnostics(state, steps, production, blockers)
        return {
            "schema_version": self.SCHEMA_VERSION,
            "state": state,
            "status_label": self._status_label(state),
            "workflow": workflow,
            "steps": steps,
            "production": production,
            "manual_debug": manual_debug,
            "comfy_debug": comfy_debug,
            "tts": production.get("tts", {}),
            "ffmpeg": production.get("ffmpeg", {}),
            "dag": production.get("dag", {}),
            "assets": self.assets,
            "blockers": blockers,
            "blocking_reasons": [item["message"] for item in blockers],
            "allowed_actions": allowed_actions,
            "next_action": next_action,
            "recommended_next_operation": next_action.get("label", ""),
            "diagnostics": diagnostics,
            "source_files": {
                "summary": str(self.task_dir / "run_summary.json") if (self.task_dir / "run_summary.json").is_file() else "",
                "production_manifest": str(self.task_dir / "production_manifest.json") if (self.task_dir / "production_manifest.json").is_file() else "",
                "production_graph": str(self.task_dir / "production_graph.json") if (self.task_dir / "production_graph.json").is_file() else "",
                "manual_debug_state": str(self.task_dir / "comfyui" / "manual_debug_state.json") if (self.task_dir / "comfyui" / "manual_debug_state.json").is_file() else "",
            },
        }

    def _task_state(self, comfy_debug: dict[str, Any]) -> str:
        active_status = str((self.active_job or {}).get("status") or "").strip().lower()
        if active_status in {"failed", "error"}:
            return "failed"
        if active_status in {"cancelled", "canceled"}:
            return "cancelled"
        if active_status in {"queued", "running"}:
            return "running"
        if active_status == "paused":
            return "paused"
        summary_status = str(self.summary.get("status") or "").strip().lower()
        if summary_status in {"cancelled", "canceled"}:
            return "cancelled"
        if summary_status in {"failed", "error"}:
            return "failed"
        if summary_status == "paused":
            production = self._production()
            if self._production_has_active_work(production):
                return "running"
            return "paused"
        if summary_status in {"completed", "success"}:
            production = self._production()
            return "completed" if self._has_final_media(production) else self._state_for_unfinished_production(production)
        if self.summary.get("awaiting_confirmation"):
            return "awaiting_confirmation"
        if str(self.summary.get("blocked_reason") or "").strip():
            return "blocked"
        production_status = str(self.summary.get("production_status") or "").strip().lower()
        if production_status.startswith("awaiting_comfyui_") and comfy_debug.get("enabled"):
            return "partial" if comfy_debug.get("enabled") and comfy_debug.get("complete") else "blocked"
        if production_status and self._is_failed_status(production_status):
            return "failed"
        manifest = self._production_manifest()
        manifest_status = str(manifest.get("status") or "").lower()
        if self._is_failed_status(manifest_status):
            return "failed"
        total_steps = int(self.summary.get("total_steps") or 0)
        completed_steps = int(self.summary.get("completed_steps") or self.summary.get("step_count") or 0)
        if (self.summary.get("final_output") or "final_output.md" in self.files) and self.summary and total_steps > 0 and completed_steps >= total_steps:
            production = self._production()
            return "completed" if self._has_final_media(production) else self._state_for_unfinished_production(production)
        if any(file.startswith("step_") for file in self.files):
            return "partial"
        return "empty"

    def _state_for_unfinished_production(self, production: dict[str, Any]) -> str:
        status = str(production.get("status") or "").strip().lower()
        if self._is_failed_status(status):
            return "failed"
        jobs = production.get("jobs") if isinstance(production.get("jobs"), list) else []
        job_statuses = [str(job.get("status") or "").strip().lower() for job in jobs if isinstance(job, dict)]
        if any(item in {"running", "queued"} for item in job_statuses):
            return "running"
        if any(item in {"pending", "not_started"} for item in job_statuses):
            return "partial"
        if (self.task_dir / "production_graph.json").is_file() or (self.task_dir / "production_plan.json").is_file():
            return "partial"
        return "partial"

    def _production_has_active_work(self, production: dict[str, Any]) -> bool:
        status = str(production.get("status") or "").strip().lower()
        if status in {"running", "queued"}:
            return True
        jobs = production.get("jobs") if isinstance(production.get("jobs"), list) else []
        return any(str(job.get("status") or "").strip().lower() in {"running", "queued"} for job in jobs if isinstance(job, dict))

    def _steps(self) -> list[dict[str, Any]]:
        workflow_steps = self._workflow_steps()
        output_files = {file: self.task_dir / file for file in self.files if re.match(r"^step_\d+_.*/output\.md$", file)}
        by_step: dict[int, dict[str, Any]] = {}
        for file, path in output_files.items():
            step_no = self._step_number_from_file(file)
            if step_no:
                by_step.setdefault(step_no, {})["output_file"] = file
                by_step[step_no]["has_output"] = True
                by_step[step_no]["size"] = path.stat().st_size if path.is_file() else 0
                by_step[step_no]["mtime"] = path.stat().st_mtime if path.is_file() else 0
        active_step = int((self.active_job or {}).get("current_step") or self.summary.get("current_step") or 0)
        completed_steps = int((self.active_job or {}).get("completed_steps") or self.summary.get("step_count") or 0)
        run_status = str((self.active_job or {}).get("status") or self.summary.get("status") or "").lower()
        max_step = max(
            [int(item.get("step") or 0) for item in workflow_steps if isinstance(item, dict)]
            + list(by_step.keys())
            + [active_step, completed_steps, 0]
        )
        awaiting_step = int(self.summary.get("awaiting_confirmation_step") or 0) if self.summary.get("awaiting_confirmation") else 0
        blocked_step = int(self.summary.get("blocked_step") or 0) if self.summary.get("blocked_reason") else 0
        steps: list[dict[str, Any]] = []
        for index in range(1, max_step + 1):
            workflow_step = next((item for item in workflow_steps if int(item.get("step") or 0) == index), {})
            metadata = self._step_metadata(index)
            output_info = by_step.get(index, {})
            has_output = bool(output_info.get("has_output"))
            if active_step == index and run_status in {"failed", "error"}:
                status = "failed"
            elif awaiting_step == index:
                status = "awaiting_confirmation"
            elif blocked_step == index:
                status = "blocked"
            elif active_step == index and run_status in {"queued", "running"}:
                status = "running"
            elif active_step == index and run_status == "paused":
                status = "paused"
            elif has_output or index <= completed_steps:
                status = "completed"
            else:
                status = "pending"
            agent = metadata.get("agent_id") or workflow_step.get("agent") or ""
            steps.append(
                {
                    "step": index,
                    "status": status,
                    "agent": agent,
                    "title": metadata.get("agent_name") or agent or f"Step {index}",
                    "task": metadata.get("task") or workflow_step.get("task") or "",
                    "expected_output": metadata.get("expected_output") or workflow_step.get("output") or "",
                    "output_file": output_info.get("output_file", ""),
                    "has_output": has_output,
                    "size": output_info.get("size", 0),
                    "mtime": output_info.get("mtime", 0),
                    "needs_confirmation": awaiting_step == index,
                    "blocked_reason": str(self.summary.get("blocked_reason") or "") if blocked_step == index else "",
                }
            )
        return steps

    def _workflow(self, steps: list[dict[str, Any]], state: str) -> dict[str, Any]:
        if self.active_job:
            run_status = str(self.active_job.get("status") or "")
            current_step = int(self.active_job.get("current_step") or 0)
            completed_steps = int(self.active_job.get("completed_steps") or 0)
            message = str(self.active_job.get("current_message") or self.active_job.get("error") or "")
        else:
            run_status = state
            current_step = int(self.summary.get("current_step") or self.summary.get("blocked_step") or self.summary.get("resume_step") or self.summary.get("resume_from_step") or 0)
            completed_steps = len([step for step in steps if step.get("status") == "completed"])
            message = str(self.summary.get("blocked_reason") or "")
        return {
            "run_status": run_status,
            "current_step": current_step,
            "completed_steps": completed_steps,
            "total_steps": len(steps),
            "awaiting_confirmation": bool(self.summary.get("awaiting_confirmation")),
            "awaiting_confirmation_step": int(self.summary.get("awaiting_confirmation_step") or 0),
            "blocked_reason": str(self.summary.get("blocked_reason") or ""),
            "message": message,
        }

    def _production(self) -> dict[str, Any]:
        manifest_path = self.task_dir / "production_manifest.json"
        manifest, manifest_error = self._json_file_with_error(manifest_path)
        history = manifest.get("production_job_history") if isinstance(manifest.get("production_job_history"), list) else []
        nodes = self._production_nodes(manifest)
        graph_backed = False
        if not nodes:
            nodes = self._production_nodes_from_graph()
            graph_backed = bool(nodes)
        jobs = self._legacy_jobs_from_manifest(manifest, nodes)
        dag = {
            "nodes": nodes,
            "counts": self._status_counts(nodes),
            "blocked_nodes": [node for node in nodes if node.get("status") == "blocked"],
            "failed_nodes": [
                node
                for node in nodes
                if node.get("status") != "blocked" and self._is_failed_status(str(node.get("status") or ""))
            ],
        }
        tts = self._stage_status(manifest, jobs, stage_id="tts", node_ids={"local_tts"})
        ffmpeg = self._stage_status(manifest, jobs, stage_id="ffmpeg", node_ids={"ffmpeg_compose", "format_export"})
        manual = self._stage_status(manifest, jobs, stage_id="manual_debug", node_ids=set())
        status = str(manifest.get("status") or self.summary.get("production_status") or ("running" if graph_backed else "off"))
        composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
        composition = self._normalized_composition_status(composition)
        return {
            "mode": str(manifest.get("mode") or "off"),
            "status": status,
            "manifest_file": str(manifest_path) if manifest_path.is_file() else "",
            "manifest_error": manifest_error,
            "composition": composition,
            "jobs": jobs,
            "history": history[-10:],
            "dag": dag,
            "tts": tts,
            "ffmpeg": ffmpeg,
            "manual_debug": manual,
            "allowed_retries": self._allowed_retries(jobs, nodes),
            "graph_backed": graph_backed,
        }

    def _normalized_composition_status(self, composition: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(composition or {})
        adapter_status = str(normalized.get("comfyui_adapter_status") or normalized.get("adapter_status") or "").strip()
        legacy_provider_status = str(normalized.get("visual_provider_status") or "").strip()
        if adapter_status and adapter_status not in {"pending", "not_configured"}:
            if legacy_provider_status in {"", "pending", "skipped", "not_configured"} or adapter_status == "success":
                normalized["visual_provider_status"] = adapter_status
                if adapter_status == "success":
                    normalized["visual_provider_reason"] = ""
        return normalized

    def _manual_debug(self, comfy_debug: dict[str, Any]) -> dict[str, Any]:
        status = "not_configured"
        if comfy_debug.get("enabled"):
            status = "approved" if comfy_debug.get("complete") else "awaiting_confirmation"
        return {
            "enabled": bool(comfy_debug.get("enabled")),
            "status": status,
            "stage": comfy_debug.get("stage") or "all",
            "approved": int(comfy_debug.get("approved") or 0),
            "total": int(comfy_debug.get("total") or 0),
            "complete": bool(comfy_debug.get("complete")),
            "current": comfy_debug.get("current") or {},
            "allowed_actions": ["confirm_comfy_debug"] if comfy_debug.get("enabled") and not comfy_debug.get("complete") else [],
        }

    def _blockers(self, workflow: dict[str, Any], production: dict[str, Any], manual_debug: dict[str, Any]) -> list[dict[str, str]]:
        blockers: list[dict[str, str]] = []
        if workflow.get("blocked_reason"):
            blockers.append({"source": "workflow", "code": "workflow_blocked", "message": str(workflow["blocked_reason"])})
        if manual_debug.get("status") == "awaiting_confirmation":
            blockers.append({"source": "manual_debug", "code": "awaiting_comfyui_confirmation", "message": "ComfyUI 调试队列等待人工确认"})
        if production.get("manifest_error"):
            blockers.append({"source": "production", "code": "invalid_manifest", "message": str(production["manifest_error"])})
        for node in production.get("dag", {}).get("blocked_nodes", []):
            blockers.append({"source": "dag", "code": str(node.get("job_id") or "blocked_node"), "message": str(node.get("blocked_reason") or node.get("error") or "生产节点阻塞")})
        for node in production.get("dag", {}).get("failed_nodes", []):
            blockers.append({"source": "dag", "code": str(node.get("job_id") or "failed_node"), "message": str(node.get("error") or node.get("blocked_reason") or "生产节点失败")})
        return blockers

    def _allowed_actions(self, state: str, blockers: list[dict[str, str]], production: dict[str, Any], manual_debug: dict[str, Any]) -> list[str]:
        actions = {"export"}
        if "final_output.md" in self.files or any(file.startswith("step_") for file in self.files):
            actions.add("rebuild_final")
        if any(file.startswith("step_") and file.endswith("/output.md") for file in self.files):
            actions.add("rerun_step")
        if state in {"awaiting_confirmation", "blocked", "failed", "partial", "cancelled", "paused"}:
            actions.add("resume")
        if self.summary.get("awaiting_confirmation") and state not in {"failed", "error", "cancelled", "completed"}:
            actions.update({"confirm_step", "cancel"})
        if state in {"running", "queued", "paused", "awaiting_confirmation", "blocked"}:
            actions.add("cancel")
        if manual_debug.get("status") == "awaiting_confirmation" and state not in {"failed", "error", "cancelled", "completed"}:
            actions.add("run_comfy_debug")
            actions.add("confirm_comfy_debug")
        for job_id in production.get("allowed_retries") or []:
            actions.add(f"retry:{job_id}")
        if blockers:
            actions.add("inspect_blockers")
        return sorted(actions)

    def _next_action(
        self,
        state: str,
        workflow: dict[str, Any],
        production: dict[str, Any],
        manual_debug: dict[str, Any],
        blockers: list[dict[str, str]],
        allowed_actions: list[str],
    ) -> dict[str, Any]:
        if manual_debug.get("status") == "awaiting_confirmation" and state not in {"failed", "error", "cancelled", "completed"}:
            return {"action": "run_comfy_debug", "label": "运行并确认 ComfyUI 调试队列", "reason": "manual_debug_awaiting_confirmation"}
        if self.summary.get("awaiting_confirmation") and state not in {"failed", "error", "cancelled", "completed"}:
            return {"action": "confirm_step", "label": "确认当前步骤并继续", "reason": "workflow_awaiting_confirmation", "step": workflow.get("awaiting_confirmation_step")}
        failed_or_blocked = production.get("dag", {}).get("blocked_nodes", []) + production.get("dag", {}).get("failed_nodes", [])
        if failed_or_blocked:
            first = failed_or_blocked[0]
            job_id = str(first.get("job_id") or "")
            retry_action = f"retry:{job_id}" if job_id else "inspect_blockers"
            return {"action": retry_action, "label": f"处理生产节点：{job_id or '未知节点'}", "reason": str(first.get("blocked_reason") or first.get("error") or ""), "job_id": job_id}
        composition = production.get("composition") if isinstance(production.get("composition"), dict) else {}
        raw_missing_slots = composition.get("missing_workflow_slots") if isinstance(composition.get("missing_workflow_slots"), list) else []
        missing_slots = [item for item in raw_missing_slots if isinstance(item, dict) and not self._is_optional_missing_workflow_slot(item)]
        has_final_media = self._has_final_media(production)
        if missing_slots and not has_final_media:
            return {
                "action": "configure_comfy_workflows",
                "label": "配置 ComfyUI 工作流槽位后重试素材",
                "reason": "missing_comfy_workflow_slots",
            }
        visual_provider = str(composition.get("visual_provider") or composition.get("visual_provider_details", {}).get("provider") or "").strip().lower()
        if visual_provider == "runninghub" and not self._has_runninghub_api_key(composition) and not has_final_media:
            return {
                "action": "configure_runninghub_api_key",
                "label": "配置 RunningHub API Key 后重试素材",
                "reason": "missing_runninghub_api_key",
            }
        if state in {"partial", "blocked", "failed", "cancelled", "paused"} and "resume" in allowed_actions:
            return {"action": "resume", "label": "继续任务", "reason": state}
        if blockers:
            return {"action": "inspect_blockers", "label": "查看阻塞原因", "reason": blockers[0].get("message", "")}
        if state == "completed":
            if has_final_media:
                return {"action": "review_output", "label": "查看最终输出", "reason": "completed"}
            return {"action": "review_or_export", "label": "检查输出或导出任务", "reason": "completed_without_final_media"}
        return {"action": "none", "label": "暂无推荐操作", "reason": state}

    def _diagnostics(self, state: str, steps: list[dict[str, Any]], production: dict[str, Any], blockers: list[dict[str, str]]) -> list[dict[str, Any]]:
        diagnostics: list[dict[str, Any]] = []
        if not steps and self.files:
            diagnostics.append({"level": "warn", "code": "missing_workflow_steps", "message": "任务有文件，但没有可识别的工作流步骤。"})
        if self.active_job and str(self.active_job.get("error") or "").strip():
            diagnostics.append({"level": "error", "code": "run_failed", "message": str(self.active_job.get("error") or "").strip()})
        if production.get("manifest_error"):
            diagnostics.append({"level": "error", "code": "invalid_production_manifest", "message": production["manifest_error"]})
        production_status = str(production.get("status") or "").lower()
        if self._is_failed_status(production_status):
            diagnostics.append({"level": "error", "code": "production_failed", "message": f"自动生产失败：{production.get('status')}"})
        composition = production.get("composition") if isinstance(production.get("composition"), dict) else {}
        raw_missing_slots = composition.get("missing_workflow_slots") if isinstance(composition.get("missing_workflow_slots"), list) else []
        missing_slots = [item for item in raw_missing_slots if isinstance(item, dict) and not self._is_optional_missing_workflow_slot(item)]
        if missing_slots:
            details = [self._workflow_slot_detail(item) for item in missing_slots]
            labels = [item["display_label"] for item in details if item.get("display_label")]
            diagnostics.append(
                {
                    "level": "warn",
                    "code": "missing_comfy_workflow_slots",
                    "message": "ComfyUI 调试台缺少生产槽位配置：" + "、".join(labels),
                    "details": details,
                    "suggestion": "请到 ComfyUI 调试台展开对应分类，为这些子模式保存 endpoint 和 nodeInfoList；配置后回到任务输出点击“重试素材”。",
                }
            )
        visual_provider = str(composition.get("visual_provider") or composition.get("visual_provider_details", {}).get("provider") or "").strip().lower()
        if visual_provider == "runninghub" and not self._has_runninghub_api_key(composition):
            diagnostics.append(
                {
                    "level": "warn",
                    "code": "missing_runninghub_api_key",
                    "message": "RunningHub API Key 未配置：请到系统配置或 ComfyUI 调试台运行参数区保存 API Key；配置后回到任务输出点击“重试素材”。",
                    "suggestion": "不要使用未知默认接口地址；生产执行只读取你在调试台/系统配置里保存过的 RunningHub 配置。",
                }
            )
        for blocker in blockers:
            diagnostics.append({"level": "warn", "code": blocker.get("code", "blocked"), "message": blocker.get("message", "")})
        if state in {"running", "partial"} and production.get("graph_backed") and not production.get("manifest_file"):
            diagnostics.append(
                {
                    "level": "info",
                    "code": "production_materials_in_progress",
                    "message": "员工步骤已完成，素材生产仍在进行中；production_manifest.json 会在素材/包装阶段返回后写入。",
                }
            )
        if state == "completed" and not self._has_final_media(production):
            diagnostics.append({"level": "info", "code": "no_final_media", "message": "任务已有文本结果，但尚未发现最终视频文件。"})
        missing_outputs = [str(step["step"]) for step in steps if step.get("status") in {"completed", "awaiting_confirmation"} and not step.get("has_output")]
        if missing_outputs:
            diagnostics.append({"level": "warn", "code": "missing_step_outputs", "message": f"步骤状态已完成但缺少 output.md：{', '.join(missing_outputs)}"})
        return diagnostics

    def _has_final_media(self, production: dict[str, Any]) -> bool:
        composition = production.get("composition") if isinstance(production.get("composition"), dict) else {}
        for value in (self.summary.get("final_video"), composition.get("final_video_file")):
            path_text = str(value or "").strip()
            if not path_text or Path(path_text).suffix.lower() != ".mp4":
                continue
            path = Path(path_text)
            if path.is_file() or (not path.is_absolute() and (self.task_dir / path).is_file()):
                return True
        return any(
            Path(file).suffix.lower() == ".mp4"
            and ("/" not in str(file).replace("\\", "/") or str(file).replace("\\", "/").startswith("export_package/"))
            for file in self.files
        )

    def _has_runninghub_api_key(self, composition: dict[str, Any]) -> bool:
        if self._as_bool(composition.get("api_key_provided"), default=False):
            return True
        if self._as_bool(self.runtime_comfy_config.get("has_api_key"), default=False):
            return True
        return bool(str(self.runtime_comfy_config.get("api_key") or "").strip())

    @staticmethod
    def _workflow_slot_detail(item: dict[str, Any]) -> dict[str, str]:
        workflow_id = str(item.get("workflow_id") or "").strip()
        mode = str(item.get("mode") or item.get("workflow_mode") or "").strip()
        raw_label = str(item.get("label") or "").strip()
        zh_label = COMFY_WORKFLOW_SLOT_LABELS.get((workflow_id, mode), "")
        fallback = raw_label or f"{workflow_id}{' / ' + mode if mode else ''}".strip()
        display_label = zh_label
        if fallback and fallback != zh_label:
            display_label = f"{zh_label}（{workflow_id} / {mode}）" if zh_label else fallback
        return {
            "workflow_id": workflow_id,
            "mode": mode,
            "label": raw_label,
            "display_label": display_label,
            "debug_console_path": zh_label,
        }

    @staticmethod
    def _is_optional_missing_workflow_slot(item: dict[str, Any]) -> bool:
        if TaskStateCenter._as_bool(item.get("optional_when_unconfigured"), default=False):
            return True
        text = " ".join(
            str(value or "").strip()
            for value in (
                item.get("workflow_id"),
                item.get("mode"),
                item.get("workflow_mode"),
                item.get("label"),
                item.get("capability"),
            )
        ).lower()
        return "enhance_video" in text or "video_enhance" in text

    @staticmethod
    def _as_bool(value: Any, default: bool = False) -> bool:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on", "y", "enabled", "启用", "是"}

    def _production_nodes(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = []
        for node in manifest.get("production_nodes") or []:
            if not isinstance(node, dict) or not node.get("job_id"):
                continue
            status = "success" if node.get("status") == "cached" else str(node.get("status") or "pending")
            nodes.append(
                {
                    "job_id": str(node.get("job_id") or ""),
                    "stage": str(node.get("stage") or ""),
                    "mode": str(node.get("mode") or ""),
                    "status": status,
                    "depends_on": [str(value) for value in (node.get("depends_on") or []) if value],
                    "outputs": [str(value) for value in (node.get("outputs") or []) if value],
                    "attempts": int(node.get("attempts") or 1),
                    "cache_hit": bool(node.get("cache_hit")),
                    "optional_when_unconfigured": self._as_bool(node.get("optional_when_unconfigured"), default=False),
                    "blocked_reason": str(node.get("blocked_reason") or ""),
                    "error": str(node.get("error") or ""),
                }
            )
        return nodes

    def _production_nodes_from_graph(self) -> list[dict[str, Any]]:
        graph, _ = self._json_file_with_error(self.task_dir / "production_graph.json")
        jobs = graph.get("jobs") if isinstance(graph.get("jobs"), list) else []
        nodes: list[dict[str, Any]] = []
        for raw in jobs:
            if not isinstance(raw, dict) or not raw.get("job_id"):
                continue
            job_id = str(raw.get("job_id") or "")
            runtime = self._visual_job_runtime_state(job_id)
            status = str(runtime.get("status") or "pending").strip().lower()
            if status in {"success", "succeeded", "completed", "complete", "done"}:
                status = "success"
            elif status in {"running", "queued"}:
                status = "running"
            elif status in {"failed", "error", "timeout"}:
                status = "failed"
            else:
                status = "pending"
            nodes.append(
                {
                    "job_id": job_id,
                    "stage": str(raw.get("stage") or "visual"),
                    "mode": str(raw.get("mode") or ""),
                    "status": status,
                    "depends_on": [str(value) for value in (raw.get("depends_on") or []) if value],
                    "outputs": [str(value) for value in (runtime.get("files") or []) if value],
                    "attempts": int(runtime.get("attempts") or 1),
                    "cache_hit": False,
                    "optional_when_unconfigured": self._as_bool(raw.get("optional_when_unconfigured"), default=False),
                    "blocked_reason": str(runtime.get("blocked_reason") or ""),
                    "error": str(runtime.get("error") or ""),
                }
            )
        return nodes

    def _visual_job_runtime_state(self, job_id: str) -> dict[str, Any]:
        job_dir = self.task_dir / "generated_images" / f"job_{job_id}"
        state, _ = self._json_file_with_error(job_dir / "runninghub_task_state.json")
        files = []
        if job_dir.is_dir():
            for path in sorted(job_dir.iterdir()):
                if path.is_file() and path.suffix.lower() not in {".json", ".txt", ".md"}:
                    files.append(str(path))
        status = str(state.get("status") or "").strip().lower()
        return {
            "status": status or ("success" if files else "pending"),
            "files": files,
            "error": state.get("error") or state.get("message") or "",
            "attempts": state.get("attempts") or 1,
        }

    def _legacy_jobs_from_manifest(self, manifest: dict[str, Any], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
        audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        image_generation = manifest.get("image_generation") if isinstance(manifest.get("image_generation"), dict) else {}
        video_generation = manifest.get("video_generation") if isinstance(manifest.get("video_generation"), dict) else {}
        mode = str(manifest.get("mode") or "").strip()
        if mode == "comfy_full":
            material_status = str(composition.get("comfyui_adapter_status") or composition.get("adapter_status") or "not_configured")
        else:
            material_status = str(image_generation.get("adapter_status") or video_generation.get("adapter_status") or "not_configured")
        if nodes and material_status in {"", "not_configured", "pending"}:
            visual_statuses = [str(node.get("status") or "pending") for node in nodes if str(node.get("stage") or "") == "visual"]
            if visual_statuses:
                if any(status in {"running", "queued"} for status in visual_statuses):
                    material_status = "running"
                elif any(self._is_failed_status(status) for status in visual_statuses):
                    material_status = "failed"
                elif all(status in {"success", "skipped"} for status in visual_statuses):
                    material_status = "success"
                else:
                    material_status = "pending"
        material_outputs = []
        for key in ("downloaded_files",):
            values = composition.get("comfyui_downloaded_files") or composition.get(key) or image_generation.get(key) or video_generation.get(key) or []
            if isinstance(values, list):
                material_outputs.extend(str(value) for value in values if value)
        material_detail = composition.get("comfyui_adapter_manifest") or composition.get("adapter_manifest") or image_generation.get("adapter_manifest") or video_generation.get("adapter_manifest") or ""
        tts_status = str(audio.get("adapter_status") or "not_configured")
        tts_outputs = [str(audio.get("voiceover_audio_file") or "")] if audio.get("voiceover_audio_file") else []
        tts_detail = audio.get("adapter_manifest") or audio.get("voice_text_reason") or ""
        ffmpeg_status = str(composition.get("local_ffmpeg_status") or (composition.get("adapter_status") if composition.get("local_ffmpeg_manifest") else "") or "not_configured")
        manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        final_video = composition.get("final_video_file") or manifest_files.get("final_video") or ""
        ffmpeg_outputs = [str(final_video)] if final_video else []
        ffmpeg_detail = composition.get("local_ffmpeg_manifest") or composition.get("target_file") or ""
        jobs = [
            {"id": "material", "label": "素材生成/匹配", "status": material_status, "detail": str(material_detail or ""), "outputs": material_outputs},
            {"id": "tts", "label": "配音", "status": tts_status, "detail": str(tts_detail or ""), "outputs": tts_outputs},
            {"id": "ffmpeg", "label": "合成", "status": ffmpeg_status, "detail": str(ffmpeg_detail or ""), "outputs": ffmpeg_outputs},
        ]
        node_labels = {
            "local_tts": "08 本地配音",
            "subtitle_build": "08 字幕生成",
            "bgm_select": "08 BGM 匹配",
            "ffmpeg_compose": "08 音画合成",
            "format_export": "08 格式导出",
        }
        for node in nodes:
            node_id = str(node.get("job_id") or "")
            jobs.append(
                {
                    "id": node_id,
                    "label": node_labels.get(node_id) or str(node.get("mode") or node_id),
                    "status": str(node.get("status") or "pending"),
                    "detail": str(node.get("blocked_reason") or node.get("error") or ("缓存命中" if node.get("cache_hit") else "")),
                    "outputs": node.get("outputs") or [],
                    "depends_on": node.get("depends_on") or [],
                    "attempts": int(node.get("attempts") or 1),
                    "cache_hit": bool(node.get("cache_hit")),
                    "optional_when_unconfigured": self._as_bool(node.get("optional_when_unconfigured"), default=False),
                    "stage": str(node.get("stage") or ""),
                }
            )
        return jobs

    @staticmethod
    def _stage_status(manifest: dict[str, Any], jobs: list[dict[str, Any]], *, stage_id: str, node_ids: set[str]) -> dict[str, Any]:
        matched = [job for job in jobs if str(job.get("id") or "") == stage_id or str(job.get("id") or "") in node_ids]
        if not matched:
            return {"status": "not_configured", "jobs": []}
        statuses = [str(job.get("status") or "pending") for job in matched]
        if all(status in {"success", "skipped", "not_configured"} for status in statuses):
            if any(status == "success" for status in statuses):
                status = "success"
            elif any(status == "skipped" for status in statuses):
                status = "skipped"
            else:
                status = "not_configured"
        elif any(status == "blocked" for status in statuses):
            status = "blocked"
        elif any(TaskStateCenter._is_failed_status(status) for status in statuses):
            status = "failed"
        elif any(status in {"pending", "running", "queued", "awaiting_confirmation"} for status in statuses):
            status = "running" if any(status in {"running", "queued"} for status in statuses) else "pending"
        else:
            status = statuses[-1]
        return {"status": status, "jobs": matched}

    @staticmethod
    def _allowed_retries(jobs: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[str]:
        retry_ids = []
        for job in jobs:
            job_id = str(job.get("id") or "")
            if job_id in {"material", "tts", "ffmpeg"}:
                retry_ids.append(job_id)
        for node in nodes:
            status = str(node.get("status") or "")
            if status in {"failed", "blocked", "quality_failed", "timeout"}:
                retry_ids.append(str(node.get("job_id") or ""))
        return [item for item in dict.fromkeys(retry_ids) if item]

    @staticmethod
    def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            status = str(item.get("status") or "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    def _comfy_debug(self) -> dict[str, Any]:
        if not self.comfy_debug_loader:
            return {}
        try:
            value = self.comfy_debug_loader(self.task_dir)
        except Exception as exc:
            return {"enabled": False, "error": str(exc)}
        return value if isinstance(value, dict) else {}

    def _production_manifest(self) -> dict[str, Any]:
        manifest, _ = self._json_file_with_error(self.task_dir / "production_manifest.json")
        return manifest

    @staticmethod
    def _json_file_with_error(path: Path) -> tuple[dict[str, Any], str]:
        if not path.is_file():
            return {}, ""
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            return {}, str(exc)
        return (loaded if isinstance(loaded, dict) else {}), ""

    def _workflow_steps(self) -> list[dict[str, Any]]:
        workflow_path = self.task_dir / "workflow.json"
        if not workflow_path.is_file():
            return []
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return []
        steps = workflow.get("steps") if isinstance(workflow, dict) else []
        if not isinstance(steps, list):
            return []
        return self._normalize_workflow_steps(steps)

    @staticmethod
    def _normalize_workflow_steps(steps: list[Any]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, raw_step in enumerate(steps, start=1):
            if not isinstance(raw_step, dict):
                continue
            step = dict(raw_step)
            step["step"] = int(step.get("step") or step.get("order") or index)
            step["agent"] = str(step.get("agent") or step.get("agent_id") or "").strip()
            step["task"] = str(step.get("task") or step.get("instruction") or "").strip()
            step["output"] = str(step.get("output") or step.get("expected_output") or "").strip()
            normalized.append(step)
        return normalized

    def _step_metadata(self, step_no: int) -> dict[str, Any]:
        matches = sorted(self.task_dir.glob(f"step_{step_no:02d}_*/metadata.json"))
        if not matches:
            return {}
        try:
            data = json.loads(matches[0].read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _step_number_from_file(file: str) -> int:
        match = re.match(r"^step_(\d+)_", str(file or ""))
        return int(match.group(1)) if match else 0

    @staticmethod
    def _is_failed_status(value: str) -> bool:
        text = str(value or "").strip().lower()
        return any(marker in text for marker in FAILED_STATUS_MARKERS)

    @staticmethod
    def _status_label(state: str) -> str:
        labels = {
            "empty": "未开始",
            "queued": "排队中",
            "running": "运行中",
            "partial": "进行中/部分完成",
            "paused": "已暂停",
            "completed": "已完成",
            "failed": "失败",
            "blocked": "阻塞",
            "awaiting_confirmation": "等待确认",
            "cancelled": "已取消",
        }
        return labels.get(state, state)
