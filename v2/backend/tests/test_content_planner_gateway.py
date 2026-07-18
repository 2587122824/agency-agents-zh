from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
TEST_RUNTIME = Path(__file__).resolve().parent / "test_runtime"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["V2_RUNTIME_ROOT"] = str(TEST_RUNTIME)

from v2.backend.app.creation.agent_gateway import AgentGatewayError
from v2.backend.app.planning.agent_gateway import ConfiguredContentPlannerGateway, ContentPlannerSelection
from v2.backend.app.providers.credentials import EnvironmentCredentialResolver


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response


def selection() -> ContentPlannerSelection:
    return ContentPlannerSelection(
        production_config_version_id="production_config_1",
        model_config_version_id="model_config_planner_1",
        provider_config_version_id="provider_config_1",
        model_provider="DeepSeek",
        model_name="Content planner",
        provider_model_id="configured-model",
        base_url="https://api.example.test/v1",
        credential_ref="env://TEST_AGENT_KEY",
        timeout_seconds=30,
        input_contract_version="content-planner-input.v1",
        prompt_contract_version="content-planner-prompt.v1",
        output_schema_version="creative-brief-candidate.v1",
        max_output_tokens=2000,
        sampling={"temperature": 0.2, "unsupported": "ignored"},
    )


def manifest(*, audio_policy: str = "off", platform: str | None = None) -> dict:
    return {
        "contract_version": "content-planner-input.v1",
        "project_id": "project_1",
        "requirement_version": {
            "id": "requirement_1",
            "fields": {"core_topic": "居家健身", "duration_seconds": 30, "aspect_ratio": "9:16", "audio_mode": audio_policy},
        },
        "confirmed_decisions": [],
        "confirmed_entity_versions": [{
            "id": "entity_version_1",
            "entity_id": "entity_1",
            "entity_type": "character",
            "display_name": "主角",
            "version_number": 1,
            "attributes": {},
        }],
        "delivery_constraints": {"duration_ms": 30_000, "aspect_ratio": "9:16"},
        "audio_policy": audio_policy,
        "platform": platform,
        "template_version_id": None,
        "system_config_version": "production_config_1",
    }


def valid_output(*, spoken_text: str | None = None, platform_adaptation: str | None = None) -> dict:
    return {
        "title": "清晨训练日记",
        "content_promise": "用一次完整训练展示坚持带来的变化",
        "audience_takeaway": "看到可以执行的训练节奏",
        "hook": {"kind": "visual_action", "content": "从系紧鞋带开始"},
        "narrative_beats": [
            {"beat_code": "BEAT_01", "purpose": "建立目标", "summary": "准备训练", "target_duration_ms": 6000},
            {"beat_code": "BEAT_02", "purpose": "展开过程", "summary": "完成训练", "target_duration_ms": 18000},
            {"beat_code": "BEAT_03", "purpose": "收束结果", "summary": "展示结果", "target_duration_ms": 6000},
        ],
        "script_segments": [
            {"segment_code": "SEG_01", "beat_code": "BEAT_01", "kind": "visual_only" if spoken_text is None else "voiceover", "spoken_text": spoken_text, "on_screen_text": None},
            {"segment_code": "SEG_02", "beat_code": "BEAT_02", "kind": "visual_only", "spoken_text": None, "on_screen_text": None},
            {"segment_code": "SEG_03", "beat_code": "BEAT_03", "kind": "visual_only", "spoken_text": None, "on_screen_text": None},
        ],
        "tone": "克制、真实",
        "pacing": "前快后稳",
        "platform_adaptation": platform_adaptation,
        "entity_version_ids": ["entity_version_1"],
        "constraints_carried_forward": ["audio_policy=off"],
        "open_questions": [],
    }


def gateway_for(response: dict) -> tuple[ConfiguredContentPlannerGateway, FakeTransport]:
    transport = FakeTransport(response)
    gateway = ConfiguredContentPlannerGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_AGENT_KEY": "secret"}, {"TEST_AGENT_KEY"}),
    )
    return gateway, transport


def test_content_planner_returns_strict_brief_without_conversation_or_retry(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    gateway, transport = gateway_for({
        "id": "planner-request-1",
        "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}],
        "usage": {"total_tokens": 88},
    })

    result = gateway.invoke(selection(), manifest())

    assert result.output.title == "清晨训练日记"
    assert result.provider_request_id == "planner-request-1"
    assert result.token_usage == {"total_tokens": 88}
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request["payload"]["response_format"] == {"type": "json_object"}
    assert request["payload"]["temperature"] == 0.2
    assert "unsupported" not in request["payload"]
    sent = request["payload"]["messages"]
    assert [item["role"] for item in sent] == ["system", "user"]
    assert "conversation" not in sent[1]["content"]


def test_content_planner_rejects_audio_when_audio_policy_is_off_without_retry(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(valid_output(spoken_text="开始训练"), ensure_ascii=False)}}]})

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest(audio_policy="off"))

    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1


def test_content_planner_rejects_unknown_entity_and_default_platform_adaptation(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    unknown = valid_output()
    unknown["entity_version_ids"] = ["entity_version_unknown"]
    gateway, _ = gateway_for({"choices": [{"message": {"content": json.dumps(unknown, ensure_ascii=False)}}]})
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())
    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"

    adapted = valid_output(platform_adaptation="按短视频平台节奏处理")
    gateway, _ = gateway_for({"choices": [{"message": {"content": json.dumps(adapted, ensure_ascii=False)}}]})
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest(platform=None))
    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"


def test_content_planner_rejects_duration_mismatch_and_non_json_without_repair(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    mismatch = valid_output()
    mismatch["narrative_beats"][2]["target_duration_ms"] = 5000
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(mismatch, ensure_ascii=False)}}]})
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())
    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1

    gateway, transport = gateway_for({"choices": [{"message": {"content": "```json\n{}\n```"}}]})
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())
    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1
