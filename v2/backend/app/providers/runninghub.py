from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from ..core.config import CONNECTED_LOCAL_ASSET_ROOT_REF, RUNTIME_ROOT
from ..creation.service import detect_media_type
from .base import (
    ProviderAdapterError,
    ProviderExecutionRequest,
    ProviderPollResult,
    ProviderSubmission,
)
from .credentials import EnvironmentCredentialResolver


class RunningHubTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]: ...

    def upload(
        self,
        url: str,
        api_key: str,
        path: Path,
        mime_type: str,
        timeout: int,
    ) -> dict[str, Any]: ...

    def download(self, url: str, timeout: int, max_bytes: int) -> tuple[bytes, str | None]: ...


class HttpxRunningHubTransport:
    def post_json(self, url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderAdapterError("RUNNINGHUB_HTTP_FAILED", "RunningHub HTTP request failed.") from exc
        if not isinstance(data, dict):
            raise ProviderAdapterError("RUNNINGHUB_RESPONSE_INVALID", "RunningHub returned a non-object response.")
        return data

    def upload(self, url: str, api_key: str, path: Path, mime_type: str, timeout: int) -> dict[str, Any]:
        try:
            with path.open("rb") as handle:
                response = httpx.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                    files={"file": (path.name, handle, mime_type)},
                    timeout=timeout,
                )
            response.raise_for_status()
            data = response.json()
        except (OSError, httpx.HTTPError, ValueError) as exc:
            raise ProviderAdapterError("RUNNINGHUB_UPLOAD_FAILED", "RunningHub input upload failed.") from exc
        if not isinstance(data, dict):
            raise ProviderAdapterError("RUNNINGHUB_UPLOAD_RESPONSE_INVALID", "RunningHub upload returned a non-object response.")
        return data

    def download(self, url: str, timeout: int, max_bytes: int) -> tuple[bytes, str | None]:
        try:
            with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or None
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProviderAdapterError("RUNNINGHUB_OUTPUT_TOO_LARGE", "RunningHub output exceeds the frozen storage limit.")
                    chunks.append(chunk)
        except ProviderAdapterError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderAdapterError("RUNNINGHUB_DOWNLOAD_FAILED", "RunningHub output download failed.") from exc
        return b"".join(chunks), content_type


