from __future__ import annotations

import hashlib
import io
from pathlib import Path
import wave

import httpx
import pytest

from v2.backend.app.providers import ProviderAdapterError, ProviderExecutionRequest
from v2.backend.app.providers.builtin import LocalSubtitleAdapter
from v2.backend.app.providers.registry import default_provider_registry
from v2.backend.app.providers.runninghub import HttpxRunningHubTransport, RunningHubAdapter
from v2.backend.app.providers.cosyvoice import CosyVoiceAdapter
import v2.backend.app.providers.runninghub as runninghub_module
import v2.backend.app.providers.cosyvoice as cosyvoice_module
import v2.backend.app.providers.builtin as builtin_module


def test_provider_registry_resolves_only_exact_registered_work_kind() -> None:
    registry = default_provider_registry()
    runninghub = registry.get("runninghub")
    assert runninghub is not None
    assert runninghub.execution_enabled is False
    assert registry.resolve("runninghub", "generate_keyframe") is runninghub
    assert registry.resolve("runninghub", "generate_t2v_clip") is runninghub
    assert registry.resolve("local", "generate_keyframe") is None
    local = registry.resolve("local", "assemble_timeline_contract")
    assert local is not None
    response = local.execute(ProviderExecutionRequest(
        work_kind="assemble_timeline_contract",
        request_fingerprint="a" * 64,
        request_manifest={"adapter_kind": "local"},
        parent_work_item_ids=("work_1", "work_2"),
    ))
    assert response == {
        "schema_version": "timeline-contract-result.v1",
        "result": "contract_assembled",
        "input_work_item_ids": ["work_1", "work_2"],
        "media_created": False,
    }


class FakeRunningHubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.submit_response = {"taskId": "rh-task-1", "status": "RUNNING"}
        self.query_response = {"taskId": "rh-task-1", "status": "RUNNING"}
        self.download_response = (b"\x89PNG\r\n\x1a\nimage-bytes", "image/png")

    def post_json(self, url: str, api_key: str, payload: dict, timeout: int) -> dict:
        self.calls.append(("post_json", url, payload, api_key, timeout))
        return self.query_response if url.endswith("/query") else self.submit_response

    def upload(self, url: str, api_key: str, path: Path, mime_type: str, timeout: int) -> dict:
        self.calls.append(("upload", url, api_key, path, mime_type, timeout))
        return {"data": {"fileName": "uploaded/input.png"}}

    def download(self, url: str, timeout: int, max_bytes: int) -> tuple[bytes, str | None]:
        self.calls.append(("download", url, timeout, max_bytes))
        return self.download_response


class FakeCosyVoiceTransport:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.calls: list[tuple] = []

    def synthesize(self, url, api_key, payload, timeout, max_bytes):
        self.calls.append((url, api_key, payload, timeout, max_bytes))
        return {"request_id": "cosy-request-1", "usage": {"characters": 4}}, self.content


def wav_bytes(*, sample_rate: int = 24000, channels: int = 1, frame_count: int = 2400) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(b"\x00\x00" * frame_count * channels)
    return buffer.getvalue()


def cosyvoice_manifest() -> dict:
    return {
        "adapter_kind": "cosyvoice",
        "input_contract": {"voiceover_text": "你好，世界。"},
        "output_contract": {"media_type": "audio"},
        "provider": {
            "provider_key": "dashscope-cosyvoice",
            "adapter_kind": "cosyvoice",
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key": "test-secret",
            "request_timeout_seconds": 60,
        },
        "workflow": {
            "provider_workflow_id": "cosyvoice-v1",
            "node_info_list": [
                {"node_id": "input", "field_path": "text", "value_source": "input_contract.voiceover_text", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "voice", "value_source": "literal:longxiaochun", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "format", "value_source": "literal:wav", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "sample_rate", "value_source": "literal:24000", "value_type": "integer", "required": True},
            ],
        },
        "storage_policy": {
            "backend_kind": "local",
            "local_root_ref": "v2.runtime.assets",
            "allowed_mime_types": ["audio/wav"],
            "max_file_size_bytes": 1024 * 1024,
        },
    }


