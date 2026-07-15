from __future__ import annotations

import os
import hashlib
import json
import struct
import zlib
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
from v2.backend.app.db.models import Asset, AssetReviewDecision, CostEvent, DAGNode, Project, QCFinding, QCReport, RequirementVersion, Shot, Timeline, TimelineItem, WorkAttempt, WorkItem
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


def publish_visual_production_configuration(client: TestClient, with_pricing: bool = False, adapter_kind: str = "mock") -> dict:
    configuration = valid_system_configuration()
    configuration["providers"][0]["adapter_kind"] = adapter_kind
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
    if with_pricing:
        configuration["pricing"] = {
            "catalog_key": "visual_pricing_cny",
            "display_name": "视觉生产价格",
            "currency": "CNY",
            "confirmation_threshold": 0.5,
            "rules": [
                {"workflow_slot_key": "keyframe_image", "unit": "call", "unit_price": 0.1},
                {"workflow_slot_key": "first_frame_video", "unit": "output_second", "unit_price": 0.02},
            ],
        }
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


def create_locked_snapshot(client: TestClient, adapter_kind: str = "mock") -> tuple[dict, dict]:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client, with_pricing=True, adapter_kind=adapter_kind)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    impact = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "execution-impact-command-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "keyframe_workflow_slot_version_id": components[("workflow_slot", "keyframe_image")]["id"],
            "video_workflow_slot_version_id": components[("workflow_slot", "first_frame_video")]["id"],
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    ).json()
    snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "execution-snapshot-command-001",
            "impact_analysis_id": impact["id"],
            "analysis_hash": impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    ).json()
    locked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json={
            "command_id": "execution-lock-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "confirm_high_risk_cost": True,
        },
    ).json()
    return project, locked


def solid_png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x20\x80\xc0" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def attach_local_provider_output(project: dict, snapshot: dict, width: int, height: int, node_index: int = 0) -> tuple[dict, dict, str]:
    with SessionLocal() as session:
        items = list(session.scalars(select(WorkItem).where(
            WorkItem.snapshot_id == snapshot["id"],
            WorkItem.kind == "generate_keyframe",
        ).order_by(WorkItem.created_at, WorkItem.id)))
        item = items[node_index]
        attempt = session.get(WorkAttempt, item.current_attempt_id)
        content = solid_png(width, height)
        relative = f"quality/{item.id}.png"
        path = TEST_RUNTIME / "assets" / "quality" / f"{item.id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        output = {
            "uri": f"runtime://assets/{relative}",
            "storage_backend": "local",
            "asset_type": "image",
            "role": "keyframe",
            "mime_type": "image/png",
            "content_hash": hashlib.sha256(content).hexdigest(),
        }
        response_manifest = {"schema_version": "provider-response.v1", "media_created": True, "outputs": [output]}
        attempt.state = "completed"
        attempt.response_manifest = response_manifest
        attempt.finished_at = attempt.created_at
        item.status = "completed"
        item.finished_at = item.created_at
        session.commit()
        return {"id": item.id, "attempt_id": attempt.id}, response_manifest, relative


