from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
TEST_RUNTIME = Path(__file__).resolve().parent / "test_runtime"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["V2_RUNTIME_ROOT"] = str(TEST_RUNTIME)

from v2.backend.app.main import app
from v2.backend.app.db.session import engine
from v2.backend.app.db.session import SessionLocal
from v2.backend.app.db.models import Project, RequirementVersion
from v2.backend.app.creation.completeness import evaluate_requirement
from sqlalchemy import select
from v2.backend.app.workers.worker import process_one


@pytest.fixture()
def client():
    TEST_DATABASE.unlink(missing_ok=True)
    if TEST_RUNTIME.exists():
        import shutil
        shutil.rmtree(TEST_RUNTIME)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    TEST_DATABASE.unlink(missing_ok=True)
    if TEST_RUNTIME.exists():
        import shutil
        shutil.rmtree(TEST_RUNTIME)


def test_project_contract_and_explicit_confirmation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/projects",
        json={
            "title": "Contract test",
            "core_topic": "A structured V2 test",
            "duration_seconds": 15,
            "aspect_ratio": "9:16",
            "audio_mode": "off",
        },
    )
    assert response.status_code == 201
    project = response.json()
    assert project["status"] == "draft"

    decision = client.post(
        f"/api/v1/projects/{project['id']}/decisions",
        json={"key": "visual_style", "label": "Visual style", "status": "pending"},
    )
    assert decision.status_code == 201
    decision_id = decision.json()["id"]

    blocked = client.post(f"/api/v1/projects/{project['id']}/confirm")
    assert blocked.status_code == 409

    resolved = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{decision_id}/resolve",
        json={"value": "documentary"},
    )
    assert resolved.status_code == 200

    confirmed = client.post(f"/api/v1/projects/{project['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    queued = client.post(
        f"/api/v1/projects/{project['id']}/queue",
        json={"kind": "contract_validation"},
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"

    assert process_one() is True
    processed = client.get(f"/api/v1/projects/{project['id']}")
    assert processed.status_code == 200
    assert processed.json()["status"] == "review_required"
    assert processed.json()["work_items"][0]["status"] == "completed"


def create_creation_project(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={
            "title": "Creation center test",
            "core_topic": "30 秒竖屏健身广告",
            "duration_seconds": 30,
            "aspect_ratio": "9:16",
            "audio_mode": "off",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_candidate_is_audited_and_requires_explicit_acceptance(client: TestClient) -> None:
    project = create_creation_project(client)
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = view["active_requirement"]["id"]
    assert view["active_requirement"]["version_number"] == 1
    assert view["next_action"]["code"] == "ADD_REQUIREMENT_MESSAGE"

    message_command = {
        "command_id": "message-command-001",
        "actor_id": "test-user",
        "content": "采用纪实训练风格，主角保持同一人。",
    }
    first_message = client.post(f"/api/v1/projects/{project['id']}/messages", json=message_command)
    replayed_message = client.post(f"/api/v1/projects/{project['id']}/messages", json=message_command)
    assert first_message.status_code == 201
    assert replayed_message.json()["id"] == first_message.json()["id"]

    generate_command = {
        "command_id": "generate-command-001",
        "actor_id": "test-user",
        "expected_base_version_id": base_id,
    }
    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json=generate_command,
    )
    assert generated.status_code == 201
    candidate = generated.json()
    assert candidate["status"] == "awaiting_review"
    assert candidate["fields"]["creative_direction"] == message_command["content"]

    before_accept = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert before_accept["active_requirement"]["version_number"] == 1
    assert before_accept["latest_agent_run"]["model_provider"] == "mock"

    accept_command = {
        "command_id": "accept-command-001",
        "actor_id": "test-user",
        "expected_base_version_id": base_id,
    }
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates/{candidate['id']}:accept",
        json=accept_command,
    )
    replayed_accept = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates/{candidate['id']}:accept",
        json=accept_command,
    )
    assert accepted.status_code == 200
    assert accepted.json()["version_number"] == 2
    assert replayed_accept.json()["id"] == accepted.json()["id"]
    ready = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert ready["next_action"]["code"] == "REQUIREMENT_READY_FOR_PLANNING"

    no_new_input = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={
            "command_id": "generate-command-002",
            "expected_base_version_id": accepted.json()["id"],
        },
    )
    assert no_new_input.status_code == 409
    assert no_new_input.headers["x-error-code"] == "NO_NEW_REQUIREMENT_INPUT"