def test_cosyvoice_synthesizes_exact_frozen_text_and_registers_wav(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cosyvoice_module, "RUNTIME_ROOT", tmp_path)
    content = wav_bytes()
    transport = FakeCosyVoiceTransport(content)
    adapter = CosyVoiceAdapter(execution_enabled=True, transport=transport)

    result = adapter.execute(ProviderExecutionRequest(
        "generate_tts", "a" * 64, cosyvoice_manifest(),
    ))

    assert transport.calls[0][2] == {
        "model": "cosyvoice-v1",
        "input": {
            "text": "你好，世界。",
            "voice": "longxiaochun",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    assert transport.calls[0][1] == "test-secret"
    assert "test-secret" not in repr(result)
    assert result["outputs"][0]["asset_type"] == "audio"
    assert result["outputs"][0]["sample_rate"] == 24000
    assert result["outputs"][0]["channels"] == 1
    assert result["outputs"][0]["duration_ms"] == 100
    assert (tmp_path / "assets" / "providers" / "cosyvoice" / ("a" * 64) / "voiceover.wav").read_bytes() == content


def test_cosyvoice_rejects_non_wav_output_without_persisting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cosyvoice_module, "RUNTIME_ROOT", tmp_path)
    adapter = CosyVoiceAdapter(execution_enabled=True, transport=FakeCosyVoiceTransport(b"not-wav"))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.execute(ProviderExecutionRequest("generate_tts", "b" * 64, cosyvoice_manifest()))

    assert caught.value.code == "COSYVOICE_OUTPUT_SIGNATURE_INVALID"
    assert not list(tmp_path.rglob("voiceover.wav"))


def test_cosyvoice_rejects_wav_with_wrong_sample_rate_before_persisting(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cosyvoice_module, "RUNTIME_ROOT", tmp_path)
    adapter = CosyVoiceAdapter(
        execution_enabled=True,
        transport=FakeCosyVoiceTransport(wav_bytes(sample_rate=16000)),
    )

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.execute(ProviderExecutionRequest("generate_tts", "e" * 64, cosyvoice_manifest()))

    assert caught.value.code == "COSYVOICE_OUTPUT_SAMPLE_RATE_MISMATCH"
    assert caught.value.response_manifest == {
        "expected_sample_rate": 24000,
        "actual_sample_rate": 16000,
    }
    assert not list(tmp_path.rglob("voiceover.wav"))


def subtitle_manifest(cues: list[dict], duration_ms: int = 3000) -> dict:
    return {
        "input_contract": {"cues": cues, "duration_ms": duration_ms},
        "output_contract": {"media_type": "subtitle"},
        "storage_policy": {
            "backend_kind": "local",
            "local_root_ref": "v2.runtime.assets",
            "max_file_size_bytes": 1024 * 1024,
        },
    }


def test_local_subtitle_adapter_writes_exact_frozen_srt(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(builtin_module, "RUNTIME_ROOT", tmp_path)
    fingerprint = "c" * 64
    result = LocalSubtitleAdapter().execute(ProviderExecutionRequest(
        "generate_subtitles",
        fingerprint,
        subtitle_manifest([
            {"timeline_in_ms": 0, "timeline_out_ms": 1200, "text": "第一句"},
            {"timeline_in_ms": 1500, "timeline_out_ms": 3000, "text": "第二句"},
        ]),
    ))

    output = tmp_path / "assets" / "providers" / "local_subtitle" / fingerprint / "subtitles.srt"
    assert output.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,200\n第一句\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\n第二句\n"
    )
    assert result["outputs"][0]["asset_type"] == "subtitle"
    assert result["outputs"][0]["duration_ms"] == 3000
    assert result["outputs"][0]["cue_count"] == 2
    assert result["outputs"][0]["content_hash"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_local_subtitle_adapter_rejects_overlapping_cues_without_writing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(builtin_module, "RUNTIME_ROOT", tmp_path)
    fingerprint = "d" * 64

    with pytest.raises(ProviderAdapterError) as caught:
        LocalSubtitleAdapter().execute(ProviderExecutionRequest(
            "generate_subtitles",
            fingerprint,
            subtitle_manifest([
                {"timeline_in_ms": 0, "timeline_out_ms": 1600, "text": "第一句"},
                {"timeline_in_ms": 1500, "timeline_out_ms": 3000, "text": "重叠"},
            ]),
        ))

    assert caught.value.code == "SUBTITLE_CUE_INVALID"
    assert not list(tmp_path.rglob("subtitles.srt"))


def runninghub_manifest(bindings: list[dict], media_type: str = "image") -> dict:
    return {
        "schema_version": "production-work-request.v3",
        "adapter_kind": "runninghub",
        "input_contract": {
            "shot": {"action": "run", "composition": "wide", "visual_prompt": "athlete running", "negative_prompt": None},
            "duration_ms": 4000,
            "duration_seconds": 4.0,
            "reference_image": None,
        },
        "output_contract": {"media_type": media_type},
        "provider": {
            "provider_key": "runninghub",
            "adapter_kind": "runninghub",
            "base_url": "https://www.runninghub.cn/openapi/v2",
            "api_key": "test-secret",
            "request_timeout_seconds": 60,
            "poll_interval_seconds": 5,
            "max_concurrency": 1,
        },
        "workflow": {
            "provider_workflow_id": "workflow-1",
            "provider_workflow_version": "v1",
            "node_info_list": bindings,
        },
        "video_spec": {"width": 480, "height": 848, "fps": 24, "long_side": 848, "frame_count": 96},
        "storage_policy": {
            "backend_kind": "local",
            "local_root_ref": "v2.runtime.assets",
            "allowed_mime_types": ["image/png", "video/mp4"],
            "max_file_size_bytes": 1024 * 1024,
        },
    }


def enabled_adapter(transport: FakeRunningHubTransport) -> RunningHubAdapter:
    return RunningHubAdapter(
        execution_enabled=True,
        transport=transport,
    )


def test_runninghub_disabled_gate_prevents_transport_calls() -> None:
    transport = FakeRunningHubTransport()
    adapter = RunningHubAdapter(
        execution_enabled=False,
        transport=transport,
    )
    request = ProviderExecutionRequest("generate_keyframe", "a" * 64, runninghub_manifest([]))
    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)
    assert caught.value.code == "EXTERNAL_PROVIDER_EXECUTION_DISABLED"
    assert transport.calls == []


def test_runninghub_resolves_only_declared_node_sources() -> None:
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    bindings = [
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
        {"node_id": "2", "field_path": "width", "value_source": "video_spec.width", "value_type": "integer", "required": True},
        {"node_id": "3", "field_path": "flag", "value_source": "literal:true", "value_type": "boolean", "required": True},
    ]
    request = ProviderExecutionRequest("generate_keyframe", "b" * 64, runninghub_manifest(bindings))
    result = adapter.submit(request)
    assert result.provider_task_id == "rh-task-1"
    payload = transport.calls[0][2]
    assert payload["nodeInfoList"] == [
        {"nodeId": "1", "fieldName": "text", "fieldValue": "athlete running"},
        {"nodeId": "2", "fieldName": "width", "fieldValue": 480},
        {"nodeId": "3", "fieldName": "flag", "fieldValue": True},
    ]
    assert "apiKey" not in payload
    assert payload["usePersonalQueue"] == "false"
    assert transport.calls[0][3] == "test-secret"
    assert "test-secret" not in repr(result)


def test_runninghub_http_transport_uses_v2_bearer_auth(monkeypatch) -> None:
    captured: dict = {}

    def fake_post(url: str, **kwargs) -> httpx.Response:
        captured.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"taskId": "rh-task-1", "status": "RUNNING"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(runninghub_module.httpx, "post", fake_post)
    response = HttpxRunningHubTransport().post_json(
        "https://www.runninghub.cn/openapi/v2/run/workflow/workflow-1",
        "test-secret",
        {"addMetadata": True, "nodeInfoList": []},
        60,
    )

    assert response["taskId"] == "rh-task-1"
    assert captured["headers"]["Authorization"] == "Bearer test-secret"
    assert captured["headers"]["Accept"] == "application/json"
    assert captured["json"] == {"addMetadata": True, "nodeInfoList": []}
    assert "apiKey" not in captured["json"]


@pytest.mark.parametrize(
    ("response", "expected_detail"),
    [
        (
            {"code": 416, "msg": "TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET"},
            "RunningHub 账户余额不足",
        ),
        (
            {"code": 433, "errorMessage": "nodeInfoList validation failed", "apiKey": "must-not-persist"},
            "RunningHub 拒绝了工作流参数",
        ),
    ],
)
def test_runninghub_preserves_sanitized_explicit_submission_rejection(
    response: dict,
    expected_detail: str,
) -> None:
    transport = FakeRunningHubTransport()
    transport.submit_response = response
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "4" * 64, runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
    ]))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)

    assert caught.value.code == "RUNNINGHUB_SUBMISSION_REJECTED"
    assert expected_detail in caught.value.detail
    assert caught.value.response_manifest["schema_version"] == "runninghub-submission-rejection.v1"
    assert caught.value.response_manifest["provider_code"] == str(response["code"])
    assert "apiKey" not in caught.value.response_manifest
    assert "must-not-persist" not in repr(caught.value.response_manifest)


