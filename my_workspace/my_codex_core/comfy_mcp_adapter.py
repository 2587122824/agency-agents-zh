from __future__ import annotations

import base64
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urljoin, urlparse


class ComfyMCPAdapter:
    """Comfy MCP visual backend adapter.

    The MCP ecosystem around ComfyUI is still young, so this adapter is
    deliberately tolerant instead of binding to one server implementation.  It
    first discovers tools, then tries the MCP JSON-RPC shape and a few common
    REST wrappers.  Returned URLs, data URLs, and local file paths are copied
    into the task output directory so the rest of the production pipeline can
    keep using the same durable artifact contract as RunningHub.
    """

    MAX_RESPONSE_BYTES = 4_000_000
    NETWORK_RETRY_ATTEMPTS = 3
    DOWNLOAD_TYPES = {"mp4", "mov", "webm", "m4v", "mp3", "wav", "aac", "png", "jpg", "jpeg", "webp"}
    SUBMIT_TOOL_CANDIDATES = (
        "submit_workflow",
        "run_workflow",
        "queue_workflow",
        "queue_prompt",
        "comfyui_submit_workflow",
        "comfyui_queue_prompt",
        "generate_video",
        "generate_image",
    )
    STATUS_TOOL_CANDIDATES = (
        "get_job_status",
        "job_status",
        "get_task_status",
        "task_status",
        "get_prompt_status",
        "get_history",
    )
    OUTPUT_TOOL_CANDIDATES = (
        "get_output",
        "get_outputs",
        "get_job_output",
        "get_task_output",
        "get_history",
    )
    WORKFLOW_DISCOVERY_TOOL_CANDIDATES = (
        "list_workflows",
        "search_workflows",
        "get_workflows",
        "list_templates",
        "search_templates",
        "get_templates",
        "comfyui_list_workflows",
        "comfyui_search_workflows",
        "comfyui_list_templates",
    )

    def __init__(self, mcp_url: str, api_key: str = "", progress_callback=None) -> None:
        self.mcp_url = str(mcp_url or "").strip().rstrip("/")
        self.api_key = str(api_key or "").strip()
        self.progress_callback = progress_callback
        self._request_id = 0

    def run(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "comfy_mcp_manifest.json"
        if not self.mcp_url:
            manifest = self._manifest(
                status="skipped",
                reason="Comfy MCP URL is missing",
                compose_config=compose_config,
                payload=comfyui_payload,
            )
            self._write_json(manifest_path, manifest)
            return self._result(manifest, manifest_path)

        started_at = time.time()
        discovery = self.discover()
        request_payload = self._build_tool_arguments(comfyui_payload, compose_config)
        tool_name = self._select_tool(
            compose_config,
            discovery.get("tools") if isinstance(discovery.get("tools"), list) else [],
            self.SUBMIT_TOOL_CANDIDATES,
            explicit_keys=("comfy_mcp_tool", "mcp_tool", "submit_tool"),
        )
        submit_response: dict[str, Any] = {}
        final_response: dict[str, Any] = {}
        status = "failed"
        reason = ""

        try:
            if not tool_name:
                tool_name = self.SUBMIT_TOOL_CANDIDATES[0]
                self._emit("Comfy MCP 未发现可用工具列表，尝试默认 submit_workflow 调用", tool=tool_name)
            submit_response = self._call_tool(tool_name, request_payload)
            remote_job_id = self._first_identifier(submit_response)
            final_response = self._poll_if_possible(remote_job_id, discovery, compose_config, submit_response)
            combined_response = {
                "submit_tool": tool_name,
                "submit_response": submit_response,
                "final_response": final_response,
            }
            downloaded_files = self._collect_artifacts(combined_response, output_dir)
            if downloaded_files:
                status = "downloaded"
                reason = f"Comfy MCP returned {len(downloaded_files)} durable artifact(s)"
            elif self._is_success_response(final_response) or self._is_success_response(submit_response):
                status = "submitted"
                reason = "Comfy MCP accepted the workflow but did not return downloadable artifacts yet"
            else:
                status = "submitted" if remote_job_id else "failed"
                reason = "Comfy MCP did not expose a terminal success response or downloadable output"
        except Exception as exc:
            downloaded_files = []
            reason = str(exc)
            combined_response = {
                "submit_tool": tool_name,
                "submit_response": submit_response,
                "final_response": final_response,
                "error": reason,
            }
            self._emit(f"Comfy MCP 调用失败：{reason}", status="failed", error=reason)

        manifest = self._manifest(
            status=status,
            reason=reason,
            compose_config=compose_config,
            payload=comfyui_payload,
            discovery=discovery,
            request_payload=request_payload,
            response=combined_response,
            downloaded_files=downloaded_files,
            elapsed_seconds=round(time.time() - started_at, 3),
        )
        self._write_json(manifest_path, manifest)
        self._emit(
            f"Comfy MCP 视觉任务结束：{status}",
            status=status,
            provider="comfy_mcp",
            downloaded_count=len(downloaded_files),
            manifest_file=str(manifest_path),
        )
        return self._result(manifest, manifest_path)

    def capabilities(self) -> dict[str, Any]:
        return {
            "search_templates": True,
            "search_models": True,
            "search_nodes": True,
            "submit_workflow": True,
            "upload_file": False,
            "get_job_status": True,
            "get_output": True,
            "use_previous_output": False,
            "cancel_job": False,
            "get_queue": True,
            "save_workflow": True,
            "share_workflow": False,
            "import_shared_workflow": True,
            "artifact_download": True,
        }

    def discover(self) -> dict[str, Any]:
        if not self.mcp_url:
            return {"status": "skipped", "reason": "missing mcp_url", "tools": [], "capabilities": self.capabilities()}
        health: dict[str, Any] = {}
        health_errors: list[str] = []
        for suffix in ("", "/health", "/status"):
            try:
                health = self._get_json(self._url(suffix))
                break
            except Exception as exc:
                health_errors.append(f"{suffix or '/'}: {exc}")
        tools, tool_errors = self._list_tools()
        status = "ready" if tools or health else "unknown"
        return {
            "provider": "comfy_mcp",
            "status": status,
            "mcp_url": self.mcp_url,
            "health": health,
            "health_errors": health_errors[:3],
            "tools": tools,
            "tool_count": len(tools),
            "tool_errors": tool_errors[:5],
            "capabilities": self.capabilities(),
        }

    def discover_workflows(self, query: str = "", limit: int = 80) -> dict[str, Any]:
        discovery = self.discover()
        tools = discovery.get("tools") if isinstance(discovery.get("tools"), list) else []
        selected_tools = self._workflow_discovery_tools(tools)
        workflow_items: list[dict[str, Any]] = []
        errors: list[str] = []

        for tool_name in selected_tools:
            try:
                response = self._call_tool(
                    tool_name,
                    {
                        "query": query,
                        "q": query,
                        "limit": limit,
                        "max_results": limit,
                        "type": "workflow",
                    },
                )
                workflow_items.extend(self._normalize_workflow_items(response, source_tool=tool_name))
            except Exception as exc:
                errors.append(f"{tool_name}: {exc}")

        if not selected_tools:
            for suffix in ("/workflows", "/templates", "/workflow-templates"):
                try:
                    response = self._get_json(self._url(suffix))
                    workflow_items.extend(self._normalize_workflow_items(response, source_tool=f"GET {suffix}"))
                except Exception as exc:
                    errors.append(f"GET {suffix}: {exc}")

        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in workflow_items:
            key = str(item.get("workflow_id") or item.get("id") or item.get("name") or "").strip().lower()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
            if len(deduped) >= limit:
                break

        return {
            "provider": "comfy_mcp",
            "status": "ready" if deduped else "empty",
            "mcp_url": self.mcp_url,
            "query": query,
            "workflow_count": len(deduped),
            "workflows": deduped,
            "selected_tools": selected_tools,
            "errors": errors[:10],
            "discovery": discovery,
        }

    def _build_tool_arguments(self, payload: dict[str, Any], compose_config: dict[str, Any]) -> dict[str, Any]:
        return {
            "workflow": payload,
            "payload": payload,
            "production_payload": payload,
            "compose_config": self._safe_compose_config(compose_config),
            "workflow_endpoint": str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip(),
            "workflow_preset_id": str(compose_config.get("workflow_preset_id") or "").strip(),
            "workflow_preset_name": str(compose_config.get("workflow_preset_name") or "").strip(),
            "visual_provider": "comfy_mcp",
        }

    def _safe_compose_config(self, compose_config: dict[str, Any]) -> dict[str, Any]:
        safe = dict(compose_config or {})
        for key in ("api_key", "comfy_api_key", "token", "authorization"):
            if key in safe:
                safe[key] = "***"
        return safe

    def _poll_if_possible(
        self,
        remote_job_id: str,
        discovery: dict[str, Any],
        compose_config: dict[str, Any],
        submit_response: dict[str, Any],
    ) -> dict[str, Any]:
        if not remote_job_id:
            return submit_response
        tools = discovery.get("tools") if isinstance(discovery.get("tools"), list) else []
        status_tool = self._select_tool(compose_config, tools, self.STATUS_TOOL_CANDIDATES, explicit_keys=("comfy_mcp_status_tool", "status_tool"))
        output_tool = self._select_tool(compose_config, tools, self.OUTPUT_TOOL_CANDIDATES, explicit_keys=("comfy_mcp_output_tool", "output_tool"))
        if not status_tool and not output_tool:
            return submit_response

        timeout_seconds = self._safe_int(compose_config.get("poll_timeout_seconds"), default=3600, minimum=10, maximum=24 * 3600)
        poll_interval = self._safe_int(compose_config.get("poll_interval_seconds"), default=3, minimum=1, maximum=60)
        deadline = time.time() + timeout_seconds
        last_response = submit_response
        while time.time() < deadline:
            if status_tool:
                last_response = self._call_tool(status_tool, self._job_arguments(remote_job_id))
                status = self._status_text(last_response)
                self._emit(f"Comfy MCP 任务 {remote_job_id} 状态：{status or 'unknown'}", task_id=remote_job_id, remote_status=status)
                if status in {"success", "succeeded", "completed", "complete", "done", "finished", "failed", "error", "cancelled", "canceled"}:
                    break
            if self._is_success_response(last_response):
                break
            time.sleep(poll_interval)
        if output_tool and (self._is_success_response(last_response) or not status_tool):
            output_response = self._call_tool(output_tool, self._job_arguments(remote_job_id))
            return {"status_response": last_response, "output_response": output_response}
        return last_response

    def _job_arguments(self, remote_job_id: str) -> dict[str, str]:
        return {
            "job_id": remote_job_id,
            "jobId": remote_job_id,
            "task_id": remote_job_id,
            "taskId": remote_job_id,
            "prompt_id": remote_job_id,
            "promptId": remote_job_id,
            "id": remote_job_id,
        }

    def _list_tools(self) -> tuple[list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        attempts = [
            ("jsonrpc", self._jsonrpc_tools_list),
            ("GET /tools", lambda: self._get_json(self._url("/tools"))),
            ("GET /tools/list", lambda: self._get_json(self._url("/tools/list"))),
            ("POST /tools/list", lambda: self._post_json(self._url("/tools/list"), {})),
        ]
        for label, fn in attempts:
            try:
                response = fn()
                tools = self._extract_tools(response)
                if tools:
                    return tools, errors
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        return [], errors

    def _workflow_discovery_tools(self, tools: list[dict[str, Any]]) -> list[str]:
        names = [str(tool.get("name") or tool.get("id") or "").strip() for tool in tools if isinstance(tool, dict)]
        lowered = {name.lower(): name for name in names if name}
        selected: list[str] = []
        for candidate in self.WORKFLOW_DISCOVERY_TOOL_CANDIDATES:
            if candidate.lower() in lowered:
                selected.append(lowered[candidate.lower()])
        if selected:
            return selected
        for name in names:
            lname = name.lower()
            if any(token in lname for token in ("workflow", "template")) and any(token in lname for token in ("list", "search", "get", "find")):
                selected.append(name)
        return selected[:6]

    def _jsonrpc_tools_list(self) -> dict[str, Any]:
        return self._post_json(self.mcp_url, self._jsonrpc("tools/list", {}))

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        attempts = [
            ("jsonrpc tools/call", lambda: self._jsonrpc_tool_call(tool_name, arguments)),
            ("POST /tools/call", lambda: self._post_json(self._url("/tools/call"), {"name": tool_name, "arguments": arguments})),
            ("POST /call_tool", lambda: self._post_json(self._url("/call_tool"), {"name": tool_name, "arguments": arguments})),
            (
                f"POST /tools/{tool_name}/call",
                lambda: self._post_json(self._url(f"/tools/{quote(tool_name, safe='')}/call"), arguments),
            ),
            (f"POST /tools/{tool_name}", lambda: self._post_json(self._url(f"/tools/{quote(tool_name, safe='')}"), arguments)),
            (f"POST /{tool_name}", lambda: self._post_json(self._url(f"/{quote(tool_name, safe='')}"), arguments)),
        ]
        for label, fn in attempts:
            try:
                response = fn()
                return self._unwrap_mcp_response(response)
            except Exception as exc:
                errors.append(f"{label}: {exc}")
        raise ValueError("; ".join(errors[-3:]) or f"Comfy MCP tool call failed: {tool_name}")

    def _jsonrpc_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._post_json(
            self.mcp_url,
            self._jsonrpc("tools/call", {"name": tool_name, "arguments": arguments}),
        )

    def _jsonrpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        return {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}

    def _unwrap_mcp_response(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {"data": response}
        if response.get("error"):
            raise ValueError(json.dumps(response.get("error"), ensure_ascii=False)[:1200])
        result = response.get("result") if "result" in response else response
        if isinstance(result, dict) and isinstance(result.get("content"), list):
            parsed_content: list[Any] = []
            for item in result["content"]:
                if not isinstance(item, dict):
                    parsed_content.append(item)
                    continue
                text = str(item.get("text") or "").strip()
                if text:
                    try:
                        parsed_content.append(json.loads(text))
                    except json.JSONDecodeError:
                        parsed_content.append(item)
                else:
                    parsed_content.append(item)
            result = {**result, "parsed_content": parsed_content}
        return result if isinstance(result, dict) else {"data": result}

    def _collect_artifacts(self, response: Any, output_dir: Path) -> list[str]:
        downloaded: list[str] = []
        for index, url in enumerate(self._find_download_urls(response), start=1):
            try:
                path = self._download_file(url, output_dir, f"comfy_mcp_result_{index:02d}")
                downloaded.append(str(path))
            except Exception as exc:
                self._emit(f"Comfy MCP 结果下载失败：{exc}", url=url, error=str(exc))
        offset = len(downloaded)
        for index, data_url in enumerate(self._find_data_urls(response), start=1):
            try:
                path = self._save_data_url(data_url, output_dir, f"comfy_mcp_result_{offset + index:02d}")
                downloaded.append(str(path))
            except Exception as exc:
                self._emit(f"Comfy MCP data URL 保存失败：{exc}", error=str(exc))
        offset = len(downloaded)
        for index, file_path in enumerate(self._find_local_files(response), start=1):
            try:
                target = self._copy_local_file(file_path, output_dir, f"comfy_mcp_result_{offset + index:02d}")
                downloaded.append(str(target))
            except Exception as exc:
                self._emit(f"Comfy MCP 本地结果复制失败：{exc}", output_file=str(file_path), error=str(exc))
        return list(dict.fromkeys(downloaded))

    def _download_file(self, url: str, output_dir: Path, stem: str) -> Path:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix.lower().lstrip(".") not in self.DOWNLOAD_TYPES:
            suffix = ".mp4"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-comfy-mcp-adapter/1.0"})
        target.write_bytes(self._read_request(req, timeout=300, label="Comfy MCP result download"))
        return target

    def _save_data_url(self, data_url: str, output_dir: Path, stem: str) -> Path:
        match = re.match(r"data:([^;,]+)(?:;[^,]*)?;base64,(.*)$", data_url, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            raise ValueError("unsupported data URL")
        mime = match.group(1).lower()
        suffix = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "audio/wav": ".wav",
            "audio/mpeg": ".mp3",
        }.get(mime, ".bin")
        target = output_dir / f"{stem}{suffix}"
        target.write_bytes(base64.b64decode(match.group(2), validate=False))
        return target

    def _copy_local_file(self, path: Path, output_dir: Path, stem: str) -> Path:
        suffix = path.suffix.lower()
        if suffix.lstrip(".") not in self.DOWNLOAD_TYPES:
            suffix = ".bin"
        target = output_dir / f"{stem}{suffix}"
        if path.resolve() == target.resolve():
            return target
        shutil.copy2(path, target)
        return target

    def _get_json(self, url: str) -> dict[str, Any]:
        req = urllib_request.Request(url, headers=self._headers(), method="GET")
        return self._parse_response(self._read_request(req, timeout=20, label="Comfy MCP GET", max_bytes=self.MAX_RESPONSE_BYTES))

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib_request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={**self._headers(), "Content-Type": "application/json"},
            method="POST",
        )
        try:
            return self._parse_response(self._read_request(req, timeout=120, label="Comfy MCP POST", max_bytes=self.MAX_RESPONSE_BYTES))
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise ValueError(f"HTTP {exc.code}: {detail}") from exc

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
                self._emit(f"{label} 暂时失败，重试 {attempt}/{self.NETWORK_RETRY_ATTEMPTS - 1}：{exc}", error=str(exc), retry_attempt=attempt)
                time.sleep(min(6, attempt * 2))
        if last_exc:
            raise last_exc
        raise ValueError(f"{label} failed without response")

    def _parse_response(self, raw: bytes) -> dict[str, Any]:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return {}
        if text.startswith("data:"):
            events = [line[5:].strip() for line in text.splitlines() if line.startswith("data:") and line[5:].strip() not in {"[DONE]", ""}]
            text = events[-1] if events else text
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "User-Agent": "agency-agents-zh-comfy-mcp-adapter/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["X-API-Key"] = self.api_key
        return headers

    def _url(self, suffix: str) -> str:
        suffix = str(suffix or "").strip()
        if not suffix:
            return self.mcp_url
        if suffix.startswith(("http://", "https://")):
            return suffix
        return urljoin(f"{self.mcp_url}/", suffix.lstrip("/"))

    def _manifest(
        self,
        *,
        status: str,
        reason: str,
        compose_config: dict[str, Any],
        payload: dict[str, Any],
        discovery: dict[str, Any] | None = None,
        request_payload: dict[str, Any] | None = None,
        response: dict[str, Any] | None = None,
        downloaded_files: list[str] | None = None,
        elapsed_seconds: float | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": "comfy_mcp",
            "status": status,
            "reason": reason,
            "mcp_url": self.mcp_url,
            "api_key_provided": bool(self.api_key),
            "discovery_mode": False,
            "capabilities": self.capabilities(),
            "discovery": discovery or {},
            "payload_hint": self._payload_hint(payload),
            "workflow_endpoint": str(compose_config.get("workflow_endpoint") or compose_config.get("endpoint") or "").strip(),
            "request_payload": request_payload or {},
            "response": response or {},
            "downloaded_files": downloaded_files or [],
            "result_count": len(downloaded_files or []),
            "elapsed_seconds": elapsed_seconds,
            "note": "Comfy MCP responses are normalized into durable local artifacts when URLs, data URLs, or local file paths are returned.",
        }

    def _result(self, manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
        return {
            "provider": "comfy_mcp",
            "status": manifest.get("status") or "unknown",
            "reason": manifest.get("reason") or "",
            "manifest_file": str(manifest_path),
            "downloaded_files": manifest.get("downloaded_files") or [],
            "capabilities": manifest.get("capabilities") or self.capabilities(),
            "discovery": manifest.get("discovery") or {},
            "jobs": [],
            "artifacts": [
                {"path": path, "type": Path(str(path)).suffix.lower().lstrip(".")}
                for path in (manifest.get("downloaded_files") or [])
            ],
        }

    def _payload_hint(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        return {
            "keys": sorted(str(key) for key in payload.keys())[:40],
            "has_visual_jobs": bool(payload.get("image_prompts") or payload.get("video_prompts") or payload.get("production_intents")),
            "image_prompt_count": len(payload.get("image_prompts") or []) if isinstance(payload.get("image_prompts"), list) else 0,
            "video_prompt_count": len(payload.get("video_prompts") or []) if isinstance(payload.get("video_prompts"), list) else 0,
            "working_width": payload.get("width") or "",
            "working_height": payload.get("height") or "",
            "fps": payload.get("fps") or "",
        }

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _emit(self, message: str, **extra: Any) -> None:
        if not self.progress_callback:
            return
        event = {"event": "production_update", "stage": "comfyui", "message": message}
        event.update(extra)
        self.progress_callback(event)

    @classmethod
    def _select_tool(
        cls,
        compose_config: dict[str, Any],
        tools: list[dict[str, Any]],
        candidates: tuple[str, ...],
        *,
        explicit_keys: tuple[str, ...],
    ) -> str:
        for key in explicit_keys:
            explicit = str(compose_config.get(key) or "").strip()
            if explicit:
                return explicit
        names = [str(tool.get("name") or tool.get("id") or "").strip() for tool in tools if isinstance(tool, dict)]
        lowered = {name.lower(): name for name in names if name}
        for candidate in candidates:
            if candidate.lower() in lowered:
                return lowered[candidate.lower()]
        for name in names:
            lname = name.lower()
            if any(token in lname for token in ("workflow", "prompt", "generate", "submit", "queue", "run")):
                return name
        return ""

    @classmethod
    def _extract_tools(cls, response: Any) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            return []
        data = response.get("result") if isinstance(response.get("result"), dict) else response
        raw_tools = data.get("tools") or data.get("data") or data.get("items") if isinstance(data, dict) else []
        if isinstance(raw_tools, dict):
            raw_tools = raw_tools.get("tools") or raw_tools.get("items") or []
        tools: list[dict[str, Any]] = []
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if isinstance(item, str):
                    tools.append({"name": item})
                elif isinstance(item, dict):
                    tools.append(item)
        return tools

    @classmethod
    def _normalize_workflow_items(cls, response: Any, *, source_tool: str) -> list[dict[str, Any]]:
        candidates: list[Any] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                for key in ("workflows", "templates", "items", "data", "results"):
                    child = value.get(key)
                    if isinstance(child, list):
                        candidates.extend(child)
                    elif isinstance(child, dict):
                        collect(child)
                if any(str(key).lower() in {"id", "workflow_id", "workflowid", "name", "title"} for key in value.keys()):
                    candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(value)

        collect(response)
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(candidates, start=1):
            if isinstance(item, str):
                text = item.strip()
                if text:
                    normalized.append(
                        {
                            "id": text,
                            "workflow_id": text,
                            "name": text,
                            "source_tool": source_tool,
                            "raw": item,
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            workflow_id = str(
                item.get("workflow_id")
                or item.get("workflowId")
                or item.get("id")
                or item.get("uuid")
                or item.get("slug")
                or item.get("name")
                or f"workflow_{index:03d}"
            ).strip()
            name = str(item.get("name") or item.get("title") or item.get("label") or workflow_id).strip()
            material_type = cls._guess_material_type(item)
            normalized.append(
                {
                    "id": workflow_id,
                    "workflow_id": workflow_id,
                    "name": name,
                    "description": str(item.get("description") or item.get("summary") or "")[:1200],
                    "material_type": material_type,
                    "capability": str(item.get("capability") or item.get("category") or item.get("type") or "").strip(),
                    "tags": cls._string_list(item.get("tags") or item.get("keywords") or item.get("categories")),
                    "endpoint": str(item.get("endpoint") or item.get("url") or item.get("api") or "").strip(),
                    "schema": item.get("schema") if isinstance(item.get("schema"), dict) else {},
                    "inputs": item.get("inputs") if isinstance(item.get("inputs"), (list, dict)) else [],
                    "outputs": item.get("outputs") if isinstance(item.get("outputs"), (list, dict)) else [],
                    "source_tool": source_tool,
                    "raw": item,
                }
            )
        return normalized

    @classmethod
    def _guess_material_type(cls, item: dict[str, Any]) -> str:
        text = json.dumps(item, ensure_ascii=False).lower()
        if any(token in text for token in ("video", "mp4", "i2v", "t2v", "视频", "影片")):
            return "video"
        if any(token in text for token in ("image", "png", "jpg", "jpeg", "webp", "图片", "图像")):
            return "image"
        if any(token in text for token in ("audio", "wav", "mp3", "音频")):
            return "audio"
        return "unknown"

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in re.split(r"[,;，；\s]+", value) if item.strip()]
        return []

    @classmethod
    def _first_identifier(cls, data: Any) -> str:
        keys = {"job_id", "jobid", "task_id", "taskid", "prompt_id", "promptid", "run_id", "runid"}
        found = cls._first_key_value(data, keys)
        return str(found or "").strip()

    @classmethod
    def _status_text(cls, data: Any) -> str:
        value = cls._first_key_value(data, {"status", "state", "remote_status", "job_status", "task_status"})
        return str(value or "").strip().lower()

    @classmethod
    def _is_success_response(cls, data: Any) -> bool:
        status = cls._status_text(data)
        if status in {"success", "succeeded", "completed", "complete", "done", "finished", "downloaded"}:
            return True
        if status in {"failed", "error", "cancelled", "canceled", "timeout"}:
            return False
        return bool(cls._find_download_urls(data) or cls._find_data_urls(data) or cls._find_local_files(data))

    @classmethod
    def _first_key_value(cls, data: Any, keys: set[str]) -> Any:
        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).replace("-", "_").lower() in keys and value not in (None, ""):
                    return value
            for value in data.values():
                found = cls._first_key_value(value, keys)
                if found not in (None, ""):
                    return found
        elif isinstance(data, list):
            for item in data:
                found = cls._first_key_value(item, keys)
                if found not in (None, ""):
                    return found
        return None

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
        return list(dict.fromkeys(found))[:50]

    @classmethod
    def _find_data_urls(cls, data: Any) -> list[str]:
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
                found.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(data)
        return list(dict.fromkeys(found))[:30]

    @classmethod
    def _find_local_files(cls, data: Any) -> list[Path]:
        found: list[Path] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                text = value.strip().strip('"')
                if re.match(r"^[a-zA-Z]:[\\/].+", text) or text.startswith(("/", "\\\\")):
                    try:
                        path = Path(text)
                        if path.is_file() and path.suffix.lower().lstrip(".") in cls.DOWNLOAD_TYPES:
                            found.append(path)
                    except OSError:
                        pass
                return
            if isinstance(value, dict):
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(data)
        deduped: list[Path] = []
        seen: set[str] = set()
        for path in found:
            key = str(path.resolve()).lower()
            if key not in seen:
                seen.add(key)
                deduped.append(path)
        return deduped[:30]

    @staticmethod
    def _safe_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))