def test_new_message_makes_pending_candidate_stale(client: TestClient) -> None:
    project = create_creation_project(client)
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = view["active_requirement"]["id"]
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "message-command-101", "content": "第一版方向",
    })
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "generate-command-101", "expected_base_version_id": base_id},
    ).json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "message-command-102", "content": "新的明确方向",
    })
    stale_accept = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates/{candidate['id']}:accept",
        json={"command_id": "accept-command-101", "expected_base_version_id": base_id},
    )
    assert stale_accept.status_code == 409
    assert stale_accept.headers["x-error-code"] == "CANDIDATE_NOT_REVIEWABLE"
    current = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert current["candidate_history"][0]["status"] == "stale"


def test_attachment_registration_does_not_create_binding(client: TestClient) -> None:
    project = create_creation_project(client)
    png = b"\x89PNG\r\n\x1a\n" + b"test-payload"
    registered = client.post(
        f"/api/v1/projects/{project['id']}/attachments",
        data={"command_id": "attachment-command-001", "actor_id": "test-user"},
        files={"file": ("athlete.png", png, "image/png")},
    )
    assert registered.status_code == 201
    attachment = registered.json()
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert view["attachments"][0]["bindings"] == []
    assert view["next_action"]["code"] == "CLASSIFY_ATTACHMENT"

    missing_entity = client.post(
        f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/bindings",
        json={"command_id": "binding-command-001", "binding_type": "identity_reference"},
    )
    assert missing_entity.status_code == 409
    assert missing_entity.headers["x-error-code"] == "ENTITY_ID_REQUIRED"

    bound = client.post(
        f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/bindings",
        json={
            "command_id": "binding-command-002",
            "binding_type": "identity_reference",
            "entity_id": "char_main",
        },
    )
    assert bound.status_code == 201
    assert bound.json()["confirmed_by"] == "local-user"
    assert bound.json()["entity_version_id"].startswith("entity_version_")
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["entity_versions"] == [{
        "id": bound.json()["entity_version_id"],
        "entity_id": "char_main",
        "entity_type": "character",
        "display_name": "char_main",
        "version_number": 1,
    }]


def test_attachment_content_type_mismatch_is_blocked(client: TestClient) -> None:
    project = create_creation_project(client)
    response = client.post(
        f"/api/v1/projects/{project['id']}/attachments",
        data={"command_id": "attachment-command-101"},
        files={"file": ("not-a-png.png", b"plain text", "image/png")},
    )
    assert response.status_code == 409
    assert response.headers["x-error-code"] == "ATTACHMENT_TYPE_MISMATCH"


def test_completeness_evaluator_does_not_block_optional_fields() -> None:
    fields = {
        "core_topic": "明确主题",
        "duration_seconds": 30,
        "aspect_ratio": "9:16",
        "audio_mode": "off",
    }
    sources = {key: {"type": "user"} for key in fields}
    assert evaluate_requirement(fields, sources) == []
    fields.pop("audio_mode")
    missing = evaluate_requirement(fields, sources)
    assert [item["field_key"] for item in missing] == ["audio_mode"]
    assert missing[0]["risk_level"] == "high"