def seed_editor_assets(client: TestClient, project: dict, snapshot: dict) -> list[dict]:
    with SessionLocal() as session:
        is_active = session.get(Project, project["id"]).active_snapshot_id == snapshot["id"]
    if not is_active:
        activated = client.post(
            f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
            json={"command_id": "editor-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
        )
        assert activated.status_code == 200
    with SessionLocal() as session:
        nodes = list(session.scalars(select(DAGNode).where(
            DAGNode.snapshot_id == snapshot["id"],
        ).order_by(DAGNode.node_key)))
        video_assets = []
        for index, node in enumerate(nodes):
            media_type = node.output_contract.get("media_type")
            if media_type not in {"image", "video", "audio"}:
                continue
            shot = session.get(Shot, node.shot_id) if node.shot_id else None
            asset = Asset(
                project_id=project["id"],
                snapshot_id=snapshot["id"],
                work_attempt_id=None,
                dag_node_id=node.id,
                output_index=index,
                asset_type=media_type,
                role=node.output_contract.get("role", media_type),
                uri=f"runtime://assets/editor/{node.id}.{media_type}",
                storage_backend="local",
                provider_output_manifest={"seeded_for_test": True},
                content_hash=hashlib.sha256(node.id.encode()).hexdigest(),
                mime_type={"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}[media_type],
                byte_size=100,
                width=480 if media_type in {"image", "video"} else None,
                height=848 if media_type in {"image", "video"} else None,
                duration_ms=shot.duration_ms if media_type == "video" and shot else None,
                state="approved",
                verified_at=None,
                approved_at=None,
            )
            session.add(asset)
            session.flush()
            if media_type == "video":
                video_assets.append({
                    "id": asset.id,
                    "node_key": node.node_key,
                    "duration_ms": asset.duration_ms,
                })
        stale_project = session.get(Project, project["id"])
        stale_project.status = "quality_review"
        session.commit()
        return sorted(video_assets, key=lambda row: row["node_key"])


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


def test_priced_snapshot_requires_exact_high_risk_cost_confirmation(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client, with_pricing=True)
    cloned_config = client.post(
        f"/api/v1/system-config/versions/{config['id']}:clone-draft",
        json={"command_id": "priced-config-clone-001", "display_name": "视觉生产价格副本"},
    ).json()
    assert any(item["component_type"] == "pricing_catalog" for item in cloned_config["components"])
    cloned_diff = client.get(
        f"/api/v1/system-config/versions/{cloned_config['id']}/diff?base_version_id={config['id']}"
    ).json()
    assert cloned_diff["changed_components"] == []
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    impact = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "priced-impact-command-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "keyframe_workflow_slot_version_id": components[("workflow_slot", "keyframe_image")]["id"],
            "video_workflow_slot_version_id": components[("workflow_slot", "first_frame_video")]["id"],
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    ).json()
    assert impact["status"] == "awaiting_confirmation"
    assert impact["execution_blockers"] == []
    assert impact["cost_status"] == "estimated"
    assert impact["estimated_cost"] == 0.9
    assert impact["currency"] == "CNY"

    snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "priced-snapshot-command-001",
            "impact_analysis_id": impact["id"],
            "analysis_hash": impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    ).json()
    assert snapshot["status"] == "preparing"
    assert snapshot["cost_status"] == "estimated"
    assert sum(item["estimated_cost"] or 0 for item in snapshot["nodes"]) == pytest.approx(0.9)

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json={
            "command_id": "snapshot-lock-unconfirmed-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": 0.9,
            "expected_currency": "CNY",
            "confirm_high_risk_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "HIGH_RISK_COST_CONFIRMATION_REQUIRED"

    wrong_amount = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json={
            "command_id": "snapshot-lock-wrong-cost-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": 0.8,
            "expected_currency": "CNY",
            "confirm_high_risk_cost": True,
        },
    )
    assert wrong_amount.status_code == 409
    assert wrong_amount.headers["x-error-code"] == "SNAPSHOT_COST_MISMATCH"

    lock_command = {
        "command_id": "snapshot-lock-confirmed-001",
        "expected_contract_hash": snapshot["contract_hash"],
        "expected_estimated_cost": 0.9,
        "expected_currency": "CNY",
        "confirm_high_risk_cost": True,
    }
    locked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json=lock_command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json=lock_command,
    )
    assert locked.status_code == 200
    assert replayed.json()["id"] == snapshot["id"]
    assert locked.json()["status"] == "locked"
    assert locked.json()["cost_status"] == "confirmed"
    assert locked.json()["locked_at"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["work_items"] == []
    with SessionLocal() as session:
        cost_events = list(session.scalars(select(CostEvent).where(CostEvent.snapshot_id == snapshot["id"])))
        assert len(cost_events) == 6
        assert all(item.kind == "estimated" and item.status == "confirmed" for item in cost_events)
        assert sum(item.amount for item in cost_events) == pytest.approx(0.9)


def test_snapshot_activation_and_exact_submission_are_separate_and_idempotent(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activate_command = {
        "command_id": "execution-activate-command-001",
        "expected_contract_hash": snapshot["contract_hash"],
    }
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json=activate_command,
    )
    replayed_activation = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json=activate_command,
    )
    assert activated.status_code == 200
    assert replayed_activation.json()["id"] == snapshot["id"]
    assert activated.json()["status"] == "active"
    assert activated.json()["activated_at"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["work_items"] == []

    node_ids = [node["id"] for node in activated.json()["nodes"]]
    base_submit = {
        "expected_contract_hash": snapshot["contract_hash"],
        "expected_estimated_cost": snapshot["estimated_cost"],
        "expected_currency": snapshot["currency"],
        "confirm_high_risk_submission": True,
    }
    wrong_nodes = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={"command_id": "execution-submit-wrong-nodes", **base_submit, "expected_dag_node_ids": node_ids[:-1]},
    )
    assert wrong_nodes.status_code == 409
    assert wrong_nodes.headers["x-error-code"] == "DAG_NODE_LIST_MISMATCH"
    wrong_amount = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={"command_id": "execution-submit-wrong-cost1", **base_submit, "expected_estimated_cost": 0.8, "expected_dag_node_ids": node_ids},
    )
    assert wrong_amount.status_code == 409
    assert wrong_amount.headers["x-error-code"] == "SNAPSHOT_COST_MISMATCH"

    submit_command = {"command_id": "execution-submit-command-001", **base_submit, "expected_dag_node_ids": node_ids}
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json=submit_command,
    )
    replayed_submit = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json=submit_command,
    )
    assert submitted.status_code == 202
    execution = submitted.json()
    assert replayed_submit.json()["active_snapshot_id"] == snapshot["id"]
    assert len(execution["work_items"]) == len(node_ids)
    assert {item["dag_node_id"] for item in execution["work_items"]} == set(node_ids)
    assert all(len(item["attempts"]) == 1 for item in execution["work_items"])
    assert all(item["attempts"][0]["provider_task_id"] is None for item in execution["work_items"])
    with SessionLocal() as session:
        assert len(list(session.scalars(select(WorkItem)))) == len(node_ids)
        assert len(list(session.scalars(select(WorkAttempt)))) == len(node_ids)
        assert len({item.request_fingerprint for item in session.scalars(select(WorkItem))}) == len(node_ids)


