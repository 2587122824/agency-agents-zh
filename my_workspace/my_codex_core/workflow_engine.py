from __future__ import annotations

import json
from dataclasses import dataclass
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


class WorkflowEngine:
    def __init__(
        self,
        workspace_root: Path,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.staff_root = workspace_root / "my_custom_staff"
        self.workflow_root = workspace_root / "my_workflows"
        self.output_root = workspace_root / "my_task_output"
        self.action_root = workspace_root / "my_action_workspace"
        self.staff_loader = StaffLoader(self.staff_root)
        self.storage = TaskStorage(self.output_root)
        self.api = CodexAPI(provider=provider, model=model, api_key=api_key, base_url=base_url)
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
            prompt = self._build_step_prompt(workflow, step, user_input, previous_outputs)
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

            result = self.api.run(agent.prompt, prompt)
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
                        "total_steps": len(steps),
                    }
                )

        final_output = self._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        self.storage.write_text(final_path, final_output)
        production_manifest = run_auto_production(task_dir, step_outputs, production_config)
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
    def _build_step_prompt(workflow: dict, step: dict, user_input: str, previous_outputs: list[dict[str, str]]) -> str:
        previous_text = "\n\n".join(
            f"## Step {item['step']} - {item['agent']}\n{item['content']}" for item in previous_outputs
        )
        if not previous_text:
            previous_text = "无。"

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

## 执行要求
1. 只完成当前步骤，不要代替后续员工完成全部流程。
2. 严格按你的 `agent.md` 中定义的职责和输出格式交付。
3. 如果信息不足，使用合理默认假设，并在输出中列出“待确认信息”。
4. 输出必须是中文 Markdown，可直接交给下一位员工继续处理。
5. 如果需要执行文件类动作，只能在输出末尾提供一个 JSON 代码块，格式为：
```json
{{"actions":[{{"action":"mkdir","params":{{"path":"demo"}}}},{{"action":"create_file","params":{{"path":"demo/readme.md","content":"内容","overwrite":false}}}}]}}
```
当前允许的动作只有 `mkdir`、`create_file`、`write_json`。动作路径必须使用相对路径，系统会限制写入 `my_action_workspace`。
"""

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