def test_clarification_resolution_creates_new_requirement_version(client: TestClient) -> None:
    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    with SessionLocal() as session:
        active = session.scalar(select(RequirementVersion).where(RequirementVersion.id == base_id))
        fields = dict(active.fields)
        sources = dict(active.field_sources)
        fields.pop("audio_mode")
        sources.pop("audio_mode")
        active.fields = fields
        active.field_sources = sources
        session.commit()

    blocked = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert blocked["next_action"]["code"] == "RESOLVE_REQUIRED_CLARIFICATIONS"
    assert len(blocked["pending_clarifications"]) == 1
    clarification = blocked["pending_clarifications"][0]
    assert clarification["field_key"] == "audio_mode"
    assert [item["value"] for item in clarification["options"]] == ["off", "voiceover"]

    invalid = client.post(
        f"/api/v1/projects/{project['id']}/clarifications/{clarification['id']}:resolve",
        json={
            "command_id": "clarification-command-001",
            "expected_base_version_id": base_id,
            "value": "auto",
        },
    )
    assert invalid.status_code == 409
    assert invalid.headers["x-error-code"] == "VALUE_NOT_ALLOWED"

    command = {
        "command_id": "clarification-command-002",
        "actor_id": "test-user",
        "expected_base_version_id": base_id,
        "value": "off",
    }
    resolved = client.post(
        f"/api/v1/projects/{project['id']}/clarifications/{clarification['id']}:resolve",
        json=command,
    )
    replay = client.post(
        f"/api/v1/projects/{project['id']}/clarifications/{clarification['id']}:resolve",
        json=command,
    )
    assert resolved.status_code == 200
    assert resolved.json()["version_number"] == 2
    assert resolved.json()["fields"]["audio_mode"] == "off"
    assert resolved.json()["field_sources"]["audio_mode"]["type"] == "user_confirmation"
    assert replay.json()["id"] == resolved.json()["id"]


def test_planning_candidates_require_explicit_acceptance(client: TestClient) -> None:
    project = create_creation_project(client)
    creation = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    requirement_id = creation["active_requirement"]["id"]
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["next_action"]["code"] == "GENERATE_CREATIVE_BRIEF"

    brief_command = {
        "command_id": "brief-generate-command-001",
        "expected_requirement_version_id": requirement_id,
    }
    generated_brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json=brief_command,
    )
    replayed_brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json=brief_command,
    )
    assert generated_brief.status_code == 201
    brief = generated_brief.json()
    assert replayed_brief.json()["id"] == brief["id"]
    assert brief["status"] == "awaiting_review"
    assert brief["brief"]["visual_style"] is None
    assert brief["brief"]["assumptions"] == []
    before_accept = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert before_accept["active_plan"] is None
    assert before_accept["next_action"]["code"] == "REVIEW_CREATIVE_BRIEF"

    accepted_brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={
            "command_id": "brief-accept-command-001",
            "expected_requirement_version_id": requirement_id,
        },
    )
    assert accepted_brief.status_code == 200
    assert accepted_brief.json()["status"] == "accepted"
    after_brief = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert after_brief["next_action"]["code"] == "GENERATE_SHOT_PLAN"

    generated_shots = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "shots-generate-command-001",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    )
    assert generated_shots.status_code == 201
    shot_candidate = generated_shots.json()
    assert shot_candidate["status"] == "awaiting_review"
    assert len(shot_candidate["shots"]) == 3
    assert sum(item["duration_ms"] for item in shot_candidate["shots"]) == 30_000
    assert all(item["scene_entity_version_id"] is None for item in shot_candidate["shots"])
    assert all(item["character_entity_version_ids"] == [] for item in shot_candidate["shots"])
    assert "provider" not in str(shot_candidate["shots"]).lower()
    before_plan = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert before_plan["active_plan"] is None
    assert before_plan["next_action"]["code"] == "REVIEW_SHOT_PLAN"

    accept_command = {
        "command_id": "shots-accept-command-001",
        "actor_id": "test-user",
        "expected_requirement_version_id": requirement_id,
    }
    accepted_plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shot_candidate['id']}:accept",
        json=accept_command,
    )
    replayed_plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shot_candidate['id']}:accept",
        json=accept_command,
    )
    assert accepted_plan.status_code == 200
    plan = accepted_plan.json()
    assert replayed_plan.json()["id"] == plan["id"]
    assert plan["version_number"] == 1
    assert plan["confirmed_by"] == "test-user"
    assert len(plan["shots"]) == 3
    final = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert final["next_action"]["code"] == "PLAN_CONFIRMED"
    assert final["active_plan"]["id"] == plan["id"]


