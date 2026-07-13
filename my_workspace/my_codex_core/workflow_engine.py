from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .codex_api import CodexAPI, LLMResult
from .action_executor import ActionExecutor
from .memory_context import load_long_term_memory_context
from .production_pipeline import run_auto_production
from .production_output_validator import validate_production_output
from .requirement_guard import (
    build_requirement_lock,
    declares_human_confirmation,
    extract_generated_context,
    extract_original_requirement,
    requirement_lock_prompt,
    validate_requirement_alignment,
)
from .reference_snapshot import snapshot_linked_assets
from .staff_loader import StaffLoader
from .task_storage import TaskStorage


@dataclass(frozen=True)
class WorkflowRunResult:
    task_dir: str
    workflow_name: str
    task_title: str
    provider: str
    step_count: int
    final_output: str
    production_manifest: str | None = None


class WorkflowCheckpointPause(RuntimeError):
    """Raised when step-by-step confirmation mode pauses after a completed step."""


class RequirementAlignmentError(RuntimeError):
    """Raised when an employee output violates an explicit validation contract."""

    def __init__(self, issue_details: list[dict] | None = None) -> None:
        self.issue_details = [item for item in (issue_details or []) if isinstance(item, dict)]
        lines = []
        for item in self.issue_details:
            source = str(item.get("source") or "未分类校验")
            message = str(item.get("message") or "输出不符合校验要求")
            lines.append(f"[{source}] {message}")
        detail = "; ".join(lines) if lines else "[未分类校验] 输出不符合校验要求"
        super().__init__(f"Employee output validation failed: {detail}")


def _is_timeout_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, TimeoutError) or "timeout" in current.__class__.__name__.lower():
            return True
        message = str(current).lower()
        if "timed out" in message or "timeout exceeded" in message or "read timeout" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _error_source(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, RequirementAlignmentError):
        return "employee_output_validation", "员工输出校验"
    if _is_timeout_error(exc):
        return "model_api_timeout", "模型/API 调用"
    return "workflow_execution", "工作流执行"


def _step_error_text(step_no: int, agent_id: str, exc: BaseException) -> str:
    source_code, source = _error_source(exc)
    if source_code == "employee_output_validation":
        guidance = "员工原始输出已保留在当前步骤目录；系统没有自动改写或重试。请根据带来源的校验明细修正后继续任务。"
    elif source_code == "model_api_timeout":
        guidance = "检测到真实超时。可以检查模型服务状态，或在管理台调整 `模型超时` 后重试。"
    else:
        guidance = "请根据错误信息修正对应模型、接口或输入后继续任务。"
    return (
        "# 当前步骤执行失败\n\n"
        f"- 步骤：{step_no}\n"
        f"- 员工：{agent_id}\n"
        f"- 错误来源：{source}\n"
        f"- 错误：{exc}\n\n"
        f"{guidance}\n"
    )


