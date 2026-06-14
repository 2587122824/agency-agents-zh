from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urljoin, urlparse


class CloudImageAdapter:
    """Call a cloud image workflow API and persist returned image assets."""

    MAX_RESPONSE_BYTES = 4_000_000

    def __init__(self, base_url: str, api_key: str, endpoint: str) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        if not self.base_url:
            raise ValueError("image base URL is required")
        if not self.api_key:
            raise ValueError("image API key is required")
        if not self.endpoint:
            raise ValueError("image workflow endpoint is required")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("image base URL must be an http/https URL")

    def run(
        self,
        prompt_text: str,
        image_config: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        provider = str(image_config.get("tool") or "").strip().lower()
        if provider == "runninghub":
            return self._run_runninghub(prompt_text, image_config, output_dir)
        return self._run_generic(prompt_text, image_config, output_dir)

    def _run_runninghub(
        self,
        prompt_text: str,
        image_config: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        payload = self._build_runninghub_payload(prompt_text, image_config)
        submit_response = self._post_json(self._endpoint_url(), payload)
        submit_path = output_dir / "runninghub_submit_response.json"
        self._write_json(submit_path, self._redact_response(submit_response))

        task_id = str(submit_response.get("taskId") or "").strip()
        if not task_id:
            raise ValueError("RunningHub did not return taskId")

        query_url = urljoin(f"{self.base_url}/", "query")
        poll_interval = self._safe_int(image_config.get("poll_interval_seconds"), default=5, minimum=2, maximum=30)
        timeout_seconds = self._safe_int(image_config.get("poll_timeout_seconds"), default=900, minimum=30, maximum=3600)
        deadline = time.time() + timeout_seconds
        query_response: dict[str, Any] = submit_response

        while time.time() < deadline:
            query_response = self._post_json(query_url, {"taskId": task_id})
            status = str(query_response.get("status") or "").upper()
            if status == "SUCCESS":
                break
            if status in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
                break
            time.sleep(poll_interval)
        else:
            query_response = {
                "taskId": task_id,
                "status": "TIMEOUT",
                "errorMessage": f"RunningHub task polling timed out after {timeout_seconds} seconds",
            }

        query_path = output_dir / "runninghub_query_response.json"
        self._write_json(query_path, self._redact_response(query_response))

        results = query_response.get("results")
        if not isinstance(results, list):
            results = []
        downloaded = []
        result_items = []
        for index, result in enumerate(results, start=1):
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            output_type = str(result.get("outputType") or "").strip().lower()
            item = {
                "nodeId": result.get("nodeId"),
                "outputType": output_type,
                "url": url,
                "text": result.get("text"),
            }
            if url and output_type in {"png", "jpg", "jpeg", "webp"}:
                path = self._download_file(url, output_dir, f"runninghub_{index:02d}", output_type)
                item["downloaded_file"] = str(path)
                downloaded.append(path)
            result_items.append(item)

        status = str(query_response.get("status") or "").upper()
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
            "note": "RunningHub result URLs expire after 24 hours; downloaded files above are the durable local copies.",
        }
        manifest_path = output_dir / "cloud_image_manifest.json"
        self._write_json(manifest_path, manifest)
        if manifest["status"] != "success":
            message = query_response.get("errorMessage") or query_response.get("failedReason") or status
            raise ValueError(f"RunningHub image task failed: {message}")
        return manifest

    def _run_generic(
        self,
        prompt_text: str,
        image_config: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        prompt = self._extract_prompt(prompt_text)
        payload = self._build_generic_payload(prompt, image_config)
        response = self._post_json(self._endpoint_url(), payload)

        response_path = output_dir / "cloud_image_response.json"
        self._write_json(response_path, self._redact_response(response))

        image_urls = self._find_image_urls(response)
        downloaded = []
        for index, image_url in enumerate(image_urls, start=1):
            downloaded.append(self._download_file(image_url, output_dir, f"cloud_image_{index:02d}", ""))

        manifest = {
            "provider": str(image_config.get("tool") or "generic"),
            "status": "submitted" if not downloaded else "downloaded",
            "endpoint": self.endpoint,
            "prompt_preview": prompt[:500],
            "response_file": str(response_path),
            "image_urls": image_urls,
            "downloaded_files": [str(path) for path in downloaded],
            "note": "If no images were downloaded, inspect cloud_image_response.json for async task IDs or provider-specific result fields.",
        }
        manifest_path = output_dir / "cloud_image_manifest.json"
        self._write_json(manifest_path, manifest)
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
            raise ValueError(f"image workflow HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"image workflow connection failed: {exc.reason}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    @staticmethod
    def _build_runninghub_payload(prompt_text: str, image_config: dict[str, Any]) -> dict[str, Any]:
        node_info = CloudImageAdapter._parse_node_info_list(image_config)
        payload: dict[str, Any] = {
            "addMetadata": bool(image_config.get("add_metadata", True)),
            "nodeInfoList": node_info,
            "instanceType": str(image_config.get("instance_type") or "default").strip(),
            "usePersonalQueue": str(image_config.get("use_personal_queue") or "false").strip().lower(),
        }
        retain_seconds = CloudImageAdapter._safe_int(image_config.get("retain_seconds"), default=0, minimum=0, maximum=180)
        if retain_seconds:
            payload["retainSeconds"] = retain_seconds
        webhook_url = str(image_config.get("webhook_url") or "").strip()
        if webhook_url:
            payload["webhookUrl"] = webhook_url
        prompt = CloudImageAdapter._extract_prompt(prompt_text)
        if node_info and "{{prompt}}" in json.dumps(node_info, ensure_ascii=False):
            payload["nodeInfoList"] = json.loads(json.dumps(node_info, ensure_ascii=False).replace("{{prompt}}", prompt))
        return payload

    @staticmethod
    def _parse_node_info_list(image_config: dict[str, Any]) -> list[Any]:
        raw = image_config.get("node_info_list")
        if isinstance(raw, list):
            return raw
        text = str(raw or image_config.get("node_info_list_json") or "").strip()
        if not text:
            return []
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("RunningHub nodeInfoList must be a JSON array")
        return data

    @staticmethod
    def _build_generic_payload(prompt: str, image_config: dict[str, Any]) -> dict[str, Any]:
        width, height = CloudImageAdapter._size_to_pixels(str(image_config.get("size") or ""))
        negative_prompt = str(image_config.get("negative_prompt") or "").strip()
        count = CloudImageAdapter._safe_int(image_config.get("count_per_shot"), default=1, minimum=1, maximum=8)
        return {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "width": width,
            "height": height,
            "count": count,
            "batch_size": count,
            "model": str(image_config.get("model") or "").strip(),
            "style": str(image_config.get("style") or "").strip(),
            "quality": str(image_config.get("quality") or "standard").strip(),
            "input": {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "width": width,
                "height": height,
                "count": count,
                "size": str(image_config.get("size") or "").strip(),
                "style": str(image_config.get("style") or "").strip(),
            },
        }

    @staticmethod
    def _extract_prompt(text: str) -> str:
        for pattern in (
            r"```(?:json)?\s*(\{.*?\})\s*```",
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
                prompt = CloudImageAdapter._first_prompt_value(data)
                if prompt:
                    return prompt
            elif candidate:
                return candidate[:6000]
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL).strip()
        return stripped[:6000] or "Generate a clean storyboard keyframe based on the workflow output."

    @staticmethod
    def _first_prompt_value(data: Any) -> str:
        if isinstance(data, dict):
            for key in ("prompt", "positive_prompt", "image_prompt", "text", "description"):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in data.values():
                found = CloudImageAdapter._first_prompt_value(value)
                if found:
                    return found
        if isinstance(data, list):
            for value in data:
                found = CloudImageAdapter._first_prompt_value(value)
                if found:
                    return found
        return ""

    @staticmethod
    def _find_image_urls(data: Any) -> list[str]:
        found: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                if re.search(r"https?://\S+\.(?:png|jpe?g|webp)(?:\?\S*)?$", value, flags=re.IGNORECASE):
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
        if suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".png"
        target = output_dir / f"{stem}{suffix}"
        req = urllib_request.Request(url, headers={"User-Agent": "agency-agents-zh-image-adapter/1.0"})
        with urllib_request.urlopen(req, timeout=120) as response:
            target.write_bytes(response.read())
        return target

    @staticmethod
    def _size_to_pixels(size: str) -> tuple[int, int]:
        text = size.strip().lower()
        match = re.match(r"^(\d{3,5})x(\d{3,5})$", text)
        if match:
            return int(match.group(1)), int(match.group(2))
        ratios = {
            "9:16": (1024, 1792),
            "16:9": (1792, 1024),
            "1:1": (1024, 1024),
            "4:5": (1024, 1280),
        }
        return ratios.get(text, (1024, 1792))

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
            return {key: ("***" if "key" in key.lower() else CloudImageAdapter._redact_response(value)) for key, value in data.items()}
        if isinstance(data, list):
            return [CloudImageAdapter._redact_response(value) for value in data]
        return data

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