def test_mock_worker_obeys_dag_order_and_makes_no_provider_submission(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "worker-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "worker-submit-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("test-worker") is True
    first = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    completed = [item for item in first["work_items"] if item["status"] == "completed"]
    assert len(completed) == 1
    assert completed[0]["kind"] == "generate_keyframe"
    for _ in range(len(activated["nodes"]) - 1):
        assert process_one("test-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    assert all(item["status"] == "completed" for item in execution["work_items"])
    assert execution["project_status"] == "quality_review"
    assert execution["snapshot"]["status"] == "execution_completed"
    assert all(item["attempts"][0]["provider_task_id"] is None for item in execution["work_items"])
    assert all(item["attempts"][0]["response_manifest"]["media_created"] is False for item in execution["work_items"])
    with SessionLocal() as session:
        cost_events = list(session.scalars(select(CostEvent).where(CostEvent.snapshot_id == snapshot["id"])))
        assert all(item.kind == "estimated" and item.status == "confirmed" for item in cost_events)


def test_unconnected_provider_blocks_without_retry_or_fallback(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "blocked-activate-command-1", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "blocked-submit-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("test-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    blocked = [item for item in execution["work_items"] if item["status"] == "blocked"]
    assert len(blocked) == 1
    assert blocked[0]["attempts"][0]["error_code"] == "PROVIDER_ADAPTER_NOT_CONNECTED"
    assert len(blocked[0]["attempts"]) == 1
    assert blocked[0]["attempts"][0]["provider_task_id"] is None


def test_asset_registration_verification_qc_and_human_approval_are_explicit(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "quality-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "quality-submit-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, _ = attach_local_provider_output(project, snapshot, 480, 848)
    response_hash = hashlib.sha256(json.dumps(response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    register_command = {
        "command_id": "quality-register-command-001",
        "output_index": 0,
        "expected_response_manifest_hash": response_hash,
    }
    registered = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json=register_command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json=register_command,
    )
    assert registered.status_code == 201
    asset = registered.json()
    assert replayed.json()["id"] == asset["id"]
    assert asset["state"] == "created"
    assert asset["content_hash"] is None

    verified = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
        json={"command_id": "quality-verify-command-001", "expected_row_version": asset["row_version"]},
    )
    assert verified.status_code == 200
    asset = verified.json()
    assert asset["state"] == "verified"
    assert asset["width"] == 480 and asset["height"] == 848
    assert asset["content_hash"] == response_manifest["outputs"][0]["content_hash"]

    qc = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:run-qc",
        json={"command_id": "quality-qc-command-001", "expected_row_version": asset["row_version"]},
    )
    assert qc.status_code == 200
    report = qc.json()
    assert report["status"] == "review_required"
    assert [finding["code"] for finding in report["findings"]] == ["VISUAL_CONTENT_REVIEW_REQUIRED"]
    review_view = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    pending_asset = next(row for row in review_view["assets"] if row["id"] == asset["id"])
    assert pending_asset["state"] == "review_required"
    assert "project.timeline" in pending_asset["affected_downstream_node_keys"]

    approved = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:approve",
        json={
            "command_id": "quality-approve-command-001",
            "expected_row_version": pending_asset["row_version"],
            "qc_report_id": report["id"],
            "rationale": "Composition and subject continuity are acceptable.",
        },
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"
    assert approved.json()["review_decisions"][0]["decision"] == "approved"
    content = client.get(f"/api/v1/projects/{project['id']}/assets/{asset['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/png")
    with SessionLocal() as session:
        assert len(list(session.scalars(select(WorkAttempt).where(WorkAttempt.work_item_id == item["id"])))) == 1
        assert session.scalar(select(AssetReviewDecision).where(AssetReviewDecision.asset_id == asset["id"])) is not None


def test_deterministic_asset_contract_failure_blocks_without_retry(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "bad-asset-activate-0001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "bad-asset-submit-000001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, _ = attach_local_provider_output(project, snapshot, 32, 32)
    response_hash = hashlib.sha256(json.dumps(response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json={"command_id": "bad-asset-register-0001", "output_index": 0, "expected_response_manifest_hash": response_hash},
    ).json()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
        json={"command_id": "bad-asset-verify-000001", "expected_row_version": asset["row_version"]},
    ).json()
    report = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:run-qc",
        json={"command_id": "bad-asset-qc-command1", "expected_row_version": asset["row_version"]},
    ).json()
    assert report["status"] == "blocked"
    assert report["findings"][0]["code"] == "MEDIA_DIMENSIONS_INVALID"
    review_view = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    archived = next(row for row in review_view["assets"] if row["id"] == asset["id"])
    assert archived["state"] == "archived"
    assert review_view["project_status"] == "blocked"
    with SessionLocal() as session:
        attempts = list(session.scalars(select(WorkAttempt).where(WorkAttempt.work_item_id == item["id"])))
        assert len(attempts) == 1
        assert len(list(session.scalars(select(QCFinding).where(QCFinding.qc_report_id == report["id"])))) == 1


def test_mock_response_cannot_be_registered_as_an_asset(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "mock-asset-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "mock-asset-submit-00001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("mock-asset-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    completed = next(item for item in execution["work_items"] if item["status"] == "completed")
    attempt = completed["attempts"][0]
    manifest_hash = hashlib.sha256(json.dumps(attempt["response_manifest"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    response = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{attempt['id']}/assets",
        json={"command_id": "mock-register-asset-0001", "output_index": 0, "expected_response_manifest_hash": manifest_hash},
    )
    assert response.status_code == 409
    assert response.headers["x-error-code"] == "ATTEMPT_CREATED_NO_MEDIA"
    with SessionLocal() as session:
        assert list(session.scalars(select(Asset))) == []


def test_file_verification_failure_is_persisted_as_blocked_evidence(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "file-block-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "file-block-submit-0001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, relative = attach_local_provider_output(project, snapshot, 480, 848)
    response_hash = hashlib.sha256(json.dumps(response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json={"command_id": "file-block-register-001", "output_index": 0, "expected_response_manifest_hash": response_hash},
    ).json()
    (TEST_RUNTIME / "assets" / Path(relative)).write_bytes(b"changed after provider manifest")
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
        json={"command_id": "file-block-verify-0001", "expected_row_version": asset["row_version"]},
    )
    assert blocked.status_code == 200
    result = blocked.json()
    assert result["state"] == "archived"
    assert result["latest_qc_report"]["status"] == "blocked"
    assert result["latest_qc_report"]["findings"][0]["code"] == "ASSET_CONTENT_HASH_MISMATCH"
    view = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    assert any(gap["code"] == "OUTPUT_NOT_APPROVED" for gap in view["output_gaps"])
    assert view["project_status"] == "blocked"


def timeline_items_for_assets(video_assets: list[dict]) -> list[dict]:
    cursor = 0
    items = []
    for sequence, asset in enumerate(video_assets, start=1):
        duration = asset["duration_ms"]
        items.append({
            "track_type": "main_video",
            "sequence_number": sequence,
            "asset_id": asset["id"],
            "label": asset["node_key"],
            "source_in_ms": 0,
            "source_out_ms": duration,
            "timeline_in_ms": cursor,
            "timeline_out_ms": cursor + duration,
            "transform": {"fit": "cover"},
        })
        cursor += duration
    return items


def test_quality_stage_and_timeline_confirmation_are_explicit_and_idempotent(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "editor-empty-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    )
    assert activated.status_code == 200
    with SessionLocal() as session:
        session.get(Project, project["id"]).status = "quality_review"
        session.commit()
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-stage-blocked-001", "expected_snapshot_id": snapshot["id"]},
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "QUALITY_STAGE_NOT_READY"

    video_assets = seed_editor_assets(client, project, snapshot)
    stage_command = {"command_id": "editor-stage-approve-001", "expected_snapshot_id": snapshot["id"]}
    approved = client.post(f"/api/v1/projects/{project['id']}/quality-stage:approve", json=stage_command)
    replayed = client.post(f"/api/v1/projects/{project['id']}/quality-stage:approve", json=stage_command)
    assert approved.status_code == 200
    assert replayed.json()["project_status"] == "editing"
    assert approved.json()["quality_stage_ready"] is True
    assert next(row for row in client.get("/api/v1/projects").json() if row["id"] == project["id"])["status"] == "editing"

    create_command = {
        "command_id": "timeline-create-command-001",
        "expected_snapshot_id": snapshot["id"],
        "source": "user",
        "track_config": {"audio_enabled": False, "subtitle_enabled": False},
        "items": timeline_items_for_assets(video_assets),
    }
    created = client.post(f"/api/v1/projects/{project['id']}/timeline-candidates", json=create_command)
    replayed_candidate = client.post(f"/api/v1/projects/{project['id']}/timeline-candidates", json=create_command)
    assert created.status_code == 201
    timeline = created.json()
    assert replayed_candidate.json()["id"] == timeline["id"]
    assert timeline["status"] == "candidate"

    validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:validate",
        json={"command_id": "timeline-validate-command-001", "expected_row_version": timeline["row_version"]},
    )
    assert validated.status_code == 200
    timeline = validated.json()
    assert timeline["status"] == "review"
    assert timeline["validation_report"] == []
    assert timeline["contract_hash"]

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:confirm",
        json={
            "command_id": "timeline-confirm-denied-001",
            "expected_row_version": timeline["row_version"],
            "expected_contract_hash": timeline["contract_hash"],
            "confirm_delivery_scope": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "TIMELINE_CONFIRMATION_REQUIRED"
    confirmed_command = {
        "command_id": "timeline-confirm-command-001",
        "expected_row_version": timeline["row_version"],
        "expected_contract_hash": timeline["contract_hash"],
        "confirm_delivery_scope": True,
    }
    confirmed = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:confirm",
        json=confirmed_command,
    )
    replayed_confirmation = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:confirm",
        json=confirmed_command,
    )
    assert confirmed.status_code == 200
    assert replayed_confirmation.json()["status"] == "confirmed"
    assert confirmed.json()["status"] == "confirmed"
    workspace = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()
    assert workspace["project_status"] == "delivery_ready"
    assert next(row for row in client.get("/api/v1/projects").json() if row["id"] == project["id"])["status"] == "delivery_ready"
    assert all(asset["state"] == "used" for asset in workspace["available_assets"] if asset["asset_type"] == "video")
    with SessionLocal() as session:
        assert session.scalar(select(Timeline).where(Timeline.project_id == project["id"])) is not None
        assert len(list(session.scalars(select(WorkAttempt)))) == 0


def test_timeline_validation_blocks_unapproved_assets_gaps_and_source_overrun(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-stage-approve-002", "expected_snapshot_id": snapshot["id"]},
    )
    items = timeline_items_for_assets(video_assets)
    items[0]["source_out_ms"] += 1000
    items[1]["asset_id"] = None
    items[1]["gap_reason"] = "等待用户取舍"
    with SessionLocal() as session:
        asset = session.get(Asset, video_assets[2]["id"])
        asset.state = "verified"
        session.commit()
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-invalid-create-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": items,
        },
    ).json()
    validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{candidate['id']}:validate",
        json={"command_id": "timeline-invalid-check-001", "expected_row_version": candidate["row_version"]},
    ).json()
    codes = {row["code"] for row in validated["validation_report"]}
    assert validated["status"] == "candidate"
    assert "SOURCE_RANGE_EXCEEDS_ASSET" in codes
    assert "TIMELINE_SPEED_CHANGE_UNDECLARED" in codes
    assert "TIMELINE_GAP_UNRESOLVED" in codes
    assert "TIMELINE_ASSET_NOT_APPROVED" in codes
    confirm = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{candidate['id']}:confirm",
        json={
            "command_id": "timeline-invalid-confirm-01",
            "expected_row_version": validated["row_version"],
            "expected_contract_hash": validated["contract_hash"],
            "confirm_delivery_scope": True,
        },
    )
    assert confirm.status_code == 409
    assert confirm.headers["x-error-code"] == "TIMELINE_NOT_READY_FOR_CONFIRMATION"


def test_confirmed_timeline_revision_creates_new_version_without_mutating_items(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-stage-approve-003", "expected_snapshot_id": snapshot["id"]},
    )
    items = timeline_items_for_assets(video_assets)
    unbound_agent = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-agent-unbound-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "editor_assistant",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": items,
        },
    )
    assert unbound_agent.status_code == 409
    assert unbound_agent.headers["x-error-code"] == "EDITOR_AGENT_RUN_INVALID"
    first = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-revision-base-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": items,
        },
    ).json()
    first = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{first['id']}:validate",
        json={"command_id": "timeline-revision-validate1", "expected_row_version": first["row_version"]},
    ).json()
    first = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{first['id']}:confirm",
        json={
            "command_id": "timeline-revision-confirm1",
            "expected_row_version": first["row_version"],
            "expected_contract_hash": first["contract_hash"],
            "confirm_delivery_scope": True,
        },
    ).json()
    unlinked = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-unlinked-version-02",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": items,
        },
    )
    assert unlinked.status_code == 409
    assert unlinked.headers["x-error-code"] == "TIMELINE_REVISION_REQUIRED"
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{first['id']}:revise",
        json={
            "command_id": "timeline-revision-create-002",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": first["row_version"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": list(reversed([
                {**item, "sequence_number": len(items) - index}
                for index, item in enumerate(items)
            ])),
        },
    )
    assert revised.status_code == 201
    second = revised.json()
    assert second["version_number"] == 2
    assert second["supersedes_timeline_id"] == first["id"]
    assert second["status"] == "candidate"
    workspace = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()
    old = next(row for row in workspace["timelines"] if row["id"] == first["id"])
    assert old["status"] == "confirmed"
    assert old["items"] == first["items"]
    assert workspace["project_status"] == "editing"


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