def test_runninghub_missing_task_id_without_rejection_remains_unknown() -> None:
    transport = FakeRunningHubTransport()
    transport.submit_response = {"code": 0, "msg": "success", "status": "RUNNING"}
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "5" * 64, runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
    ]))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)

    assert caught.value.code == "RUNNINGHUB_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.response_manifest == {
        "schema_version": "runninghub-submission-unknown.v1",
        "provider": "runninghub",
        "remote_status": "RUNNING",
    }


def test_runninghub_transport_failure_remains_unknown_without_retry() -> None:
    class FailingTransport(FakeRunningHubTransport):
        def post_json(self, url: str, api_key: str, payload: dict, timeout: int) -> dict:
            raise ProviderAdapterError("RUNNINGHUB_HTTP_FAILED", "request timeout")

    transport = FailingTransport()
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "6" * 64, runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
    ]))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)

    assert caught.value.code == "RUNNINGHUB_SUBMISSION_OUTCOME_UNKNOWN"
    assert caught.value.response_manifest is None


def test_runninghub_uploads_only_frozen_primary_reference_and_omits_optional_null(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    content = b"\x89PNG\r\n\x1a\nreference"
    path = tmp_path / "uploads" / "project" / "reference.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    manifest = runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
        {"node_id": "2", "field_path": "negative", "value_source": "shot.negative_prompt", "value_type": "string", "required": False},
        {"node_id": "3", "field_path": "image", "value_source": "reference_image.primary", "value_type": "image", "required": True},
        {"node_id": "4", "field_path": "has_image", "value_source": "reference_image.present", "value_type": "boolean", "required": True},
    ])
    manifest["input_contract"]["reference_image"] = {
        "role": "primary",
        "entity_version_id": "entity-version-1",
        "attachment_id": "attachment-1",
        "uri": "runtime://attachments/uploads/project/reference.png",
        "mime_type": "image/png",
        "byte_size": len(content),
        "content_hash": hashlib.sha256(content).hexdigest(),
    }
    adapter.submit(ProviderExecutionRequest("generate_keyframe", "1" * 64, manifest))
    assert [call[0] for call in transport.calls] == ["upload", "post_json"]
    assert transport.calls[0][3] == path
    assert transport.calls[1][2]["nodeInfoList"] == [
        {"nodeId": "1", "fieldName": "text", "fieldValue": "athlete running"},
        {"nodeId": "3", "fieldName": "image", "fieldValue": "uploaded/input.png"},
        {"nodeId": "4", "fieldName": "has_image", "fieldValue": True},
    ]


