from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse


class CloudComfyUIAdapter:
    """Call a cloud ComfyUI final-production workflow and persist returned assets."""

    MAX_RESPONSE_BYTES = 4_000_000
    DOWNLOAD_TYPES = {"mp4", "mov", "webm", "m4v", "mp3", "wav", "aac", "png", "jpg", "jpeg", "webp"}

    def __init__(self, base_url: str, api_key: str, endpoint: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        if not self.base_url:
            raise ValueError("ComfyUI base URL is required")
        if not self.api_key:
            raise ValueError("ComfyUI API key is required")
        if not self.endpoint:
            raise ValueError("ComfyUI workflow endpoint is required")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ComfyUI base URL must be an http/https URL")

    def run(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        provider = str(compose_config.get("provider") or compose_config.get("tool") or "runninghub").strip().lower()
        if provider == "runninghub":
            return self._run_runninghub(comfyui_payload, compose_config, output_dir)
        return self._run_generic(comfyui_payload, compose_config, output_dir)

    def _run_runninghub(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        payload = self._build_runninghub_payload(comfyui_payload, compose_config)
        submit_response = self._post_json(self._endpoint_url(), payload)
        submit_path = output_dir / "runninghub_comfyui_submit_response.json"
        self._write_json(submit_path, self._redact_response(submit_response))

        task_id = self._first_value(submit_response, ("taskId", "task_id"))
        if not task_id:
            raise ValueError("RunningHub ComfyUI workflow did not return taskId")

        query_url = urljoin(f"{self.base_url}/", "query")
        poll_interval = self._safe_int(compose_config.get("poll_interval_seconds"), default=10, minimum=2, maximum=60)
        timeout_seconds = self._safe_int(compose_config.get("poll_timeout_seconds"), default=3600, minimum=60, maximum=10800)
        deadline = time.time() + timeout_seconds
        query_response: dict[str, Any] = submit_response

        while time.time() < deadline:
            query_response = self._post_json(query_url, {"taskId": task_id})
            status = self._status(query_response)
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
                path = self._download_file(url, output_dir, f"comfyui_result_{index:02d}", output_type)
                item["downloaded_file"] = str(path)
                downloaded.append(path)
            result_items.append(item)

        status = self._status(query_response)
        manifest = {
            "provider": "runninghub",
            "status": "success" if status == "SUCCESS" else "failed" if status != "TIMEOUT" else "timeout",
            "taskId": task_id,
            "endpoint": self.endpoint,
            "submit_response_file": str(submit_path),
            "query_response_file": str(query_path),
            "result_count": len(result_items),
            "downloaded_files": [str(path) for path in downloaded],
            "results": result_items,
            "note": "Result URLs may expire; downloaded files above are durable local copies.",
        }
        manifest_path = output_dir / "cloud_comfyui_manifest.json"
        self._write_json(manifest_path, manifest)
        if manifest["status"] != "success":
            message = self._first_value(query_response, ("errorMessage", "message")) or query_response.get("failedReason") or status
            raise ValueError(f"RunningHub ComfyUI workflow failed: {message}")
        return manifest

    def _run_generic(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        response = self._post_json(self._endpoint_url(), comfyui_payload)
        response_path = output_dir / "cloud_comfyui_response.json"
        self._write_json(response_path, self._redact_response(response))
        urls = self._find_download_urls(response)
        downloaded = [
            self._download_file(url, output_dir, f"comfyui_result_{index:02d}", "")
            for index, url in enumerate(urls, start=1)
        ]
        manifest = {
            "provider": str(compose_config.get("provider") or "generic"),
            "status": "submitted" if not downloaded else "downloaded",
            "endpoint": self.endpoint,
            "response_file": str(response_path),
            "download_urls": urls,
            "downloaded_files": [str(path) for path in downloaded],
        }
        self._write_json(output_dir / "cloud_comfyui_manifest.json", manifest)
        return manifest

    def _build_runninghub_payload(self, comfyui_payload: dict[str, Any], compose_config: dict[str, Any]) -> dict[str, Any]:
        node_info = self._parse_node_info_list(compose_config)
        replacements = {
            "{{payload}}": json.dumps(comfyui_payload, ensure_ascii=False),
            "{{negative_prompt}}": str(comfyui_payload.get("negative_prompt") or ""),
            "{{image_prompt}}": self._first_list_or_value(comfyui_payload, "image_prompts", "image_prompt"),
            "{{video_prompt}}": self._first_list_or_value(comfyui_payload, "video_prompts", "video_prompt"),
            "{{reference_image}}": self._first_reference_image(comfyui_payload),
            "{{seed}}": str(comfyui_payload.get("seed") or ""),
            "{{width}}": str(comfyui_payload.get("width") or ""),
            "{{height}}": str(comfyui_payload.get("height") or ""),
            "{{prompt}}": self._first_prompt(comfyui_payload),
        }
        if node_info:
            node_info = self._replace_placeholders(node_info, replacements)
        payload: dict[str, Any] = {
            "apiKey": self.api_key,
            "addMetadata": bool(compose_config.get("add_metadata", True)),
            "nodeInfoList": node_info,
            "instanceType": str(compose_config.get("instance_type") or "default").strip(),
            "usePersonalQueue": str(compose_config.get("use_personal_queue") or "false").strip().lower(),
        }
        app_id = self._app_id_from_endpoint()
        if app_id:
            payload["webappId"] = app_id
        return payload

    def _endpoint_url(self) -> str:
        if self.endpoint.startswith(("http://", "https://")):
            return self.endpoint
        return urljoin(f"{self.base_url}/", self.endpoint.lstrip("/"))

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=120) as response:
                raw = response.read(self.MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1200]
            raise ValueError(f"ComfyUI workflow HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"ComfyUI workflow connection failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _app_id_from_endpoint(self) -> str:
        match = re.search(r"/ai-app/([^/?#]+)", self.endpoint)
        return match.group(1) if match else ""

    @classmethod
    def _replace_placeholders(cls, value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, str):
            for key, replacement in replacements.items():
                value = value.replace(key, replacement)
            return value
        if isinstance(value, list):
            return [cls._replace_placeholders(item, replacements) for item in value]
        if isinstance(value, dict):
            return {key: cls._replace_placeholders(item, replacements) for key, item in value.items()}
        return value

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

    @staticmethod
    def _download_file(url: str, output_dir: Path, stem: str, output_type: str) -> Path:
        suffix = f".{output_type.lstrip('.')}" if output_type else Path(urlparse(url).path).suffix.lower()
        if suffix.lower().lstrip(".") not in CloudComfyUIAdapter.DOWNLOAD_TYPES:
            suffix = ".mp4"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-comfyui-adapter/1.0"})
        with urllib_request.urlopen(req, timeout=300) as response:
            target.write_bytes(response.read())
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
        values = payload.get("reference_images")
        if isinstance(values, list) and values:
            first = values[0]
            if isinstance(first, str):
                return first.strip()
            if isinstance(first, dict):
                for key in ("url", "path", "file", "image"):
                    item = first.get(key)
                    if isinstance(item, str) and item.strip():
                        return item.strip()
        return ""

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        return str(CloudComfyUIAdapter._first_value(data, ("status",)) or "").upper()

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
    def _redact_response(data: Any) -> Any:
        if isinstance(data, dict):
            return {key: ("***" if "key" in key.lower() else CloudComfyUIAdapter._redact_response(value)) for key, value in data.items()}
        if isinstance(data, list):
            return [CloudComfyUIAdapter._redact_response(value) for value in data]
        return data

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
