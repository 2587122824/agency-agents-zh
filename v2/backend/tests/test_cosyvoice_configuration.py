from copy import deepcopy
import io
import json
from types import SimpleNamespace
import wave

import pytest

from v2.backend.app.configuration.contracts import (
    AudioConfigDraft,
    PricingCatalogDraft,
    PricingRuleDraft,
    StoragePolicyDraft,
)
from v2.scripts.enable_cosyvoice_audio import enable_cosyvoice
from v2.scripts.upgrade_audio_execution_contract import upgrade_audio_execution
from v2.scripts.validate_cosyvoice_connection import (
    CosyVoiceValidationContract,
    CosyVoiceValidationError,
    run_validation,
)
import v2.backend.app.providers.cosyvoice as cosyvoice_module
import v2.scripts.validate_cosyvoice_connection as validation_module


def _draft():
    return SimpleNamespace(
        providers=[],
        workflow_slots=[],
        audio=AudioConfigDraft(
            config_key="audio",
            display_name="Audio Off",
            supported_modes=["off"],
            sample_rate=24000,
            channels=2,
            format="mp3",
            speaking_rate_min=0.8,
            speaking_rate_max=1.2,
            speaking_rate_default=1.0,
            voice_presets=[],
            volume_min=0,
            volume_max=100,
            volume_default=50,
            duration_tolerance_ms=1500,
        ),
        storage=StoragePolicyDraft(
            policy_key="storage",
            display_name="Local",
            backend_kind="local",
            allowed_mime_types=["video/mp4"],
            max_file_size_bytes=10_000_000,
            public_url_policy="none",
            local_root_ref="v2.runtime.assets",
        ),
        pricing=PricingCatalogDraft(
            catalog_key="test",
            display_name="Test",
            currency="TEST",
            confirmation_threshold=0,
            rules=[PricingRuleDraft(
                workflow_slot_key="video",
                unit="call",
                unit_price=1,
            )],
        ),
    )


def test_enable_cosyvoice_adds_one_explicit_audio_route() -> None:
    draft = _draft()

    assert enable_cosyvoice(draft) is True
    assert enable_cosyvoice(draft) is False
    assert [provider.adapter_kind for provider in draft.providers] == ["cosyvoice"]
    assert draft.audio.supported_modes == ["off", "voiceover"]
    assert draft.audio.tts_workflow_slot_key == "cosyvoice-voiceover-wav"
    assert draft.audio.format == "wav"
    assert draft.audio.default_voice_key == "warm_female"
    assert [preset.provider_voice_id for preset in draft.audio.voice_presets] == [
        "longxiaochun", "longxiaoxia", "longxiaocheng", "longxiaobai", "longlaotie",
    ]
    bindings = {binding.field_path: binding.value_source for binding in draft.workflow_slots[0].node_info_list}
    assert bindings["voice"] == "input_contract.voice.provider_voice_id"
    assert bindings["rate"] == "input_contract.speaking_rate"
    assert bindings["volume"] == "input_contract.volume"
    assert "audio/wav" in draft.storage.allowed_mime_types
    assert draft.pricing.rules[-1].unit == "output_second"


def test_upgrade_audio_execution_replaces_literal_voice_with_frozen_selection() -> None:
    draft = _draft()
    assert enable_cosyvoice(draft) is True
    workflow = draft.workflow_slots[0]
    workflow.input_schema_version = "cosyvoice-tts-input.v1"
    workflow.node_info_list = [
        binding for binding in workflow.node_info_list
        if binding.field_path not in {"rate", "volume"}
    ]
    workflow.node_info_list[1].value_source = "literal:longxiaochun"

    upgrade_audio_execution(draft)

    bindings = {binding.field_path: binding.value_source for binding in workflow.node_info_list}
    assert workflow.input_schema_version == "cosyvoice-tts-input.v2"
    assert bindings == {
        "text": "input_contract.voiceover_text",
        "voice": "input_contract.voice.provider_voice_id",
        "rate": "input_contract.speaking_rate",
        "volume": "input_contract.volume",
        "format": "literal:wav",
        "sample_rate": "literal:24000",
    }


