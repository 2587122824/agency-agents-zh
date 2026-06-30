from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ComfyMCPAdapter:
    """Discovery-first Comfy MCP adapter scaffold.

    The current project can now record Comfy MCP as a visual backend choice
    without changing the production DAG. Real MCP execution can be wired later
    once the protocol client and credentials flow are finalized.
    """

    def __init__(self, mcp_url: str, api_key: str = "", progress_callback=None) -> None:
        self.mcp_url = str(mcp_url or "").strip()
        self.api_key = str(api_key or "").strip()
        self.progress_callback = progress_callback

    def run(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "comfy_mcp_manifest.json"
        profile = {
            "provider": "comfy_mcp",
            "status": "skipped",
            "reason": "Comfy MCP execution is scaffolded but not yet wired into the runtime; use it for discovery/template selection first."
            if self.mcp_url
            else "Comfy MCP URL is missing",
            "mcp_url": self.mcp_url,
            "api_key_provided": bool(self.api_key),
            "discovery_mode": True,
            "capabilities": self.capabilities(),
            "payload_hint": self._payload_hint(comfyui_payload),
            "workflow_endpoint": str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip(),
        }
        manifest_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._emit(
            "Comfy MCP 已作为视觉后端预留到生产配置中；当前版本只做模板发现与配置占位。",
            status=profile["status"],
            provider="comfy_mcp",
            mcp_url=self.mcp_url,
        )
        return {
            "status": profile["status"],
            "reason": profile["reason"],
            "manifest_file": str(manifest_path),
            "downloaded_files": [],
            "capabilities": profile["capabilities"],
            "discovery_mode": True,
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "search_templates": True,
            "search_models": True,
            "search_nodes": True,
            "submit_workflow": False,
            "upload_file": False,
            "get_job_status": False,
            "get_output": False,
            "use_previous_output": False,
            "cancel_job": False,
            "get_queue": False,
            "save_workflow": False,
            "share_workflow": False,
            "import_shared_workflow": False,
        }

    def _payload_hint(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        return {
            "keys": sorted(str(key) for key in payload.keys())[:40],
            "has_visual_jobs": bool(payload.get("image_prompts") or payload.get("video_prompts") or payload.get("production_intents")),
            "working_width": payload.get("width") or "",
            "working_height": payload.get("height") or "",
            "fps": payload.get("fps") or "",
        }

    def _emit(self, message: str, **extra: Any) -> None:
        if not self.progress_callback:
            return
        event = {"event": "production_update", "stage": "comfyui", "message": message}
        event.update(extra)
        self.progress_callback(event)