def valid_system_configuration() -> dict:
    return {
        "config_key": "studio_primary",
        "display_name": "主生产配置",
        "description": "显式测试配置，不连接供应商",
        "providers": [{
            "provider_key": "mock_visual",
            "display_name": "Mock 视觉供应商",
            "adapter_kind": "mock",
            "base_url": "https://provider.invalid/api",
            "capabilities": ["image_generation"],
            "request_timeout_seconds": 60,
            "poll_interval_seconds": 5,
            "max_concurrency": 1,
        }],
        "models": [],
        "workflow_slots": [{
            "slot_key": "keyframe_image",
            "display_name": "关键帧图片",
            "operation_kind": "image_generation",
            "provider_key": "mock_visual",
            "provider_workflow_id": "mock-workflow-not-executable",
            "input_schema_version": "keyframe-input.v1",
            "output_schema_version": "image-output.v1",
            "node_info_list": [{
                "node_id": "prompt",
                "field_path": "text",
                "value_source": "shot.action",
                "value_type": "string",
                "required": True,
            }],
            "supported_video_spec_keys": ["vertical_480p"],
        }],
        "video_specs": [{
            "spec_key": "vertical_480p",
            "display_name": "竖屏工作规格",
            "width": 480,
            "height": 848,
            "aspect_ratio": "9:16",
            "fps": 24,
            "duration_min_seconds": 1,
            "duration_max_seconds": 30,
            "frame_count_rule": {"type": "duration_times_fps"},
            "container": "mp4",
            "video_codec": "h264",
            "pixel_format": "yuv420p",
        }],
        "audio": {
            "config_key": "audio_off",
            "display_name": "关闭音频",
            "supported_modes": ["off"],
            "sample_rate": 48000,
            "channels": 2,
            "format": "wav",
            "speaking_rate_min": 0.8,
            "speaking_rate_max": 1.2,
        },
        "storage": {
            "policy_key": "local_runtime",
            "display_name": "本地运行目录",
            "backend_kind": "local",
            "allowed_mime_types": ["image/png", "video/mp4", "audio/wav"],
            "max_file_size_bytes": 524288000,
            "public_url_policy": "none",
            "local_root_ref": "v2.runtime.assets",
        },
    }


def create_confirmed_plan(client: TestClient) -> tuple[dict, dict]:
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "snapshot-brief-generate-001", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "snapshot-brief-accept-001", "expected_requirement_version_id": requirement_id},
    )
    shots = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={"command_id": "snapshot-shots-generate-001", "expected_requirement_version_id": requirement_id, "creative_brief_candidate_id": brief["id"]},
    ).json()
    plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shots['id']}:accept",
        json={"command_id": "snapshot-shots-accept-001", "expected_requirement_version_id": requirement_id},
    ).json()
    return project, plan


def publish_visual_production_configuration(client: TestClient) -> dict:
    configuration = valid_system_configuration()
    configuration["providers"][0]["capabilities"].append("video_generation")
    configuration["workflow_slots"].append({
        "slot_key": "first_frame_video",
        "display_name": "首帧视频",
        "operation_kind": "video_generation",
        "provider_key": "mock_visual",
        "provider_workflow_id": "mock-video-workflow-not-executable",
        "input_schema_version": "i2v-input.v1",
        "output_schema_version": "video-output.v1",
        "node_info_list": [{
            "node_id": "source",
            "field_path": "image",
            "value_source": "source_image",
            "value_type": "image",
            "required": True,
        }],
        "supported_video_spec_keys": ["vertical_480p"],
    })
    draft = client.post("/api/v1/system-config/versions", json={
        "command_id": "snapshot-config-create-001",
        "configuration": configuration,
    }).json()
    ready = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:validate",
        json={"command_id": "snapshot-config-validate-001", "expected_row_version": draft["row_version"]},
    ).json()
    response = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:publish",
        json={"command_id": "snapshot-config-publish-001", "expected_row_version": ready["row_version"], "confirm_high_risk_changes": True},
    )
    assert response.status_code == 200
    return response.json()