def _validation_contract(api_key: str | None) -> CosyVoiceValidationContract:
    return CosyVoiceValidationContract(
        configuration=SimpleNamespace(id="config-v54", version_number=54, config_hash="f" * 64),
        provider=SimpleNamespace(
            id="provider-cosyvoice",
            provider_key="dashscope-cosyvoice",
            adapter_kind="cosyvoice",
            base_url="https://dashscope.aliyuncs.com",
            api_key=api_key,
            request_timeout_seconds=60,
        ),
        workflow=SimpleNamespace(
            id="workflow-cosyvoice",
            slot_key="cosyvoice-voiceover-wav",
            provider_workflow_id="cosyvoice-v1",
            provider_workflow_version="v1",
            input_schema_version="cosyvoice-tts-input.v1",
            output_schema_version="cosyvoice-wav-output.v1",
            node_info_list=[
                {"node_id": "input", "field_path": "text", "value_source": "input_contract.voiceover_text", "value_type": "string"},
                {"node_id": "input", "field_path": "voice", "value_source": "literal:longxiaochun", "value_type": "string"},
                {"node_id": "input", "field_path": "format", "value_source": "literal:wav", "value_type": "string"},
                {"node_id": "input", "field_path": "sample_rate", "value_source": "literal:24000", "value_type": "integer"},
            ],
        ),
        audio=SimpleNamespace(sample_rate=24000, channels=1, format="wav"),
        storage=SimpleNamespace(
            backend_kind="local",
            local_root_ref="v2.runtime.assets",
            allowed_mime_types=["audio/wav"],
            max_file_size_bytes=1_000_000,
        ),
    )


class _ValidationTransport:
    def __init__(self) -> None:
        self.calls = []

    def synthesize(self, url, api_key, payload, timeout, max_bytes):
        self.calls.append((url, api_key, payload, timeout, max_bytes))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(24000)
            target.writeframes(b"\x00\x00" * 2400)
        return {"request_id": "real-request-id", "usage": {"characters": 12}}, buffer.getvalue()


def test_cosyvoice_validation_check_is_read_only_and_never_reports_api_key(monkeypatch) -> None:
    monkeypatch.setenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "true")
    secret = "test-secret-that-must-not-leak"

    report = run_validation(_validation_contract(secret))

    assert report["status"] == "ready_for_paid_validation"
    assert report["network_probe_performed"] is False
    assert report["provider"]["api_key_state"] == "configured"
    assert secret not in json.dumps(report, ensure_ascii=False)


def test_cosyvoice_paid_validation_refuses_missing_key_without_network(monkeypatch) -> None:
    monkeypatch.setenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "true")
    transport = _ValidationTransport()

    with pytest.raises(CosyVoiceValidationError) as caught:
        run_validation(
            _validation_contract(None),
            confirm_paid_call=True,
            transport=transport,
        )

    assert caught.value.code == "COSYVOICE_CREDENTIAL_MISSING"
    assert transport.calls == []


def test_cosyvoice_paid_validation_uses_frozen_contract_and_reports_verified_wav(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "true")
    monkeypatch.setattr(cosyvoice_module, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(validation_module, "RUNTIME_ROOT", tmp_path)
    transport = _ValidationTransport()
    secret = "test-secret-that-must-not-leak"

    report = run_validation(
        _validation_contract(secret),
        text="片场真实验收。",
        confirm_paid_call=True,
        transport=transport,
    )

    assert report["status"] == "passed"
    assert report["network_probe_performed"] is True
    assert report["request_id"] == "real-request-id"
    assert report["output"]["mime_type"] == "audio/wav"
    assert report["output"]["duration_ms"] == 100
    assert report["output"]["sample_rate"] == 24000
    assert report["output"]["channels"] == 1
    assert transport.calls[0][2] == {
        "model": "cosyvoice-v1",
        "input": {
            "text": "片场真实验收。",
            "voice": "longxiaochun",
            "format": "wav",
            "sample_rate": 24000,
        },
    }
    assert secret not in json.dumps(report, ensure_ascii=False)
