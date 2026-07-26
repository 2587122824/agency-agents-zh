from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx

from ..core.config import CONNECTED_LOCAL_ASSET_ROOT_REF, RUNTIME_ROOT
from .base import ProviderAdapterError, ProviderExecutionRequest


class CosyVoiceTransport(Protocol):
    def synthesize(
        self,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[dict[str, Any], bytes]: ...


class HttpxCosyVoiceTransport:
    def synthesize(
        self,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout: int,
        max_bytes: int,
    ) -> tuple[dict[str, Any], bytes]:
        try:
            response = httpx.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                body = exc.response.json()
            except ValueError:
                body = {}
            raise ProviderAdapterError(
                "COSYVOICE_SUBMISSION_REJECTED",
                "CosyVoice 明确拒绝了本次合成请求。",
                {
                    "schema_version": "cosyvoice-submission-rejection.v1",
                    "http_status": exc.response.status_code,
                    "provider_code": str(body.get("code") or "") if isinstance(body, dict) else "",
                    "provider_message": str(body.get("message") or "")[:1000] if isinstance(body, dict) else "",
                },
            ) from exc
        except (httpx.RequestError, ValueError) as exc:
            raise ProviderAdapterError(
                "COSYVOICE_SUBMISSION_OUTCOME_UNKNOWN",
                "CosyVoice 请求结果未知，需要人工核对后再决定是否重跑。",
            ) from exc
        if not isinstance(data, dict):
            raise ProviderAdapterError("COSYVOICE_RESPONSE_INVALID", "CosyVoice 返回了非对象响应。")
        output = data.get("output") if isinstance(data.get("output"), dict) else {}
        audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
        encoded = str(audio.get("data") or "").strip()
        audio_url = str(audio.get("url") or "").strip()
        try:
            if encoded:
                content = base64.b64decode(encoded, validate=True)
            elif audio_url:
                with httpx.stream("GET", audio_url, timeout=timeout, follow_redirects=True) as download:
                    download.raise_for_status()
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in download.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise ProviderAdapterError(
                                "COSYVOICE_OUTPUT_TOO_LARGE",
                                "CosyVoice 音频超过冻结存储策略的文件上限。",
                            )
                        chunks.append(chunk)
                content = b"".join(chunks)
            else:
                raise ProviderAdapterError(
                    "COSYVOICE_AUDIO_MISSING",
                    "CosyVoice 成功响应中没有音频 URL 或音频数据。",
                    _safe_response(data),
                )
        except ProviderAdapterError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderAdapterError(
                "COSYVOICE_AUDIO_DOWNLOAD_FAILED",
                "CosyVoice 音频下载或解码失败。",
                _safe_response(data),
            ) from exc
        if len(content) > max_bytes:
            raise ProviderAdapterError("COSYVOICE_OUTPUT_TOO_LARGE", "CosyVoice 音频超过冻结存储策略的文件上限。")
        return data, content


def _safe_response(data: dict[str, Any]) -> dict[str, Any]:
    output = data.get("output") if isinstance(data.get("output"), dict) else {}
    audio = output.get("audio") if isinstance(output.get("audio"), dict) else {}
    return {
        "schema_version": "cosyvoice-response-evidence.v1",
        "request_id": str(data.get("request_id") or ""),
        "code": str(data.get("code") or ""),
        "message": str(data.get("message") or "")[:1000],
        "audio_url_present": bool(audio.get("url")),
        "audio_data_present": bool(audio.get("data")),
        "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
    }


def cosyvoice_workflow_contract_issues(bindings: list[dict]) -> list[dict]:
    issues: list[dict] = []
    allowed_sources = {
        "input_contract.voiceover_text",
        "literal:wav",
        "literal:longxiaochun",
        "literal:longxiaoxia",
        "literal:longxiaocheng",
        "literal:longxiaobai",
        "literal:longlaotie",
    }
    allowed_fields = {"text", "voice", "format", "sample_rate", "rate", "volume", "pitch"}
    seen_fields: set[str] = set()
    for index, binding in enumerate(bindings):
        field_path = str(binding.get("field_path") or "")
        source = str(binding.get("value_source") or "")
        if str(binding.get("node_id") or "") != "input":
            issues.append({"code": "COSYVOICE_BINDING_NODE_INVALID", "path": f"node_info_list.{index}.node_id"})
        if field_path not in allowed_fields:
            issues.append({"code": "COSYVOICE_BINDING_FIELD_UNSUPPORTED", "path": f"node_info_list.{index}.field_path"})
        if field_path in seen_fields:
            issues.append({"code": "COSYVOICE_BINDING_DUPLICATE", "path": f"node_info_list.{index}.field_path"})
        seen_fields.add(field_path)
        if not (source in allowed_sources or source.startswith("literal:")):
            issues.append({"code": "COSYVOICE_BINDING_SOURCE_UNSUPPORTED", "path": f"node_info_list.{index}.value_source"})
    for required in ("text", "voice", "format", "sample_rate"):
        if required not in seen_fields:
            issues.append({"code": "COSYVOICE_BINDING_REQUIRED", "path": f"node_info_list.{required}", "field": required})
    return issues


def _literal(source: str, value_type: str) -> Any:
    raw = source.removeprefix("literal:")
    if value_type == "integer":
        return int(raw)
    if value_type == "number":
        return float(raw)
    if value_type == "boolean":
        if raw not in {"true", "false"}:
            raise ValueError(raw)
        return raw == "true"
    return raw


@dataclass(frozen=True)
class CosyVoiceAdapter:
    adapter_kind: str = "cosyvoice"
    display_name: str = "阿里云 CosyVoice"
    external: bool = True
    execution_enabled: bool = False
    requires_credential: bool = True
    supported_work_kinds: frozenset[str] = frozenset({"generate_tts"})
    transport: CosyVoiceTransport = field(default_factory=HttpxCosyVoiceTransport)

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        if not self.execution_enabled:
            raise ProviderAdapterError("EXTERNAL_PROVIDER_EXECUTION_DISABLED", "CosyVoice 外部执行尚未启用。")
        manifest = request.request_manifest
        provider = manifest.get("provider") if isinstance(manifest.get("provider"), dict) else {}
        workflow = manifest.get("workflow") if isinstance(manifest.get("workflow"), dict) else {}
        storage = manifest.get("storage_policy") if isinstance(manifest.get("storage_policy"), dict) else {}
        input_contract = manifest.get("input_contract") if isinstance(manifest.get("input_contract"), dict) else {}
        api_key = str(provider.get("api_key") or "").strip()
        if not api_key:
            raise ProviderAdapterError("COSYVOICE_CREDENTIAL_MISSING", "CosyVoice API Key 未配置。")
        if storage.get("backend_kind") != "local" or storage.get("local_root_ref") != CONNECTED_LOCAL_ASSET_ROOT_REF:
            raise ProviderAdapterError("COSYVOICE_STORAGE_UNSUPPORTED", "CosyVoice 当前只连接 V2 本地素材库。")
        max_bytes = storage.get("max_file_size_bytes")
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ProviderAdapterError("COSYVOICE_STORAGE_LIMIT_INVALID", "CosyVoice 缺少冻结的文件大小上限。")
        bindings = workflow.get("node_info_list")
        if not isinstance(bindings, list):
            raise ProviderAdapterError("COSYVOICE_WORKFLOW_INVALID", "CosyVoice 工作流缺少输入绑定。")
        issues = cosyvoice_workflow_contract_issues(bindings)
        if issues:
            raise ProviderAdapterError("COSYVOICE_WORKFLOW_INVALID", "CosyVoice 工作流输入合同无效。", {"issues": issues})
        payload_input: dict[str, Any] = {}
        for binding in bindings:
            source = str(binding["value_source"])
            try:
                if source == "input_contract.voiceover_text":
                    value = input_contract["voiceover_text"]
                elif source.startswith("literal:"):
                    value = _literal(source, str(binding["value_type"]))
                else:
                    raise KeyError(source)
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderAdapterError(
                    "COSYVOICE_BINDING_VALUE_INVALID",
                    f"CosyVoice 输入 {binding.get('field_path')} 无法从冻结合同解析。",
                ) from exc
            payload_input[str(binding["field_path"])] = value
        text = str(payload_input.get("text") or "").strip()
        if not text:
            raise ProviderAdapterError("COSYVOICE_TEXT_EMPTY", "冻结的旁白文本为空。")
        if payload_input.get("format") != "wav":
            raise ProviderAdapterError("COSYVOICE_FORMAT_UNSUPPORTED", "V2 首期配音只接受 WAV 输出。")
        model = str(workflow.get("provider_workflow_id") or "").strip()
        if not model:
            raise ProviderAdapterError("COSYVOICE_MODEL_MISSING", "CosyVoice 工作流没有冻结模型 ID。")
        base_url = str(provider.get("base_url") or "").strip()
        url = urljoin(base_url.rstrip("/") + "/", "api/v1/services/audio/tts/SpeechSynthesizer")
        data, content = self.transport.synthesize(
            url,
            api_key,
            {"model": model, "input": payload_input},
            int(provider.get("request_timeout_seconds") or 600),
            max_bytes,
        )
        if not (content.startswith(b"RIFF") and content[8:12] == b"WAVE"):
            raise ProviderAdapterError(
                "COSYVOICE_OUTPUT_SIGNATURE_INVALID",
                "CosyVoice 返回内容不是有效的 WAV 文件签名。",
                _safe_response(data),
            )
        relative = Path("assets") / "providers" / "cosyvoice" / request.request_fingerprint / "voiceover.wav"
        output_path = RUNTIME_ROOT / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise ProviderAdapterError("COSYVOICE_OUTPUT_PATH_EXISTS", "CosyVoice 目标输出路径已存在，不能覆盖。")
        output_path.write_bytes(content)
        content_hash = hashlib.sha256(content).hexdigest()
        return {
            "schema_version": "cosyvoice-response.v1",
            "provider": "cosyvoice",
            "request_id": str(data.get("request_id") or ""),
            "usage": data.get("usage") if isinstance(data.get("usage"), dict) else {},
            "media_created": True,
            "outputs": [{
                "uri": f"runtime://{relative.as_posix()}",
                "storage_backend": "local",
                "asset_type": "audio",
                "role": "voiceover",
                "mime_type": "audio/wav",
                "content_hash": content_hash,
            }],
        }