def test_production_impact_and_snapshot_compile_exact_dag_without_work_items(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    with SessionLocal() as session:
        stale_project = session.get(Project, project["id"])
        stale_project.audio_mode = "voiceover"
        session.commit()
    config = publish_visual_production_configuration(client)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    selection = {
        "plan_version_id": plan["id"],
        "production_config_version_id": config["id"],
        "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
        "keyframe_workflow_slot_version_id": components[("workflow_slot", "keyframe_image")]["id"],
        "video_workflow_slot_version_id": components[("workflow_slot", "first_frame_video")]["id"],
    }
    analysis_command = {"command_id": "production-impact-command-001", **selection}
    analyzed = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json=analysis_command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json=analysis_command,
    )
    assert analyzed.status_code == 201
    impact = analyzed.json()
    assert replayed.json()["id"] == impact["id"]
    assert impact["status"] == "awaiting_confirmation"
    assert impact["manifest"]["audio_mode"] == "off"
    assert impact["validation_errors"] == []
    assert impact["estimated_call_count"] == 6
    assert impact["cost_status"] == "not_configured"
    assert impact["execution_blockers"][0]["code"] == "COST_ESTIMATE_REQUIRED"
    video_nodes = [item for item in impact["manifest"]["dag"]["nodes"] if item["kind"] == "generate_i2v_clip"]
    assert len(video_nodes) == 3
    assert all(len(item["input_contract"]["source_image_node_keys"]) == 1 for item in video_nodes)

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "production-snapshot-command-001",
            "impact_analysis_id": impact["id"],
            "analysis_hash": impact["analysis_hash"],
            "confirm_contract_scope": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "CONTRACT_SCOPE_CONFIRMATION_REQUIRED"

    snapshot_command = {
        "command_id": "production-snapshot-command-002",
        "impact_analysis_id": impact["id"],
        "analysis_hash": impact["analysis_hash"],
        "confirm_contract_scope": True,
    }
    created = client.post(f"/api/v1/projects/{project['id']}/production-snapshots", json=snapshot_command)
    replayed_snapshot = client.post(f"/api/v1/projects/{project['id']}/production-snapshots", json=snapshot_command)
    assert created.status_code == 201
    snapshot = created.json()
    assert replayed_snapshot.json()["id"] == snapshot["id"]
    assert snapshot["status"] == "preparing"
    assert snapshot["locked_at"] is None
    assert len(snapshot["nodes"]) == 7
    assert len(snapshot["edges"]) == 6
    assert all(item["kind"] != "generate_tts" for item in snapshot["nodes"])
    assert client.get(f"/api/v1/projects/{project['id']}").json()["work_items"] == []
    references = client.get(f"/api/v1/system-config/versions/{config['id']}/references").json()
    assert references == [{"ref_type": "snapshot", "ref_id": snapshot["id"], "created_at": references[0]["created_at"]}]


def test_production_impact_blocks_wrong_explicit_workflow_kind(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    analyzed = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "production-impact-invalid-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "keyframe_workflow_slot_version_id": components[("workflow_slot", "keyframe_image")]["id"],
            "video_workflow_slot_version_id": components[("workflow_slot", "keyframe_image")]["id"],
        },
    )
    assert analyzed.status_code == 201
    impact = analyzed.json()
    assert impact["status"] == "blocked"
    assert any(item["code"] == "VIDEO_SLOT_KIND_INVALID" for item in impact["validation_errors"])
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "production-snapshot-invalid-001",
            "impact_analysis_id": impact["id"],
            "analysis_hash": impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "IMPACT_ANALYSIS_BLOCKED"


