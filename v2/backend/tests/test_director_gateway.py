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
from v2.backend.app.planning.director_gateway import ConfiguredDirectorGateway, DirectorSelection
from v2.backend.app.providers.credentials import EnvironmentCredentialResolver


class FakeTransport:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response


def selection() -> DirectorSelection:
    return DirectorSelection(
        production_config_version_id="production_config_1",
        model_config_version_id="model_config_director_1",
        provider_config_version_id="provider_config_1",
        model_provider="DeepSeek",
        model_name="Director",
        provider_model_id="configured-model",
        base_url="https://api.example.test/v1",
        credential_ref="env://TEST_AGENT_KEY",
        timeout_seconds=30,
        input_contract_version="director-input.v1",
        prompt_contract_version="director-prompt.v1",
        output_schema_version="shot-plan.v2",
        max_output_tokens=4000,
        sampling={"temperature": 0.2},
    )


def manifest(*, audio_policy: str = "off") -> dict:
    return {
        "contract_version": "director-input.v1",
        "project_id": "project_1",
        "requirement_version": {"id": "requirement_1", "fields": {"audio_mode": audio_policy}},
        "accepted_creative_brief": {"id": "brief_1", "brief": {
            "content_promise": "展示一次完整训练",
            "narrative_beats": [
                {"beat_code": "BEAT_01", "purpose": "开始", "summary": "准备", "target_duration_ms": 5000},
                {"beat_code": "BEAT_02", "purpose": "过程", "summary": "训练", "target_duration_ms": 5000},
            ],
        }},
        "confirmed_decisions": [],
        "confirmed_entity_versions": [{
            "id": "entity_version_1",
            "entity_id": "entity_1",
            "entity_type": "character",
            "display_name": "主角",
            "version_number": 1,
            "attributes": {},
            "source_attachment": {"id": "attachment_1", "mime_type": "image/png", "verified": True},
        }],
        "delivery_constraints": {"duration_ms": 10_000, "aspect_ratio": "9:16"},
        "audio_policy": audio_policy,
        "system_config_version": "production_config_1",
    }


def shot(code: str, sequence: int, beat: str, duration: int, *, continuity: str | None = None) -> dict:
    return {
        "shot_code": code,
        "sequence_number": sequence,
        "duration_ms": duration,
        "narrative_beat_code": beat,
        "continuity_group_id": continuity,
        "action_count": 1,
        "shot_type": "character_action",
        "scene_entity_version_id": None,
        "character_entity_version_ids": ["entity_version_1"],
        "outfit_entity_version_ids": [],
        "product_entity_version_ids": [],
        "primary_reference_entity_version_id": "entity_version_1",
        "face_visibility": "required",
        "text_policy": "forbidden",
        "motion_requirement": "significant",
        "audio_requirement": "off",
        "composition": "中景",
        "action": "向前跑",
        "visual_prompt": "主角在跑道向前跑",
        "negative_prompt": None,
    }


def valid_output() -> dict:
    return {"shots": [shot("SH-001", 1, "BEAT_01", 5000), shot("SH-002", 2, "BEAT_02", 5000)]}


def gateway_for(output: object) -> tuple[ConfiguredDirectorGateway, FakeTransport]:
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    transport = FakeTransport({"id": "director-request-1", "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 55}})
    gateway = ConfiguredDirectorGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_AGENT_KEY": "secret"}, {"TEST_AGENT_KEY"}),
    )
    return gateway, transport


def test_director_returns_strict_shot_plan_once(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for(valid_output())

    result = gateway.invoke(selection(), manifest())

    assert [item.shot_code for item in result.output.shots] == ["SH-001", "SH-002"]
    assert result.provider_request_id == "director-request-1"
    assert len(transport.calls) == 1
    sent = transport.calls[0]["payload"]["messages"]
    assert [item["role"] for item in sent] == ["system", "user"]
    assert "NodeInfoList" in sent[0]["content"]
    assert '"face_visibility":"required|optional|not_visible"' in sent[0]["content"]


@pytest.mark.parametrize("mutate", [
    lambda value: value["shots"][0].update({"action_count": 2}),
    lambda value: value["shots"][0].update({"narrative_beat_code": "BEAT_99"}),
    lambda value: value["shots"][0].update({"duration_ms": 4000}),
    lambda value: value["shots"][0].update({"audio_requirement": "configured"}),
    lambda value: value["shots"][0].update({"character_entity_version_ids": ["entity_version_unknown"], "primary_reference_entity_version_id": None}),
])
def test_director_rejects_invalid_contract_without_retry(monkeypatch, mutate) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    mutate(output)
    gateway, transport = gateway_for(output)

    with pytest.raises(AgentGatewayError):
        gateway.invoke(selection(), manifest())

    assert len(transport.calls) == 1


def test_director_rejects_non_json_without_repair(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for("```json\n{}\n```")

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "DIRECTOR_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1
