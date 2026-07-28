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
        api_key="secret",
        timeout_seconds=30,
        input_contract_version="director-input.v3",
        prompt_contract_version="director-prompt.v7",
        output_schema_version="shot-plan.v4",
        max_output_tokens=4000,
        sampling={"temperature": 0.2},
    )


def manifest(*, audio_policy: str = "off") -> dict:
    return {
        "contract_version": "director-input.v3",
        "project_id": "project_1",
        "production_profile": {
            "video_motion_strategy": "adaptive",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
        "requirement_version": {"id": "requirement_1", "fields": {"audio_mode": audio_policy}},
        "accepted_creative_brief": {"id": "brief_1", "brief": {
            "content_promise": "展示一次完整训练",
            "narrative_beats": [
                {"beat_code": "BEAT_01", "purpose": "开始", "summary": "准备", "target_duration_ms": 5000},
                {"beat_code": "BEAT_02", "purpose": "过程", "summary": "训练", "target_duration_ms": 5000},
            ],
            "script_segments": [
                {"segment_code": "SEG_01", "beat_code": "BEAT_01", "kind": "visual_only", "spoken_text": None, "on_screen_text": None},
                {"segment_code": "SEG_02", "beat_code": "BEAT_02", "kind": "visual_only", "spoken_text": None, "on_screen_text": None},
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
        "brief_segment_codes": ["SEG_01" if beat == "BEAT_01" else "SEG_02"],
        "continuity_group_id": continuity,
        "continuity_relation": "same_moment",
        "action_count": 1,
        "shot_purpose": "develop",
        "framing": "medium",
        "camera_angle": "eye_level",
        "camera_motion": "tracking",
        "subject_motion": "significant",
        "scene_entity_version_id": None,
        "character_entity_version_ids": ["entity_version_1"],
        "outfit_entity_version_ids": [],
        "product_entity_version_ids": [],
        "primary_reference_entity_version_id": "entity_version_1",
        "face_visibility": "required",
        "face_subject_entity_version_ids": ["entity_version_1"],
        "text_policy": "forbidden",
        "required_on_screen_text": None,
        "audio_requirement": "off",
        "composition": "中景",
        "action": "向前跑",
        "visual_prompt": "主角在跑道向前跑",
        "guide_frame_prompts": None,
        "negative_prompt": None,
        "new_information": f"展示 {beat} 的新训练信息",
        "generation_requirements": {
            "reference_image_required": True,
            "multi_frame_required": False,
            "identity_consistency_required": True,
            "precise_text_required": False,
        },
    }


def valid_output() -> dict:
    return {"shots": [shot("SH-001", 1, "BEAT_01", 5000), shot("SH-002", 2, "BEAT_02", 5000)]}


def gateway_for(output: object) -> tuple[ConfiguredDirectorGateway, FakeTransport]:
    content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    transport = FakeTransport({"id": "director-request-1", "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 55}})
    gateway = ConfiguredDirectorGateway(transport=transport)
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
    assert '"brief_segment_codes":["SEG_01"]' in sent[0]["content"]
    assert "每个脚本段必须至少被一个镜头覆盖" in sent[0]["content"]
    assert "其他镜头必须与 source_shots 结构完全一致" in sent[0]["content"]


def test_director_prompt_freezes_every_enum_and_continuity_id_format(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    gateway, transport = gateway_for(valid_output())

    gateway.invoke(selection(), manifest())

    prompt = transport.calls[0]["payload"]["messages"][0]["content"]
    expected_contracts = (
        "^CONT-[0-9]{3}$",
        "same_moment | time_jump | location_change | outfit_change",
        "establish | develop | demonstrate | contrast | transition | resolve",
        "extreme_close_up | close_up | medium | full | wide",
        "eye_level | high | low | top_down | over_shoulder",
        "locked | pan | tilt | dolly | tracking | handheld",
        "none | subtle | moderate | significant",
        "required | optional | not_visible",
        "forbidden | allowed | required",
        "off | lip_motion_only | configured",
    )
    assert all(value in prompt for value in expected_contracts)
    assert "禁止 CG_01、group_01" in prompt
    assert "普通标题、字幕和教学标注由后期文字轨完成" in prompt
    assert "precise_text_required=true" in prompt
    assert prompt.count("你是片场 V2 的分镜导演智能体") == 1


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


def test_director_requires_all_three_guide_prompts_for_multi_frame_shot(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["shots"][0]["generation_requirements"]["multi_frame_required"] = True
    output["shots"][0]["guide_frame_prompts"] = {
        "start": "起跑前准备",
        "middle": "加速途中",
    }
    gateway, transport = gateway_for(output)

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "DIRECTOR_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1


def test_director_rejects_guide_prompts_for_single_frame_shot(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    output["shots"][0]["guide_frame_prompts"] = {
        "start": "起跑前准备",
        "middle": "加速途中",
        "end": "冲过终点",
    }
    gateway, transport = gateway_for(output)

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest())

    assert raised.value.code == "DIRECTOR_OUTPUT_SCHEMA_INVALID"
    assert len(transport.calls) == 1


def test_director_enforces_project_level_three_frame_profile(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    strict_manifest = manifest()
    strict_manifest["production_profile"]["video_motion_strategy"] = "three_frame"
    gateway, transport = gateway_for(valid_output())

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), strict_manifest)

    assert raised.value.code == "DIRECTOR_OUTPUT_CONTRACT_INVALID"
    assert "首中尾三帧生产模式" in str(raised.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("mutate", [
    lambda value: value["shots"][0].update({"brief_segment_codes": ["SEG_99"]}),
    lambda value: value["shots"][0].update({"brief_segment_codes": ["SEG_02"]}),
    lambda value: value["shots"][0].update({"face_subject_entity_version_ids": []}),
    lambda value: value["shots"][0].update({"text_policy": "required", "required_on_screen_text": None}),
    lambda value: value["shots"][0].update({"generation_requirements": {"reference_image_required": False, "multi_frame_required": False, "identity_consistency_required": True, "precise_text_required": False}}),
])
def test_director_rejects_v3_semantic_contract_violations(monkeypatch, mutate) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    output = valid_output()
    mutate(output)
    gateway, transport = gateway_for(output)
    with pytest.raises(AgentGatewayError):
        gateway.invoke(selection(), manifest())
    assert len(transport.calls) == 1


def test_director_revision_cannot_change_unselected_shot(monkeypatch) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    source = valid_output()["shots"]
    output = {"shots": json.loads(json.dumps(source, ensure_ascii=False))}
    output["shots"][1]["action"] = "未授权变更"
    revision_manifest = manifest()
    revision_manifest["revision_request"] = {
        "source_candidate_id": "candidate_1",
        "source_shots": source,
        "selected_shot_codes": ["SH-001"],
        "revision_instruction": "只调整第一个镜头",
    }
    gateway, transport = gateway_for(output)
    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), revision_manifest)
    assert raised.value.code == "DIRECTOR_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1
