from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ActionResult:
    action: str
    status: str
    target: str
    message: str


class ActionExecutor:
    """Execute a conservative subset of file actions requested by agents.

    All paths are constrained to action_root. The executor intentionally starts
    small: directories, text files, and JSON files. Shell commands, deletes,
    overwrites without explicit opt-in, and external API calls are left for a
    later approval layer.
    """

    SUPPORTED_ACTIONS = {"mkdir", "create_file", "write_json"}

    def __init__(self, action_root: Path) -> None:
        self.action_root = action_root.resolve()
        self.action_root.mkdir(parents=True, exist_ok=True)

    def execute_from_text(self, text: str, task_dir: Path) -> list[dict[str, Any]]:
        actions = self.extract_actions(text)
        results = [asdict(self.execute(action)) for action in actions]
        if results:
            log_path = task_dir / "action_log.json"
            existing: list[dict[str, Any]] = []
            if log_path.exists():
                existing = json.loads(log_path.read_text(encoding="utf-8"))
            existing.extend(results)
            log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return results

    def extract_actions(self, text: str) -> list[dict[str, Any]]:
        blocks = self._json_blocks(text)
        actions: list[dict[str, Any]] = []
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("actions"), list):
                actions.extend(item for item in data["actions"] if isinstance(item, dict))
            elif isinstance(data, list):
                actions.extend(item for item in data if isinstance(item, dict))
        return actions

    def execute(self, item: dict[str, Any]) -> ActionResult:
        action = str(item.get("action") or "").strip()
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if action not in self.SUPPORTED_ACTIONS:
            return ActionResult(action=action or "unknown", status="skipped", target="", message="unsupported action")

        try:
            if action == "mkdir":
                target = self._safe_path(str(params.get("path") or ""))
                target.mkdir(parents=True, exist_ok=True)
                return ActionResult(action=action, status="done", target=str(target), message="directory created")

            if action == "create_file":
                target = self._safe_path(str(params.get("path") or ""))
                overwrite = bool(params.get("overwrite"))
                if target.exists() and not overwrite:
                    return ActionResult(action=action, status="blocked", target=str(target), message="file exists; overwrite not enabled")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(params.get("content") or ""), encoding="utf-8")
                return ActionResult(action=action, status="done", target=str(target), message="file written")

            if action == "write_json":
                target = self._safe_path(str(params.get("path") or ""))
                overwrite = bool(params.get("overwrite"))
                if target.exists() and not overwrite:
                    return ActionResult(action=action, status="blocked", target=str(target), message="file exists; overwrite not enabled")
                data = params.get("data")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return ActionResult(action=action, status="done", target=str(target), message="json written")
        except Exception as exc:
            return ActionResult(action=action, status="error", target=str(params.get("path") or ""), message=str(exc))

        return ActionResult(action=action, status="skipped", target="", message="no handler")

    def _safe_path(self, path_text: str) -> Path:
        if not path_text:
            raise ValueError("path is required")
        if "\x00" in path_text:
            raise ValueError("invalid path")
        path = Path(path_text)
        if path.is_absolute():
            raise ValueError("absolute paths are not allowed")
        target = (self.action_root / path).resolve()
        try:
            target.relative_to(self.action_root)
        except ValueError as exc:
            raise ValueError("path escapes action workspace") from exc
        return target

    @staticmethod
    def _json_blocks(text: str) -> list[str]:
        blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            blocks.append(stripped)
        return blocks