def test_system_configuration_publish_is_versioned_and_explicit(client: TestClient) -> None:
    create_command = {
        "command_id": "config-create-command-001",
        "actor_id": "test-admin",
        "configuration": valid_system_configuration(),
    }
    created = client.post("/api/v1/system-config/versions", json=create_command)
    replayed = client.post("/api/v1/system-config/versions", json=create_command)
    assert created.status_code == 201
    draft = created.json()
    assert replayed.json()["id"] == draft["id"]
    assert draft["status"] == "draft"
    assert len(draft["components"]) == 5
    assert all("secret" not in str(item).lower() for item in draft["components"])

    validated = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:validate",
        json={
            "command_id": "config-validate-command-001",
            "actor_id": "test-admin",
            "expected_row_version": draft["row_version"],
        },
    )
    assert validated.status_code == 200
    ready = validated.json()
    assert ready["status"] == "ready"
    assert ready["config_hash"]
    assert ready["validation_report"] == []

    unconfirmed = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:publish",
        json={
            "command_id": "config-publish-command-001",
            "actor_id": "test-admin",
            "expected_row_version": ready["row_version"],
            "confirm_high_risk_changes": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "HIGH_RISK_CONFIRMATION_REQUIRED"

    published = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:publish",
        json={
            "command_id": "config-publish-command-002",
            "actor_id": "test-admin",
            "expected_row_version": ready["row_version"],
            "confirm_high_risk_changes": True,
        },
    )
    assert published.status_code == 200
    authority = published.json()
    assert authority["status"] == "published"
    assert all(item["status"] == "published" for item in authority["components"])

    immutable = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:revise",
        json={
            "command_id": "config-revise-command-001",
            "expected_row_version": authority["row_version"],
            "configuration": valid_system_configuration(),
        },
    )
    assert immutable.status_code == 409
    assert immutable.headers["x-error-code"] == "CONFIGURATION_IMMUTABLE"

    cloned = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:clone-draft",
        json={"command_id": "config-clone-command-001", "display_name": "主生产配置 v2 草稿"},
    )
    assert cloned.status_code == 201
    clone = cloned.json()
    assert clone["status"] == "draft"
    assert clone["version_number"] == 2
    assert clone["supersedes_version_id"] == draft["id"]
    diff = client.get(
        f"/api/v1/system-config/versions/{clone['id']}/diff?base_version_id={draft['id']}"
    )
    assert diff.status_code == 200
    assert diff.json()["incurs_production_cost"] is False
    assert diff.json()["changed_components"] == []
    assert diff.json()["high_risk_changes"] == []

    reused_command = client.post(
        f"/api/v1/system-config/versions/{clone['id']}:validate",
        json={
            "command_id": "config-clone-command-001",
            "expected_row_version": clone["row_version"],
        },
    )
    assert reused_command.status_code == 409
    assert reused_command.headers["x-error-code"] == "COMMAND_ID_REUSED"


def test_system_configuration_validation_fails_without_route_substitution(client: TestClient) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0]["capabilities"] = ["video_generation"]
    configuration["workflow_slots"][0]["node_info_list"].append(
        dict(configuration["workflow_slots"][0]["node_info_list"][0])
    )
    response = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-create-invalid-001",
        "configuration": configuration,
    })
    assert response.status_code == 201
    created = response.json()
    validated = client.post(
        f"/api/v1/system-config/versions/{created['id']}:validate",
        json={
            "command_id": "config-validate-invalid-001",
            "expected_row_version": created["row_version"],
        },
    )
    assert validated.status_code == 200
    invalid = validated.json()
    assert invalid["status"] == "validation_failed"
    assert any(item["code"] == "PROVIDER_CAPABILITY_MISSING" for item in invalid["validation_report"])
    assert any(item["code"] == "NODE_BINDING_DUPLICATE" for item in invalid["validation_report"])
    assert any(item["key"] == "mock_visual" for item in invalid["components"])

    publish = client.post(
        f"/api/v1/system-config/versions/{created['id']}:publish",
        json={
            "command_id": "config-publish-invalid-001",
            "expected_row_version": invalid["row_version"],
            "confirm_high_risk_changes": True,
        },
    )
    assert publish.status_code == 409
    assert publish.headers["x-error-code"] == "CONFIGURATION_NOT_READY"


def test_system_configuration_contract_rejects_secret_fields(client: TestClient) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0]["api_key"] = "must-not-enter-database"
    response = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-secret-command-001",
        "configuration": configuration,
    })
    assert response.status_code == 422
    assert client.get("/api/v1/system-config/versions").json() == []

    configuration = valid_system_configuration()
    configuration["providers"][0]["base_url"] = "https://user:password@provider.invalid/api"
    embedded = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-secret-command-002",
        "configuration": configuration,
    })
    assert embedded.status_code == 422