def test_runninghub_optional_reference_is_omitted_without_upload() -> None:
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    manifest = runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
        {"node_id": "2", "field_path": "image", "value_source": "reference_image.primary", "value_type": "image", "required": False},
        {"node_id": "3", "field_path": "has_image", "value_source": "reference_image.present", "value_type": "boolean", "required": True},
    ])
    adapter.submit(ProviderExecutionRequest("generate_keyframe", "2" * 64, manifest))
    assert [call[0] for call in transport.calls] == ["post_json"]
    assert transport.calls[0][2]["nodeInfoList"] == [
        {"nodeId": "1", "fieldName": "text", "fieldValue": "athlete running"},
        {"nodeId": "3", "fieldName": "has_image", "fieldValue": False},
    ]


def test_runninghub_blocks_changed_reference_before_network(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    path = tmp_path / "uploads" / "project" / "reference.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    manifest = runninghub_manifest([
        {"node_id": "1", "field_path": "image", "value_source": "reference_image.primary", "value_type": "image", "required": True},
    ])
    manifest["input_contract"]["reference_image"] = {
        "role": "primary",
        "entity_version_id": "entity-version-1",
        "attachment_id": "attachment-1",
        "uri": "runtime://attachments/uploads/project/reference.png",
        "mime_type": "image/png",
        "byte_size": path.stat().st_size,
        "content_hash": "0" * 64,
    }
    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(ProviderExecutionRequest("generate_keyframe", "3" * 64, manifest))
    assert caught.value.code == "REFERENCE_IMAGE_HASH_MISMATCH"
    assert transport.calls == []