def _nested(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    for source in (data, nested):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _status(data: dict[str, Any]) -> str:
    return _first(data, ("status", "state", "taskStatus", "task_status")).upper()


def _results(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("results")
    if not isinstance(result, list) and isinstance(data.get("data"), dict):
        result = data["data"].get("results")
    return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []


def _submission_rejection(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]] | None:
    nested = response.get("data") if isinstance(response.get("data"), dict) else {}
    sources = (response, nested)
    raw_code = _first(response, ("code", "errorCode", "error_code"))
    evidence: dict[str, Any] = {
        "schema_version": "runninghub-submission-rejection.v1",
        "provider": "runninghub",
    }
    if raw_code:
        evidence["provider_code"] = raw_code
    for target, keys in (
        ("message", ("msg", "message")),
        ("error_message", ("errorMessage", "error_message")),
        ("failed_reason", ("failedReason", "failed_reason")),
    ):
        value = ""
        for source in sources:
            for key in keys:
                candidate = source.get(key)
                if isinstance(candidate, (str, int, float, bool)):
                    value = str(candidate).strip()
                    if value:
                        break
            if value:
                break
        if value:
            evidence[target] = value[:2000]

    normalized_code = raw_code.upper()
    explicit_error = any(key in evidence for key in ("error_message", "failed_reason"))
    rejected_code = bool(raw_code and normalized_code not in {"0", "200", "SUCCESS", "OK"})
    if not rejected_code and not explicit_error:
        return None
    if raw_code == "416":
        detail = "RunningHub 账户余额不足，请充值后重新创建并提交制作方案。"
    elif raw_code == "433":
        detail = "RunningHub 拒绝了工作流参数，请根据供应商返回信息检查当前工作流配置。"
    else:
        provider_message = evidence.get("error_message") or evidence.get("failed_reason") or evidence.get("message")
        detail = f"RunningHub 拒绝创建任务：{provider_message}" if provider_message else "RunningHub 明确拒绝创建任务。"
    return "RUNNINGHUB_SUBMISSION_REJECTED", detail, evidence


def _local_output_path(uri: str) -> Path:
    prefix = "runtime://assets/"
    if not uri.startswith(prefix):
        raise ProviderAdapterError("PARENT_OUTPUT_URI_UNSUPPORTED", "The parent image is not in the V2 local asset store.")
    relative = PurePosixPath(uri[len(prefix):])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ProviderAdapterError("PARENT_OUTPUT_URI_INVALID", "The parent image URI is invalid.")
    root = (RUNTIME_ROOT / "assets").resolve()
    path = root.joinpath(*relative.parts).resolve()
    if not path.is_relative_to(root):
        raise ProviderAdapterError("PARENT_OUTPUT_URI_INVALID", "The parent image URI leaves the V2 asset store.")
    return path


def _local_attachment_path(uri: str) -> Path:
    prefix = "runtime://attachments/"
    if not uri.startswith(prefix):
        raise ProviderAdapterError("REFERENCE_IMAGE_URI_UNSUPPORTED", "The frozen reference image is not in the V2 attachment store.")
    relative = PurePosixPath(uri[len(prefix):])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ProviderAdapterError("REFERENCE_IMAGE_URI_INVALID", "The frozen reference image URI is invalid.")
    root = RUNTIME_ROOT.resolve()
    path = (root / PurePath(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise ProviderAdapterError("REFERENCE_IMAGE_URI_INVALID", "The frozen reference image path escapes the V2 runtime root.")
    return path


@dataclass
class RunningHubAdapter:
    execution_enabled: bool = False
    transport: RunningHubTransport = field(default_factory=HttpxRunningHubTransport)
    credential_resolver: EnvironmentCredentialResolver = field(default_factory=EnvironmentCredentialResolver.from_environment)
    adapter_kind: str = "runninghub"
    display_name: str = "RunningHub"
    external: bool = True
    requires_credential: bool = True
    supported_work_kinds: frozenset[str] = frozenset({"generate_keyframe", "generate_i2v_clip", "generate_three_frame_i2v_clip", "generate_t2v_clip"})

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        raise ProviderAdapterError("EXTERNAL_PROVIDER_LIFECYCLE_REQUIRED", "External work must use persisted submit and poll phases.")

    def _contract(self, request: ProviderExecutionRequest) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        manifest = request.request_manifest
        if manifest.get("schema_version") != "production-work-request.v3":
            raise ProviderAdapterError("PROVIDER_REQUEST_SCHEMA_UNSUPPORTED", "RunningHub requires production-work-request.v3.")
        provider = manifest.get("provider")
        workflow = manifest.get("workflow")
        storage = manifest.get("storage_policy")
        if not all(isinstance(item, dict) for item in (provider, workflow, storage)):
            raise ProviderAdapterError("PROVIDER_REQUEST_CONTRACT_INCOMPLETE", "The frozen provider, workflow, or storage contract is missing.")
        if provider.get("adapter_kind") != self.adapter_kind:
            raise ProviderAdapterError("PROVIDER_ADAPTER_MISMATCH", "The frozen request does not target RunningHub.")
        if storage.get("backend_kind") != "local" or storage.get("local_root_ref") != CONNECTED_LOCAL_ASSET_ROOT_REF:
            raise ProviderAdapterError("STORAGE_ADAPTER_NOT_CONNECTED", "RunningHub V2 output requires the connected local storage policy.")
        credential = self.credential_resolver.resolve(provider.get("credential_ref"))
        if not credential.available or not credential.secret:
            raise ProviderAdapterError("PROVIDER_CREDENTIAL_NOT_READY", "The frozen RunningHub credential reference is not available to V2.")
        return provider, workflow, storage, credential.secret

    def _source_image(self, request: ProviderExecutionRequest, source: str) -> tuple[Path, str]:
        expected_kind = "generate_three_frame_i2v_clip" if source != "source_image" else "generate_i2v_clip"
        if request.work_kind != expected_kind:
            raise ProviderAdapterError("NODE_BINDING_SOURCE_INVALID", f"{source} is not valid for this work kind.")
        images = [
            item for item in request.parent_outputs
            if item.get("asset_type") == "image"
            and (item.get("input_slot") == source or (source == "source_image" and item.get("input_slot") in {None, source}))
        ]
        expected_total = 3 if request.work_kind == "generate_three_frame_i2v_clip" else 1
        if len(request.parent_outputs) != expected_total or len(images) != 1:
            raise ProviderAdapterError("I2V_PARENT_IMAGE_COUNT_INVALID", "Image-to-video parent images do not match the frozen input slots.")
        output = images[0]
        if output.get("storage_backend") != "local" or not str(output.get("mime_type", "")).startswith("image/"):
            raise ProviderAdapterError("I2V_PARENT_IMAGE_INVALID", "The exact parent output is not a local image.")
        path = _local_output_path(str(output.get("uri") or ""))
        if not path.is_file():
            raise ProviderAdapterError("I2V_PARENT_IMAGE_MISSING", "The exact parent image file does not exist.")
        return path, str(output["mime_type"])

    def _reference_image(self, request: ProviderExecutionRequest) -> tuple[Path, str] | None:
        if request.work_kind != "generate_keyframe":
            raise ProviderAdapterError("NODE_BINDING_SOURCE_INVALID", "reference_image.primary is valid only for keyframe image work.")
        reference = request.request_manifest.get("input_contract", {}).get("reference_image")
        if reference is None:
            return None
        required = {"role", "entity_version_id", "attachment_id", "uri", "mime_type", "byte_size", "content_hash"}
        if not isinstance(reference, dict) or set(reference) != required or reference.get("role") != "primary":
            raise ProviderAdapterError("REFERENCE_IMAGE_CONTRACT_INVALID", "The frozen primary reference contract is invalid.")
        mime_type = str(reference.get("mime_type") or "")
        if not mime_type.startswith("image/"):
            raise ProviderAdapterError("REFERENCE_IMAGE_MIME_INVALID", "The frozen primary reference is not an image.")
        path = _local_attachment_path(str(reference.get("uri") or ""))
        if not path.is_file():
            raise ProviderAdapterError("REFERENCE_IMAGE_FILE_MISSING", "The frozen primary reference file does not exist.")
        if path.stat().st_size != reference.get("byte_size"):
            raise ProviderAdapterError("REFERENCE_IMAGE_SIZE_MISMATCH", "The frozen primary reference file size changed.")
        content = path.read_bytes()
        if detect_media_type(content) != mime_type:
            raise ProviderAdapterError("REFERENCE_IMAGE_MIME_MISMATCH", "The frozen primary reference MIME changed.")
        if hashlib.sha256(content).hexdigest() != reference.get("content_hash"):
            raise ProviderAdapterError("REFERENCE_IMAGE_HASH_MISMATCH", "The frozen primary reference hash changed.")
        return path, mime_type

    def _binding_value(self, request: ProviderExecutionRequest, source: str, uploaded_values: dict[str, str]) -> Any:
        manifest = request.request_manifest
        if source.startswith("source_image"):
            if source not in uploaded_values:
                raise ProviderAdapterError("NODE_BINDING_SOURCE_INVALID", "source_image was not uploaded for this request.")
            return uploaded_values[source]
        if source == "reference_image.primary":
            value = uploaded_values.get(source)
            if value is None:
                raise ProviderAdapterError("NODE_BINDING_VALUE_MISSING", "The frozen primary reference image is not present.")
            return value
        if source == "reference_image.present":
            return manifest.get("input_contract", {}).get("reference_image") is not None
        if source.startswith("literal:"):
            try:
                return json.loads(source[len("literal:"):])
            except json.JSONDecodeError as exc:
                raise ProviderAdapterError("NODE_BINDING_LITERAL_INVALID", "A literal NodeInfoList value is not valid JSON.") from exc
        roots = {
            "shot": manifest.get("input_contract", {}).get("shot"),
            "duration_ms": manifest.get("input_contract", {}).get("duration_ms"),
            "duration_seconds": manifest.get("input_contract", {}).get("duration_seconds"),
            "video_spec": manifest.get("video_spec"),
            "seed": manifest.get("input_contract", {}).get("seed"),
        }
        if source in {"duration_ms", "duration_seconds", "seed"}:
            value = roots[source]
        elif source.startswith("shot."):
            try:
                value = _nested(roots["shot"], source[len("shot."):])
            except (KeyError, TypeError):
                value = None
        elif source.startswith("video_spec."):
            try:
                value = _nested(roots["video_spec"], source[len("video_spec."):])
            except (KeyError, TypeError):
                value = None
        else:
            raise ProviderAdapterError("NODE_BINDING_SOURCE_UNSUPPORTED", f"Unsupported NodeInfoList value source: {source}")
        if value is None:
            raise ProviderAdapterError("NODE_BINDING_VALUE_MISSING", f"NodeInfoList value source has no frozen value: {source}")
        return value

    @staticmethod
    def _coerce(value: Any, value_type: str) -> Any:
        try:
            if value_type in {"string", "image", "audio"}:
                if isinstance(value, (dict, list, bool)):
                    raise ValueError
                return str(value)
            if value_type == "integer":
                if isinstance(value, bool) or int(value) != float(value):
                    raise ValueError
                return int(value)
            if value_type == "number":
                if isinstance(value, bool):
                    raise ValueError
                return float(value)
            if value_type == "boolean":
                if not isinstance(value, bool):
                    raise ValueError
                return value
            if value_type == "json":
                return value
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderAdapterError("NODE_BINDING_TYPE_INVALID", f"NodeInfoList value cannot be represented as {value_type}.") from exc
        raise ProviderAdapterError("NODE_BINDING_TYPE_UNSUPPORTED", f"Unsupported NodeInfoList value type: {value_type}")

    def submit(self, request: ProviderExecutionRequest) -> ProviderSubmission:
        if not self.execution_enabled:
            raise ProviderAdapterError("EXTERNAL_PROVIDER_EXECUTION_DISABLED", "Real external provider execution is disabled.")
        provider, workflow, _storage, api_key = self._contract(request)
        base_url = str(provider["base_url"]).rstrip("/") + "/"
        timeout = int(provider["request_timeout_seconds"])
        bindings = workflow.get("node_info_list")
        if not isinstance(bindings, list) or not bindings:
            raise ProviderAdapterError("NODE_BINDING_LIST_INVALID", "The frozen RunningHub NodeInfoList is empty or invalid.")
        source_image_sources = [
            item.get("value_source")
            for item in bindings
            if isinstance(item, dict)
            and str(item.get("value_source") or "").startswith("source_image")
        ]
        source_image_count = len(source_image_sources)
        if request.work_kind == "generate_i2v_clip" and source_image_count != 1:
            raise ProviderAdapterError("I2V_SOURCE_IMAGE_BINDING_INVALID", "Image-to-video requires exactly one source_image NodeInfoList binding.")
        if request.work_kind == "generate_three_frame_i2v_clip" and set(source_image_sources) != {"source_image.start", "source_image.middle", "source_image.end"}:
            raise ProviderAdapterError("I2V_SOURCE_IMAGE_BINDING_INVALID", "Three-frame video requires exact start, middle, and end image bindings.")
        if request.work_kind not in {"generate_i2v_clip", "generate_three_frame_i2v_clip"} and source_image_count:
            raise ProviderAdapterError("NODE_BINDING_SOURCE_INVALID", "source_image is valid only for image-to-video work.")
        reference_image_count = sum(
            item.get("value_source") == "reference_image.primary"
            for item in bindings
            if isinstance(item, dict)
        )
        if request.work_kind != "generate_keyframe" and reference_image_count:
            raise ProviderAdapterError("NODE_BINDING_SOURCE_INVALID", "reference_image.primary is valid only for keyframe image work.")
        if reference_image_count > 1:
            raise ProviderAdapterError("REFERENCE_IMAGE_BINDING_INVALID", "Keyframe generation accepts at most one primary reference binding.")
        uploaded_values: dict[str, str] = {}
        for source in source_image_sources:
            image_path, mime_type = self._source_image(request, source)
            upload = self.transport.upload(urljoin(base_url, "media/upload/binary"), api_key, image_path, mime_type, timeout)
            uploaded_values[source] = _first(upload, ("fileName", "file_name", "filename", "path", "filePath", "file_path", "url"))
            if not uploaded_values[source]:
                raise ProviderAdapterError("RUNNINGHUB_UPLOAD_VALUE_MISSING", "RunningHub upload did not return a usable file value.")
        if reference_image_count:
            reference = self._reference_image(request)
            if reference is not None:
                image_path, mime_type = reference
                upload = self.transport.upload(urljoin(base_url, "media/upload/binary"), api_key, image_path, mime_type, timeout)
                uploaded_values["reference_image.primary"] = _first(
                    upload,
                    ("fileName", "file_name", "filename", "path", "filePath", "file_path", "url"),
                )
                if not uploaded_values["reference_image.primary"]:
                    raise ProviderAdapterError("RUNNINGHUB_UPLOAD_VALUE_MISSING", "RunningHub upload did not return a usable file value.")
        node_info: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for binding in bindings:
            if not isinstance(binding, dict):
                raise ProviderAdapterError("NODE_BINDING_INVALID", "A frozen NodeInfoList row is not an object.")
            identity = (str(binding.get("node_id") or ""), str(binding.get("field_path") or ""))
            if not all(identity) or identity in seen:
                raise ProviderAdapterError("NODE_BINDING_INVALID", "A frozen NodeInfoList row is incomplete or duplicated.")
            seen.add(identity)
            try:
                value = self._binding_value(request, str(binding.get("value_source") or ""), uploaded_values)
            except ProviderAdapterError as exc:
                if exc.code == "NODE_BINDING_VALUE_MISSING" and binding.get("required") is False:
                    continue
                raise
            node_info.append({"nodeId": identity[0], "fieldName": identity[1], "fieldValue": self._coerce(value, str(binding.get("value_type") or ""))})
        payload = {
            "apiKey": api_key,
            "addMetadata": True,
            "nodeInfoList": node_info,
            "instanceType": "default",
            "usePersonalQueue": False,
        }
        workflow_id = str(workflow.get("provider_workflow_id") or "").strip()
        if not workflow_id:
            raise ProviderAdapterError("RUNNINGHUB_WORKFLOW_ID_MISSING", "The frozen RunningHub workflow ID is missing.")
        try:
            response = self.transport.post_json(urljoin(base_url, f"run/workflow/{workflow_id}"), payload, timeout)
        except ProviderAdapterError as exc:
            raise ProviderAdapterError(
                "RUNNINGHUB_SUBMISSION_OUTCOME_UNKNOWN",
                "RunningHub submission outcome is unknown; manual reconciliation is required.",
            ) from exc
        task_id = _first(response, ("taskId", "task_id"))
        if not task_id:
            rejection = _submission_rejection(response)
            if rejection:
                code, detail, evidence = rejection
                raise ProviderAdapterError(code, detail, evidence)
            raise ProviderAdapterError(
                "RUNNINGHUB_SUBMISSION_OUTCOME_UNKNOWN",
                "RunningHub submission returned no task ID; manual reconciliation is required.",
                {
                    "schema_version": "runninghub-submission-unknown.v1",
                    "provider": "runninghub",
                    "remote_status": _status(response) or "UNKNOWN",
                },
            )
        return ProviderSubmission(task_id, {
            "schema_version": "runninghub-submission.v1",
            "provider": "runninghub",
            "provider_task_id": task_id,
            "remote_status": _status(response) or "SUBMITTED",
        })

    def poll(self, request: ProviderExecutionRequest, provider_task_id: str) -> ProviderPollResult:
        if not self.execution_enabled:
            raise ProviderAdapterError("EXTERNAL_PROVIDER_EXECUTION_DISABLED", "Real external provider execution is disabled.")
        provider, _workflow, storage, api_key = self._contract(request)
        timeout = int(provider["request_timeout_seconds"])
        base_url = str(provider["base_url"]).rstrip("/") + "/"
        response = self.transport.post_json(urljoin(base_url, "query"), {"taskId": provider_task_id}, timeout)
        state = _status(response)
        if state in {"FAILED", "FAIL", "ERROR", "CANCELED", "CANCELLED"}:
            return ProviderPollResult("failed", {"schema_version": "runninghub-response.v1", "provider_task_id": provider_task_id, "remote_status": state}, "RUNNINGHUB_TASK_FAILED", "RunningHub reported a terminal task failure.")
        if state != "SUCCESS":
            return ProviderPollResult("running", {"schema_version": "runninghub-response.v1", "provider_task_id": provider_task_id, "remote_status": state or "UNKNOWN"})
        expected_type = str(request.request_manifest.get("output_contract", {}).get("media_type") or "")
        max_bytes = int(storage["max_file_size_bytes"])
        allowed_mimes = set(storage.get("allowed_mime_types") or [])
        outputs: list[dict[str, Any]] = []
        output_root = RUNTIME_ROOT / "assets" / "providers" / "runninghub" / request.request_fingerprint
        try:
            output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProviderAdapterError("LOCAL_OUTPUT_WRITE_FAILED", "The V2 provider output directory could not be created.") from exc
        for index, result in enumerate(_results(response)):
            url = str(result.get("url") or result.get("fileUrl") or result.get("download_url") or "").strip()
            if not url:
                continue
            content, header_mime = self.transport.download(url, timeout, max_bytes)
            suffix = Path(urlparse(url).path).suffix.lower()
            mime_type = header_mime or mimetypes.types_map.get(suffix)
            if not mime_type or mime_type not in allowed_mimes:
                raise ProviderAdapterError("RUNNINGHUB_OUTPUT_MIME_INVALID", "RunningHub output MIME is not allowed by the frozen storage policy.")
            asset_type = mime_type.split("/", 1)[0]
            if asset_type != expected_type:
                continue
            extension = mimetypes.guess_extension(mime_type) or suffix or ".bin"
            path = output_root / f"output-{index:02d}{extension}"
            try:
                path.write_bytes(content)
            except OSError as exc:
                raise ProviderAdapterError("LOCAL_OUTPUT_WRITE_FAILED", "The RunningHub output could not be written to local storage.") from exc
            outputs.append({
                "uri": f"runtime://assets/providers/runninghub/{request.request_fingerprint}/{path.name}",
                "storage_backend": "local",
                "asset_type": asset_type,
                "role": "provider_output",
                "mime_type": mime_type,
                "content_hash": hashlib.sha256(content).hexdigest(),
                "byte_size": len(content),
                "provider_result_index": index,
            })
        if not outputs:
            raise ProviderAdapterError("RUNNINGHUB_MEDIA_OUTPUT_MISSING", "RunningHub succeeded but returned no matching media output.")
        return ProviderPollResult("succeeded", {
            "schema_version": "provider-response.v1",
            "provider": "runninghub",
            "provider_task_id": provider_task_id,
            "remote_status": state,
            "media_created": True,
            "outputs": outputs,
        })
