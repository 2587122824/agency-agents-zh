from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


SUPPORTED_VISUAL_PROVIDERS = {"runninghub", "comfy_mcp", "local_comfyui"}


def normalize_visual_provider(value: Any, *, base_url: str = "", endpoint: str = "", compose_tool: str = "") -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"runninghub", "rh", "cloud_runninghub"}:
        return "runninghub"
    if text in {"comfy_mcp", "comfyui_mcp", "mcp", "comfy_cloud_mcp", "cloud_comfy_mcp"}:
        return "comfy_mcp"
    if text in {"local_comfyui", "local", "comfyui", "self_hosted", "local_api"}:
        return "local_comfyui"

    if _looks_like_runninghub(base_url, endpoint, compose_tool):
        return "runninghub"
    if _looks_like_local_comfyui(base_url, endpoint):
        return "local_comfyui"
    return "runninghub"


def build_visual_provider_profile(compose_config: dict[str, Any] | None) -> dict[str, Any]:
    config = compose_config if isinstance(compose_config, dict) else {}
    provider = normalize_visual_provider(
        config.get("visual_provider") or config.get("provider"),
        base_url=str(config.get("base_url") or ""),
        endpoint=str(config.get("workflow_endpoint") or config.get("endpoint") or ""),
        compose_tool=str(config.get("tool") or ""),
    )
    base_url = str(config.get("base_url") or "").strip()
    endpoint = str(config.get("workflow_endpoint") or config.get("endpoint") or "").strip()
    mcp_url = str(config.get("comfy_mcp_url") or config.get("mcp_url") or "").strip()

    reason = ""
    supported = provider in SUPPORTED_VISUAL_PROVIDERS
    if provider == "comfy_mcp" and not mcp_url:
        supported = False
        reason = "Comfy MCP provider selected but comfy_mcp_url is missing"
    elif provider == "runninghub" and not endpoint:
        supported = False
        reason = "runninghub provider selected without a workflow endpoint"
    elif provider == "local_comfyui" and not base_url:
        supported = False
        reason = "local_comfyui provider selected without a base URL"

    return {
        "provider": provider,
        "supported": supported,
        "reason": reason,
        "base_url": base_url,
        "endpoint": endpoint,
        "comfy_mcp_url": mcp_url,
        "compose_tool": str(config.get("tool") or "").strip(),
        "discovery_only": False,
        "source": str(config.get("visual_provider") or config.get("provider") or "auto").strip() or "auto",
    }


def _looks_like_runninghub(base_url: str, endpoint: str, compose_tool: str) -> bool:
    text = " ".join([base_url, endpoint, compose_tool]).lower()
    return "runninghub" in text or endpoint.startswith(("/run/workflow/", "/run/ai-app/"))


def _looks_like_local_comfyui(base_url: str, endpoint: str) -> bool:
    if not base_url and not endpoint:
        return False
    url = (base_url or endpoint).strip().lower()
    if url.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1", "https://localhost")):
        return True
    if endpoint.startswith("/prompt") or endpoint.startswith("/api/"):
        return True
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc and parsed.hostname in {"127.0.0.1", "localhost"}