def test_runninghub_rejects_legacy_prompt_placeholder_before_transport() -> None:
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    bindings = [{"node_id": "1", "field_path": "text", "value_source": "{{prompt}}", "value_type": "string", "required": True}]
    request = ProviderExecutionRequest("generate_keyframe", "c" * 64, runninghub_manifest(bindings))
    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)
    assert caught.value.code == "NODE_BINDING_SOURCE_UNSUPPORTED"
    assert transport.calls == []


def test_runninghub_i2v_requires_exactly_one_local_parent_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    bindings = [{"node_id": "1", "field_path": "image", "value_source": "source_image", "value_type": "image", "required": True}]
    manifest = runninghub_manifest(bindings, "video")
    request = ProviderExecutionRequest("generate_i2v_clip", "d" * 64, manifest, parent_outputs=())
    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(request)
    assert caught.value.code == "I2V_PARENT_IMAGE_COUNT_INVALID"
    assert transport.calls == []

    image_path = tmp_path / "assets" / "parents" / "input.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"png")
    parent = {"uri": "runtime://assets/parents/input.png", "storage_backend": "local", "asset_type": "image", "mime_type": "image/png"}
    request = ProviderExecutionRequest("generate_i2v_clip", "e" * 64, manifest, parent_outputs=(parent,))
    adapter.submit(request)
    assert transport.calls[0][0] == "upload"
    assert transport.calls[1][2]["nodeInfoList"][0]["fieldValue"] == "uploaded/input.png"


def test_runninghub_three_frame_video_uploads_exact_named_parent_slots(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    bindings = [
        {"node_id": str(index), "field_path": "image", "value_source": f"source_image.{role}", "value_type": "image", "required": True}
        for index, role in enumerate(("start", "middle", "end"), 1)
    ]
    manifest = runninghub_manifest(bindings, "video")
    parents = []
    for role in ("start", "middle", "end"):
        path = tmp_path / "assets" / "parents" / f"{role}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(role.encode())
        parents.append({
            "uri": f"runtime://assets/parents/{role}.png",
            "storage_backend": "local",
            "asset_type": "image",
            "mime_type": "image/png",
            "input_slot": f"source_image.{role}",
        })
    adapter.submit(ProviderExecutionRequest(
        "generate_three_frame_i2v_clip", "7" * 64, manifest, parent_outputs=tuple(parents),
    ))
    assert [call[0] for call in transport.calls] == ["upload", "upload", "upload", "post_json"]
    assert [item["nodeId"] for item in transport.calls[-1][2]["nodeInfoList"]] == ["1", "2", "3"]


def test_runninghub_three_frame_video_rejects_a_missing_named_parent(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    bindings = [
        {"node_id": str(index), "field_path": "image", "value_source": f"source_image.{role}", "value_type": "image", "required": True}
        for index, role in enumerate(("start", "middle", "end"), 1)
    ]
    manifest = runninghub_manifest(bindings, "video")
    parents = []
    for role in ("start", "end"):
        path = tmp_path / "assets" / "parents" / f"{role}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(role.encode())
        parents.append({
            "uri": f"runtime://assets/parents/{role}.png",
            "storage_backend": "local",
            "asset_type": "image",
            "mime_type": "image/png",
            "input_slot": f"source_image.{role}",
        })

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.submit(ProviderExecutionRequest(
            "generate_three_frame_i2v_clip", "8" * 64, manifest, parent_outputs=tuple(parents),
        ))

    assert caught.value.code == "I2V_PARENT_IMAGE_COUNT_INVALID"
    assert transport.calls == []


def test_runninghub_text_to_video_uses_no_parent_image_or_upload() -> None:
    transport = FakeRunningHubTransport()
    adapter = enabled_adapter(transport)
    manifest = runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True},
        {"node_id": "2", "field_path": "duration", "value_source": "duration_seconds", "value_type": "number", "required": True},
    ], "video")
    request = ProviderExecutionRequest("generate_t2v_clip", "9" * 64, manifest, parent_outputs=())
    adapter.submit(request)
    assert [call[0] for call in transport.calls] == ["post_json"]
    assert transport.calls[0][2]["nodeInfoList"] == [
        {"nodeId": "1", "fieldName": "text", "fieldValue": "athlete running"},
        {"nodeId": "2", "fieldName": "duration", "fieldValue": 4.0},
    ]


