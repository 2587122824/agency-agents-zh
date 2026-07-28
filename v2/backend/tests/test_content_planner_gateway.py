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
        api_key="secret",
        timeout_seconds=30,
        input_contract_version="content-planner-input.v3",
        prompt_contract_version="content-planner-prompt.v7",
        output_schema_version="creative-brief-candidate.v4",
        max_output_tokens=2000,
        sampling={"temperature": 0.2, "unsupported": "ignored"},
    )


def manifest(*, audio_policy: str = "off", platform: str | None = None) -> dict:
    return {
        "contract_version": "content-planner-input.v3",
        "project_id": "project_1",
        "production_profile": {
            "video_motion_strategy": "adaptive",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
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
            ({"segment_code": "SEG_01", "beat_code": "BEAT_01", "kind": "visual_only"} if spoken_text is None else {"segment_code": "SEG_01", "beat_code": "BEAT_01", "kind": "voiceover", "spoken_text": spoken_text}),
            {"segment_code": "SEG_02", "beat_code": "BEAT_02", "kind": "visual_only"},
            {"segment_code": "SEG_03", "beat_code": "BEAT_03", "kind": "visual_only"},
        ],
        "tone": "克制、真实",
        "pacing": "前快后稳",
        "platform_adaptation": platform_adaptation,
        "entity_version_ids": ["entity_version_1"],
        "constraints_carried_forward": ["audio_policy=off"],
        "creative_additions": [{
            "addition_code": "ADDITION_01",
            "category": "narrative_structure",
            "content": "按准备、训练和结果组织完整过程。",
            "purpose": "让训练主题形成清晰推进。",
            "basis_refs": [{"type": "requirement_field", "reference_id": "core_topic"}],
        }],
        "facts_requiring_confirmation": [],
        "open_questions": [],
    }


def gateway_for(response: dict) -> tuple[ConfiguredContentPlannerGateway, FakeTransport]:
    transport = FakeTransport(response)
    gateway = ConfiguredContentPlannerGateway(transport=transport)
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
    assert "绝不能写 requirement ID、完整 JSON 路径" in sent[0]["content"]
    assert "权威 JSON Schema" in sent[0]["content"]
    assert '"discriminator":{"mapping"' in sent[0]["content"]
    assert "需要同时表达纯画面与画面文字时，必须拆成两个连续脚本段" in sent[0]["content"]
    assert "任何脚本段都不得出现 spoken_text 字段" in sent[0]["content"]


def test_content_planner_accepts_structured_open_question_options(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["open_questions"] = [{
        "question_code": "QUESTION_01",
        "prompt": "是否需要展示具体研究数据？",
        "reason": "数据精度会影响脚本文案和画面信息密度。",
        "options": [
            {
                "option_code": "OPTION_01",
                "label": "展示核心数据",
                "description": "保留少量关键数字并标明来源。",
                "answer": "展示少量核心研究数据并标明来源。",
            },
            {
                "option_code": "OPTION_02",
                "label": "保持通俗",
                "description": "不展示具体数字，只解释结论。",
                "answer": "不展示具体研究数字，使用通俗结论表达。",
            },
        ],
    }]
    gateway, _ = gateway_for({
        "id": "planner-request-question-options",
        "choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}],
        "usage": {"total_tokens": 92},
    })

    result = gateway.invoke(selection(), manifest())

    question = result.output.open_questions[0]
    assert question.question_code == "QUESTION_01"
    assert [item.option_code for item in question.options] == ["OPTION_01", "OPTION_02"]


def test_content_planner_accepts_creative_addition_with_real_manifest_basis(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    result = gateway.invoke(selection(), manifest())

    assert result.output.creative_additions[0].basis_refs[0].reference_id == "core_topic"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("reference_type", "reference_id"),
    [
        ("requirement_field", "unknown_field"),
        ("decision", "decision_unknown"),
        ("entity_version", "entity_version_unknown"),
    ],
)
def test_content_planner_rejects_creative_addition_with_unknown_basis_without_retry(
    monkeypatch, reference_type: str, reference_id: str,
) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["creative_additions"][0]["basis_refs"] = [{"type": reference_type, "reference_id": reference_id}]
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1


def test_content_planner_rejects_pending_fact_without_matching_question(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["facts_requiring_confirmation"] = [{
        "fact_code": "FACT_01",
        "statement": "训练后体重下降五公斤",
        "reason": "输入合同没有提供结果数据。",
        "resolution_question_code": "QUESTION_01",
    }]
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1


def test_content_planner_passes_frozen_revision_request_once(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for({
        "id": "planner-revision-request-1",
        "choices": [{"message": {"content": json.dumps(valid_output(), ensure_ascii=False)}}],
    })
    payload = manifest()
    payload["revision_request"] = {
        "source_candidate_id": "brief_candidate_1",
        "source_revision_number": 1,
        "source_brief": valid_output(),
        "instruction": "缩短开场，突出最终结果",
    }

    gateway.invoke(selection(), payload)

    assert len(transport.calls) == 1
    sent_payload = transport.calls[0]["payload"]["messages"][1]["content"]
    assert '"source_candidate_id":"brief_candidate_1"' in sent_payload
    assert '"instruction":"缩短开场，突出最终结果"' in sent_payload


def test_content_planner_rejects_audio_when_audio_policy_is_off_without_retry(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(valid_output(spoken_text="开始训练"), ensure_ascii=False)}}]})

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest(audio_policy="off"))

    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1


def test_content_planner_accepts_empty_constraint_summary_when_output_obeys_manifest(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["constraints_carried_forward"] = []
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    result = gateway.invoke(selection(), manifest(audio_policy="off"))

    assert result.output.constraints_carried_forward == []
    assert all(item.kind not in {"voiceover", "dialogue"} for item in result.output.script_segments)
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

    missing_adaptation = valid_output(platform_adaptation=None)
    gateway, _ = gateway_for({"choices": [{"message": {"content": json.dumps(missing_adaptation, ensure_ascii=False)}}]})
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest(platform="抖音"))
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

    gateway, transport = gateway_for({
        "id": "planner-invalid-json",
        "choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}],
        "usage": {"total_tokens": 21},
    })
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())
    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_JSON_INVALID"
    assert raised.value.raw_output == {"content": "```json\n{}\n```", "finish_reason": "stop"}
    assert raised.value.provider_request_id == "planner-invalid-json"
    assert raised.value.token_usage == {"total_tokens": 21}
    assert raised.value.diagnostics[0]["type"] == "json_decode_error"
    assert len(transport.calls) == 1


def test_content_planner_script_segment_kinds_are_mutually_exclusive(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["script_segments"][0]["on_screen_text"] = "这段文字不能混入纯画面对象"
    gateway, transport = gateway_for({"choices": [{"message": {"content": json.dumps(output, ensure_ascii=False)}}]})

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID"
    assert raised.value.raw_output == output
    assert len(transport.calls) == 1


def test_content_planner_preserves_missing_content_response_evidence(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    response = {
        "id": "planner-empty-response",
        "choices": [{"finish_reason": "length", "message": {"content": ""}}],
        "usage": {"total_tokens": 8192},
    }
    gateway, transport = gateway_for(response)

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "CONTENT_PLANNER_RESPONSE_CONTENT_MISSING"
    assert raised.value.raw_output == {"provider_response": response}
    assert raised.value.provider_request_id == "planner-empty-response"
    assert raised.value.token_usage == {"total_tokens": 8192}
    assert len(transport.calls) == 1