class WorkflowEngine:
    def __init__(
        self,
        workspace_root: Path,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.staff_root = workspace_root / "my_custom_staff"
        self.workflow_root = workspace_root / "my_workflows"
        self.output_root = workspace_root / "my_task_output"
        self.action_root = workspace_root / "my_action_workspace"
        self.staff_loader = StaffLoader(self.staff_root)
        self.storage = TaskStorage(self.output_root)
        self.api = CodexAPI(provider=provider, model=model, api_key=api_key, base_url=base_url, timeout=timeout)
        self.action_executor = ActionExecutor(self.action_root)

    def run(
        self,
        workflow_key: str,
        user_input: str,
        task_title: str | None = None,
        production_config: dict | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> WorkflowRunResult:
        workflow_path = self._resolve_workflow_path(workflow_key)
        workflow = self._load_workflow(workflow_path)
        workflow_name = workflow.get("name") or workflow_path.stem
        task_title = (task_title or "").strip()
        steps = workflow.get("steps", [])
        task_dir = self.storage.create_task_dir(workflow_path.stem, task_title=task_title)
        agents = self.staff_loader.load_all()

        self.storage.write_json(task_dir / "workflow.json", workflow)
        user_input = snapshot_linked_assets(task_dir, user_input)
        self.storage.write_text(task_dir / "input.md", user_input)
        self.storage.write_json(task_dir / "task_brief.json", build_requirement_lock(user_input))
        production_config = self._restore_production_config(task_dir, production_config)
        if progress_callback:
            progress_callback(
                {
                    "event": "started",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "total_steps": len(steps),
                }
            )

        previous_outputs: list[dict[str, str]] = []
        step_outputs: list[dict[str, str]] = []
        provider_used = "offline"
        image_assets_context_added = False

        for step in steps:
            step_no = int(step["step"])
            agent = self.staff_loader.resolve_agent(agents, step["agent"])
            step_dir = task_dir / f"step_{step_no:02d}_{agent.agent_id}"
            if self._should_inject_manual_image_assets(agent.agent_id, image_assets_context_added):
                image_assets_context = self._manual_debug_assets_output(task_dir, "image")
                if image_assets_context:
                    previous_outputs.append(image_assets_context)
                    image_assets_context_added = True
            prompt = self._build_step_prompt(workflow, step, user_input, previous_outputs, production_config)
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_started",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "total_steps": len(steps),
                    }
                )
            step_started_at = time.time()

            self.storage.write_text(step_dir / "system.md", agent.prompt)
            self.storage.write_text(step_dir / "prompt.md", prompt)
            self.storage.write_json(
                step_dir / "metadata.json",
                {
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "task": step.get("task"),
                    "expected_output": step.get("output"),
                    "flow_rule": agent.flow_rule,
                },
            )

            try:
                if progress_callback:
                    progress_callback(
                        {
                            "event": "step_update",
                            "step": step_no,
                            "agent_id": agent.agent_id,
                            "agent_name": agent.name,
                            "message": f"正在调用模型/API：{self.api.provider} / {self.api.model}，超时 {self.api.timeout} 秒",
                            "provider": self.api.provider,
                            "model": self.api.model,
                            "timeout_seconds": self.api.timeout,
                            "total_steps": len(steps),
                        }
                    )
                result = self._run_model_with_requirement_guard(agent.prompt, prompt, user_input, step, step_dir, previous_outputs)
            except Exception as exc:
                source_code, source_label = _error_source(exc)
                error_text = _step_error_text(step_no, agent.agent_id, exc)
                self.storage.write_text(step_dir / "output.md", error_text)
                self.storage.write_json(
                    step_dir / "error.json",
                    {
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "error": str(exc),
                        "error_source": source_code,
                        "error_source_label": source_label,
                        "issue_details": getattr(exc, "issue_details", []),
                    },
                )
                raise
            elapsed_seconds = round(time.time() - step_started_at, 1)
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_update",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "message": f"模型/API 已返回，耗时 {elapsed_seconds} 秒，正在写入输出",
                        "provider": result.provider,
                        "model": result.model,
                        "elapsed_seconds": elapsed_seconds,
                        "total_steps": len(steps),
                    }
                )
            provider_used = result.provider
            self.storage.write_text(step_dir / "output.md", result.content)
            action_results = self.action_executor.execute_from_text(result.content, task_dir)

            step_record = {
                "step": str(step_no),
                "agent": agent.agent_id,
                "task": step.get("task", ""),
                "expected_output": step.get("output", ""),
                "output_path": str(step_dir / "output.md"),
                "content": result.content,
                "action_results": json.dumps(action_results, ensure_ascii=False),
            }
            previous_outputs.append(step_record)
            step_outputs.append(step_record)
            self._pause_for_declared_human_confirmation(
                task_dir,
                workflow_name,
                task_title,
                provider_used,
                step_no,
                len(steps),
                str(step_dir / "output.md"),
                result.content,
                progress_callback,
            )
            if self._requires_material_gate(workflow, step, production_config):
                self._run_material_gate_or_block(
                    task_dir,
                    workflow,
                    workflow_name,
                    task_title,
                    user_input,
                    step_outputs,
                    production_config,
                    step,
                    agent,
                    provider_used,
                    progress_callback,
                )
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_completed",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "output_path": str(step_dir / "output.md"),
                        "message": f"步骤完成，耗时 {elapsed_seconds} 秒",
                        "elapsed_seconds": elapsed_seconds,
                        "total_steps": len(steps),
                    }
                )
            self._pause_for_step_confirmation_if_needed(
                task_dir,
                workflow_name,
                task_title,
                provider_used,
                step_no,
                len(steps),
                str(step_dir / "output.md"),
                production_config,
                progress_callback,
            )

        final_output = self._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        self.storage.write_text(final_path, final_output)
        production_manifest = run_auto_production(task_dir, step_outputs, production_config, progress_callback=progress_callback)
        self.storage.write_json(
            task_dir / "run_summary.json",
            {
                "status": "completed",
                "employee_workflow_status": "completed",
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "workflow_file": str(workflow_path),
                "provider": provider_used,
                "step_count": len(step_outputs),
                "final_output": str(final_path),
                "production_manifest": production_manifest["files"]["manifest"] if production_manifest else "",
                "production_status": production_manifest["status"] if production_manifest else "off",
            },
        )
        if progress_callback:
            progress_callback(
                {
                    "event": "completed",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "provider": provider_used,
                    "step_count": len(step_outputs),
                    "final_output": str(final_path),
                    "production_manifest": production_manifest["files"]["manifest"] if production_manifest else "",
                    "production_status": production_manifest["status"] if production_manifest else "off",
                    "total_steps": len(steps),
                }
            )

        return WorkflowRunResult(
            task_dir=str(task_dir),
            workflow_name=workflow_name,
            task_title=task_title,
            provider=provider_used,
            step_count=len(step_outputs),
            final_output=str(final_path),
            production_manifest=production_manifest["files"]["manifest"] if production_manifest else None,
        )

    def rerun_step(self, task_dir: Path, step_no: int, progress_callback: Callable[[dict], None] | None = None) -> dict:
        workflow_path = task_dir / "workflow.json"
        input_path = task_dir / "input.md"
        if not workflow_path.is_file():
            raise FileNotFoundError("workflow.json")
        if not input_path.is_file():
            raise FileNotFoundError("input.md")

        workflow = self._load_workflow(workflow_path)
        workflow_name = workflow.get("name") or task_dir.name
        task_title = self._summary_value(task_dir, "task_title")
        steps = workflow.get("steps", [])
        target_step = next((step for step in steps if int(step.get("step") or 0) == step_no), None)
        if not target_step:
            raise ValueError(f"Step not found: {step_no}")

        user_input = input_path.read_text(encoding="utf-8", errors="replace")
        agents = self.staff_loader.load_all()
        agent = self.staff_loader.resolve_agent(agents, target_step["agent"])
        step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
        previous_outputs = self._collect_step_outputs(workflow, task_dir, before_step=step_no)
        prompt = self._build_step_prompt(workflow, target_step, user_input, previous_outputs)

        if progress_callback:
            progress_callback(
                {
                    "event": "started",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "total_steps": len(steps),
                    "rerun": True,
                    "rerun_step": step_no,
                }
            )
            for step in steps:
                current_step_no = int(step.get("step") or 0)
                if current_step_no == step_no:
                    break
                previous_agent = self.staff_loader.resolve_agent(agents, step["agent"])
                previous_step_dir = self._step_dir(task_dir, current_step_no, previous_agent.agent_id)
                progress_callback(
                    {
                        "event": "step_completed",
                        "step": current_step_no,
                        "agent_id": previous_agent.agent_id,
                        "agent_name": previous_agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "output_path": str(previous_step_dir / "output.md"),
                        "total_steps": len(steps),
                    }
                )
            progress_callback(
                {
                    "event": "step_started",
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "task": target_step.get("task", ""),
                    "expected_output": target_step.get("output", ""),
                    "total_steps": len(steps),
                    "rerun": True,
                }
            )

        output_path = step_dir / "output.md"
        if output_path.exists():
            backup = step_dir / f"output_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            backup.write_text(output_path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")

        self.storage.write_text(step_dir / "system.md", agent.prompt)
        self.storage.write_text(step_dir / "prompt.md", prompt)
        self.storage.write_json(
            step_dir / "metadata.json",
            {
                "step": step_no,
                "agent_id": agent.agent_id,
                "agent_name": agent.name,
                "task": target_step.get("task"),
                "expected_output": target_step.get("output"),
                "flow_rule": agent.flow_rule,
                "rerun_at": datetime.now().isoformat(timespec="seconds"),
            },
        )

        result = self._run_model_with_requirement_guard(agent.prompt, prompt, user_input, target_step, step_dir, previous_outputs)
        self.storage.write_text(output_path, result.content)
        action_results = self.action_executor.execute_from_text(result.content, task_dir)
        self.storage.write_json(
            step_dir / "rerun_result.json",
            {
                "step": step_no,
                "agent_id": agent.agent_id,
                "provider": result.provider,
                "output_path": str(output_path),
                "action_results": action_results,
                "rerun_at": datetime.now().isoformat(timespec="seconds"),
            },
        )

        step_outputs = self._collect_step_outputs(workflow, task_dir)
        final_output = self._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        self.storage.write_text(final_path, final_output)
        self._append_rerun_summary(task_dir, step_no, agent.agent_id, result.provider, str(output_path), str(final_path))

        if progress_callback:
            progress_callback(
                {
                    "event": "step_completed",
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "task": target_step.get("task", ""),
                    "expected_output": target_step.get("output", ""),
                    "output_path": str(output_path),
                    "total_steps": len(steps),
                    "rerun": True,
                }
            )
            progress_callback(
                {
                    "event": "completed",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "provider": result.provider,
                    "step_count": step_no,
                    "final_output": str(final_path),
                    "total_steps": len(steps),
                    "rerun": True,
                    "rerun_step": step_no,
                }
            )

        return {
            "step": step_no,
            "agent": agent.agent_id,
            "provider": result.provider,
            "file": output_path.relative_to(task_dir).as_posix(),
            "final_output": final_path.relative_to(task_dir).as_posix(),
        }

    def resume(
        self,
        task_dir: Path,
        production_config: dict | None = None,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> WorkflowRunResult:
        workflow_path = task_dir / "workflow.json"
        input_path = task_dir / "input.md"
        if not workflow_path.is_file():
            raise FileNotFoundError("workflow.json")
        if not input_path.is_file():
            raise FileNotFoundError("input.md")

        workflow = self._load_workflow(workflow_path)
        production_config = self._restore_production_config(task_dir, production_config)
        workflow_name = workflow.get("name") or task_dir.name
        task_title = self._summary_value(task_dir, "task_title")
        user_input = input_path.read_text(encoding="utf-8", errors="replace")
        self.storage.write_json(task_dir / "task_brief.json", build_requirement_lock(user_input))
        steps = workflow.get("steps", [])
        agents = self.staff_loader.load_all()
        self._confirm_pending_step_checkpoint(task_dir, production_config)
        resume_step = self._first_incomplete_step(workflow, task_dir)

        if progress_callback:
            progress_callback(
                {
                    "event": "started",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "total_steps": len(steps),
                    "resume": True,
                    "resume_step": resume_step,
                }
            )

        if progress_callback:
            for step in steps:
                step_no = int(step.get("step") or 0)
                if resume_step is not None and step_no >= resume_step:
                    continue
                agent = self.staff_loader.resolve_agent(agents, step["agent"])
                step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
                progress_callback(
                    {
                        "event": "step_completed",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "output_path": str(step_dir / "output.md"),
                        "total_steps": len(steps),
                    }
                )

        previous_outputs = self._collect_step_outputs(workflow, task_dir, before_step=resume_step) if resume_step else []
        image_assets_context_added = False
        if resume_step and self._step_number_for_agent_prefix(workflow, "07_") == resume_step:
            image_assets_context = self._manual_debug_assets_output(task_dir, "image")
            if image_assets_context:
                previous_outputs.append(image_assets_context)
                image_assets_context_added = True
        provider_used = self._summary_value(task_dir, "provider") or "offline"

        for step in steps:
            step_no = int(step.get("step") or 0)
            if resume_step is None or step_no < resume_step:
                continue
            agent = self.staff_loader.resolve_agent(agents, step["agent"])
            step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
            if self._should_inject_manual_image_assets(agent.agent_id, image_assets_context_added):
                image_assets_context = self._manual_debug_assets_output(task_dir, "image")
                if image_assets_context:
                    previous_outputs.append(image_assets_context)
                    image_assets_context_added = True
            prompt = self._build_step_prompt(workflow, step, user_input, previous_outputs, production_config)
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_started",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "total_steps": len(steps),
                    }
                )
            step_started_at = time.time()

            self.storage.write_text(step_dir / "system.md", agent.prompt)
            self.storage.write_text(step_dir / "prompt.md", prompt)
            self.storage.write_json(
                step_dir / "metadata.json",
                {
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "task": step.get("task"),
                    "expected_output": step.get("output"),
                    "flow_rule": agent.flow_rule,
                    "resume_at": datetime.now().isoformat(timespec="seconds"),
                },
            )

            try:
                if progress_callback:
                    progress_callback(
                        {
                            "event": "step_update",
                            "step": step_no,
                            "agent_id": agent.agent_id,
                            "agent_name": agent.name,
                            "message": f"正在调用模型/API：{self.api.provider} / {self.api.model}，超时 {self.api.timeout} 秒",
                            "provider": self.api.provider,
                            "model": self.api.model,
                            "timeout_seconds": self.api.timeout,
                            "total_steps": len(steps),
                        }
                    )
                result = self._run_model_with_requirement_guard(agent.prompt, prompt, user_input, step, step_dir, previous_outputs)
            except Exception as exc:
                source_code, source_label = _error_source(exc)
                error_text = _step_error_text(step_no, agent.agent_id, exc)
                self.storage.write_text(step_dir / "output.md", error_text)
                self.storage.write_json(
                    step_dir / "error.json",
                    {
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "error": str(exc),
                        "error_source": source_code,
                        "error_source_label": source_label,
                        "issue_details": getattr(exc, "issue_details", []),
                        "resume_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
                raise
            elapsed_seconds = round(time.time() - step_started_at, 1)
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_update",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "message": f"模型/API 已返回，耗时 {elapsed_seconds} 秒，正在写入输出",
                        "provider": result.provider,
                        "model": result.model,
                        "elapsed_seconds": elapsed_seconds,
                        "total_steps": len(steps),
                    }
                )

            provider_used = result.provider
            self.storage.write_text(step_dir / "output.md", result.content)
            error_path = step_dir / "error.json"
            if error_path.exists():
                error_path.unlink()
            action_results = self.action_executor.execute_from_text(result.content, task_dir)
            step_record = {
                "step": str(step_no),
                "agent": agent.agent_id,
                "task": step.get("task", ""),
                "expected_output": step.get("output", ""),
                "output_path": str(step_dir / "output.md"),
                "content": result.content,
                "action_results": json.dumps(action_results, ensure_ascii=False),
            }
            previous_outputs.append(step_record)
            self._pause_for_declared_human_confirmation(
                task_dir,
                workflow_name,
                task_title,
                provider_used,
                step_no,
                len(steps),
                str(step_dir / "output.md"),
                result.content,
                progress_callback,
                resume_step=resume_step,
            )
            if self._requires_material_gate(workflow, step, production_config):
                self._run_material_gate_or_block(
                    task_dir,
                    workflow,
                    workflow_name,
                    task_title,
                    user_input,
                    previous_outputs,
                    production_config,
                    step,
                    agent,
                    provider_used,
                    progress_callback,
                    resume_step=resume_step,
                )
            if progress_callback:
                progress_callback(
                    {
                        "event": "step_completed",
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "task": step.get("task", ""),
                        "expected_output": step.get("output", ""),
                        "output_path": str(step_dir / "output.md"),
                        "message": f"步骤完成，耗时 {elapsed_seconds} 秒",
                        "elapsed_seconds": elapsed_seconds,
                        "total_steps": len(steps),
                    }
                )
            self._pause_for_step_confirmation_if_needed(
                task_dir,
                workflow_name,
                task_title,
                provider_used,
                step_no,
                len(steps),
                str(step_dir / "output.md"),
                production_config,
                progress_callback,
                resume_step=resume_step,
            )

        step_outputs = self._collect_step_outputs(workflow, task_dir)
        final_output = self._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        self.storage.write_text(final_path, final_output)
        production_manifest = run_auto_production(task_dir, step_outputs, production_config, progress_callback=progress_callback)
        self._append_resume_summary(
            task_dir,
            workflow_name,
            task_title,
            provider_used,
            len(step_outputs),
            str(final_path),
            production_manifest["files"]["manifest"] if production_manifest else "",
            production_manifest["status"] if production_manifest else "off",
            resume_step,
        )
        if progress_callback:
            progress_callback(
                {
                    "event": "completed",
                    "workflow_name": workflow_name,
                    "task_title": task_title,
                    "task_dir": str(task_dir),
                    "provider": provider_used,
                    "step_count": len(step_outputs),
                    "final_output": str(final_path),
                    "production_manifest": production_manifest["files"]["manifest"] if production_manifest else "",
                    "production_status": production_manifest["status"] if production_manifest else "off",
                    "total_steps": len(steps),
                    "resume": True,
                    "resume_step": resume_step,
                }
            )

        return WorkflowRunResult(
            task_dir=str(task_dir),
            workflow_name=workflow_name,
            task_title=task_title,
            provider=provider_used,
            step_count=len(step_outputs),
            final_output=str(final_path),
            production_manifest=production_manifest["files"]["manifest"] if production_manifest else None,
        )

    def _requires_material_gate(self, workflow: dict, step: dict, production_config: dict | None) -> bool:
        if not isinstance(production_config, dict):
            return False
        if str(production_config.get("mode") or "").strip() != "comfy_full":
            return False
        gate = production_config.get("comfy_debug_gate") if isinstance(production_config.get("comfy_debug_gate"), dict) else {}
        if gate.get("enabled"):
            agent = str(step.get("agent") or "")
            return agent.startswith(("06_", "07_"))
        gate_step_no = self._material_step_number(workflow)
        return bool(gate_step_no and int(step.get("step") or 0) == gate_step_no)

    def _run_material_gate_or_block(
        self,
        task_dir: Path,
        workflow: dict,
        workflow_name: str,
        task_title: str,
        user_input: str,
        step_outputs: list[dict[str, str]],
        production_config: dict | None,
        step: dict,
        agent,
        provider_used: str,
        progress_callback: Callable[[dict], None] | None,
        resume_step: int | None = None,
    ) -> None:
        step_no = int(step.get("step") or 0)
        if progress_callback:
            progress_callback(
                {
                    "event": "production_update",
                    "stage": "material_gate",
                    "message": f"第 {step_no} 步正在调用 ComfyUI / RunningHub 生成素材；素材全部完成后才会进入剪辑步骤",
                    "total_steps": len(workflow.get("steps", [])),
                }
            )

        final_output = self._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        self.storage.write_text(final_path, final_output)
        gate_stage = self._material_gate_stage_for_step(step)
        gate_production_config = self._production_config_for_material_gate(production_config, gate_stage)
        production_manifest = run_auto_production(
            task_dir,
            step_outputs,
            gate_production_config,
            progress_callback=progress_callback,
            stop_after_comfyui=True,
        )
        step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
        if self._material_gate_passed(production_manifest):
            if progress_callback:
                progress_callback(
                    {
                        "event": "production_update",
                        "stage": "material_gate",
                        "message": f"第 {step_no} 步素材已全部准备完成，可以进入剪辑步骤",
                        "status": "success",
                    }
                )
            return

        if self._manual_comfy_debug_waiting(production_manifest, gate_production_config):
            summary = self._read_summary(task_dir)
            summary.update(
                {
                    "task_dir": str(task_dir),
                    "workflow": workflow_name,
                    "task_title": task_title,
                    "provider": provider_used,
                    "step_count": len(step_outputs),
                    "final_output": str(final_path),
                    "production_manifest": production_manifest.get("files", {}).get("manifest", "") if production_manifest else "",
                    "production_status": production_manifest.get("status") if production_manifest else "off",
                    "awaiting_confirmation": True,
                    "awaiting_confirmation_step": step_no,
                    "workflow_advance_mode": "step_confirm",
                    "blocked_step": step_no,
                    "blocked_reason": f"ComfyUI {gate_stage or 'all'} 调试队列等待人工确认；请按调试台顺序运行并确认满意后继续下一步",
                    "resume_from_step": step_no,
                    "resume_step": resume_step,
                    "last_step_output": str(step_dir / "output.md"),
                    "paused_at": datetime.now().isoformat(timespec="seconds"),
                }
            )
            self._write_summary(task_dir, summary)
            message = "ComfyUI 调试队列等待人工确认。请在任务输出页按顺序运行调试项，满意后继续下一步。"
            if progress_callback:
                progress_callback(
                    {
                        "event": "checkpoint",
                        "step": step_no,
                        "message": message,
                        "output_path": str(step_dir / "output.md"),
                        "total_steps": len(workflow.get("steps", [])),
                        "awaiting_confirmation": True,
                    }
                )
            raise WorkflowCheckpointPause(message)

        blocker = self._material_gate_blocker_text(production_manifest)
        self.storage.write_text(step_dir / "output.md", blocker)
        self.storage.write_json(
            step_dir / "error.json",
            {
                "step": step_no,
                "agent_id": agent.agent_id,
                "agent_name": agent.name,
                "error": "ComfyUI 素材未全部准备完成，当前素材编排步骤失败",
                "production_status": production_manifest.get("status") if production_manifest else "off",
                "production_manifest": production_manifest.get("files", {}).get("manifest", "") if production_manifest else "",
                "blocked_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self.storage.write_json(
            task_dir / "run_summary.json",
            {
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "provider": provider_used,
                "step_count": len(step_outputs),
                "final_output": str(final_path),
                "production_manifest": production_manifest.get("files", {}).get("manifest", "") if production_manifest else "",
                "production_status": production_manifest.get("status") if production_manifest else "off",
                "blocked_step": step_no,
                "blocked_reason": f"第 {step_no} 步 ComfyUI 素材未全部准备完成，未进入剪辑步骤",
                "resume_from_step": step_no,
                "resume_step": resume_step,
            },
        )
        if progress_callback:
            progress_callback(
                {
                    "event": "step_started",
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "task": step.get("task", ""),
                    "expected_output": step.get("output", ""),
                    "total_steps": len(workflow.get("steps", [])),
                }
            )
            progress_callback(
                {
                    "event": "step_error",
                    "step": step_no,
                    "agent_id": agent.agent_id,
                    "agent_name": agent.name,
                    "message": f"第 {step_no} 步素材未全部准备完成，请修正 ComfyUI / RunningHub 配置后继续",
                    "total_steps": len(workflow.get("steps", [])),
                }
            )
        raise RuntimeError(f"第 {step_no} 步 ComfyUI 素材未全部准备完成，已暂停进入剪辑。请修正 ComfyUI 工作流接口/节点映射后点击继续任务。")

    @staticmethod
    def _material_gate_passed(production_manifest: dict | None) -> bool:
        if not isinstance(production_manifest, dict):
            return False
        if production_manifest.get("status") in {"comfyui_manual_approved", "comfyui_image_manual_approved", "comfyui_video_manual_approved"}:
            composition = production_manifest.get("composition") or {}
            return bool(composition.get("manual_debug_completed"))
        composition = production_manifest.get("composition") or {}
        if composition.get("adapter_status") != "success":
            return False
        downloaded = composition.get("downloaded_files") or []
        if not downloaded:
            return False
        manifest_file = str(composition.get("adapter_manifest") or "")
        if not manifest_file:
            return False
        try:
            adapter_manifest = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
        except Exception:
            return False
        job_count = int(adapter_manifest.get("job_count") or 0)
        success_count = int(adapter_manifest.get("success_count") or 0)
        failed_count = int(adapter_manifest.get("failed_count") or 0)
        if not (job_count > 0 and success_count == job_count and failed_count == 0):
            return False
        return WorkflowEngine._adapter_manifest_downloads_complete(adapter_manifest, job_count)

    @staticmethod
    def _adapter_manifest_downloads_complete(adapter_manifest: dict, job_count: int) -> bool:
        jobs = adapter_manifest.get("jobs") if isinstance(adapter_manifest.get("jobs"), list) else []
        complete_statuses = {"success", "cached", "downloaded"}
        if jobs:
            required_jobs = [
                job
                for job in jobs
                if isinstance(job, dict)
                and str(job.get("status") or "").strip().lower() != "skipped"
            ]
            if len(required_jobs) < job_count:
                return False
            for job in required_jobs:
                status = str(job.get("status") or "").strip().lower()
                files = [
                    Path(str(path))
                    for path in (job.get("downloaded_files") or job.get("outputs") or [])
                    if str(path).strip()
                ]
                if status not in complete_statuses or not files or any(not path.is_file() for path in files):
                    return False
            return True
        downloaded_files = [
            Path(str(path))
            for path in (adapter_manifest.get("downloaded_files") or [])
            if str(path).strip()
        ]
        return len(downloaded_files) >= job_count and all(path.is_file() for path in downloaded_files)

    @staticmethod
    def _manual_comfy_debug_waiting(production_manifest: dict | None, production_config: dict | None) -> bool:
        if not isinstance(production_manifest, dict) or not isinstance(production_config, dict):
            return False
        gate = production_config.get("comfy_debug_gate") if isinstance(production_config.get("comfy_debug_gate"), dict) else {}
        return (
            bool(gate.get("enabled"))
            and str(production_manifest.get("status") or "").startswith("awaiting_comfyui_")
        )

    @staticmethod
    def _material_gate_stage_for_step(step: dict) -> str:
        agent = str(step.get("agent") or "")
        if agent.startswith("06_"):
            return "image"
        if agent.startswith("07_"):
            return "video"
        return "all"

    @staticmethod
    def _production_config_for_material_gate(production_config: dict | None, stage: str) -> dict | None:
        if not isinstance(production_config, dict):
            return production_config
        cloned = json.loads(json.dumps(production_config, ensure_ascii=False))
        gate = cloned.setdefault("comfy_debug_gate", {})
        if isinstance(gate, dict):
            gate["stage"] = stage or "all"
        return cloned

    @staticmethod
    def _material_gate_blocker_text(production_manifest: dict | None) -> str:
        status = production_manifest.get("status") if isinstance(production_manifest, dict) else "off"
        composition = production_manifest.get("composition") if isinstance(production_manifest, dict) else {}
        adapter_manifest = str((composition or {}).get("adapter_manifest") or "")
        detail = ""
        if adapter_manifest:
            try:
                data = json.loads(Path(adapter_manifest).read_text(encoding="utf-8"))
                detail = (
                    f"- ComfyUI 任务总数：{data.get('job_count', 0)}\n"
                    f"- 成功素材数：{data.get('success_count', 0)}\n"
                    f"- 失败素材数：{data.get('failed_count', 0)}\n"
                )
                jobs = data.get("jobs") or []
                first_error = next((str(job.get("error") or "") for job in jobs if job.get("error")), "")
                if first_error:
                    detail += f"- 首个错误：{first_error}\n"
            except Exception:
                detail = f"- ComfyUI 明细文件：{adapter_manifest}\n"
        return (
            "# ComfyUI 素材步骤失败：素材未准备完成\n\n"
            "系统没有进入最终剪辑步骤，因为当前 ComfyUI 素材步骤还没有全部生成并下载完成。\n\n"
            f"- 当前生产状态：{status}\n"
            f"- 素材清单：{adapter_manifest or '未生成'}\n"
            f"{detail}\n"
            "请在管理台修正 ComfyUI / RunningHub 工作流接口和节点映射，确认素材能生成后，点击“继续任务”。\n"
        )

    def _resolve_workflow_path(self, workflow_key: str) -> Path:
        candidates = [
            self.workflow_root / workflow_key,
            self.workflow_root / f"{workflow_key}.json",
            self.workflow_root / f"workflow_{workflow_key}.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate

        matches = sorted(self.workflow_root.glob(f"*{workflow_key}*.json"))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(path.name for path in matches)
            raise ValueError(f"Multiple workflows match '{workflow_key}': {names}")

        available = ", ".join(path.stem for path in sorted(self.workflow_root.glob("*.json")))
        raise FileNotFoundError(f"Workflow not found: {workflow_key}. Available: {available}")

    def _run_model_with_requirement_guard(
        self,
        system_prompt: str,
        prompt: str,
        user_input: str,
        step: dict,
        step_dir: Path,
        previous_outputs: list[dict[str, str]] | None = None,
    ):
        step_no = int(step.get("step") or 0)
        lock = build_requirement_lock(user_input)
        attempts: list[dict] = []
        result = self.api.run(system_prompt, prompt)
        if result.provider == "offline":
            return result
        validation = self._combined_output_validation(lock, result.content, step, previous_outputs or [])
        attempts.append(validation)
        if validation.get("passed"):
            self.storage.write_json(step_dir / "requirement_validation.json", {"passed": True, "attempts": attempts})
            self.storage.write_json(step_dir / "production_contract_validation.json", validation.get("production_contract") or {})
            return result

        rejected_path = step_dir / f"output_rejected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        self.storage.write_text(rejected_path, result.content)
        self.storage.write_json(
            step_dir / "requirement_validation.json",
            {
                "passed": False,
                "auto_retry_count": 0,
                "strict_employee_output": True,
                "rejected_output": rejected_path.name,
                "attempts": attempts,
            },
        )
        self.storage.write_json(step_dir / "production_contract_validation.json", validation.get("production_contract") or {})
        raise RequirementAlignmentError(validation.get("issue_details") or [])

    @staticmethod
    def _combined_output_validation(
        lock: dict,
        content: str,
        step: dict,
        previous_outputs: list[dict[str, str]],
    ) -> dict:
        requirement = validate_requirement_alignment(
            lock,
            content,
            int(step.get("step") or 0),
            str(step.get("agent") or ""),
        )
        contract = validate_production_output(step, content, lock, previous_outputs)
        issues = [*(requirement.get("issues") or []), *(contract.get("issues") or [])]
        issue_details = [
            *(requirement.get("issue_details") or []),
            *(contract.get("issue_details") or []),
        ]
        return {
            "passed": not issues,
            "step": int(step.get("step") or 0),
            "agent": str(step.get("agent") or ""),
            "issues": issues,
            "issue_details": issue_details,
            "core_topic": requirement.get("core_topic") or lock.get("core_topic") or "",
            "requirement_alignment": requirement,
            "production_contract": contract,
        }

    @staticmethod
    def _build_step_prompt(
        workflow: dict,
        step: dict,
        user_input: str,
        previous_outputs: list[dict[str, str]],
        production_config: dict | None = None,
    ) -> str:
        previous_text = "\n\n".join(
            f"## Step {item['step']} - {item['agent']}\n{item['content']}" for item in previous_outputs
        )
        if not previous_text:
            previous_text = "无。"
        scoped_context = WorkflowEngine._scoped_video_context(step, production_config)
        lock = build_requirement_lock(user_input)
        prompt_input = WorkflowEngine._scoped_user_context(step, user_input)
        locked_context = requirement_lock_prompt(lock)

        return f"""# 工作流执行任务

## 工作流
- 名称：{workflow.get("name")}
- 说明：{workflow.get("description")}

## 用户原始需求
{prompt_input}

{locked_context}

## 当前步骤
- 步骤：{step.get("step")}
- 员工：{step.get("agent")}
- 任务：{step.get("task")}
- 期望输出：{step.get("output")}

## 上游步骤输出
{previous_text}
{scoped_context}

## 执行要求
1. 只完成当前步骤，不要代替后续员工完成全部流程。
2. 严格按你的 `agent.md` 中定义的职责和输出格式交付。
3. 仅继承用户明确要求和已提供的结构化数据，不添加用户未要求的创意约束、默认风格、品牌、人物设定或生产方向。
4. 缺少完成当前步骤所必需的信息时明确指出缺失项，不自行补写；输出必须是中文 Markdown，可直接交给下一位员工继续处理。
5. 如当前步骤无法继续，输出 `human_confirmation_required: true` 和标题 `## 人工确认（阻塞）`，并说明具体缺失信息；否则不要添加待确认段落。
6. 如果需要执行受控动作，只能在输出末尾提供一个 JSON 代码块，格式为：
```json
{{"actions":[{{"action":"mkdir","params":{{"path":"demo"}}}},{{"action":"create_file","params":{{"path":"demo/readme.md","content":"内容","overwrite":false}}}},{{"action":"open_url","params":{{"url":"https://example.com"}}}},{{"action":"fetch_url","params":{{"url":"https://example.com","path":"web/example.txt","overwrite":true}}}}]}}
```
当前允许的动作只有 `mkdir`、`create_file`、`write_json`、`open_url`、`fetch_url`、`open_workspace_path`。文件路径必须使用相对路径，系统会限制写入或打开 `my_action_workspace`；网页动作只允许 http/https URL，不允许 shell 命令或任意系统路径。
"""

    @staticmethod
    def _scoped_video_context(step: dict, production_config: dict | None) -> str:
        if not isinstance(production_config, dict):
            return ""
        context = str(production_config.get("video_memory_context") or "").strip()
        if not context:
            return ""
        agent = str(step.get("agent") or "")
        allowed_prefixes = ("06_", "07_", "20_", "22_")
        if not agent.startswith(allowed_prefixes):
            return ""
        return f"\n\n## 视频输出长期记忆\n{context}\n"

    @staticmethod
    def _scoped_user_context(step: dict, user_input: str) -> str:
        original = extract_original_requirement(user_input)
        agent = str(step.get("agent") or "")
        allowed_markers: tuple[str, ...] = ()
        if agent.startswith("06_"):
            allowed_markers = (
                "## 关联资产上下文",
                "## 可复用素材库",
                "## 参考图片",
                "## 长期记忆",
            )
        elif agent.startswith(("07_", "20_", "22_")):
            allowed_markers = ("## 长期记忆",)
        context = extract_generated_context(user_input, allowed_markers)
        return f"{original}\n\n{context}".strip() if context else original

    @staticmethod
    def _build_final_output(workflow: dict, user_input: str, step_outputs: list[dict[str, str]]) -> str:
        sections = [
            f"# {workflow.get('name', '工作流')} - 最终输出",
            "",
            "## 用户原始需求",
            user_input,
            "",
            "## 工作流步骤产出",
        ]
        for item in step_outputs:
            sections.extend(
                [
                    "",
                    f"### Step {item['step']} - {item['agent']}",
                    "",
                    item["content"],
                ]
            )
        return "\n".join(sections).rstrip() + "\n"

    @staticmethod
    def _step_dir(task_dir: Path, step_no: int, agent_id: str) -> Path:
        matches = sorted(task_dir.glob(f"step_{step_no:02d}_*"))
        if matches:
            return matches[0]
        return task_dir / f"step_{step_no:02d}_{agent_id}"

    @classmethod
    def _collect_step_outputs(cls, workflow: dict, task_dir: Path, before_step: int | None = None) -> list[dict[str, str]]:
        outputs: list[dict[str, str]] = []
        for step in workflow.get("steps", []):
            step_no = int(step.get("step") or 0)
            if before_step is not None and step_no >= before_step:
                continue
            matches = sorted(task_dir.glob(f"step_{step_no:02d}_*/output.md"))
            content = matches[0].read_text(encoding="utf-8", errors="replace") if matches else ""
            outputs.append(
                {
                    "step": str(step_no),
                    "agent": str(step.get("agent") or ""),
                    "task": str(step.get("task") or ""),
                    "expected_output": str(step.get("output") or ""),
                    "output_path": str(matches[0]) if matches else "",
                    "content": content,
                    "action_results": "",
                }
            )
        return outputs

    @staticmethod
    def _summary_value(task_dir: Path, key: str) -> str:
        summary_path = task_dir / "run_summary.json"
        if not summary_path.exists():
            return ""
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return ""
        return str(summary.get(key) or "")

    @staticmethod
    def _step_confirmation_enabled(production_config: dict | None) -> bool:
        if not isinstance(production_config, dict):
            return False
        return (
            production_config.get("step_confirmation") is True
            or str(production_config.get("workflow_advance_mode") or "").strip() == "step_confirm"
        )

    @staticmethod
    def _read_summary(task_dir: Path) -> dict:
        summary_path = task_dir / "run_summary.json"
        if not summary_path.exists():
            return {}
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_summary(task_dir: Path, summary: dict) -> None:
        (task_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def _confirm_pending_step_checkpoint(self, task_dir: Path, production_config: dict | None) -> None:
        summary = self._read_summary(task_dir)
        pending_step = int(summary.get("awaiting_confirmation_step") or 0)
        if pending_step <= 0:
            return
        was_awaiting_confirmation = bool(summary.get("awaiting_confirmation"))
        confirmed = summary.setdefault("confirmed_steps", [])
        if pending_step not in confirmed:
            confirmed.append(pending_step)
        summary["awaiting_confirmation_step"] = 0
        summary["awaiting_confirmation"] = False
        summary["last_confirmed_step"] = pending_step
        summary["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
        summary["workflow_advance_mode"] = str((production_config or {}).get("workflow_advance_mode") or "auto").strip() or "auto"
        if int(summary.get("blocked_step") or 0) == pending_step and was_awaiting_confirmation:
            summary["blocked_step"] = 0
            summary["blocked_reason"] = ""
        self._write_summary(task_dir, summary)

    def _pause_for_step_confirmation_if_needed(
        self,
        task_dir: Path,
        workflow_name: str,
        task_title: str,
        provider: str,
        step_no: int,
        total_steps: int,
        output_path: str,
        production_config: dict | None,
        progress_callback: Callable[[dict], None] | None,
        resume_step: int | None = None,
    ) -> None:
        if not self._step_confirmation_enabled(production_config):
            return
        summary = self._read_summary(task_dir)
        confirmed = summary.get("confirmed_steps") if isinstance(summary.get("confirmed_steps"), list) else []
        if step_no in confirmed:
            return
        summary.update(
            {
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "provider": provider,
                "step_count": step_no,
                "awaiting_confirmation": True,
                "awaiting_confirmation_step": step_no,
                "workflow_advance_mode": "step_confirm",
                "blocked_step": step_no,
                "blocked_reason": f"第 {step_no} 步已完成，等待人工确认后继续",
                "resume_from_step": step_no + 1 if step_no < total_steps else None,
                "resume_step": resume_step,
                "last_step_output": output_path,
                "paused_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._write_summary(task_dir, summary)
        message = f"第 {step_no} 步已完成，等待确认。确认输出后点击“继续下一步”。"
        if progress_callback:
            progress_callback(
                {
                    "event": "checkpoint",
                    "step": step_no,
                    "message": message,
                    "output_path": output_path,
                    "total_steps": total_steps,
                    "awaiting_confirmation": True,
                }
            )
        raise WorkflowCheckpointPause(message)

    def _pause_for_declared_human_confirmation(
        self,
        task_dir: Path,
        workflow_name: str,
        task_title: str,
        provider: str,
        step_no: int,
        total_steps: int,
        output_path: str,
        content: str,
        progress_callback: Callable[[dict], None] | None,
        resume_step: int | None = None,
    ) -> None:
        if not declares_human_confirmation(content):
            return
        summary = self._read_summary(task_dir)
        reason = f"第 {step_no} 步声明了会改变生产方向的阻塞型人工确认"
        summary.update(
            {
                "status": "blocked",
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "provider": provider,
                "step_count": step_no,
                "awaiting_confirmation": True,
                "awaiting_confirmation_step": step_no,
                "confirmation_kind": "human_required",
                "blocked_step": step_no,
                "blocked_reason": reason,
                "resume_from_step": step_no + 1 if step_no < total_steps else None,
                "resume_step": resume_step,
                "last_step_output": output_path,
                "paused_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self._write_summary(task_dir, summary)
        if progress_callback:
            progress_callback(
                {
                    "event": "checkpoint",
                    "step": step_no,
                    "message": reason,
                    "output_path": output_path,
                    "total_steps": total_steps,
                    "awaiting_confirmation": True,
                    "confirmation_kind": "human_required",
                }
            )
        raise WorkflowCheckpointPause(reason)

    @classmethod
    def _first_incomplete_step(cls, workflow: dict, task_dir: Path) -> int | None:
        material_step_no = cls._material_step_number(workflow)
        image_step_no = cls._step_number_for_agent_prefix(workflow, "06_")
        video_step_no = cls._step_number_for_agent_prefix(workflow, "07_")
        if material_step_no:
            material_gate_passed = cls._task_material_gate_passed(task_dir)
            if not material_gate_passed and cls._task_uses_comfy_full(task_dir) and cls._material_step_needs_gate_resume(task_dir, material_step_no):
                return material_step_no
            summary_path = task_dir / "run_summary.json"
            if not material_gate_passed and summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
                except json.JSONDecodeError:
                    summary = {}
                blocked_step = int(summary.get("blocked_step") or 0)
                blocked_reason = str(summary.get("blocked_reason") or "")
                blocked_reason_lower = blocked_reason.lower()
                production_status = str(summary.get("production_status") or "").strip().lower()
                if production_status == "awaiting_comfyui_image_debug":
                    return cls._next_step_number(workflow, image_step_no) if cls._manual_debug_stage_complete(task_dir, "image") else image_step_no
                if production_status == "awaiting_comfyui_video_debug":
                    return cls._next_step_number(workflow, video_step_no) if cls._manual_debug_stage_complete(task_dir, "video") else video_step_no
                if production_status in {"awaiting_comfyui_debug", "comfyui_partial_failed", "comfyui_adapter_failed", "quality_failed"}:
                    return material_step_no
                if blocked_step >= material_step_no and "comfyui" in blocked_reason_lower and ("素材" in blocked_reason or "material" in blocked_reason_lower):
                    return material_step_no
            if not material_gate_passed:
                for later_step in workflow.get("steps", []):
                    later_step_no = int(later_step.get("step") or 0)
                    if later_step_no <= material_step_no:
                        continue
                    for error_path in sorted(task_dir.glob(f"step_{later_step_no:02d}_*/error.json")):
                        try:
                            error_data = json.loads(error_path.read_text(encoding="utf-8-sig"))
                        except json.JSONDecodeError:
                            error_data = {}
                        error_text = json.dumps(error_data, ensure_ascii=False)
                        error_text_lower = error_text.lower()
                        if "comfyui" in error_text_lower and ("素材" in error_text or "material" in error_text_lower):
                            return material_step_no
        for step in workflow.get("steps", []):
            step_no = int(step.get("step") or 0)
            if step_no <= 0:
                continue
            step_dirs = sorted(task_dir.glob(f"step_{step_no:02d}_*"))
            if not step_dirs:
                return step_no
            step_dir = step_dirs[0]
            if (step_dir / "error.json").exists():
                return step_no
            output_path = step_dir / "output.md"
            if not output_path.exists():
                return step_no
            if not output_path.read_text(encoding="utf-8", errors="replace").strip():
                return step_no
        return None

    @classmethod
    def _task_material_gate_passed(cls, task_dir: Path) -> bool:
        manifest_path = task_dir / "production_manifest.json"
        if not manifest_path.exists():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return False
        return cls._material_gate_passed(manifest if isinstance(manifest, dict) else {})

    @staticmethod
    def _task_uses_comfy_full(task_dir: Path) -> bool:
        input_path = task_dir / "input.md"
        if not input_path.exists():
            return False
        return "comfy_full" in input_path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _material_step_needs_gate_resume(task_dir: Path, material_step_no: int) -> bool:
        manifest_path = task_dir / "production_manifest.json"
        if manifest_path.exists():
            return False
        step_dirs = sorted(task_dir.glob(f"step_{material_step_no:02d}_*"))
        if not step_dirs:
            return False
        step_dir = step_dirs[0]
        if (step_dir / "error.json").exists():
            return False
        output_path = step_dir / "output.md"
        return output_path.exists() and bool(output_path.read_text(encoding="utf-8", errors="replace").strip())

    @staticmethod
    def _material_step_number(workflow: dict) -> int | None:
        steps = workflow.get("steps", [])
        for step in steps:
            agent = str(step.get("agent") or "")
            if agent.startswith("21_"):
                step_no = int(step.get("step") or 0)
                return step_no or None
        for step in steps:
            agent = str(step.get("agent") or "")
            if agent.startswith("07_"):
                step_no = int(step.get("step") or 0)
                return step_no or None
        return None

    @staticmethod
    def _step_number_for_agent_prefix(workflow: dict, prefix: str) -> int | None:
        for step in workflow.get("steps", []):
            if str(step.get("agent") or "").startswith(prefix):
                step_no = int(step.get("step") or 0)
                return step_no or None
        return None

    @staticmethod
    def _normalize_workflow(workflow: dict) -> dict:
        if not isinstance(workflow, dict):
            raise ValueError("Workflow JSON must be an object")
        normalized = dict(workflow)
        raw_steps = normalized.get("steps") if isinstance(normalized.get("steps"), list) else []
        steps: list[dict] = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                raise ValueError(f"Workflow step {index} must be an object")
            step_no = int(raw_step.get("step") or raw_step.get("order") or index)
            agent = str(raw_step.get("agent") or raw_step.get("agent_id") or "").strip()
            task = str(raw_step.get("task") or raw_step.get("instruction") or "").strip()
            output = str(raw_step.get("output") or raw_step.get("expected_output") or "").strip()
            if not agent:
                raise ValueError(f"Workflow step {index} is missing agent/agent_id")
            if not task:
                raise ValueError(f"Workflow step {index} is missing task/instruction")
            if not output:
                raise ValueError(f"Workflow step {index} is missing output/expected_output")
            step = dict(raw_step)
            step["step"] = step_no
            step["agent"] = agent
            step["task"] = task
            step["output"] = output
            steps.append(step)
        normalized["steps"] = steps
        return normalized

    def _load_workflow(self, workflow_path: Path) -> dict:
        return self._normalize_workflow(json.loads(workflow_path.read_text(encoding="utf-8-sig")))

    @staticmethod
    def _next_step_number(workflow: dict, current_step_no: int | None) -> int | None:
        if not current_step_no:
            return None
        later = sorted(int(step.get("step") or 0) for step in workflow.get("steps", []) if int(step.get("step") or 0) > current_step_no)
        return later[0] if later else None

    @staticmethod
    def _manual_debug_stage_complete(task_dir: Path, stage: str) -> bool:
        payload_path = task_dir / "comfyui" / "comfyui_payload.json"
        state_path = task_dir / "comfyui" / "manual_debug_state.json"
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8-sig"))
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return False
        source_key = "image_prompts" if stage == "image" else "video_prompts"
        values = payload.get(source_key) if isinstance(payload, dict) else []
        if not isinstance(values, list) or not values:
            return False
        state_items = state.get("items") if isinstance(state, dict) and isinstance(state.get("items"), dict) else {}
        for index, raw in enumerate(values, 1):
            entry = raw if isinstance(raw, dict) else {"prompt": str(raw)}
            workflow_id = str(entry.get("workflow_id") or entry.get("workflow") or ("01_base_asset_image" if stage == "image" else "06_i2v_first_frame")).strip()
            mode = str(entry.get("workflow_mode") or entry.get("image_task_mode") or entry.get("video_task_mode") or entry.get("task_type") or entry.get("asset_tag") or "").strip()
            item_id = str(entry.get("id") or entry.get("shot_id") or entry.get("scene_id") or f"{source_key}_{index:03d}").strip()
            state_key = f"{workflow_id}:{mode or 'default'}:{item_id}"
            if not isinstance(state_items.get(state_key), dict) or state_items[state_key].get("status") != "approved":
                return False
        return True

    @staticmethod
    def _should_inject_manual_image_assets(agent_id: str, already_added: bool) -> bool:
        return (not already_added) and str(agent_id or "").startswith("07_")

    @staticmethod
    def _manual_debug_assets_output(task_dir: Path, stage: str) -> dict[str, str] | None:
        state_path = task_dir / "comfyui" / "manual_debug_state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            return None
        state_items = state.get("items") if isinstance(state, dict) and isinstance(state.get("items"), dict) else {}
        lines = []
        for item_id, item in state_items.items():
            if not isinstance(item, dict) or item.get("status") != "approved":
                continue
            if stage == "image" and not any(token in str(item_id) for token in ("image", "keyframe", "base", "turnaround", "style", "cover", "inpaint", "background")):
                continue
            files = [str(file) for file in item.get("files") or [] if file]
            if files:
                normalized_files = [file.replace("\\", "/").replace("comfyui_manual_debug/", "comfyui/manual_debug/") for file in files]
                lines.append(f"- {item_id}: " + "；".join(normalized_files))
        if not lines:
            return None
        return {
            "step": "ComfyUI",
            "agent": "comfyui_manual_debug",
            "task": "已确认的 ComfyUI 图片素材",
            "expected_output": "供后续视频生成员工引用的图片/关键帧路径",
            "output_path": str(state_path),
            "content": "## 已确认的 ComfyUI 图片素材\n\n"
            + "\n".join(lines)
            + "\n\n07_视频生成执行员必须原样引用这些已确认图片/关键帧路径作为 reference_image / first_frame_image / last_frame_image。"
            + "路径必须保持 `comfyui/manual_debug/...`，不要改写成 `comfyui_manual_debug/...`，不要重新假设图片尚未生成。",
            "action_results": "",
        }

    @staticmethod
    def _append_rerun_summary(
        task_dir: Path,
        step_no: int,
        agent_id: str,
        provider: str,
        output_path: str,
        final_path: str,
    ) -> None:
        summary_path = task_dir / "run_summary.json"
        summary = {}
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                summary = {}
        history = summary.setdefault("rerun_history", [])
        history.append(
            {
                "step": step_no,
                "agent": agent_id,
                "provider": provider,
                "output_path": output_path,
                "final_output": final_path,
                "rerun_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        summary["provider"] = provider
        summary["final_output"] = final_path
        workflow_path = task_dir / "workflow.json"
        total_steps = 0
        if workflow_path.is_file():
            try:
                workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
                total_steps = len(workflow.get("steps") or []) if isinstance(workflow, dict) else 0
            except (json.JSONDecodeError, OSError):
                total_steps = 0
        completed_steps = len(list(task_dir.glob("step_*_*/output.md")))
        fully_completed = bool(total_steps and completed_steps >= total_steps)
        summary["status"] = "completed" if fully_completed else "partial"
        summary["employee_workflow_status"] = "completed" if fully_completed else "partial"
        summary["step_count"] = min(completed_steps, total_steps) if total_steps else completed_steps
        summary["total_steps"] = total_steps or int(summary.get("total_steps") or 0)
        summary["current_step"] = step_no
        summary["awaiting_confirmation"] = False
        for key in (
            "error",
            "traceback",
            "failed_at",
            "blocked_step",
            "blocked_reason",
            "awaiting_confirmation_step",
        ):
            summary.pop(key, None)
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _append_resume_summary(
        task_dir: Path,
        workflow_name: str,
        task_title: str,
        provider: str,
        step_count: int,
        final_path: str,
        production_manifest: str,
        production_status: str,
        resume_step: int | None,
    ) -> None:
        summary_path = task_dir / "run_summary.json"
        summary = {}
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        summary.update(
            {
                "status": "completed",
                "employee_workflow_status": "completed",
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "provider": provider,
                "step_count": step_count,
                "final_output": final_path,
                "production_manifest": production_manifest,
                "production_status": production_status,
                "total_steps": step_count,
                "current_step": step_count,
                "awaiting_confirmation": False,
            }
        )
        for key in (
            "error",
            "traceback",
            "failed_at",
            "blocked_step",
            "blocked_reason",
            "awaiting_confirmation_step",
            "confirmation_kind",
        ):
            summary.pop(key, None)
        history = summary.setdefault("resume_history", [])
        history.append(
            {
                "resume_from_step": resume_step,
                "provider": provider,
                "final_output": final_path,
                "resumed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def _restore_production_config(self, task_dir: Path, incoming: dict | None) -> dict:
        """Persist non-secret task production settings and reuse them on resume.

        Browser state is transient; task mode, dimensions, TTS provider and workflow
        routing must survive a refresh or a step rerun. Runtime credentials are never
        written to the task directory and always come from the current request/cache.
        """
        snapshot_path = task_dir / "production_config_snapshot.json"
        saved: dict = {}
        if snapshot_path.is_file():
            try:
                loaded = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
                saved = loaded if isinstance(loaded, dict) else {}
            except (json.JSONDecodeError, OSError):
                saved = {}
        merged = self._deep_merge_dicts(saved, incoming if isinstance(incoming, dict) else {})
        if "video_memory_context" in saved or (
            isinstance(incoming, dict) and "video_memory_context" in incoming
        ):
            merged["video_memory_context"] = load_long_term_memory_context(self.workspace_root / "my_memory")
        sanitized = self._sanitize_production_config(merged)
        self.storage.write_json(snapshot_path, sanitized)
        return merged

    @classmethod
    def _sanitize_production_config(cls, value):
        secret_keys = {"api_key", "access_token", "token", "password", "secret", "authorization"}
        def is_secret_key(key: object) -> bool:
            normalized = str(key).strip().lower()
            return normalized in secret_keys or normalized.endswith("_api_key") or normalized.endswith("_token")

        if isinstance(value, dict):
            return {
                str(key): cls._sanitize_production_config(item)
                for key, item in value.items()
                if not is_secret_key(key)
            }
        if isinstance(value, list):
            return [cls._sanitize_production_config(item) for item in value]
        return value

    @classmethod
    def _deep_merge_dicts(cls, base: dict, override: dict) -> dict:
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge_dicts(merged[key], value)
            elif value not in (None, ""):
                merged[key] = value
        return merged
