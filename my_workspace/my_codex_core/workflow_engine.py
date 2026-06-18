from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .codex_api import CodexAPI
from .action_executor import ActionExecutor
from .production_pipeline import run_auto_production
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
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow_name = workflow.get("name") or workflow_path.stem
        task_title = (task_title or "").strip()
        steps = workflow.get("steps", [])
        task_dir = self.storage.create_task_dir(workflow_path.stem, task_title=task_title)
        agents = self.staff_loader.load_all()

        self.storage.write_json(task_dir / "workflow.json", workflow)
        self.storage.write_text(task_dir / "input.md", user_input)
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

        for step in steps:
            step_no = int(step["step"])
            agent = self.staff_loader.resolve_agent(agents, step["agent"])
            step_dir = task_dir / f"step_{step_no:02d}_{agent.agent_id}"
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
                result = self.api.run(agent.prompt, prompt)
            except Exception as exc:
                error_text = (
                    "# 当前步骤执行失败\n\n"
                    f"- 步骤：{step_no}\n"
                    f"- 员工：{agent.agent_id}\n"
                    f"- 错误：{exc}\n\n"
                    "如果使用本地 Ollama 模型，通常是模型生成时间超过超时设置。"
                    "可以在管理台把 `模型超时` 调到 900 秒或 1800 秒后重试。\n"
                )
                self.storage.write_text(step_dir / "output.md", error_text)
                self.storage.write_json(
                    step_dir / "error.json",
                    {
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "error": str(exc),
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
            if self._requires_material_gate(step, production_config):
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

        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
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

        result = self.api.run(agent.prompt, prompt)
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

        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow_name = workflow.get("name") or task_dir.name
        task_title = self._summary_value(task_dir, "task_title")
        user_input = input_path.read_text(encoding="utf-8", errors="replace")
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
        provider_used = self._summary_value(task_dir, "provider") or "offline"

        for step in steps:
            step_no = int(step.get("step") or 0)
            if resume_step is None or step_no < resume_step:
                continue
            agent = self.staff_loader.resolve_agent(agents, step["agent"])
            step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
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
                result = self.api.run(agent.prompt, prompt)
            except Exception as exc:
                error_text = (
                    "# 当前步骤执行失败\n\n"
                    f"- 步骤：{step_no}\n"
                    f"- 员工：{agent.agent_id}\n"
                    f"- 错误：{exc}\n\n"
                    "可以在管理台修正模型/API/上下文后点击“继续任务”，系统会从这个步骤继续执行。\n"
                )
                self.storage.write_text(step_dir / "output.md", error_text)
                self.storage.write_json(
                    step_dir / "error.json",
                    {
                        "step": step_no,
                        "agent_id": agent.agent_id,
                        "agent_name": agent.name,
                        "error": str(exc),
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
            if self._requires_material_gate(step, production_config):
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

    def _requires_material_gate(self, step: dict, production_config: dict | None) -> bool:
        if not isinstance(production_config, dict):
            return False
        if str(production_config.get("mode") or "").strip() != "comfy_full":
            return False
        return str(step.get("agent") or "").startswith("21_")

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
        production_manifest = run_auto_production(
            task_dir,
            step_outputs,
            production_config,
            progress_callback=progress_callback,
            stop_after_comfyui=True,
        )
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

        blocker = self._material_gate_blocker_text(production_manifest)
        step_dir = self._step_dir(task_dir, step_no, agent.agent_id)
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
        return job_count > 0 and success_count == job_count and failed_count == 0

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

        return f"""# 工作流执行任务

## 工作流
- 名称：{workflow.get("name")}
- 说明：{workflow.get("description")}

## 用户原始需求
{user_input}

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
3. 如果信息不足，使用合理默认假设，并在输出中列出“待确认信息”。
4. 输出必须是中文 Markdown，可直接交给下一位员工继续处理。
5. 如果需要执行受控动作，只能在输出末尾提供一个 JSON 代码块，格式为：
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
        allowed_prefixes = ("06_", "07_", "20_", "21_", "22_")
        if not agent.startswith(allowed_prefixes):
            return ""
        return f"\n\n## 视频输出长期记忆\n{context}\n"

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

    @classmethod
    def _first_incomplete_step(cls, workflow: dict, task_dir: Path) -> int | None:
        material_step_no = cls._material_step_number(workflow)
        if material_step_no:
            summary_path = task_dir / "run_summary.json"
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
                except json.JSONDecodeError:
                    summary = {}
                blocked_step = int(summary.get("blocked_step") or 0)
                blocked_reason = str(summary.get("blocked_reason") or "")
                blocked_reason_lower = blocked_reason.lower()
                production_status = str(summary.get("production_status") or "").strip().lower()
                if production_status in {"comfyui_partial_failed", "comfyui_adapter_failed", "quality_failed"}:
                    return material_step_no
                if blocked_step >= material_step_no and "comfyui" in blocked_reason_lower and ("素材" in blocked_reason or "material" in blocked_reason_lower):
                    return material_step_no
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

    @staticmethod
    def _material_step_number(workflow: dict) -> int | None:
        for step in workflow.get("steps", []):
            agent = str(step.get("agent") or "")
            if agent.startswith("21_"):
                step_no = int(step.get("step") or 0)
                return step_no or None
        return None

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
                "task_dir": str(task_dir),
                "workflow": workflow_name,
                "task_title": task_title,
                "provider": provider,
                "step_count": step_count,
                "final_output": final_path,
                "production_manifest": production_manifest,
                "production_status": production_status,
            }
        )
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
