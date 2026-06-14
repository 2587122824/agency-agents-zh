from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse


class CloudVideoAdapter:
    """Call a cloud video task API and persist returned video assets."""

    MAX_RESPONSE_BYTES = 4_000_000
    VIDEO_TYPES = {"mp4", "mov", "webm", "m4v"}

    def __init__(self, base_url: str, api_key: str, endpoint: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        if not self.base_url:
            raise ValueError("video base URL is required")
        if not self.api_key:
            raise ValueError("video API key is required")
        if not self.endpoint:
            raise ValueError("video workflow endpoint is required")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("video base URL must be an http/https URL")

    def run(self, prompt_text: str, video_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        provider = str(video_config.get("tool") or "").strip().lower()
        if provider == "runninghub":
            return self._run_runninghub(prompt_text, video_config, output_dir)
        return self._run_generic(prompt_text, video_config, output_dir)

    def _run_runninghub(self, prompt_text: str, video_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        payload = self._build_runninghub_payload(prompt_text, video_config)
        submit_response = self._post_json(self._endpoint_url(), payload)
        submit_path = output_dir / "runninghub_video_submit_response.json"
        self._write_json(submit_path, self._redact_response(submit_response))

        task_id = self._first_value(submit_response, ("taskId", "task_id"))
        if not task_id:
            raise ValueError("RunningHub did not return taskId")

        query_url = urljoin(f"{self.base_url}/", "query")
        poll_interval = self._safe_int(video_config.get("poll_interval_seconds"), default=8, minimum=2, maximum=60)
        timeout_seconds = self._safe_int(video_config.get("poll_timeout_seconds"), default=1800, minimum=60, maximum=7200)
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
                "errorMessage": f"RunningHub video task polling timed out after {timeout_seconds} seconds",
            }

        query_path = output_dir / "runninghub_video_query_response.json"
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
            if url and output_type in self.VIDEO_TYPES:
                path = self._download_file(url, output_dir, f"runninghub_video_{index:02d}", output_type)
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
            "note": "RunningHub result URLs expire; downloaded files above are the durable local copies.",
        }
        manifest_path = output_dir / "cloud_video_manifest.json"
        self._write_json(manifest_path, manifest)
        if manifest["status"] != "success":
            message = self._first_value(query_response, ("errorMessage", "message")) or query_response.get("failedReason") or status
            raise ValueError(f"RunningHub video task failed: {message}")
        return manifest

    def _run_generic(self, prompt_text: str, video_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        payload = {
            "prompt": self._extract_prompt(prompt_text),
            "model": str(video_config.get("model") or "").strip(),
            "aspect_ratio": str(video_config.get("aspect_ratio") or "").strip(),
            "duration": str(video_config.get("duration") or "").strip(),
            "style": str(video_config.get("style") or "").strip(),
        }
        response = self._post_json(self._endpoint_url(), payload)
        response_path = output_dir / "cloud_video_response.json"
        self._write_json(response_path, self._redact_response(response))
        video_urls = self._find_video_urls(response)
        downloaded = [
            self._download_file(url, output_dir, f"cloud_video_{index:02d}", "")
            for index, url in enumerate(video_urls, start=1)
        ]
        manifest = {
            "provider": str(video_config.get("tool") or "generic"),
            "status": "submitted" if not downloaded else "downloaded",
            "endpoint": self.endpoint,
            "response_file": str(response_path),
            "video_urls": video_urls,
            "downloaded_files": [str(path) for path in downloaded],
        }
        self._write_json(output_dir / "cloud_video_manifest.json", manifest)
        return manifest

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
            raise ValueError(f"video workflow HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"video workflow connection failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _build_runninghub_payload(self, prompt_text: str, video_config: dict[str, Any]) -> dict[str, Any]:
        node_info = self._parse_node_info_list(video_config)
        prompt = self._extract_prompt(prompt_text)
        if node_info and "{{prompt}}" in json.dumps(node_info, ensure_ascii=False):
            node_info = json.loads(json.dumps(node_info, ensure_ascii=False).replace("{{prompt}}", prompt))
        payload: dict[str, Any] = {
            "apiKey": self.api_key,
            "addMetadata": bool(video_config.get("add_metadata", True)),
            "nodeInfoList": node_info,
            "instanceType": str(video_config.get("instance_type") or "default").strip(),
            "usePersonalQueue": str(video_config.get("use_personal_queue") or "false").strip().lower(),
        }
        app_id = self._app_id_from_endpoint()
        if app_id:
            payload["webappId"] = app_id
        return payload

    def _app_id_from_endpoint(self) -> str:
        match = re.search(r"/ai-app/([^/?#]+)", self.endpoint)
        return match.group(1) if match else ""

    @staticmethod
    def _parse_node_info_list(video_config: dict[str, Any]) -> list[Any]:
        raw = video_config.get("node_info_list")
        if isinstance(raw, list):
            return raw
        text = str(raw or video_config.get("node_info_list_json") or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("RunningHub nodeInfoList must be a JSON array")
        return data

    @staticmethod
    def _extract_prompt(text: str) -> str:
        for pattern in (
            r"```(?:json)?\s*(\{.*?\})\s*```",
            r"视频提示词[:：]\s*(.+)",
            r"正向提示词[:：]\s*(.+)",
            r"Prompt[:：]\s*(.+)",
        ):
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if not match:
                continue
            candidate = match.group(1).strip()
            if pattern.startswith("```"):
                try:
                    data = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                prompt = CloudVideoAdapter._first_prompt_value(data)
                if prompt:
                    return prompt
            elif candidate:
                return candidate[:8000]
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()
        return stripped[:8000] or "Generate a short vertical social media video based on the workflow output."

    @staticmethod
    def _first_prompt_value(data: Any) -> str:
        if isinstance(data, dict):
            for key in ("prompt", "video_prompt", "positive_prompt", "text", "description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in data.values():
                found = CloudVideoAdapter._first_prompt_value(value)
                if found:
                    return found
        if isinstance(data, list):
            for value in data:
                found = CloudVideoAdapter._first_prompt_value(value)
                if found:
                    return found
        return ""

    @classmethod
    def _find_video_urls(cls, data: Any) -> list[str]:
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if re.search(r"https?://\S+\.(?:mp4|mov|webm|m4v)(?:\?\S*)?$", value, flags=re.IGNORECASE):
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
        return deduped[:20]

    @staticmethod
    def _download_file(url: str, output_dir: Path, stem: str, output_type: str) -> Path:
        suffix = f".{output_type.lstrip('.')}" if output_type else Path(urlparse(url).path).suffix.lower()
        if suffix.lower().lstrip(".") not in CloudVideoAdapter.VIDEO_TYPES:
            suffix = ".mp4"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-video-adapter/1.0"})
        with urllib_request.urlopen(req, timeout=300) as response:
            target.write_bytes(response.read())
        return target

    @staticmethod
    def _status(data: dict[str, Any]) -> str:
        return str(CloudVideoAdapter._first_value(data, ("status",)) or "").upper()

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
                return CloudVideoAdapter._first_value(nested, keys)
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
            return {key: ("***" if "key" in key.lower() else CloudVideoAdapter._redact_response(value)) for key, value in data.items()}
        if isinstance(data, list):
            return [CloudVideoAdapter._redact_response(value) for value in data]
        return data

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
