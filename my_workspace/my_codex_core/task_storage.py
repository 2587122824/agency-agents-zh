from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path


class TaskStorage:
    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.output_root.mkdir(parents=True, exist_ok=True)

    def create_task_dir(self, workflow_name: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in workflow_name)
        task_dir = self.output_root / f"task_{timestamp}_{safe_name}"
        task_dir.mkdir(parents=True, exist_ok=False)
        return task_dir

    @staticmethod
    def write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def write_json(path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if is_dataclass(data):
            data = asdict(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