def test_runninghub_poll_downloads_deterministic_local_output(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    transport.query_response = {"taskId": "rh-task-1", "status": "SUCCESS", "results": [{"url": "https://files.invalid/result.png"}]}
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "f" * 64, runninghub_manifest([
        {"node_id": "1", "field_path": "text", "value_source": "shot.visual_prompt", "value_type": "string", "required": True}
    ]))
    result = adapter.poll(request, "rh-task-1")
    assert result.state == "succeeded"
    output = result.response_manifest["outputs"][0]
    assert output["uri"] == f"runtime://assets/providers/runninghub/{'f' * 64}/output-00.png"
    assert (tmp_path / "assets" / "providers" / "runninghub" / ("f" * 64) / "output-00.png").read_bytes() == b"\x89PNG\r\n\x1a\nimage-bytes"
    assert transport.calls[0][2] == {"taskId": "rh-task-1"}
    assert transport.calls[0][3] == "test-secret"


def test_runninghub_poll_ignores_declared_auxiliary_text_before_validating_image(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    transport.query_response = {
        "taskId": "rh-task-1",
        "status": "SUCCESS",
        "results": [
            {
                "url": "https://files.invalid/metadata.txt",
                "nodeId": "35",
                "outputType": "txt",
            },
            {
                "url": "https://files.invalid/result.png",
                "nodeId": "48",
                "outputType": "png",
            },
        ],
    }
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "7" * 64, runninghub_manifest([]))

    result = adapter.poll(request, "rh-task-1")

    assert result.state == "succeeded"
    assert [call[1] for call in transport.calls if call[0] == "download"] == ["https://files.invalid/result.png"]
    assert result.response_manifest["outputs"][0]["provider_result_index"] == 1
    assert result.response_manifest["outputs"][0]["uri"].endswith("/output-01.png")
    assert result.response_manifest["ignored_outputs"] == [{
        "schema_version": "runninghub-output-validation.v1",
        "provider": "runninghub",
        "provider_task_id": "rh-task-1",
        "remote_status": "SUCCESS",
        "expected_media_type": "image",
        "provider_result_index": 0,
        "provider_node_id": "35",
        "provider_output_type": "txt",
        "declared_mime_type": "text/plain",
        "url_suffix_mime_type": "text/plain",
        "response_mime_type": None,
        "detected_mime_type": None,
    }]


def test_runninghub_poll_blocks_target_media_when_bytes_disagree_with_declared_mime(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    transport.query_response = {
        "taskId": "rh-task-1",
        "status": "SUCCESS",
        "results": [{"url": "https://files.invalid/result.png", "outputType": "png"}],
    }
    transport.download_response = (b"\xff\xd8\xffjpeg-bytes", "image/png")
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "8" * 64, runninghub_manifest([]))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.poll(request, "rh-task-1")

    assert caught.value.code == "RUNNINGHUB_OUTPUT_MIME_MISMATCH"
    assert caught.value.response_manifest["declared_mime_type"] == "image/png"
    assert caught.value.response_manifest["response_mime_type"] == "image/png"
    assert caught.value.response_manifest["detected_mime_type"] == "image/jpeg"


def test_runninghub_poll_reports_verified_disallowed_target_mime(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(runninghub_module, "RUNTIME_ROOT", tmp_path)
    transport = FakeRunningHubTransport()
    transport.query_response = {
        "taskId": "rh-task-1",
        "status": "SUCCESS",
        "results": [{"url": "https://files.invalid/result.webp", "outputType": "webp"}],
    }
    transport.download_response = (b"RIFF\x08\x00\x00\x00WEBPpayload", "image/webp")
    adapter = enabled_adapter(transport)
    request = ProviderExecutionRequest("generate_keyframe", "0" * 64, runninghub_manifest([]))

    with pytest.raises(ProviderAdapterError) as caught:
        adapter.poll(request, "rh-task-1")

    assert caught.value.code == "RUNNINGHUB_OUTPUT_MIME_INVALID"
    assert "image/webp" in caught.value.detail
    assert caught.value.response_manifest["detected_mime_type"] == "image/webp"
    assert caught.value.response_manifest["allowed_mime_types"] == ["image/png", "video/mp4"]
