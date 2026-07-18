from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
TEST_RUNTIME = Path(__file__).resolve().parent / "test_runtime"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["V2_RUNTIME_ROOT"] = str(TEST_RUNTIME)

from v2.backend.app.creation.agent_gateway import (
    AgentGatewayError,
    ConfiguredCreativeAgentGateway,
    CreativeAgentSelection,
)
from v2.backend.app.providers.credentials import EnvironmentCredentialResolver


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response


def selection() -> CreativeAgentSelection:
    return CreativeAgentSelection(
        production_config_version_id="production_config_1",
        model_config_version_id="model_config_1",
        provider_config_version_id="provider_config_1",
        model_provider="DeepSeek",
        model_name="Creative model",
        provider_model_id="configured-model",
        base_url="https://api.example.test/v1",
        credential_ref="env://TEST_AGENT_KEY",
        timeout_seconds=30,
        prompt_contract_version="creative-dialogue.v2",
        output_schema_version="creative-turn.v2",
        max_output_tokens=1000,
        sampling={"temperature": 0.2, "unsupported": "ignored"},
    )


def manifest() -> dict:
    return {
        "runtime_context": {"assistant_name": "片场创作制片人", "locale": "zh-CN"},
        "project_context": {
            "active_requirement": {"id": "requirement_1", "fields": {}},
            "confirmed_attachment_bindings": [],
            "confirmed_decisions": [],
        },
        "conversation": {"messages": [
            {"id": "message_1", "role": "user", "content": "给我三个训练短片方向", "reply_to": None},
            {"id": "message_2", "role": "assistant", "content": "可以选择训练日记、挑战记录或技巧教学。", "reply_to": "message_1"},
            {"id": "message_3", "role": "user", "content": "第一个", "reply_to": "message_2"},
        ]},
        "system_config_version": "production_config_1",
    }


def test_configured_gateway_returns_strict_output_without_retry(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    content = {
        "assistant_reply": "我已记录训练短片方向。",
        "suggestion_sets": [{
            "category": "content_direction",
            "title": "选择内容结构",
            "options": [
                {"label": "训练日记", "summary": "按训练过程推进", "proposed_updates": [{"field_key": "content_structure", "value": "training_diary", "source_message_ids": ["message_3"]}]},
                {"label": "挑战记录", "summary": "突出前后对比", "proposed_updates": [{"field_key": "content_structure", "value": "challenge_record", "source_message_ids": ["message_3"]}]},
            ],
        }],
        "explicit_updates": [],
        "clarifying_question": None,
    }
    transport = FakeTransport({
        "id": "provider-request-1",
        "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    })
    gateway = ConfiguredCreativeAgentGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_AGENT_KEY": "secret"}, {"TEST_AGENT_KEY"}),
    )

    result = gateway.invoke(selection(), manifest())

    assert result.output.assistant_reply == "我已记录训练短片方向。"
    assert result.output.suggestion_sets[0].options[0].label == "训练日记"
    assert result.provider_request_id == "provider-request-1"
    assert result.token_usage["total_tokens"] == 18
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request["url"] == "https://api.example.test/v1/chat/completions"
    assert request["payload"]["model"] == "configured-model"
    assert request["payload"]["response_format"] == {"type": "json_object"}
    assert request["payload"]["temperature"] == 0.2
    assert "unsupported" not in request["payload"]
    sent_messages = request["payload"]["messages"]
    assert sent_messages[-2]["role"] == "assistant"
    assert sent_messages[-1]["role"] == "user"
    assert "[message_id=message_3]" in sent_messages[-1]["content"]
    assert "第一个" in sent_messages[-1]["content"]


def test_configured_gateway_rejects_non_json_without_repair_or_retry(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    transport = FakeTransport({"choices": [{"message": {"content": "```json\n{}\n```"}}]})
    gateway = ConfiguredCreativeAgentGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_AGENT_KEY": "secret"}, {"TEST_AGENT_KEY"}),
    )

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "AGENT_MODEL_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1


def test_configured_gateway_requires_explicit_execution_authorization(monkeypatch) -> None:
    monkeypatch.delenv("V2_AGENT_MODEL_EXECUTION_ENABLED", raising=False)
    transport = FakeTransport({})
    gateway = ConfiguredCreativeAgentGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_AGENT_KEY": "secret"}, {"TEST_AGENT_KEY"}),
    )

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "AGENT_MODEL_EXECUTION_DISABLED"
    assert transport.calls == []
