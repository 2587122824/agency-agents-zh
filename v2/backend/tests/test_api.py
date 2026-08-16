from __future__ import annotations

import os
import hashlib
import io
import json
import struct
import wave
import zlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
TEST_RUNTIME = Path(__file__).resolve().parent / "test_runtime"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["V2_RUNTIME_ROOT"] = str(TEST_RUNTIME)

from v2.backend.app.main import app
from v2.backend.app.db.session import Base, engine
from v2.backend.app.db.session import SessionLocal
from v2.backend.app.db.models import AgentInputManifest, AgentRun, Asset, AssetReviewDecision, AssetRevisionRequest, Attachment, CommandReceipt, CostEvent, CosyVoiceValidationRun, CreativeBriefCandidate, DAGNode, Decision, DecisionChangeImpactAnalysis, DeliveryAttempt, DependencyEdge, Entity, EntityVersion, PlanVersion, ProductionSnapshot, Project, ProjectEvent, QCFinding, QCReport, QCReportCandidate, RequirementVersion, Shot, ShotPlanCandidate, StoragePolicyVersion, Timeline, TimelineItem, VoiceCloneAuthorizationVersion, WorkflowSlotVersion, WorkAttempt, WorkItem
from v2.backend.app.creation.completeness import evaluate_requirement
from v2.backend.app.creation.service import _validated_update_value
from sqlalchemy import select
from v2.backend.app.workers.worker import process_one
from v2.backend.app.providers import ProviderAdapterError, ProviderExecutionRequest, ProviderPollResult, ProviderSubmission
from v2.backend.app.providers.registry import ProviderAdapterRegistry
from v2.backend.app.providers.contracts import CosyVoicePaidValidationCommand
from v2.backend.app.providers.cosyvoice_validation import execute_cosyvoice_paid_validation
from v2.backend.app.creation.agent_gateway import AgentGatewayError, CreativeAgentOutput, CreativeAgentResult, DeterministicCreativeAgentGateway, get_creative_agent_gateway
from v2.backend.app.planning.agent_gateway import ContentPlannerOutput, ContentPlannerResult, DeterministicContentPlannerGateway, get_content_planner_gateway
from v2.backend.app.planning.director_gateway import DeterministicDirectorGateway, get_director_gateway
from v2.backend.app.production.agent_gateway import DeterministicProductionPlannerGateway, ProductionPlannerOutput, ProductionPlannerResult, get_production_planner_gateway
from v2.backend.app.editor.agent_gateway import DeterministicEditorAssistantGateway, EditorAssistantResult, get_editor_assistant_gateway
from v2.backend.app.editor import service as editor_service
from v2.backend.app.quality.agent_gateway import DeterministicQCGateway, get_qc_gateway
from v2.backend.app.delivery import service as delivery_service
from v2.backend.app.delivery.renderer import FFmpegReadiness, LocalRenderResult
from v2.backend.app.quality.service import resolve_local_asset_path
from v2.backend.app.repositories import SqlAlchemyEventRepository


class FailOnceQCGateway(DeterministicQCGateway):
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, selection, manifest_payload, media_path):
        self.calls += 1
        if self.calls == 1:
            raise AgentGatewayError("QC_TEST_FAILURE", "模拟质量审核模型失败。")
        return super().invoke(selection, manifest_payload, media_path)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "false")
    TEST_DATABASE.unlink(missing_ok=True)
    if TEST_RUNTIME.exists():
        import shutil
        shutil.rmtree(TEST_RUNTIME)
    app.dependency_overrides[get_creative_agent_gateway] = lambda: DeterministicCreativeAgentGateway()
    app.dependency_overrides[get_content_planner_gateway] = lambda: DeterministicContentPlannerGateway()
    app.dependency_overrides[get_director_gateway] = lambda: DeterministicDirectorGateway()
    app.dependency_overrides[get_production_planner_gateway] = lambda: DeterministicProductionPlannerGateway()
    app.dependency_overrides[get_editor_assistant_gateway] = lambda: DeterministicEditorAssistantGateway()
    app.dependency_overrides[get_qc_gateway] = lambda: DeterministicQCGateway()
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_creative_agent_gateway, None)
    app.dependency_overrides.pop(get_content_planner_gateway, None)
    app.dependency_overrides.pop(get_director_gateway, None)
    app.dependency_overrides.pop(get_production_planner_gateway, None)
    app.dependency_overrides.pop(get_editor_assistant_gateway, None)
    app.dependency_overrides.pop(get_qc_gateway, None)
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
            "production_profile": {
                "video_motion_strategy": "adaptive",
                "keyframe_strategy": "adaptive",
                "enforcement": "required",
            },
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
    decision_required = client.get(f"/api/v1/projects/{project['id']}").json()
    assert decision_required["status"] == "decision_required"
    assert decision_required["row_version"] == 2

    blocked = client.post(f"/api/v1/projects/{project['id']}/confirm")
    assert blocked.status_code == 409

    resolved = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{decision_id}/resolve",
        json={"value": "documentary"},
    )
    assert resolved.status_code == 200
    collecting = client.get(f"/api/v1/projects/{project['id']}").json()
    assert collecting["status"] == "planning"
    assert collecting["row_version"] == 3

    confirmed = client.post(f"/api/v1/projects/{project['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert confirmed.json()["state_trigger"] == "legacy_contract_confirmed"

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
    assert processed.json()["state_trigger"] == "legacy_validation_completed"
    assert processed.json()["work_items"][0]["status"] == "completed"


def test_project_creation_freezes_explicit_three_frame_profile(client: TestClient) -> None:
    options = client.get("/api/v1/project-production-profile-options")
    assert options.status_code == 200
    motion = {item["key"]: item for item in options.json()["video_motion_strategies"]}
    references = {item["key"]: item for item in options.json()["keyframe_strategies"]}
    assert motion["three_frame"]["available"] is True
    assert motion["start_end"]["available"] is False
    assert references["omni_reference"]["available"] is False

    response = client.post("/api/v1/projects", json={
        "title": "Three frame profile",
        "core_topic": "用首中尾三帧制作训练短片",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "audio_mode": "off",
        "production_profile": {
            "video_motion_strategy": "three_frame",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
    })
    assert response.status_code == 201
    project = response.json()
    profile = project["production_profile"]
    assert profile["video_motion_strategy"] == "three_frame"
    assert profile["required_frame_roles"] == ["start_frame", "middle_frame", "end_frame"]
    assert len(profile["contract_hash"]) == 64

    control = client.get(f"/api/v1/projects/{project['id']}/control-center").json()
    assert control["video_motion_strategy"] == "three_frame"
    assert control["production_profile_contract_hash"] == profile["contract_hash"]

    unavailable = client.post("/api/v1/projects", json={
        "title": "Unavailable profile",
        "core_topic": "不能静默使用不存在的首尾帧工作流",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "audio_mode": "off",
        "production_profile": {
            "video_motion_strategy": "start_end",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
    })
    assert unavailable.status_code == 409
    assert unavailable.headers["x-error-code"] == "PROJECT_VIDEO_MOTION_STRATEGY_UNAVAILABLE"


def test_legacy_validation_with_invalid_project_state_blocks_item_without_stopping_worker(client: TestClient) -> None:
    project = create_creation_project(client)
    with SessionLocal() as session:
        item = WorkItem(project_id=project["id"], kind="contract_validation", payload={}, status="queued")
        session.add(item)
        session.commit()
        item_id = item.id

    assert process_one("legacy-state-test-worker") is True

    with SessionLocal() as session:
        item = session.get(WorkItem, item_id)
        current_project = session.get(Project, project["id"])
        assert item is not None and item.status == "blocked"
        assert item.error.startswith("LEGACY_PROJECT_STATE_INVALID:")
        assert current_project is not None and current_project.status == "draft"


def test_pending_decision_blocks_planning_until_explicit_resolution(client: TestClient) -> None:
    project = create_creation_project(client)
    pending = client.post(
        f"/api/v1/projects/{project['id']}/decisions",
        json={"key": "visual_style", "label": "画面风格", "status": "pending"},
    )
    assert pending.status_code == 201
    requirement_id = client.get(
        f"/api/v1/projects/{project['id']}/creation-center"
    ).json()["active_requirement"]["id"]

    blocked = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "pending-gate-brief", "expected_requirement_version_id": requirement_id},
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "PROJECT_DECISIONS_UNRESOLVED"
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "decision_required"

    resolved = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{pending.json()['id']}/resolve",
        json={"value": "documentary"},
    )
    assert resolved.status_code == 200
    generated = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "resolved-gate-brief", "expected_requirement_version_id": requirement_id},
    )
    assert generated.status_code == 201
    state = client.get(f"/api/v1/projects/{project['id']}").json()
    assert state["status"] == "plan_review"
    assert state["state_trigger"] == "brief_candidate_created"


def test_project_control_list_uses_persisted_facts_for_new_project(client: TestClient) -> None:
    project = create_creation_project(client)
    controls = client.get("/api/v1/project-controls")
    assert controls.status_code == 200
    summary = next(row for row in controls.json() if row["project_id"] == project["id"])
    assert summary["persisted_status"] == "draft"
    assert summary["state_row_version"] == 1
    assert summary["state_trigger"] == "project_created"
    assert summary["state_reason_code"] is None
    assert summary["blocked_from_state"] is None
    assert summary["evaluated_stage"] == "requirements"
    assert summary["active_plan_version"] is None
    assert summary["active_snapshot_number"] is None
    assert summary["blocker_count"] == 0
    assert summary["next_action"] == {
        "code": "CONTINUE_REQUIREMENTS",
        "label": "继续确认创作需求",
        "path": f"/projects/{project['id']}",
        "incurs_production_cost": False,
        "confirmation_level": "none",
    }
    detail = client.get(f"/api/v1/projects/{project['id']}/control-center")
    assert detail.status_code == 200
    assert detail.json()["recent_events"][0]["event_type"] == "project.created.v1"
    assert detail.json()["costs"] == []
    assert detail.json()["routes"] == []


def test_project_archive_hides_from_default_lists_and_restore_preserves_state(client: TestClient) -> None:
    project = create_creation_project(client)
    archive_payload = {
        "command_id": "project-archive-command-001",
        "actor_id": "test-user",
        "expected_row_version": project["row_version"],
        "confirm_archive": True,
    }
    archived = client.post(f"/api/v1/projects/{project['id']}:archive", json=archive_payload)
    assert archived.status_code == 200
    archived_project = archived.json()
    assert archived_project["status"] == project["status"]
    assert archived_project["row_version"] == project["row_version"] + 1
    assert archived_project["archived_at"] is not None
    assert archived_project["archived_by"] == "test-user"

    assert all(item["id"] != project["id"] for item in client.get("/api/v1/projects").json())
    assert all(item["project_id"] != project["id"] for item in client.get("/api/v1/project-controls").json())
    archived_list = client.get("/api/v1/projects?include_archived=true").json()
    assert any(item["id"] == project["id"] and item["archived_at"] for item in archived_list)
    archived_controls = client.get("/api/v1/project-controls?include_archived=true").json()
    assert any(item["project_id"] == project["id"] and item["archived_at"] for item in archived_controls)

    events_before_replay = client.get(f"/api/v1/projects/{project['id']}/control-center").json()["recent_events"]
    assert events_before_replay[0]["event_type"] == "project.archived.v1"
    replay = client.post(f"/api/v1/projects/{project['id']}:archive", json=archive_payload)
    assert replay.status_code == 200
    assert replay.json()["row_version"] == archived_project["row_version"]
    events_after_replay = client.get(f"/api/v1/projects/{project['id']}/control-center").json()["recent_events"]
    assert len(events_after_replay) == len(events_before_replay)

    restore_payload = {
        "command_id": "project-restore-command-001",
        "actor_id": "test-user",
        "expected_row_version": archived_project["row_version"],
    }
    restored = client.post(f"/api/v1/projects/{project['id']}:restore", json=restore_payload)
    assert restored.status_code == 200
    restored_project = restored.json()
    assert restored_project["status"] == project["status"]
    assert restored_project["row_version"] == archived_project["row_version"] + 1
    assert restored_project["archived_at"] is None
    assert restored_project["archived_by"] is None
    assert any(item["id"] == project["id"] for item in client.get("/api/v1/projects").json())
    restored_events = client.get(f"/api/v1/projects/{project['id']}/control-center").json()["recent_events"]
    assert restored_events[0]["event_type"] == "project.restored.v1"


def test_project_archive_rejects_active_work_and_stale_version(client: TestClient) -> None:
    project = create_creation_project(client)
    stale = client.post(
        f"/api/v1/projects/{project['id']}:archive",
        json={
            "command_id": "project-archive-stale-001",
            "actor_id": "test-user",
            "expected_row_version": project["row_version"] + 1,
            "confirm_archive": True,
        },
    )
    assert stale.status_code == 409
    assert stale.headers["x-error-code"] == "PROJECT_ROW_VERSION_CONFLICT"

    with SessionLocal() as session:
        session.add(WorkItem(
            project_id=project["id"],
            kind="contract_validation",
            payload={"project_id": project["id"]},
            status="queued",
        ))
        session.commit()
    active = client.post(
        f"/api/v1/projects/{project['id']}:archive",
        json={
            "command_id": "project-archive-active-001",
            "actor_id": "test-user",
            "expected_row_version": project["row_version"],
            "confirm_archive": True,
        },
    )
    assert active.status_code == 409
    assert active.headers["x-error-code"] == "PROJECT_ACTIVE_WORK_EXISTS"
    current = client.get(f"/api/v1/projects/{project['id']}").json()
    assert current["archived_at"] is None


def create_creation_project(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/projects",
        json={
            "title": "Creation center test",
            "core_topic": "30 秒竖屏健身广告",
            "duration_seconds": 30,
            "aspect_ratio": "9:16",
            "audio_mode": "off",
            "production_profile": {
                "video_motion_strategy": "adaptive",
                "keyframe_strategy": "adaptive",
                "enforcement": "required",
            },
        },
    )
    assert response.status_code == 201
    return response.json()


def test_candidate_is_audited_and_requires_explicit_acceptance(client: TestClient) -> None:
    project = create_creation_project(client)
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = view["active_requirement"]["id"]
    assert view["active_requirement"]["version_number"] == 1
    assert view["next_action"]["code"] == "INITIALIZE_CREATIVE_CONVERSATION"

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
    latest_run = before_accept["latest_agent_run"]
    assert latest_run["model_provider"] == "mock"
    assert latest_run["agent_role"] == "creative"
    assert latest_run["input_manifest"]["base_requirement_version_id"] == base_id
    assert latest_run["input_manifest"]["message_ids"] == [first_message.json()["id"]]
    assert latest_run["input_manifest"]["decision_ids"] == []
    assert latest_run["input_manifest"]["attachment_binding_ids"] == []
    assert latest_run["input_manifest"]["system_config_version"] == "v2.creation.test.v1"
    assert latest_run["model_config_version_id"] == "model_config_test_creative"
    assert latest_run["provider_request_id"] == "test-request"
    assert latest_run["token_usage"] == {"total_tokens": 1}
    assert len(latest_run["input_manifest"]["input_hash"]) == 64
    assert latest_run == before_accept["agent_runs"][0]
    assert "raw_output" not in latest_run
    assistant_messages = [item for item in before_accept["messages"] if item["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["reply_to_message_id"] == first_message.json()["id"]
    assert assistant_messages[0]["agent_run_id"] == latest_run["id"]
    proposal = before_accept["active_creative_proposal"]
    assert proposal["agent_run_id"] == latest_run["id"]
    assert proposal["assistant_message_id"] == assistant_messages[0]["id"]
    assert proposal["suggestion_sets"][0]["field_key"] == "content_structure"
    assert len(proposal["suggestion_sets"][0]["options"]) == 3
    assert proposal["suggestion_sets"][0]["options"][0]["recommended"] is True
    assert all(
        len(option["proposed_updates"]) == 1
        and option["proposed_updates"][0]["field_key"] == "content_structure"
        for option in proposal["suggestion_sets"][0]["options"]
    )

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


def test_creative_agent_rejects_explicit_update_equal_to_active_value(client: TestClient) -> None:
    class NoChangeGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            latest = manifest_payload["conversation"]["messages"][-1]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "保持当前静音设置。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "shaping",
                    "summary": "音频策略已经明确。", "established_fields": ["audio_mode"],
                    "open_gaps": [{"field_key": "content_structure", "reason": "仍需明确内容如何展开。"}],
                    "focus_field": "content_structure", "focus_reason": "内容结构是当前最重要的未决信息。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [],
                "proposal_selections": [],
                "explicit_updates": [{
                    "field_key": "audio_mode",
                    "value": "off",
                    "source_message_ids": [latest["id"]],
                }],
                "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "no-change", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "no-change-message-001",
        "content": "保持静音。",
    })
    app.dependency_overrides[get_creative_agent_gateway] = lambda: NoChangeGateway()

    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "no-change-generate-001", "expected_base_version_id": initial["active_requirement"]["id"]},
    )

    assert generated.status_code == 502
    assert generated.headers["x-error-code"] == "AGENT_MODEL_OUTPUT_NO_CHANGE"
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert view["latest_agent_run"]["status"] == "failed"
    assert view["current_candidate"] is None
    assert view["active_requirement"]["fields"]["audio_mode"] == "off"
    assert view["active_requirement"]["field_sources"]["audio_mode"]["type"] == "user"


def test_failed_creative_turn_requires_explicit_confirmed_retry(client: TestClient) -> None:
    class FailingCreativeGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            raise AgentGatewayError("AGENT_MODEL_HTTP_FAILED", "测试中的创作模型调用失败。")

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    message = client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "retry-message-001",
        "content": "给我三个真实感训练方向。",
    })
    assert message.status_code == 201
    app.dependency_overrides[get_creative_agent_gateway] = lambda: FailingCreativeGateway()
    failed = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "retry-generate-001", "expected_base_version_id": base_id},
    )
    assert failed.status_code == 502
    failed_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    failed_run = failed_view["latest_agent_run"]
    assert failed_run["status"] == "failed"
    assert failed_view["next_action"] == {
        "code": "RETRY_FAILED_CREATIVE_TURN",
        "target_ids": [failed_run["id"]],
        "label": "确认后重跑失败轮次",
        "incurs_model_cost": True,
        "incurs_production_cost": False,
    }

    not_confirmed = client.post(
        f"/api/v1/projects/{project['id']}/creative-agent-runs/{failed_run['id']}:retry",
        json={
            "command_id": "retry-command-001",
            "expected_base_version_id": base_id,
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": False,
        },
    )
    assert not_confirmed.status_code == 409
    assert not_confirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"

    app.dependency_overrides[get_creative_agent_gateway] = lambda: DeterministicCreativeAgentGateway()
    retried = client.post(
        f"/api/v1/projects/{project['id']}/creative-agent-runs/{failed_run['id']}:retry",
        json={
            "command_id": "retry-command-002",
            "expected_base_version_id": base_id,
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == "awaiting_review"
    retried_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert retried_view["latest_agent_run"]["status"] == "succeeded"
    assert retried_view["current_candidate"]["id"] == retried.json()["id"]


def test_creative_manifest_exposes_attachment_metadata_without_media_content(client: TestClient) -> None:
    class CapturingCreativeGateway(DeterministicCreativeAgentGateway):
        manifest_payload = None

        def invoke(self, selection, manifest_payload):
            self.manifest_payload = manifest_payload
            return super().invoke(selection, manifest_payload)

    gateway = CapturingCreativeGateway()
    app.dependency_overrides[get_creative_agent_gateway] = lambda: gateway
    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    attachment = client.post(
        f"/api/v1/projects/{project['id']}/attachments",
        data={"command_id": "metadata-attachment-001"},
        files={"file": ("reference.png", b"\x89PNG\r\n\x1a\nmetadata", "image/png")},
    ).json()
    binding = client.post(
        f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/bindings",
        json={"command_id": "metadata-binding-001", "binding_type": "inspiration_only"},
    )
    assert binding.status_code == 201
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "metadata-message-001",
        "content": "参考我上传的文件给出方向。",
    })
    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "metadata-generate-001", "expected_base_version_id": initial["active_requirement"]["id"]},
    )
    assert generated.status_code == 201
    facts = gateway.manifest_payload["project_context"]["confirmed_attachment_bindings"]
    assert facts == [{
        "id": binding.json()["id"],
        "type": "inspiration_only",
        "entity_id": None,
        "attachment": {
            "id": attachment["id"],
            "original_filename": "reference.png",
            "mime_type": "image/png",
            "byte_size": len(b"\x89PNG\r\n\x1a\nmetadata"),
            "verification_status": "verified",
            "content_access": "metadata_only",
        },
    }]


def test_creative_suggestion_selection_creates_candidate_without_confirming_requirement(client: TestClient) -> None:
    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    first = client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "suggestion-message-001",
        "content": "给我三个可选的内容方向。",
    })
    assert first.status_code == 201
    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "suggestion-generate-001", "expected_base_version_id": base_id},
    )
    assert generated.status_code == 201
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    proposal = view["active_creative_proposal"]
    suggestion_set = proposal["suggestion_sets"][0]
    option = suggestion_set["options"][0]

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "suggestion-select-unconfirmed-001",
            "actor_id": "test-user",
            "expected_base_version_id": base_id,
            "suggestion_set_id": suggestion_set["id"],
            "option_id": option["id"],
            "confirm_model_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"

    selected = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "suggestion-select-001",
            "actor_id": "test-user",
            "expected_base_version_id": base_id,
            "suggestion_set_id": suggestion_set["id"],
            "option_id": option["id"],
            "confirm_model_cost": True,
        },
    )
    assert selected.status_code == 201, selected.text
    candidate = selected.json()
    assert candidate["status"] == "awaiting_review"
    assert candidate["fields"]["content_structure"] == "训练日记"
    assert candidate["field_sources"]["content_structure"]["type"] == "user_selection"

    after_selection = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert after_selection["active_requirement"]["id"] == base_id
    assert after_selection["active_requirement"]["version_number"] == 1
    assert after_selection["current_candidate"]["id"] == candidate["id"]
    assert after_selection["active_creative_proposal"]["id"] != proposal["id"]
    assert after_selection["active_creative_proposal"]["suggestion_sets"][0]["field_key"] == "target_audience"
    assert after_selection["messages"][-2]["role"] == "user"
    assert "我选择了" in after_selection["messages"][-2]["content"]
    assert after_selection["messages"][-1]["role"] == "assistant"

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "suggestion-select-002",
            "expected_base_version_id": base_id,
            "suggestion_set_id": suggestion_set["id"],
            "option_id": suggestion_set["options"][1]["id"],
            "confirm_model_cost": True,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.headers["x-error-code"] == "CREATIVE_SUGGESTION_ALREADY_SELECTED"

    new_session = client.post(
        f"/api/v1/projects/{project['id']}/conversation-sessions",
        json={"command_id": "conversation-session-002", "actor_id": "test-user"},
    )
    assert new_session.status_code == 201
    new_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert new_view["conversation_session_id"] == new_session.json()["id"]
    assert new_view["messages"] == []
    assert new_view["active_requirement"]["id"] == base_id
    assert new_view["active_creative_proposal"] is None


def test_selection_followup_failure_keeps_choice_and_allows_exact_retry(client: TestClient) -> None:
    class FailingFollowupGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            if manifest_payload["runtime_context"].get("turn_intent") == "selection_followup":
                raise AgentGatewayError("TEST_SELECTION_FOLLOWUP_FAILED", "测试后续引导失败。")
            return super().invoke(selection, manifest_payload)

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "followup-failure-message-001",
        "content": "请给我几个内容结构方向。",
    })
    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "followup-failure-generate-001", "expected_base_version_id": base_id},
    )
    assert generated.status_code == 201
    proposal = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_creative_proposal"]
    suggestion_set = proposal["suggestion_sets"][0]
    option = suggestion_set["options"][0]
    app.dependency_overrides[get_creative_agent_gateway] = lambda: FailingFollowupGateway()

    failed = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "followup-failure-select-001",
            "expected_base_version_id": base_id,
            "suggestion_set_id": suggestion_set["id"],
            "option_id": option["id"],
            "confirm_model_cost": True,
        },
    )
    assert failed.status_code == 502
    failed_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert failed_view["current_candidate"]["fields"]["content_structure"] == option["proposed_updates"][0]["value"]
    assert failed_view["latest_agent_run"]["status"] == "failed"
    assert failed_view["next_action"]["code"] == "RETRY_FAILED_CREATIVE_TURN"
    assert failed_view["messages"][-1]["role"] == "user"

    app.dependency_overrides[get_creative_agent_gateway] = lambda: DeterministicCreativeAgentGateway()
    retried = client.post(
        f"/api/v1/projects/{project['id']}/creative-agent-runs/{failed_view['latest_agent_run']['id']}:retry",
        json={
            "command_id": "followup-failure-retry-001",
            "expected_base_version_id": base_id,
            "failed_agent_run_id": failed_view["latest_agent_run"]["id"],
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 201, retried.text
    retried_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert retried_view["messages"][-1]["role"] == "assistant"
    assert retried_view["active_creative_proposal"]["suggestion_sets"][0]["field_key"] != "content_structure"
    assert retried_view["latest_agent_run"]["status"] == "succeeded"
    assert retried_view["next_action"]["code"] == "REVIEW_REQUIREMENT_CANDIDATE"


def test_typed_suggestion_selection_uses_frozen_option_ids(client: TestClient) -> None:
    class TypedSelectionGateway(DeterministicCreativeAgentGateway):
        manifest_payload = None

        def invoke(self, selection, manifest_payload):
            self.manifest_payload = manifest_payload
            latest = manifest_payload["conversation"]["messages"][-1]
            proposal = manifest_payload["conversation"]["selection_scope"]
            suggestion_set = proposal["suggestion_sets"][0]
            option = suggestion_set["options"][1]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": f"你选择了{option['label']}。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "shaping",
                    "summary": "内容结构已经选定，可以继续明确目标受众。", "established_fields": ["content_structure"],
                    "open_gaps": [{"field_key": "target_audience", "reason": "受众会影响表达重点。"}],
                    "focus_field": "target_audience", "focus_reason": "这是选择结构后的下一个关键创作变量。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [],
                "proposal_selections": [{
                    "proposal_id": proposal["proposal_id"],
                    "suggestion_set_id": suggestion_set["id"],
                    "option_id": option["id"],
                    "source_message_ids": [latest["id"]],
                }],
                "explicit_updates": [],
                "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "typed-selection", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "typed-selection-message-001",
        "content": "给我三个可选方向。",
    })
    client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "typed-selection-generate-001", "expected_base_version_id": base_id},
    )
    first_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    first_proposal = first_view["active_creative_proposal"]
    assistant_message_id = first_proposal["assistant_message_id"]
    expected_option = first_proposal["suggestion_sets"][0]["options"][1]
    typed_message = client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "typed-selection-message-002",
        "content": "我选第二个。",
        "reply_to_message_id": assistant_message_id,
    })
    gateway = TypedSelectionGateway()
    app.dependency_overrides[get_creative_agent_gateway] = lambda: gateway
    selected = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "typed-selection-generate-002", "expected_base_version_id": base_id},
    )

    assert selected.status_code == 201
    candidate = selected.json()
    assert candidate["fields"]["content_structure"] == expected_option["proposed_updates"][0]["value"]
    assert candidate["field_sources"]["content_structure"] == {
        "type": "user_selection",
        "reference_id": typed_message.json()["id"],
    }
    selection_scope = gateway.manifest_payload["conversation"]["selection_scope"]
    assert selection_scope["proposal_id"] == first_proposal["id"]
    assert selection_scope["assistant_message_id"] == assistant_message_id
    assert selection_scope["suggestion_sets"] == first_proposal["suggestion_sets"]
    proposal_history = gateway.manifest_payload["conversation"]["proposal_history"]
    assert proposal_history[0]["assistant_message_id"] == assistant_message_id
    assert "id" not in proposal_history[0]
    assert "id" not in proposal_history[0]["suggestion_sets"][0]
    assert "id" not in proposal_history[0]["suggestion_sets"][0]["options"][0]


def test_selected_history_cannot_be_resubmitted_during_unrelated_update(client: TestClient) -> None:
    class ExplicitUpdateGateway(DeterministicCreativeAgentGateway):
        manifest_payload = None

        def invoke(self, selection, manifest_payload):
            self.manifest_payload = manifest_payload
            latest = manifest_payload["conversation"]["messages"][-1]
            assert manifest_payload["conversation"]["selection_scope"] is None
            history = manifest_payload["conversation"]["proposal_history"]
            assert history[0]["selections"][0]["option_label"] == "训练日记"
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "已将音频模式修改整理为待确认候选。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "refining",
                    "summary": "音频偏好已表达，创作框架仍需补充目标受众。", "established_fields": ["audio_mode", "content_structure"],
                    "open_gaps": [{"field_key": "target_audience", "reason": "受众会影响信息取舍。"}],
                    "focus_field": "target_audience", "focus_reason": "当前应先确认内容主要服务谁。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [],
                "proposal_selections": [],
                "explicit_updates": [{
                    "field_key": "audio_mode",
                    "value": "voiceover",
                    "source_message_ids": [latest["id"]],
                }],
                "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "explicit-update", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "history-scope-message-001",
        "content": "给我三个可选方向。",
    })
    client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "history-scope-generate-001", "expected_base_version_id": base_id},
    )
    first_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    proposal = first_view["active_creative_proposal"]
    suggestion_set = proposal["suggestion_sets"][0]
    option = suggestion_set["options"][0]
    selected = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "history-scope-select-001",
            "actor_id": "test-user",
            "expected_base_version_id": base_id,
            "suggestion_set_id": suggestion_set["id"],
            "option_id": option["id"],
            "confirm_model_cost": True,
        },
    )
    assert selected.status_code == 201
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "history-scope-message-002",
        "content": "把音频模式改成旁白。",
    })
    gateway = ExplicitUpdateGateway()
    app.dependency_overrides[get_creative_agent_gateway] = lambda: gateway

    updated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "history-scope-generate-002", "expected_base_version_id": base_id},
    )

    assert updated.status_code == 201
    assert updated.json()["fields"]["audio_mode"] == "voiceover"
    assert updated.json()["fields"]["content_structure"] == selected.json()["fields"]["content_structure"]
    inherited = next(
        item for item in client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["candidate_history"]
        if item["id"] == updated.json()["supersedes_candidate_id"]
    )
    assert inherited["fields"]["content_structure"] == selected.json()["fields"]["content_structure"]
    assert inherited["supersedes_candidate_id"] == selected.json()["id"]
    assert updated.json()["change_summary"] == [{
        "field_key": "audio_mode",
        "before": "off",
        "after": "voiceover",
        "source_message_ids": [gateway.manifest_payload["conversation"]["messages"][-1]["id"]],
        "risk_level": "high",
    }]


def test_typed_suggestion_selection_rejects_unknown_option_id(client: TestClient) -> None:
    class InvalidSelectionGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            latest = manifest_payload["conversation"]["messages"][-1]
            proposal = manifest_payload["conversation"]["selection_scope"]
            suggestion_set = proposal["suggestion_sets"][0]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "已选择。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "shaping",
                    "summary": "正在处理用户对现有方向的选择。", "established_fields": ["core_topic"],
                    "open_gaps": [{"field_key": "content_structure", "reason": "需要确认内容组织方式。"}],
                    "focus_field": "content_structure", "focus_reason": "本轮正在确认该创作变量。",
                    "source_message_ids": [latest["id"]],
                },
                "proposal_selections": [{
                    "proposal_id": proposal["proposal_id"],
                    "suggestion_set_id": suggestion_set["id"],
                    "option_id": "sgopt_not_in_manifest",
                    "source_message_ids": [latest["id"]],
                }],
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "invalid-selection", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    base_id = initial["active_requirement"]["id"]
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "invalid-selection-message-001",
        "content": "给我三个方向。",
    })
    client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "invalid-selection-generate-001", "expected_base_version_id": base_id},
    )
    first_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "invalid-selection-message-002",
        "content": "我选其中一个。",
        "reply_to_message_id": first_view["active_creative_proposal"]["assistant_message_id"],
    })
    app.dependency_overrides[get_creative_agent_gateway] = lambda: InvalidSelectionGateway()
    invalid = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "invalid-selection-generate-002", "expected_base_version_id": base_id},
    )

    assert invalid.status_code == 502
    assert invalid.headers["x-error-code"] == "AGENT_MODEL_SELECTION_OPTION_INVALID"
    failed_view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert failed_view["latest_agent_run"]["status"] == "failed"
    assert failed_view["current_candidate"] is not None
    assert failed_view["current_candidate"]["status"] == "awaiting_review"


def test_new_message_keeps_draft_until_inherited_revision_succeeds(client: TestClient) -> None:
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
    before_generation = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert before_generation["current_candidate"]["id"] == candidate["id"]
    revised = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "generate-command-102", "expected_base_version_id": base_id},
    )
    assert revised.status_code == 201
    assert revised.json()["supersedes_candidate_id"] == candidate["id"]
    assert revised.json()["fields"]["creative_direction"] == "新的明确方向"
    current = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    history = {item["id"]: item for item in current["candidate_history"]}
    assert history[candidate["id"]]["status"] == "stale"
    assert current["current_candidate"]["id"] == revised.json()["id"]


def test_initial_guidance_is_persisted_once_and_does_not_mutate_draft(client: TestClient) -> None:
    project = create_creation_project(client)
    view = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    initialized = client.post(
        f"/api/v1/projects/{project['id']}/creative-conversation:initialize",
        json={"command_id": "initialize-creative-001", "expected_base_version_id": view["active_requirement"]["id"]},
    )
    assert initialized.status_code == 201
    assert initialized.json()["status"] == "no_change"
    after = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert after["initialization_status"] == "succeeded"
    assert after["current_candidate"] is None
    assert len(after["messages"]) == 1
    assert after["messages"][0]["role"] == "assistant"
    assert len(after["active_creative_proposal"]["suggestion_sets"][0]["options"]) == 3
    assert after["active_creative_proposal"]["creative_diagnosis"]["focus_field"] == "creative_direction"
    assert after["active_creative_proposal"]["creative_diagnosis"]["stage"] == "exploring"
    assert all(
        option["proposed_updates"][0]["value"] == option["label"]
        for option in after["active_creative_proposal"]["suggestion_sets"][0]["options"]
    )
    repeated = client.post(
        f"/api/v1/projects/{project['id']}/creative-conversation:initialize",
        json={"command_id": "initialize-creative-002", "expected_base_version_id": view["active_requirement"]["id"]},
    )
    assert repeated.status_code == 409
    assert repeated.headers["x-error-code"] == "CREATIVE_CONVERSATION_ALREADY_INITIALIZED"
    proposal = after["active_creative_proposal"]
    suggestion_set = proposal["suggestion_sets"][0]
    selected = client.post(
        f"/api/v1/projects/{project['id']}/creative-proposals/{proposal['id']}:select",
        json={
            "command_id": "initialize-select-001",
            "expected_base_version_id": view["active_requirement"]["id"],
            "suggestion_set_id": suggestion_set["id"],
            "option_id": suggestion_set["options"][0]["id"],
            "confirm_model_cost": True,
        },
    )
    assert selected.status_code == 201
    assert selected.json()["fields"]["creative_direction"] == "真实记录"


def test_creative_diagnosis_focus_must_match_suggestion(client: TestClient) -> None:
    class MismatchedDiagnosisGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            latest = manifest_payload["conversation"]["messages"][-1]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "我建议先确定内容结构。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "shaping",
                    "summary": "当前需要继续形成内容框架。", "established_fields": ["core_topic"],
                    "open_gaps": [{"field_key": "target_audience", "reason": "受众会影响表达重点。"}],
                    "focus_field": "target_audience", "focus_reason": "需要先知道内容主要给谁看。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [{
                    "category": "content_direction", "title": "选择内容结构",
                    "field_key": "content_structure", "source_message_ids": [latest["id"]],
                    "options": [
                        {"label": "过程记录", "summary": "按过程推进。", "value": "过程记录"},
                        {"label": "结果对比", "summary": "突出前后变化。", "value": "结果对比"},
                    ],
                }],
                "proposal_selections": [], "explicit_updates": [], "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "mismatch", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "diagnosis-message-001", "content": "帮我继续完善这个主题。",
    })
    app.dependency_overrides[get_creative_agent_gateway] = lambda: MismatchedDiagnosisGateway()

    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "diagnosis-generate-001", "expected_base_version_id": initial["active_requirement"]["id"]},
    )

    assert generated.status_code == 502
    assert generated.headers["x-error-code"] == "AGENT_MODEL_DIAGNOSIS_SUGGESTION_MISMATCH"


def test_creative_constraints_validation_reports_exact_shape_error() -> None:
    with pytest.raises(AgentGatewayError, match="必须是文本列表"):
        _validated_update_value("creative_constraints", "不要出现字幕")
    with pytest.raises(AgentGatewayError, match="共有 21 项"):
        _validated_update_value("creative_constraints", [f"限制 {index}" for index in range(21)])
    with pytest.raises(AgentGatewayError, match="第 2 项"):
        _validated_update_value("creative_constraints", ["不要字幕", " "])


def test_creative_constraints_are_not_a_required_gap_or_suggestion(client: TestClient) -> None:
    class ConstraintGapGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            latest = manifest_payload["conversation"]["messages"][-1]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "接下来选择创作限制。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "refining",
                    "summary": "其他需求已经明确。", "established_fields": ["core_topic"],
                    "open_gaps": [{"field_key": "creative_constraints", "reason": "继续补充限制。"}],
                    "focus_field": "creative_constraints", "focus_reason": "需要补充限制。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [{
                    "category": "constraints", "title": "选择限制",
                    "field_key": "creative_constraints", "source_message_ids": [latest["id"]],
                    "options": [
                        {"label": "不要字幕", "summary": "画面不出现字幕。", "value": ["不要字幕"]},
                        {"label": "不要品牌", "summary": "画面不出现品牌。", "value": ["不要品牌"]},
                    ],
                }],
                "proposal_selections": [], "explicit_updates": [], "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "constraint-gap", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "constraint-gap-message-001", "content": "其他内容已经可以了。",
    })
    app.dependency_overrides[get_creative_agent_gateway] = lambda: ConstraintGapGateway()

    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "constraint-gap-generate-001", "expected_base_version_id": initial["active_requirement"]["id"]},
    )

    assert generated.status_code == 502
    assert generated.headers["x-error-code"] == "AGENT_MODEL_CONSTRAINT_GAP_FORBIDDEN"
    with SessionLocal() as session:
        run = session.scalar(select(AgentRun).where(AgentRun.project_id == project["id"]))
        assert run is not None
        assert run.raw_output["creative_diagnosis"]["focus_field"] == "creative_constraints"


def test_creative_constraints_cannot_be_suggested_by_the_agent(client: TestClient) -> None:
    class ConstraintSuggestionGateway(DeterministicCreativeAgentGateway):
        def invoke(self, selection, manifest_payload):
            latest = manifest_payload["conversation"]["messages"][-1]
            output = CreativeAgentOutput.model_validate({
                "assistant_reply": "先选择内容方向。",
                "creative_diagnosis": {
                    "project_type": "personal_record", "stage": "exploring",
                    "summary": "需要明确内容方向。", "established_fields": ["core_topic"],
                    "open_gaps": [{"field_key": "creative_direction", "reason": "尚未选择内容方向。"}],
                    "focus_field": "creative_direction", "focus_reason": "先确定内容方向。",
                    "source_message_ids": [latest["id"]],
                },
                "suggestion_sets": [
                    {
                        "category": "direction", "title": "选择内容方向",
                        "field_key": "creative_direction", "source_message_ids": [latest["id"]],
                        "options": [
                            {"label": "过程记录", "summary": "记录真实过程。", "value": "过程记录"},
                            {"label": "经验分享", "summary": "分享可复用经验。", "value": "经验分享"},
                        ],
                    },
                    {
                        "category": "constraints", "title": "选择创作限制",
                        "field_key": "creative_constraints", "source_message_ids": [latest["id"]],
                        "options": [
                            {"label": "不要字幕", "summary": "画面不出现字幕。", "value": ["不要字幕"]},
                            {"label": "不要品牌", "summary": "画面不出现品牌。", "value": ["不要品牌"]},
                        ],
                    },
                ],
                "proposal_selections": [], "explicit_updates": [], "clarifying_question": None,
            })
            return CreativeAgentResult(output, output.model_dump(mode="json"), "constraint-suggestion", {"total_tokens": 1})

    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    client.post(f"/api/v1/projects/{project['id']}/messages", json={
        "command_id": "constraint-suggestion-message-001", "content": "帮我继续完善这个主题。",
    })
    app.dependency_overrides[get_creative_agent_gateway] = lambda: ConstraintSuggestionGateway()

    generated = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "constraint-suggestion-generate-001", "expected_base_version_id": initial["active_requirement"]["id"]},
    )

    assert generated.status_code == 502
    assert generated.headers["x-error-code"] == "AGENT_MODEL_CONSTRAINT_SUGGESTION_FORBIDDEN"


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
    assert missing_entity.headers["x-error-code"] == "ENTITY_BINDING_MODE_REQUIRED"
    assert client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["attachments"][0]["id"] == attachment["id"]

    bound = client.post(
        f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/bindings",
        json={
            "command_id": "binding-command-002",
            "binding_type": "identity_reference",
            "create_new_entity": True,
            "entity_display_name": "运动员参考",
        },
    )
    assert bound.status_code == 201
    assert bound.json()["confirmed_by"] == "local-user"
    assert bound.json()["entity_version_id"].startswith("entity_version_")
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["entity_versions"] == [{
        "id": bound.json()["entity_version_id"],
        "entity_id": bound.json()["entity_id"],
        "entity_type": "character",
        "display_name": "运动员参考",
        "version_number": 1,
        "source_attachment_id": attachment["id"],
        "source_mime_type": "image/png",
        "source_attachment_verified": True,
    }]
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "registry-brief-generate-001", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "registry-brief-accept-001", "expected_requirement_version_id": requirement_id},
    )
    shots = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "registry-shots-generate-001",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shots['id']}:accept",
        json={"command_id": "registry-shots-accept-001", "expected_requirement_version_id": requirement_id, "expected_candidate_row_version": shots["row_version"]},
    )
    registry = client.get("/api/v1/entity-registry")
    assert registry.status_code == 200
    view = registry.json()
    assert view["counts"] == {"character": 1, "outfit": 0, "scene": 0, "product": 0, "voice": 0}
    entity = view["entities"][0]
    assert entity["id"] == bound.json()["entity_id"]
    assert entity["id"].startswith("entity_")
    assert entity["project_id"] == project["id"]
    assert entity["active_version_id"] == bound.json()["entity_version_id"]
    version = entity["versions"][0]
    assert version["attributes"] == {"binding_type": "identity_reference"}
    assert version["source_attachment"]["id"] == attachment["id"]
    assert version["bindings"][0]["binding_type"] == "identity_reference"
    assert version["snapshot_references"] == []
    assert [item["shot_code"] for item in version["shot_references"]] == ["SH-001", "SH-002", "SH-003"]
    assert all(item["role"] == "character" for item in version["shot_references"])
    content = client.get(f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"
    assert content.content == png
    other_project = create_creation_project(client)
    cross_project = client.get(f"/api/v1/projects/{other_project['id']}/attachments/{attachment['id']}/content")
    assert cross_project.status_code == 404


def test_attachment_binding_uses_generated_entity_ids_across_projects(client: TestClient) -> None:
    def upload(project_id: str, command_id: str, filename: str) -> dict:
        response = client.post(
            f"/api/v1/projects/{project_id}/attachments",
            data={"command_id": command_id},
            files={"file": (filename, b"\x89PNG\r\n\x1a\n" + command_id.encode(), "image/png")},
        )
        assert response.status_code == 201
        return response.json()

    first_project = create_creation_project(client)
    second_project = create_creation_project(client)
    first_attachment = upload(first_project["id"], "generated-entity-upload-001", "first.png")
    second_attachment = upload(second_project["id"], "generated-entity-upload-002", "second.png")

    first_binding = client.post(
        f"/api/v1/projects/{first_project['id']}/attachments/{first_attachment['id']}/bindings",
        json={
            "command_id": "generated-entity-bind-001",
            "binding_type": "identity_reference",
            "create_new_entity": True,
            "entity_display_name": "第一位人物",
        },
    )
    second_binding = client.post(
        f"/api/v1/projects/{second_project['id']}/attachments/{second_attachment['id']}/bindings",
        json={
            "command_id": "generated-entity-bind-002",
            "binding_type": "identity_reference",
            "create_new_entity": True,
            "entity_display_name": "第二位人物",
        },
    )
    assert first_binding.status_code == second_binding.status_code == 201
    assert first_binding.json()["entity_id"].startswith("entity_")
    assert second_binding.json()["entity_id"].startswith("entity_")
    assert first_binding.json()["entity_id"] != second_binding.json()["entity_id"]

    followup_attachment = upload(first_project["id"], "existing-entity-upload-001", "first-new.png")
    existing_binding = client.post(
        f"/api/v1/projects/{first_project['id']}/attachments/{followup_attachment['id']}/bindings",
        json={
            "command_id": "existing-entity-bind-001",
            "binding_type": "identity_reference",
            "entity_id": first_binding.json()["entity_id"],
        },
    )
    assert existing_binding.status_code == 201
    assert existing_binding.json()["entity_id"] == first_binding.json()["entity_id"]
    assert existing_binding.json()["entity_version_id"] != first_binding.json()["entity_version_id"]

    ambiguous = client.post(
        f"/api/v1/projects/{second_project['id']}/attachments/{second_attachment['id']}/bindings",
        json={
            "command_id": "ambiguous-entity-bind-001",
            "binding_type": "identity_reference",
            "entity_id": second_binding.json()["entity_id"],
            "create_new_entity": True,
            "entity_display_name": "重复选择",
        },
    )
    assert ambiguous.status_code == 409
    assert ambiguous.headers["x-error-code"] == "ENTITY_BINDING_MODE_REQUIRED"


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
        "production_profile": {
            "video_motion_strategy": "adaptive",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
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
    assert planning["next_action"]["incurs_model_cost"] is True

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
    assert brief["brief"]["title"].endswith("内容方案")
    assert [item["beat_code"] for item in brief["brief"]["narrative_beats"]] == ["BEAT_01", "BEAT_02", "BEAT_03"]
    assert sum(item["target_duration_ms"] for item in brief["brief"]["narrative_beats"]) == 30_000
    assert brief["brief"]["audio_mode"] == "off"
    assert all(item["kind"] not in {"voiceover", "dialogue"} for item in brief["brief"]["script_segments"])
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
        "expected_candidate_row_version": shot_candidate["row_version"],
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


def test_content_planner_failure_requires_explicit_exact_retry(client: TestClient) -> None:
    class FailOncePlannerGateway(DeterministicContentPlannerGateway):
        calls = 0

        def invoke(self, selection, manifest_payload):
            self.calls += 1
            if self.calls == 1:
                raise AgentGatewayError(
                    "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID",
                    "invalid planner output",
                    raw_output={"unexpected": True},
                    diagnostics=[{"type": "value_error", "ctx": {"error": ValueError("invalid segment")}}],
                )
            result = super().invoke(selection, manifest_payload)
            output = result.output.model_copy(update={"constraints_carried_forward": []})
            return ContentPlannerResult(output, output.model_dump(mode="json"), result.provider_request_id, result.token_usage)

    gateway = FailOncePlannerGateway()
    app.dependency_overrides[get_content_planner_gateway] = lambda: gateway
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    first = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "planner-failure-command-001", "expected_requirement_version_id": requirement_id},
    )
    assert first.status_code == 502
    assert first.headers["x-error-code"] == "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID"
    second = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "planner-failure-command-002", "expected_requirement_version_id": requirement_id},
    )
    assert second.status_code == 409
    assert second.headers["x-error-code"] == "CONTENT_PLANNER_ALREADY_ATTEMPTED"
    assert gateway.calls == 1
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["next_action"]["code"] == "RETRY_FAILED_CREATIVE_BRIEF"
    assert planning["next_action"]["incurs_model_cost"] is True
    assert planning["latest_planner_run"]["status"] == "failed"
    failed_run_id = planning["latest_planner_run"]["id"]
    failed_manifest_id = planning["latest_planner_run"]["input_manifest_id"]
    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/content-planner-runs/{failed_run_id}:retry",
        json={
            "command_id": "planner-retry-unconfirmed-001",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": failed_run_id,
            "confirm_model_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"
    assert gateway.calls == 1
    retried = client.post(
        f"/api/v1/projects/{project['id']}/content-planner-runs/{failed_run_id}:retry",
        json={
            "command_id": "planner-retry-confirmed-001",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": failed_run_id,
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 201
    assert retried.json()["status"] == "awaiting_review"
    assert gateway.calls == 2
    recovered = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert recovered["next_action"]["code"] == "REVIEW_CREATIVE_BRIEF"
    assert recovered["latest_planner_run"]["status"] == "succeeded"
    assert recovered["latest_planner_run"]["input_manifest_id"] == failed_manifest_id
    with SessionLocal() as session:
        runs = list(session.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project["id"], AgentRun.agent_role == "planner")
            .order_by(AgentRun.started_at)
        ))
        assert [run.status for run in runs] == ["failed", "succeeded"]
        assert runs[0].raw_output == {"unexpected": True}
        assert runs[0].input_manifest_id == runs[1].input_manifest_id


def test_content_planner_contract_upgrade_requires_explicit_regeneration_with_new_manifest(client: TestClient) -> None:
    class ContractUpgradePlannerGateway(DeterministicContentPlannerGateway):
        select_calls = 0
        invoke_calls = 0

        def select(self, session):
            self.select_calls += 1
            current = super().select(session)
            if self.select_calls == 1:
                return replace(
                    current,
                    production_config_version_id="production_config_old_planner",
                    model_config_version_id="model_config_old_planner",
                    provider_config_version_id="provider_config_old_planner",
                    prompt_contract_version="content-planner-prompt.v5",
                    output_schema_version="creative-brief-candidate.v3",
                )
            return current

        def invoke(self, selection, manifest_payload):
            self.invoke_calls += 1
            if self.invoke_calls == 1:
                raise AgentGatewayError(
                    "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID",
                    "旧合同没有返回合法脚本段。",
                    raw_output={"script_segments": [{"kind": "visual_only", "on_screen_text": "非法组合"}]},
                )
            return super().invoke(selection, manifest_payload)

    gateway = ContractUpgradePlannerGateway()
    app.dependency_overrides[get_content_planner_gateway] = lambda: gateway
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]

    failed = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "planner-old-contract-generate", "expected_requirement_version_id": requirement_id},
    )

    assert failed.status_code == 502
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    failed_run = planning["latest_planner_run"]
    assert planning["next_action"]["code"] == "REGENERATE_CREATIVE_BRIEF_WITH_CURRENT_CONTRACT"
    assert planning["next_action"]["incurs_model_cost"] is True
    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/content-planner-runs/{failed_run['id']}:regenerate-with-current-contract",
        json={
            "command_id": "planner-current-contract-unconfirmed",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"
    assert gateway.invoke_calls == 1

    regenerated = client.post(
        f"/api/v1/projects/{project['id']}/content-planner-runs/{failed_run['id']}:regenerate-with-current-contract",
        json={
            "command_id": "planner-current-contract-confirmed",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": True,
        },
    )

    assert regenerated.status_code == 201
    recovered = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert recovered["next_action"]["code"] == "REVIEW_CREATIVE_BRIEF"
    assert recovered["latest_planner_run"]["prompt_contract_version"] == "content-planner-prompt.v7"
    assert recovered["latest_planner_run"]["output_schema_version"] == "creative-brief-candidate.v4"
    assert recovered["latest_planner_run"]["input_manifest_id"] != failed_run["input_manifest_id"]
    with SessionLocal() as session:
        runs = list(session.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project["id"], AgentRun.agent_role == "planner")
            .order_by(AgentRun.started_at)
        ))
        assert [run.status for run in runs] == ["failed", "succeeded"]
        assert runs[0].raw_output == {"script_segments": [{"kind": "visual_only", "on_screen_text": "非法组合"}]}


def test_creative_brief_revision_preserves_requirement_and_supersedes_only_after_success(client: TestClient) -> None:
    class RecordingPlannerGateway(DeterministicContentPlannerGateway):
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def invoke(self, selection, manifest_payload):
            self.calls.append(manifest_payload)
            return super().invoke(selection, manifest_payload)

    gateway = RecordingPlannerGateway()
    app.dependency_overrides[get_content_planner_gateway] = lambda: gateway
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    original = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "brief-revision-generate", "expected_requirement_version_id": requirement_id},
    ).json()

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{original['id']}:revise",
        json={
            "command_id": "brief-revision-unconfirmed",
            "actor_id": "test-editor",
            "expected_requirement_version_id": requirement_id,
            "revision_instruction": "开头更快进入结果",
            "confirm_model_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"
    assert len(gateway.calls) == 1

    revised = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{original['id']}:revise",
        json={
            "command_id": "brief-revision-confirmed",
            "actor_id": "test-editor",
            "expected_requirement_version_id": requirement_id,
            "revision_instruction": "开头更快进入结果",
            "confirm_model_cost": True,
        },
    )
    assert revised.status_code == 201
    candidate = revised.json()
    assert candidate["requirement_version_id"] == requirement_id
    assert candidate["supersedes_candidate_id"] == original["id"]
    assert candidate["revision_number"] == 2
    assert candidate["source"] == "planner_revision"
    assert candidate["created_by"] == "test-editor"
    assert len(gateway.calls) == 2
    request = gateway.calls[-1]["revision_request"]
    assert request["source_candidate_id"] == original["id"]
    assert request["source_revision_number"] == 1
    assert request["source_brief"] == original["brief"]
    assert request["instruction"] == "开头更快进入结果"

    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["active_requirement"]["id"] == requirement_id
    assert planning["current_brief_candidate"]["id"] == candidate["id"]
    history = {item["id"]: item for item in planning["brief_history"]}
    assert history[original["id"]]["status"] == "superseded"
    assert history[candidate["id"]]["status"] == "awaiting_review"


def test_selected_shot_director_revision_is_explicit_and_preserves_source_on_failure(client: TestClient) -> None:
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "ai-revision-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "ai-revision-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    original = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={"command_id": "ai-revision-shots-generate", "expected_requirement_version_id": requirement_id, "creative_brief_candidate_id": brief["id"]},
    ).json()

    class FailingRevisionGateway(DeterministicDirectorGateway):
        def invoke(self, selection, manifest_payload):
            if "revision_request" in manifest_payload:
                raise AgentGatewayError("DIRECTOR_REVISION_TEST_FAILURE", "模拟选中镜头调整失败。")
            return super().invoke(selection, manifest_payload)

    app.dependency_overrides[get_director_gateway] = lambda: FailingRevisionGateway()
    failed = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise-with-director",
        json={"command_id": "ai-revision-fail", "expected_requirement_version_id": requirement_id, "expected_candidate_row_version": original["row_version"], "selected_shot_codes": ["SH-001"], "revision_instruction": "只调整开场构图", "confirm_model_cost": True},
    )
    assert failed.status_code == 502
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["current_shot_candidate"]["id"] == original["id"]
    failed_run_id = planning["latest_director_run"]["id"]

    app.dependency_overrides[get_director_gateway] = lambda: DeterministicDirectorGateway()
    retried = client.post(
        f"/api/v1/projects/{project['id']}/director-runs/{failed_run_id}:retry",
        json={"command_id": "ai-revision-retry", "expected_requirement_version_id": requirement_id, "failed_agent_run_id": failed_run_id, "confirm_model_cost": True},
    )
    assert retried.status_code == 201, retried.text
    revised = retried.json()
    assert revised["source"] == "director_revision"
    assert revised["supersedes_candidate_id"] == original["id"]
    assert revised["shots"] == original["shots"]


def test_failed_brief_revision_keeps_original_and_retries_exact_manifest(client: TestClient) -> None:
    class FailRevisionOnceGateway(DeterministicContentPlannerGateway):
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.failed = False

        def invoke(self, selection, manifest_payload):
            self.calls.append(manifest_payload)
            if manifest_payload.get("revision_request") and not self.failed:
                self.failed = True
                raise AgentGatewayError("CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID", "invalid revision")
            return super().invoke(selection, manifest_payload)

    gateway = FailRevisionOnceGateway()
    app.dependency_overrides[get_content_planner_gateway] = lambda: gateway
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    original = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "failed-revision-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    failed = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{original['id']}:revise",
        json={
            "command_id": "failed-revision-attempt",
            "expected_requirement_version_id": requirement_id,
            "revision_instruction": "减少过程说明",
            "confirm_model_cost": True,
        },
    )
    assert failed.status_code == 502
    assert len(gateway.calls) == 2
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["current_brief_candidate"]["id"] == original["id"]
    assert planning["current_brief_candidate"]["status"] == "awaiting_review"
    assert planning["next_action"]["code"] == "RETRY_FAILED_CREATIVE_BRIEF"
    failed_run = planning["latest_planner_run"]

    retried = client.post(
        f"/api/v1/projects/{project['id']}/content-planner-runs/{failed_run['id']}:retry",
        json={
            "command_id": "failed-revision-retry",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 201
    assert retried.json()["supersedes_candidate_id"] == original["id"]
    assert len(gateway.calls) == 3
    assert gateway.calls[1] == gateway.calls[2]

    with SessionLocal() as session:
        failed_manifest = session.get(AgentInputManifest, failed_run["input_manifest_id"])
        runs = list(session.scalars(
            select(AgentRun)
            .where(AgentRun.project_id == project["id"], AgentRun.agent_role == "planner")
            .order_by(AgentRun.started_at)
        ))
        assert failed_manifest.payload["revision_request"]["source_brief"] == original["brief"]
        assert [run.status for run in runs] == ["succeeded", "failed", "succeeded"]
        assert runs[1].input_manifest_id == runs[2].input_manifest_id


def test_rejecting_brief_reopens_requirements_and_new_confirmation_creates_version(client: TestClient) -> None:
    project = create_creation_project(client)
    initial = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "brief-reject-generate", "expected_requirement_version_id": initial["id"]},
    ).json()
    rejected = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:reject",
        json={
            "command_id": "brief-reject-for-requirement-change",
            "expected_requirement_version_id": initial["id"],
            "reason": "修改基础创作需求",
        },
    )
    assert rejected.status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "collecting_requirements"
    reopened = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()
    assert reopened["active_requirement"]["id"] == initial["id"]

    client.post(
        f"/api/v1/projects/{project['id']}/messages",
        json={"command_id": "brief-change-message", "content": "整体方向改为轻松的训练日记。"},
    )
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "brief-change-candidate", "expected_base_version_id": initial["id"]},
    ).json()
    confirmed = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates/{candidate['id']}:accept",
        json={"command_id": "brief-change-confirm", "expected_base_version_id": initial["id"]},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["id"] != initial["id"]
    assert confirmed.json()["version_number"] == initial["version_number"] + 1


def test_director_failure_requires_explicit_exact_retry(client: TestClient) -> None:
    class FailOnceDirectorGateway(DeterministicDirectorGateway):
        calls = 0

        def invoke(self, selection, manifest_payload):
            self.calls += 1
            if self.calls == 1:
                raise AgentGatewayError("DIRECTOR_OUTPUT_SCHEMA_INVALID", "invalid director output", raw_output={"unexpected": True})
            return super().invoke(selection, manifest_payload)

    gateway = FailOnceDirectorGateway()
    app.dependency_overrides[get_director_gateway] = lambda: gateway
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "director-failure-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "director-failure-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    failed = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "director-failure-generate-001",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    )
    assert failed.status_code == 502
    assert failed.headers["x-error-code"] == "DIRECTOR_OUTPUT_SCHEMA_INVALID"
    repeated = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "director-failure-generate-002",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    )
    assert repeated.status_code == 409
    assert repeated.headers["x-error-code"] == "DIRECTOR_ALREADY_ATTEMPTED"
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["next_action"]["code"] == "RETRY_FAILED_SHOT_PLAN"
    run = planning["latest_director_run"]
    assert run["status"] == "failed"
    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/director-runs/{run['id']}:retry",
        json={
            "command_id": "director-failure-retry-unconfirmed",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": run["id"],
            "confirm_model_cost": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "MODEL_COST_CONFIRMATION_REQUIRED"
    recovered = client.post(
        f"/api/v1/projects/{project['id']}/director-runs/{run['id']}:retry",
        json={
            "command_id": "director-failure-retry-confirmed",
            "expected_requirement_version_id": requirement_id,
            "failed_agent_run_id": run["id"],
            "confirm_model_cost": True,
        },
    )
    assert recovered.status_code == 201
    assert recovered.json()["status"] == "awaiting_review"
    assert gateway.calls == 2
    after = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert after["next_action"]["code"] == "REVIEW_SHOT_PLAN"
    assert after["latest_director_run"]["input_manifest_id"] == run["input_manifest_id"]


def test_content_planner_open_questions_block_candidate_acceptance(client: TestClient) -> None:
    class QuestioningPlannerGateway(DeterministicContentPlannerGateway):
        def invoke(self, selection, manifest_payload):
            result = super().invoke(selection, manifest_payload)
            output = ContentPlannerOutput.model_validate({
                **result.output.model_dump(mode="json"),
                "open_questions": [{
                    "question_code": "QUESTION_01",
                    "prompt": "是否需要明确训练场地？",
                    "reason": "场地会影响可执行动作和画面内容。",
                    "options": [
                        {
                            "option_code": "OPTION_01",
                            "label": "居家训练",
                            "description": "只使用普通室内空间和轻量器材。",
                            "answer": "训练场地使用普通居家空间。",
                        },
                        {
                            "option_code": "OPTION_02",
                            "label": "专业场馆",
                            "description": "使用健身房环境和专业器械。",
                            "answer": "训练场地使用专业健身房。",
                        },
                    ],
                }],
            })
            return ContentPlannerResult(output, output.model_dump(mode="json"), result.provider_request_id, result.token_usage)

    app.dependency_overrides[get_content_planner_gateway] = lambda: QuestioningPlannerGateway()
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "planner-question-command-001", "expected_requirement_version_id": requirement_id},
    ).json()
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "planner-question-accept-001", "expected_requirement_version_id": requirement_id},
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "BRIEF_OPEN_QUESTIONS_UNRESOLVED"
    assert brief["brief"]["open_questions"][0]["options"][0]["label"] == "居家训练"


def test_content_planner_pending_fact_requires_answered_revision_before_acceptance(client: TestClient) -> None:
    class PendingFactPlannerGateway(DeterministicContentPlannerGateway):
        def invoke(self, selection, manifest_payload):
            result = super().invoke(selection, manifest_payload)
            if manifest_payload.get("revision_request"):
                return result
            output = ContentPlannerOutput.model_validate({
                **result.output.model_dump(mode="json"),
                "facts_requiring_confirmation": [{
                    "fact_code": "FACT_01",
                    "statement": "挑战结束后体重下降五公斤",
                    "reason": "已确认需求没有提供真实结果数据。",
                    "resolution_question_code": "QUESTION_01",
                }],
                "open_questions": [{
                    "question_code": "QUESTION_01",
                    "prompt": "结果数据应如何表达？",
                    "reason": "具体数字需要用户提供或明确放弃。",
                    "options": [
                        {
                            "option_code": "OPTION_01",
                            "label": "使用真实数据",
                            "description": "由用户提供可确认的真实结果。",
                            "answer": "使用用户确认的真实结果数据。",
                        },
                        {
                            "option_code": "OPTION_02",
                            "label": "不写具体数字",
                            "description": "只表达过程变化，不声称具体结果。",
                            "answer": "删除未经确认的具体结果数字。",
                        },
                    ],
                }],
            })
            return ContentPlannerResult(output, output.model_dump(mode="json"), result.provider_request_id, result.token_usage)

    app.dependency_overrides[get_content_planner_gateway] = lambda: PendingFactPlannerGateway()
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    original = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "planner-fact-generate", "expected_requirement_version_id": requirement_id},
    ).json()

    blocked = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{original['id']}:accept",
        json={"command_id": "planner-fact-accept-blocked", "expected_requirement_version_id": requirement_id},
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "BRIEF_FACTS_UNCONFIRMED"

    revised = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{original['id']}:revise",
        json={
            "command_id": "planner-fact-resolve",
            "expected_requirement_version_id": requirement_id,
            "revision_instruction": "QUESTION_01 用户选择：删除未经确认的具体结果数字。",
            "confirm_model_cost": True,
        },
    )
    assert revised.status_code == 201
    assert revised.json()["brief"]["facts_requiring_confirmation"] == []
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{revised.json()['id']}:accept",
        json={"command_id": "planner-fact-accept-revised", "expected_requirement_version_id": requirement_id},
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"


def test_shot_plan_revision_creates_a_new_reviewable_candidate(client: TestClient) -> None:
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "revision-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "revision-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    original = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "revision-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    command = {
        "command_id": "revision-shots-revise",
        "actor_id": "test-editor",
        "expected_requirement_version_id": requirement_id,
        "expected_candidate_row_version": original["row_version"],
        "patches": [{
            "target_shot_code": "SH-002",
            "changes": {"action": "展示训练动作细节", "subject_motion": "moderate"},
        }],
    }
    response = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise",
        json=command,
    )
    replay = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise",
        json=command,
    )
    assert response.status_code == 201
    revised = response.json()
    assert replay.json()["id"] == revised["id"]
    assert revised["supersedes_candidate_id"] == original["id"]
    assert revised["revision_number"] == 2
    assert revised["source"] == "user_revision"
    assert revised["agent_run_id"] is None
    assert revised["created_by"] == "test-editor"
    assert revised["shots"][1]["action"] == "展示训练动作细节"
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["current_shot_candidate"]["id"] == revised["id"]
    history = {item["id"]: item for item in planning["shot_plan_history"]}
    assert history[original["id"]]["status"] == "superseded"
    stale_accept = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:accept",
        json={
            "command_id": "revision-stale-accept",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": original["row_version"],
        },
    )
    assert stale_accept.status_code == 409
    assert stale_accept.headers["x-error-code"] == "SHOT_PLAN_NOT_REVIEWABLE"
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{revised['id']}:accept",
        json={
            "command_id": "revision-latest-accept",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": revised["row_version"],
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["shot_plan_candidate_id"] == revised["id"]


def test_rejected_shot_plan_can_be_revised_without_rerunning_director(client: TestClient) -> None:
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "rejected-revision-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "rejected-revision-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    original = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "rejected-revision-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    original_run_id = original["agent_run_id"]
    rejected = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:reject",
        json={
            "command_id": "rejected-revision-shots-reject",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": original["row_version"],
            "reason": "调整具体镜头后再确认",
        },
    )
    assert rejected.status_code == 200

    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["current_shot_candidate"] is None
    assert planning["next_action"] == {
        "code": "REVISE_REJECTED_SHOT_PLAN",
        "label": "调整被拒绝的分镜方案",
        "target_ids": [original["id"]],
        "incurs_model_cost": False,
        "incurs_production_cost": False,
    }

    revised = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise",
        json={
            "command_id": "rejected-revision-shots-revise",
            "actor_id": "test-editor",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": rejected.json()["row_version"],
            "patches": [{
                "target_shot_code": "SH-001",
                "changes": {"action": "调整后的开场动作"},
            }],
        },
    )
    assert revised.status_code == 201
    assert revised.json()["status"] == "awaiting_review"
    assert revised.json()["supersedes_candidate_id"] == original["id"]

    after = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert after["current_shot_candidate"]["id"] == revised.json()["id"]
    assert after["latest_director_run"]["id"] == original_run_id
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "plan_review"
    history = {item["id"]: item for item in after["shot_plan_history"]}
    assert history[original["id"]]["status"] == "superseded"


def test_invalid_shot_plan_revision_does_not_supersede_source(client: TestClient) -> None:
    project = create_creation_project(client)
    requirement_id = client.get(f"/api/v1/projects/{project['id']}/creation-center").json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "invalid-revision-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "invalid-revision-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    original = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "invalid-revision-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    invalid = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise",
        json={
            "command_id": "invalid-revision-command",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": original["row_version"],
            "patches": [{"target_shot_code": "SH-002", "changes": {"sequence_number": 1}}],
        },
    )
    assert invalid.status_code == 409
    assert invalid.headers["x-error-code"] == "SHOT_PLAN_REVISION_INVALID"
    undeclared_reference = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:revise",
        json={
            "command_id": "invalid-primary-reference-command",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": original["row_version"],
            "patches": [{
                "target_shot_code": "SH-001",
                "changes": {"primary_reference_entity_version_id": "entity_version_not_declared"},
            }],
        },
    )
    assert undeclared_reference.status_code == 409
    assert undeclared_reference.headers["x-error-code"] == "SHOT_PLAN_REVISION_INVALID"
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["current_shot_candidate"]["id"] == original["id"]
    assert len(planning["shot_plan_history"]) == 1

    with SessionLocal() as session:
        stored = session.get(ShotPlanCandidate, original["id"])
        shots = [dict(item) for item in stored.shots]
        shots[0].pop("visual_prompt")
        stored.shots = shots
        session.commit()
    rejected = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{original['id']}:accept",
        json={
            "command_id": "missing-visual-prompt-accept",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": original["row_version"],
        },
    )
    assert rejected.status_code == 409
    assert rejected.headers["x-error-code"] == "SHOT_PLAN_VALIDATION_FAILED"


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
                "value_source": "shot.visual_prompt",
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
            "speaking_rate_default": 1.0,
            "voice_presets": [],
            "default_voice_key": None,
            "volume_min": 0,
            "volume_max": 100,
            "volume_default": 50,
            "duration_tolerance_ms": 1500,
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
        json={"command_id": "snapshot-shots-accept-001", "expected_requirement_version_id": requirement_id, "expected_candidate_row_version": shots["row_version"]},
    ).json()
    return project, plan


def test_confirmed_plan_can_open_cancel_and_confirm_explicit_revision(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    requirement_id = plan["requirement_version_id"]
    started = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-revisions",
        json={"command_id": "manual-revision-start-001", "actor_id": "local-user", "expected_plan_version_id": plan["id"]},
    )
    assert started.status_code == 201
    draft = started.json()
    assert draft["status"] == "revision_draft"
    assert draft["source"] == "manual_revision_draft"
    assert draft["shots"] == plan["shots"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "contract_ready"

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-revisions",
        json={"command_id": "manual-revision-start-002", "actor_id": "local-user", "expected_plan_version_id": plan["id"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.headers["x-error-code"] == "SHOT_PLAN_REVISION_ALREADY_OPEN"

    cancelled = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{draft['id']}:cancel-revision",
        json={"command_id": "manual-revision-cancel-001", "actor_id": "local-user", "expected_candidate_row_version": draft["row_version"]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    draft = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-revisions",
        json={"command_id": "manual-revision-start-003", "actor_id": "local-user", "expected_plan_version_id": plan["id"]},
    ).json()
    target = draft["shots"][0]
    revised = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{draft['id']}:revise",
        json={
            "command_id": "manual-revision-save-001",
            "actor_id": "local-user",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": draft["row_version"],
            "patches": [{"target_shot_code": target["shot_code"], "changes": {"action": f"{target['action']}，保持人物身份一致"}}],
        },
    )
    assert revised.status_code == 201
    candidate = revised.json()
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "plan_review"
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{candidate['id']}:accept",
        json={
            "command_id": "manual-revision-accept-001",
            "actor_id": "local-user",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": candidate["row_version"],
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["version_number"] == plan["version_number"] + 1
    with SessionLocal() as session:
        old_plan = session.get(PlanVersion, plan["id"])
        assert old_plan is not None and old_plan.status == "superseded" and old_plan.is_active is False


def production_workflow_assignments(plan: dict, keyframe_slot_id: str, video_slot_id: str) -> list[dict]:
    return [{
        "shot_code": shot["shot_code"],
        "keyframe_workflow_slot_version_id": keyframe_slot_id,
        "video_workflow_slot_version_id": video_slot_id,
    } for shot in plan["shots"]]


def test_decision_impact_graph_uses_frozen_manifest_lineage_without_key_inference(client: TestClient) -> None:
    project = create_creation_project(client)
    resolved = client.post(
        f"/api/v1/projects/{project['id']}/decisions",
        json={
            "key": "visual_style",
            "label": "画面风格",
            "value": "documentary",
            "status": "resolved",
        },
    )
    assert resolved.status_code == 201
    base_requirement_id = client.get(
        f"/api/v1/projects/{project['id']}/creation-center"
    ).json()["active_requirement"]["id"]
    client.post(
        f"/api/v1/projects/{project['id']}/messages",
        json={"command_id": "impact-message", "content": "保持纪实训练风格"},
    )
    requirement_candidate = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates:generate",
        json={"command_id": "impact-requirement-generate", "expected_base_version_id": base_requirement_id},
    ).json()
    requirement = client.post(
        f"/api/v1/projects/{project['id']}/requirement-candidates/{requirement_candidate['id']}:accept",
        json={"command_id": "impact-requirement-accept", "expected_base_version_id": base_requirement_id},
    ).json()
    requirement_id = requirement["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "impact-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "impact-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    shot_plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "impact-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    generated_shot_plan = shot_plan
    shot_plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{generated_shot_plan['id']}:revise",
        json={
            "command_id": "impact-shots-revise",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": generated_shot_plan["row_version"],
            "patches": [{
                "target_shot_code": "SH-002",
                "changes": {"composition": "侧面中景跟拍"},
            }],
        },
    ).json()
    plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shot_plan['id']}:accept",
        json={"command_id": "impact-shots-accept", "expected_requirement_version_id": requirement_id, "expected_candidate_row_version": shot_plan["row_version"]},
    ).json()
    with SessionLocal() as session:
        pending = Decision(
            project_id=project["id"],
            key="character_identity",
            label="人物身份",
            status="pending",
            source="user",
        )
        session.add(pending)
        session.flush()
        pending_id = pending.id
        entity = Entity(
            id="impact-character",
            project_id=project["id"],
            entity_type="character",
            display_name="训练者",
        )
        session.add(entity)
        session.flush()
        version = EntityVersion(
            id="impact-character-v1",
            project_id=project["id"],
            entity_id=entity.id,
            version_number=1,
            status="confirmed",
            is_active=True,
        )
        session.add(version)
        first_shot = session.scalar(select(Shot).where(
            Shot.project_id == project["id"],
            Shot.plan_version_id == plan["id"],
            Shot.sequence_number == 1,
        ))
        first_shot.character_entity_version_ids = [version.id]
        session.commit()
        events_before = len(list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"]
        ))))

    response = client.get(f"/api/v1/projects/{project['id']}/decision-impact-graph")
    assert response.status_code == 200
    graph = response.json()
    assert graph["scope"] == "observed_lineage"
    summaries = {item["decision_id"]: item for item in graph["decisions"]}
    resolved_summary = summaries[resolved.json()["id"]]
    pending_summary = summaries[pending_id]
    assert resolved_summary["observation_status"] == "observed"
    assert resolved_summary["current_value"] == "documentary"
    assert len(resolved_summary["direct_manifest_ids"]) == 3
    assert resolved_summary["downstream_counts"]["requirement_candidate"] == 1
    assert resolved_summary["downstream_counts"]["requirement_version"] == 1
    assert resolved_summary["downstream_counts"]["shot_plan"] == 2
    assert resolved_summary["downstream_counts"]["plan"] == 1
    assert resolved_summary["downstream_counts"]["shot"] == 3
    assert resolved_summary["downstream_counts"]["entity_version"] == 1
    assert resolved_summary["downstream_counts"]["entity"] == 1
    assert pending_summary["observation_status"] == "not_observed"
    assert pending_summary["direct_manifest_ids"] == []
    assert pending_summary["downstream_node_ids"] == []
    plan_node = next(item for item in graph["nodes"] if item["record_id"] == plan["id"])
    assert plan_node["record_type"] == "plan"
    assert any(
        edge["source_node_id"] == f"shot_plan:{generated_shot_plan['id']}"
        and edge["target_node_id"] == f"shot_plan:{shot_plan['id']}"
        and edge["relation"] == "superseded_by"
        for edge in graph["edges"]
    )
    assert "不会按决策名称推断" in graph["boundary"]
    analyze_command = {
        "command_id": "decision-change-impact-analyze",
        "actor_id": "impact-reviewer",
        "proposed_value": "cinematic",
    }
    analyzed = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{resolved.json()['id']}/change-impact-analyses",
        json=analyze_command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{resolved.json()['id']}/change-impact-analyses",
        json=analyze_command,
    )
    assert analyzed.status_code == 201
    analysis = analyzed.json()
    assert replayed.json()["id"] == analysis["id"]
    mismatched_replay = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{resolved.json()['id']}/change-impact-analyses",
        json={**analyze_command, "proposed_value": "illustration"},
    )
    assert mismatched_replay.status_code == 409
    assert mismatched_replay.headers["x-error-code"] == "COMMAND_REPLAY_MISMATCH"
    assert analysis["status"] == "completed"
    assert analysis["current_value"] == "documentary"
    assert analysis["proposed_value"] == "cinematic"
    assert analysis["target_counts"]["shot"] == 3
    assert analysis["target_counts"]["entity_version"] == 1
    assert analysis["target_counts"]["entity"] == 1
    assert analysis["estimated_work_count"] == 0
    assert analysis["cost_status"] == "not_applicable"
    assert analysis["estimated_cost"] is None
    assert all(item["impact_kind"] == "review_candidate" for item in analysis["targets"])
    assert all(item["included_in_estimate"] is False for item in analysis["targets"])
    workspace = client.get(
        f"/api/v1/projects/{project['id']}/decision-change-impact-analyses"
    ).json()
    assert [item["id"] for item in workspace["analyses"]] == [analysis["id"]]
    assert "不会创建重做或重试任务" in workspace["boundary"]
    unchanged = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{resolved.json()['id']}/change-impact-analyses",
        json={"command_id": "decision-change-impact-unchanged", "proposed_value": "documentary"},
    )
    assert unchanged.status_code == 409
    assert unchanged.headers["x-error-code"] == "DECISION_VALUE_UNCHANGED"
    with SessionLocal() as session:
        events_after = len(list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"]
        ))))
        stored_decision = session.get(Decision, resolved.json()["id"])
        assert stored_decision.value == "documentary"
        assert len(list(session.scalars(select(CostEvent).where(CostEvent.project_id == project["id"])))) == 0
        assert len(list(session.scalars(select(WorkItem).where(WorkItem.project_id == project["id"])))) == 0
    assert events_after == events_before + 1


def test_unobserved_decision_change_analysis_persists_insufficient_evidence(client: TestClient) -> None:
    project = create_creation_project(client)
    decision = client.post(
        f"/api/v1/projects/{project['id']}/decisions",
        json={
            "key": "visual_style",
            "label": "画面风格",
            "value": "documentary",
            "status": "resolved",
        },
    ).json()
    response = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{decision['id']}/change-impact-analyses",
        json={"command_id": "unobserved-impact-analysis", "proposed_value": "cinematic"},
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["status"] == "insufficient_evidence"
    assert analysis["observed_manifest_ids"] == []
    assert analysis["target_counts"] == {}
    assert analysis["targets"] == []
    assert analysis["cost_status"] == "not_applicable"


def publish_visual_production_configuration(
    client: TestClient,
    with_pricing: bool = False,
    adapter_kind: str = "mock",
    runtime_pricing: bool = False,
    reference_required: bool = False,
    with_voiceover: bool = False,
    command_prefix: str = "snapshot-config",
) -> dict:
    configuration = valid_system_configuration()
    configuration["providers"][0]["adapter_kind"] = adapter_kind
    configuration["providers"][0]["capabilities"].append("video_generation")
    if reference_required:
        configuration["workflow_slots"][0]["node_info_list"].append({
            "node_id": "reference",
            "field_path": "image",
            "value_source": "reference_image.primary",
            "value_type": "image",
            "required": True,
        })
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
    if with_voiceover:
        configuration["providers"].append({
            "provider_key": "dashscope_cosyvoice",
            "display_name": "CosyVoice",
            "adapter_kind": "cosyvoice",
            "base_url": "https://dashscope.aliyuncs.com",
            "api_key": "test-cosyvoice-key",
            "capabilities": ["tts"],
            "request_timeout_seconds": 60,
            "poll_interval_seconds": 5,
            "max_concurrency": 1,
        })
        configuration["workflow_slots"].append({
            "slot_key": "cosyvoice_voiceover",
            "display_name": "CosyVoice 旁白",
            "operation_kind": "tts",
            "provider_key": "dashscope_cosyvoice",
            "provider_workflow_id": "cosyvoice-v1",
            "input_schema_version": "cosyvoice-tts-input.v2",
            "output_schema_version": "cosyvoice-wav-output.v1",
            "node_info_list": [
                {"node_id": "input", "field_path": "text", "value_source": "input_contract.voiceover_text", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "voice", "value_source": "input_contract.voice.provider_voice_id", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "rate", "value_source": "input_contract.speaking_rate", "value_type": "number", "required": True},
                {"node_id": "input", "field_path": "volume", "value_source": "input_contract.volume", "value_type": "integer", "required": True},
                {"node_id": "input", "field_path": "format", "value_source": "literal:wav", "value_type": "string", "required": True},
                {"node_id": "input", "field_path": "sample_rate", "value_source": "literal:24000", "value_type": "integer", "required": True},
            ],
            "supported_video_spec_keys": [],
        })
        configuration["audio"] = {
            **configuration["audio"],
            "display_name": "版本化旁白",
            "supported_modes": ["off", "voiceover"],
            "tts_workflow_slot_key": "cosyvoice_voiceover",
            "voice_presets": [{
                "key": "steady_male",
                "display_name": "沉稳男声",
                "provider_voice_id": "longxiaocheng",
                "description": "稳定、可信，适合解说",
                "preview_text": "片场 V2 配音试听。",
            }],
            "default_voice_key": "steady_male",
            "sample_rate": 24000,
            "channels": 1,
            "speaking_rate_min": 0.8,
            "speaking_rate_max": 1.2,
            "speaking_rate_default": 1.0,
            "volume_min": 20,
            "volume_max": 80,
            "volume_default": 50,
            "duration_tolerance_ms": 1200,
            "loudness_target": -16,
        }
    if with_pricing:
        configuration["pricing"] = {
            "catalog_key": "visual_pricing_cny",
            "display_name": "视觉生产价格",
            "currency": "CNY",
            "confirmation_threshold": 0.5,
            "rules": [
                {"workflow_slot_key": "keyframe_image", "unit": "call", "unit_price": 0.1},
                {
                    "workflow_slot_key": "first_frame_video",
                    "unit": "runtime_second" if runtime_pricing else "output_second",
                    "unit_price": 0.02,
                    **({"estimated_runtime_seconds": 12} if runtime_pricing else {}),
                },
            ],
        }
    draft_response = client.post("/api/v1/system-config/versions", json={
        "command_id": f"{command_prefix}-create-001",
        "configuration": configuration,
    })
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    ready = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:validate",
        json={"command_id": f"{command_prefix}-validate-001", "expected_row_version": draft["row_version"]},
    ).json()
    response = client.post(
        f"/api/v1/system-config/versions/{draft['id']}:publish",
        json={"command_id": f"{command_prefix}-publish-001", "expected_row_version": ready["row_version"], "confirm_high_risk_changes": True},
    )
    assert response.status_code == 200
    return response.json()


def test_voiceover_impact_freezes_exact_voice_rate_volume_and_rejects_invalid_selection(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    with SessionLocal() as session:
        stored = session.get(PlanVersion, plan["id"])
        assert stored is not None
        first_beat = stored.creative_brief["narrative_beats"][0]["beat_code"]
        stored.creative_brief = {
            **stored.creative_brief,
            "audio_mode": "voiceover",
            "script_segments": [{
                "segment_code": "SEG_VOICE_01",
                "beat_code": first_beat,
                "kind": "voiceover",
                "spoken_text": "这是冻结进生产快照的旁白。",
                "on_screen_text": None,
            }],
        }
        session.commit()
    config = publish_visual_production_configuration(
        client,
        with_voiceover=True,
        command_prefix="voiceover-execution-config",
    )
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation")
    assert preparation.status_code == 200
    published_audio = next(
        item for item in preparation.json()["published_configurations"]
        if item["id"] == config["id"]
    )["audio_config"]
    assert published_audio["voice_presets"] == [{
        "key": "steady_male",
        "display_name": "沉稳男声",
        "provider_voice_id": "longxiaocheng",
        "description": "稳定、可信，适合解说",
        "preview_text": "片场 V2 配音试听。",
    }]
    assert published_audio["default_voice_key"] == "steady_male"
    assert published_audio["speaking_rate_range"] == {"min": 0.8, "max": 1.2}
    assert published_audio["volume_range"] == {"min": 20, "max": 80}
    assert published_audio["duration_tolerance_ms"] == 1200
    base = {
        "plan_version_id": plan["id"],
        "production_config_version_id": config["id"],
        "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
        "shot_workflow_assignments": production_workflow_assignments(
            plan,
            components[("workflow_slot", "keyframe_image")]["id"],
            components[("workflow_slot", "first_frame_video")]["id"],
        ),
        "tts_workflow_slot_version_id": components[("workflow_slot", "cosyvoice_voiceover")]["id"],
    }
    missing = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "voiceover-selection-missing", **base},
    )
    assert missing.status_code == 201
    assert "VOICEOVER_EXECUTION_SELECTION_REQUIRED" in {
        item["code"] for item in missing.json()["validation_errors"]
    }
    invalid = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "voiceover-selection-invalid",
            **base,
            "audio_execution": {"voice_key": "unknown_voice", "speaking_rate": 1.3, "volume": 81},
        },
    )
    assert invalid.status_code == 201
    assert {
        "VOICE_PRESET_NOT_IN_AUDIO_CONFIG",
        "SPEAKING_RATE_OUT_OF_RANGE",
        "VOLUME_OUT_OF_RANGE",
    } <= {item["code"] for item in invalid.json()["validation_errors"]}
    valid = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "voiceover-selection-valid",
            **base,
            "audio_execution": {"voice_key": "steady_male", "speaking_rate": 1.1, "volume": 62},
        },
    )
    assert valid.status_code == 201, valid.text
    analysis = valid.json()
    assert analysis["validation_errors"] == []
    voiceover = next(node for node in analysis["manifest"]["dag"]["nodes"] if node["node_key"] == "project.voiceover")
    assert voiceover["input_contract"]["voice"] == {
        "key": "steady_male",
        "display_name": "沉稳男声",
        "provider_voice_id": "longxiaocheng",
    }
    assert voiceover["input_contract"]["speaking_rate"] == 1.1
    assert voiceover["input_contract"]["volume"] == 62
    assert voiceover["input_contract"]["duration_tolerance_ms"] == 1200
    assert voiceover["input_contract"]["loudness_target_lufs"] == -16


def test_voiceover_impact_freezes_authorized_clone_and_rejects_changed_authorization_facts(
    client: TestClient,
) -> None:
    project, plan = create_confirmed_plan(client)
    with SessionLocal() as session:
        stored = session.get(PlanVersion, plan["id"])
        assert stored is not None
        first_beat = stored.creative_brief["narrative_beats"][0]["beat_code"]
        stored.creative_brief = {
            **stored.creative_brief,
            "audio_mode": "voiceover",
            "script_segments": [{
                "segment_code": "SEG_CLONE_01",
                "beat_code": first_beat,
                "kind": "voiceover",
                "spoken_text": "这是经过授权的复刻声音旁白。",
                "on_screen_text": None,
            }],
        }
        session.commit()
    config = publish_visual_production_configuration(
        client,
        with_voiceover=True,
        command_prefix="voice-clone-impact-config",
    )
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    base = {
        "plan_version_id": plan["id"],
        "production_config_version_id": config["id"],
        "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
        "shot_workflow_assignments": production_workflow_assignments(
            plan,
            components[("workflow_slot", "keyframe_image")]["id"],
            components[("workflow_slot", "first_frame_video")]["id"],
        ),
        "tts_workflow_slot_version_id": components[("workflow_slot", "cosyvoice_voiceover")]["id"],
    }
    bootstrap_impact = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "voice-clone-bootstrap-impact-001",
            **base,
            "audio_execution": {"voice_key": "steady_male", "speaking_rate": 1, "volume": 50},
        },
    ).json()
    bootstrap_snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "voice-clone-bootstrap-snapshot-001",
            "impact_analysis_id": bootstrap_impact["id"],
            "analysis_hash": bootstrap_impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    ).json()
    with SessionLocal() as session:
        sample_hash = hashlib.sha256(f"{project['id']}-clone-sample".encode()).hexdigest()
        sample = Asset(
            project_id=project["id"],
            snapshot_id=bootstrap_snapshot["id"],
            output_index=0,
            asset_type="audio",
            role="voice_clone_sample",
            uri=f"runtime://assets/voice-clones/{project['id']}.wav",
            storage_backend="local",
            provider_output_manifest={"authorization_sample": True},
            content_hash=sample_hash,
            mime_type="audio/wav",
            byte_size=1000,
            duration_ms=5000,
            state="approved",
        )
        session.add(sample)
        session.commit()
        sample_id = sample.id
    authorization_response = client.post(
        f"/api/v1/projects/{project['id']}/voice-clone-authorizations",
        json={
            "command_id": "voice-clone-impact-auth-001",
            "authorization_key": "founder_voice",
            "sample_asset_id": sample_id,
            "subject_name": "品牌创始人",
            "provider_voice_id": f"cosyvoice-clone-{project['id']}",
            "authorization_basis": "self",
            "authorization_scope": ["tts", "commercial"],
            "consent_evidence": "本人明确授权该声音样本用于当前项目商业视频旁白复刻，证据编号 CONSENT-CLONE-001。",
            "authorized_by": "品牌创始人本人",
            "valid_from": "2026-07-01T00:00:00+08:00",
            "expires_at": "2027-07-01T00:00:00+08:00",
            "confirm_authority": True,
        },
    )
    assert authorization_response.status_code == 201, authorization_response.text
    authorization = authorization_response.json()
    base = {
        **base,
        "audio_execution": {
            "voice_clone_version_id": authorization["id"],
            "speaking_rate": 1.05,
            "volume": 58,
        },
    }
    valid = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "voice-clone-impact-valid-001", **base},
    )
    assert valid.status_code == 201, valid.text
    assert valid.json()["validation_errors"] == []
    voice = next(
        node for node in valid.json()["manifest"]["dag"]["nodes"]
        if node["node_key"] == "project.voiceover"
    )["input_contract"]["voice"]
    assert voice == {
        "key": "clone.founder_voice.v1",
        "display_name": "品牌创始人",
        "provider_voice_id": f"cosyvoice-clone-{project['id']}",
        "source": "authorized_clone",
        "voice_clone_version_id": authorization["id"],
        "authorization_contract_hash": authorization["contract_hash"],
        "sample_asset_id": sample_id,
        "sample_content_hash": sample_hash,
    }

    revoked = client.post(
        f"/api/v1/projects/{project['id']}/voice-clone-authorizations/{authorization['id']}:revoke",
        json={
            "command_id": "voice-clone-impact-revoke-001",
            "expected_contract_hash": authorization["contract_hash"],
            "reason": "验证撤销后不能创建新的生产影响分析。",
            "confirm_revoke": True,
        },
    )
    assert revoked.status_code == 200
    invalid_revoked = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "voice-clone-impact-revoked-001", **base},
    ).json()
    assert "VOICE_CLONE_AUTHORIZATION_INVALID" in {
        error["code"] for error in invalid_revoked["validation_errors"]
    }

    with SessionLocal() as session:
        stored_authorization = session.get(VoiceCloneAuthorizationVersion, authorization["id"])
        stored_authorization.status = "active"
        stored_authorization.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    invalid_expired = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "voice-clone-impact-expired-001", **base},
    ).json()
    assert "VOICE_CLONE_AUTHORIZATION_INVALID" in {
        error["code"] for error in invalid_expired["validation_errors"]
    }

    with SessionLocal() as session:
        stored_authorization = session.get(VoiceCloneAuthorizationVersion, authorization["id"])
        stored_authorization.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
        stored_sample = session.get(Asset, sample_id)
        stored_sample.content_hash = hashlib.sha256(b"changed-clone-sample").hexdigest()
        session.commit()
    invalid_sample = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "voice-clone-impact-sample-changed-001", **base},
    ).json()
    assert "VOICE_CLONE_AUTHORIZATION_INVALID" in {
        error["code"] for error in invalid_sample["validation_errors"]
    }


def test_production_planner_proposes_routes_and_requires_explicit_acceptance(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    publish_visual_production_configuration(client, with_pricing=True, command_prefix="production-planner-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]
    video_spec_id = config["video_specs"][0]["id"]

    generated = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={
            "command_id": "production-planner-generate-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": video_spec_id,
        },
    )
    assert generated.status_code == 201, generated.text
    candidate = generated.json()
    assert candidate["status"] == "awaiting_review"
    assert [item["shot_code"] for item in candidate["proposed_assignments"]] == [
        shot["shot_code"] for shot in plan["shots"]
    ]
    assert all(item["required_input_sources"] for item in candidate["proposed_assignments"])
    assert client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()["snapshots"] == []

    assignments = [{
        "shot_code": item["shot_code"],
        "keyframe_workflow_slot_version_id": item["keyframe_workflow_slot_version_id"],
        "video_workflow_slot_version_id": item["video_workflow_slot_version_id"],
    } for item in candidate["proposed_assignments"]]
    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates/{candidate['id']}:decide",
        json={
            "command_id": "production-planner-decide-unconfirmed",
            "expected_row_version": candidate["row_version"],
            "accept": True,
            "confirmed_assignments": assignments,
            "confirm_candidate_scope": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "PRODUCTION_PLAN_CONFIRMATION_REQUIRED"

    accepted = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates/{candidate['id']}:decide",
        json={
            "command_id": "production-planner-decide-accepted",
            "expected_row_version": candidate["row_version"],
            "accept": True,
            "confirmed_assignments": assignments,
            "confirm_candidate_scope": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["confirmed_assignments"] == assignments
    after = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    assert after["snapshots"] == []
    assert after["analyses"] == []
    assert after["latest_production_planner_run"]["status"] == "succeeded"


def test_production_planner_rejects_unknown_route_and_only_retries_exact_failure(client: TestClient) -> None:
    class UnknownRouteGateway(DeterministicProductionPlannerGateway):
        calls = 0

        def invoke(self, selection, manifest_payload):
            self.calls += 1
            result = super().invoke(selection, manifest_payload)
            raw = result.output.model_dump(mode="json")
            raw["assignments"][0]["keyframe_workflow_slot_version_id"] = "workflow_slot_unknown"
            output = ProductionPlannerOutput.model_validate(raw)
            return ProductionPlannerResult(output, raw, result.provider_request_id, result.token_usage)

    gateway = UnknownRouteGateway()
    app.dependency_overrides[get_production_planner_gateway] = lambda: gateway
    project, plan = create_confirmed_plan(client)
    publish_visual_production_configuration(client, command_prefix="production-planner-failure-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]
    payload = {
        "command_id": "production-planner-invalid-route-001",
        "plan_version_id": plan["id"],
        "production_config_version_id": config["id"],
        "video_spec_version_id": config["video_specs"][0]["id"],
    }
    failed = client.post(f"/api/v1/projects/{project['id']}/production-plan-candidates:generate", json=payload)
    assert failed.status_code == 502
    assert failed.headers["x-error-code"] == "PRODUCTION_PLANNER_OUTPUT_CONTRACT_INVALID"
    assert gateway.calls == 1
    view = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    assert view["production_plan_candidates"] == []
    failed_run = view["latest_production_planner_run"]
    assert failed_run["status"] == "failed"

    repeated = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={**payload, "command_id": "production-planner-invalid-route-002"},
    )
    assert repeated.status_code == 409
    assert repeated.headers["x-error-code"] == "PRODUCTION_PLANNER_ALREADY_ATTEMPTED"
    assert gateway.calls == 1

    app.dependency_overrides[get_production_planner_gateway] = lambda: DeterministicProductionPlannerGateway()
    retried = client.post(
        f"/api/v1/projects/{project['id']}/production-planner-runs/{failed_run['id']}:retry",
        json={
            "command_id": "production-planner-retry-001",
            "failed_agent_run_id": failed_run["id"],
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == "awaiting_review"


def test_production_planner_preflight_rejects_impossible_routes_without_model_run(client: TestClient) -> None:
    class CountingGateway(DeterministicProductionPlannerGateway):
        calls = 0

        def invoke(self, selection, manifest_payload):
            self.calls += 1
            return super().invoke(selection, manifest_payload)

    gateway = CountingGateway()
    project, plan = create_confirmed_plan(client)
    with SessionLocal() as session:
        shots = list(session.scalars(select(Shot).where(Shot.plan_version_id == plan["id"])))
        for current in shots:
            current.generation_requirements = {
                **(current.generation_requirements or {}),
                "precise_text_required": True,
            }
        manifest_count_before = len(list(session.scalars(select(AgentInputManifest))))
        session.commit()

    app.dependency_overrides[get_production_planner_gateway] = lambda: gateway
    publish_visual_production_configuration(client, command_prefix="production-planner-preflight-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]
    failed = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={
            "command_id": "production-planner-preflight-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": config["video_specs"][0]["id"],
        },
    )

    assert failed.status_code == 409
    assert failed.headers["x-error-code"] == "PRODUCTION_PLAN_NO_FEASIBLE_ROUTE"
    assert "PRODUCTION_PLAN_PRECISE_TEXT_CAPABILITY_MISSING" in failed.json()["detail"]
    assert gateway.calls == 0
    with SessionLocal() as session:
        assert len(list(session.scalars(select(AgentInputManifest)))) == manifest_count_before
        assert session.scalar(select(AgentRun).where(
            AgentRun.project_id == project["id"],
            AgentRun.agent_role == "production_planner",
        )) is None


def test_production_planner_rejects_missing_required_input_sources(client: TestClient) -> None:
    class MissingInputsGateway(DeterministicProductionPlannerGateway):
        def invoke(self, selection, manifest_payload):
            result = super().invoke(selection, manifest_payload)
            raw = result.output.model_dump(mode="json")
            raw["assignments"][0]["required_input_sources"] = ["shot.visual_prompt"]
            output = ProductionPlannerOutput.model_validate(raw)
            return ProductionPlannerResult(output, raw, result.provider_request_id, result.token_usage)

    app.dependency_overrides[get_production_planner_gateway] = lambda: MissingInputsGateway()
    project, plan = create_confirmed_plan(client)
    publish_visual_production_configuration(client, command_prefix="production-planner-input-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]
    failed = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={
            "command_id": "production-planner-missing-input-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": config["video_specs"][0]["id"],
        },
    )
    assert failed.status_code == 502
    assert failed.headers["x-error-code"] == "PRODUCTION_PLANNER_OUTPUT_CONTRACT_INVALID"
    with SessionLocal() as session:
        run = session.scalar(select(AgentRun).where(
            AgentRun.project_id == project["id"], AgentRun.agent_role == "production_planner"
        ))
        assert run is not None
        assert run.status == "failed"
        assert run.raw_output["assignments"][0]["required_input_sources"] == ["shot.visual_prompt"]


def test_production_planner_accepts_required_inputs_in_any_order(client: TestClient) -> None:
    class ReorderedInputsGateway(DeterministicProductionPlannerGateway):
        def invoke(self, selection, manifest_payload):
            result = super().invoke(selection, manifest_payload)
            raw = result.output.model_dump(mode="json")
            raw["assignments"][0]["required_input_sources"].reverse()
            output = ProductionPlannerOutput.model_validate(raw)
            return ProductionPlannerResult(output, raw, result.provider_request_id, result.token_usage)

    app.dependency_overrides[get_production_planner_gateway] = lambda: ReorderedInputsGateway()
    project, plan = create_confirmed_plan(client)
    publish_visual_production_configuration(client, command_prefix="production-planner-reordered-input-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]

    generated = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={
            "command_id": "production-planner-reordered-input-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": config["video_specs"][0]["id"],
        },
    )

    assert generated.status_code == 201, generated.text
    assert generated.json()["status"] == "awaiting_review"


def test_production_planner_rejects_duplicate_required_inputs(client: TestClient) -> None:
    class DuplicateInputsGateway(DeterministicProductionPlannerGateway):
        def invoke(self, selection, manifest_payload):
            result = super().invoke(selection, manifest_payload)
            raw = result.output.model_dump(mode="json")
            raw["assignments"][0]["required_input_sources"].append(
                raw["assignments"][0]["required_input_sources"][0]
            )
            output = ProductionPlannerOutput.model_validate(raw)
            return ProductionPlannerResult(output, raw, result.provider_request_id, result.token_usage)

    app.dependency_overrides[get_production_planner_gateway] = lambda: DuplicateInputsGateway()
    project, plan = create_confirmed_plan(client)
    publish_visual_production_configuration(client, command_prefix="production-planner-duplicate-input-config")
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    config = preparation["published_configurations"][0]

    failed = client.post(
        f"/api/v1/projects/{project['id']}/production-plan-candidates:generate",
        json={
            "command_id": "production-planner-duplicate-input-001",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": config["video_specs"][0]["id"],
        },
    )

    assert failed.status_code == 502
    assert failed.headers["x-error-code"] == "PRODUCTION_PLANNER_OUTPUT_CONTRACT_INVALID"


def test_production_preparation_lists_only_current_published_configuration(client: TestClient) -> None:
    project = create_creation_project(client)
    previous = publish_visual_production_configuration(
        client,
        command_prefix="preparation-previous-config",
    )
    current = publish_visual_production_configuration(
        client,
        command_prefix="preparation-current-config",
    )

    preparation = client.get(
        f"/api/v1/projects/{project['id']}/production-preparation"
    )
    assert preparation.status_code == 200
    choices = preparation.json()["published_configurations"]
    assert [item["id"] for item in choices] == [current["id"]]
    assert previous["status"] == "published"
    assert current["version_number"] > previous["version_number"]


def create_plan_with_explicit_primary_reference(client: TestClient) -> tuple[dict, dict, dict]:
    project = create_creation_project(client)
    content = b"\x89PNG\r\n\x1a\n" + b"primary-reference"
    attachment = client.post(
        f"/api/v1/projects/{project['id']}/attachments",
        data={"command_id": "primary-reference-upload"},
        files={"file": ("primary.png", content, "image/png")},
    ).json()
    binding = client.post(
        f"/api/v1/projects/{project['id']}/attachments/{attachment['id']}/bindings",
        json={
            "command_id": "primary-reference-bind",
            "binding_type": "identity_reference",
            "create_new_entity": True,
            "entity_display_name": "主参考人物",
        },
    ).json()
    requirement_id = client.get(
        f"/api/v1/projects/{project['id']}/creation-center"
    ).json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "primary-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "primary-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "primary-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    assert all(item["primary_reference_entity_version_id"] is None for item in candidate["shots"])
    revised = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{candidate['id']}:revise",
        json={
            "command_id": "primary-shots-revise",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": candidate["row_version"],
            "patches": [{
                "target_shot_code": item["shot_code"],
                "changes": {
                    "character_entity_version_ids": [binding["entity_version_id"]],
                    "face_visibility": "required",
                    "face_subject_entity_version_ids": [binding["entity_version_id"]],
                    "primary_reference_entity_version_id": binding["entity_version_id"],
                    "generation_requirements": {
                        **item["generation_requirements"],
                        "reference_image_required": True,
                        "identity_consistency_required": True,
                    },
                },
            } for item in candidate["shots"]],
        },
    ).json()
    plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{revised['id']}:accept",
        json={
            "command_id": "primary-shots-accept",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": revised["row_version"],
        },
    ).json()
    return project, plan, attachment


def test_character_reference_can_be_bound_to_multiple_shots(client: TestClient) -> None:
    project, plan, _ = create_plan_with_explicit_primary_reference(client)
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    active_plan = next(item for item in planning["plan_history"] if item["id"] == plan["id"])
    shots = active_plan["shots"]
    assert len(shots) > 1
    character_id = shots[0]["primary_reference_entity_version_id"]
    assert character_id
    assert all(item["character_entity_version_ids"] == [character_id] for item in shots)
    assert all(item["face_visibility"] == "required" for item in shots)
    assert all(item["face_subject_entity_version_ids"] == [character_id] for item in shots)
    assert all(item["primary_reference_entity_version_id"] == character_id for item in shots)
    assert all(item["generation_requirements"]["reference_image_required"] for item in shots)
    assert all(item["generation_requirements"]["identity_consistency_required"] for item in shots)


def test_required_primary_reference_blocks_without_guessing(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(
        client,
        with_pricing=True,
        adapter_kind="runninghub",
        reference_required=True,
    )
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "required-reference-impact",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["status"] == "blocked"
    assert [item["code"] for item in analysis["validation_errors"]].count("REQUIRED_PRIMARY_REFERENCE_MISSING") == 3
    assert all(
        node["input_contract"]["reference_image"] is None
        for node in analysis["manifest"]["dag"]["nodes"]
        if node["kind"] == "generate_keyframe"
    )
    with SessionLocal() as session:
        assert session.scalar(select(WorkItem).where(WorkItem.project_id == project["id"])) is None


def test_production_impact_requires_explicit_assignment_for_every_shot(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client, with_pricing=True, adapter_kind="runninghub")
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    assignments = production_workflow_assignments(
        plan,
        components[("workflow_slot", "keyframe_image")]["id"],
        components[("workflow_slot", "first_frame_video")]["id"],
    )[:-1]
    response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "missing-shot-workflow-assignment",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": assignments,
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["status"] == "blocked"
    assert [item["code"] for item in analysis["validation_errors"]].count("SHOT_WORKFLOW_ASSIGNMENT_MISSING") == 1
    assert analysis["manifest"]["dag"]["nodes"] == []


def test_text_to_video_assignment_compiles_without_keyframe_or_parent_edge(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client, with_pricing=True, adapter_kind="runninghub")
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    video = components[("workflow_slot", "first_frame_video")]
    with SessionLocal() as session:
        workflow = session.get(WorkflowSlotVersion, video["id"])
        assert workflow is not None
        workflow.operation_kind = "text_to_video_generation"
        workflow.capability_tags = ["text_to_video", "broll"]
        workflow.node_info_list = [{
            "node_id": "1",
            "field_path": "text",
            "value_source": "shot.visual_prompt",
            "value_type": "string",
            "required": True,
        }]
        session.commit()
    response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "text-to-video-shot-assignments",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": [{
                "shot_code": shot["shot_code"],
                "keyframe_workflow_slot_version_id": None,
                "video_workflow_slot_version_id": video["id"],
            } for shot in plan["shots"]],
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["status"] == "awaiting_confirmation"
    assert {node["kind"] for node in analysis["manifest"]["dag"]["nodes"]} == {
        "generate_t2v_clip",
        "assemble_timeline_contract",
    }
    assert all(edge["input_slot"] == "timeline_input" for edge in analysis["manifest"]["dag"]["edges"])

    created = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "text-to-video-snapshot-command",
            "impact_analysis_id": analysis["id"],
            "analysis_hash": analysis["analysis_hash"],
            "confirm_contract_scope": True,
        },
    )
    assert created.status_code == 201, created.text
    snapshot = created.json()
    assert snapshot["image_phase_required"] is False
    preparation = client.get(
        f"/api/v1/projects/{project['id']}/production-preparation"
    ).json()
    assert preparation["current_snapshot"]["id"] == snapshot["id"]
    assert preparation["snapshots"][0]["id"] == snapshot["id"]
    assert preparation["next_action"]["code"] == "CONFIRM_PRODUCTION_COST"
    locked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json={
            "command_id": "text-to-video-lock-command",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "confirm_high_risk_cost": True,
        },
    )
    assert locked.status_code == 200
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={
            "command_id": "text-to-video-activate-command",
            "expected_contract_hash": snapshot["contract_hash"],
        },
    )
    assert activated.status_code == 200
    activated_snapshot = activated.json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "text-to-video-submit-command",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated_snapshot["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202
    execution = submitted.json()
    assert execution["phases"][0]["status"] == "not_required"
    assert execution["phases"][1]["status"] == "producing"
    assert all(item["status"] == "queued" for item in execution["work_items"])


def test_production_impact_requires_exactly_one_visual_prompt_binding(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(
        client,
        with_pricing=True,
        adapter_kind="runninghub",
    )
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    keyframe = components[("workflow_slot", "keyframe_image")]
    with SessionLocal() as session:
        workflow = session.get(WorkflowSlotVersion, keyframe["id"])
        assert workflow is not None
        workflow.node_info_list = [
            *workflow.node_info_list,
            {
                "node_id": "duplicate-prompt",
                "field_path": "text",
                "value_source": "shot.visual_prompt",
                "value_type": "string",
                "required": True,
            },
        ]
        session.commit()

    response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "duplicate-visual-prompt-impact",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, keyframe["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["status"] == "blocked"
    issue = next(
        item for item in analysis["validation_errors"]
        if item["code"] == "RUNNINGHUB_VISUAL_PROMPT_BINDING_COUNT_INVALID"
    )
    assert issue["actual"] == 2
    with SessionLocal() as session:
        assert session.scalar(select(WorkItem).where(WorkItem.project_id == project["id"])) is None


def test_snapshot_freezes_exact_primary_reference_and_detects_change(client: TestClient) -> None:
    project, plan, attachment = create_plan_with_explicit_primary_reference(client)
    config = publish_visual_production_configuration(
        client,
        with_pricing=True,
        adapter_kind="runninghub",
        reference_required=True,
    )
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    analysis = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "frozen-reference-impact",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    ).json()
    assert analysis["status"] == "awaiting_confirmation"
    references = [
        node["input_contract"]["reference_image"]
        for node in analysis["manifest"]["dag"]["nodes"]
        if node["kind"] == "generate_keyframe"
    ]
    assert len(references) == 3
    assert all(item["attachment_id"] == attachment["id"] for item in references)
    assert all(item["content_hash"] == attachment["content_hash"] for item in references)
    with SessionLocal() as session:
        stored = session.get(Attachment, attachment["id"])
        (TEST_RUNTIME / stored.storage_path).write_bytes(b"\x89PNG\r\n\x1a\nchanged")
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "frozen-reference-snapshot",
            "impact_analysis_id": analysis["id"],
            "analysis_hash": analysis["analysis_hash"],
            "confirm_contract_scope": True,
        },
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "REFERENCE_IMAGE_CHANGED_AFTER_ANALYSIS"


def test_historical_shot_plan_is_not_automatically_upgraded(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    with SessionLocal() as session:
        stored_plan = session.get(PlanVersion, plan["id"])
        stored_plan.contract_schema_version = "shot-plan.v1"
        session.commit()
    config = publish_visual_production_configuration(client, with_pricing=True)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    analysis = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "historical-plan-impact",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    ).json()
    assert analysis["status"] == "blocked"
    assert "SHOT_PLAN_SCHEMA_UNSUPPORTED" in {item["code"] for item in analysis["validation_errors"]}
    with SessionLocal() as session:
        assert session.get(PlanVersion, plan["id"]).contract_schema_version == "shot-plan.v1"


@pytest.mark.parametrize(
    "rule",
    [
        {"workflow_slot_key": "keyframe_image", "unit": "runtime_second", "unit_price": 0.1},
        {"workflow_slot_key": "keyframe_image", "unit": "call", "unit_price": 0.1, "estimated_runtime_seconds": 12},
    ],
)
def test_pricing_runtime_estimate_contract_rejects_ambiguous_rules(client: TestClient, rule: dict) -> None:
    configuration = valid_system_configuration()
    configuration["pricing"] = {
        "catalog_key": "invalid_runtime_pricing",
        "display_name": "Invalid runtime pricing",
        "currency": "CNY",
        "confirmation_threshold": 0,
        "rules": [rule],
    }
    response = client.post("/api/v1/system-config/versions", json={
        "command_id": f"invalid-runtime-pricing-{rule['unit']}",
        "configuration": configuration,
    })
    assert response.status_code == 422


def test_runtime_second_pricing_uses_explicit_workflow_runtime_per_dag_node(client: TestClient) -> None:
    project, plan = create_confirmed_plan(client)
    config = publish_visual_production_configuration(client, with_pricing=True, runtime_pricing=True)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "runtime-second-impact-command",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    )
    assert response.status_code == 201
    impact = response.json()
    assert impact["validation_errors"] == []
    assert impact["execution_blockers"] == []
    assert impact["cost_status"] == "estimated"
    assert impact["estimated_cost"] == pytest.approx(1.02)
    video_nodes = [node for node in impact["manifest"]["dag"]["nodes"] if node["kind"] == "generate_i2v_clip"]
    assert len(video_nodes) == 3
    assert all(node["pricing_unit"] == "runtime_second" for node in video_nodes)
    assert all(node["pricing_quantity"] == 12 for node in video_nodes)
    assert all(node["estimated_cost"] == pytest.approx(0.24) for node in video_nodes)


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
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
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


def test_manual_plan_revision_supersedes_old_locked_snapshot_on_confirmation(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    plan = planning["active_plan"]
    draft = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-revisions",
        json={"command_id": "locked-manual-start", "actor_id": "local-user", "expected_plan_version_id": plan["id"]},
    ).json()
    target = draft["shots"][0]
    revised = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{draft['id']}:revise",
        json={
            "command_id": "locked-manual-save",
            "actor_id": "local-user",
            "expected_requirement_version_id": plan["requirement_version_id"],
            "expected_candidate_row_version": draft["row_version"],
            "patches": [{"target_shot_code": target["shot_code"], "changes": {"action": f"{target['action']}，使用新人物参考"}}],
        },
    )
    assert revised.status_code == 201
    candidate = revised.json()
    accepted = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{candidate['id']}:accept",
        json={
            "command_id": "locked-manual-accept",
            "actor_id": "local-user",
            "expected_requirement_version_id": plan["requirement_version_id"],
            "expected_candidate_row_version": candidate["row_version"],
        },
    )
    assert accepted.status_code == 200
    with SessionLocal() as session:
        old_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        stored_project = session.get(Project, project["id"])
        assert old_snapshot is not None and old_snapshot.status == "superseded"
        assert stored_project is not None and stored_project.active_snapshot_id is None


def create_observed_active_snapshot(client: TestClient) -> tuple[dict, dict, dict]:
    project = create_creation_project(client)
    decision = client.post(
        f"/api/v1/projects/{project['id']}/decisions",
        json={
            "key": "visual_style",
            "label": "画面风格",
            "value": "documentary",
            "status": "resolved",
        },
    ).json()
    requirement_id = client.get(
        f"/api/v1/projects/{project['id']}/creation-center"
    ).json()["active_requirement"]["id"]
    brief = client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates:generate",
        json={"command_id": "observed-brief-generate", "expected_requirement_version_id": requirement_id},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/creative-brief-candidates/{brief['id']}:accept",
        json={"command_id": "observed-brief-accept", "expected_requirement_version_id": requirement_id},
    )
    shot_plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates:generate",
        json={
            "command_id": "observed-shots-generate",
            "expected_requirement_version_id": requirement_id,
            "creative_brief_candidate_id": brief["id"],
        },
    ).json()
    plan = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{shot_plan['id']}:accept",
        json={
            "command_id": "observed-shots-accept",
            "expected_requirement_version_id": requirement_id,
            "expected_candidate_row_version": shot_plan["row_version"],
        },
    ).json()
    config = publish_visual_production_configuration(client, with_pricing=True)
    components = {(item["component_type"], item["key"]): item for item in config["components"]}
    production_impact = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "observed-production-impact",
            "plan_version_id": plan["id"],
            "production_config_version_id": config["id"],
            "video_spec_version_id": components[("video_spec", "vertical_480p")]["id"],
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
            "pricing_catalog_version_id": components[("pricing_catalog", "visual_pricing_cny")]["id"],
        },
    ).json()
    snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "observed-snapshot-create",
            "impact_analysis_id": production_impact["id"],
            "analysis_hash": production_impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    ).json()
    locked = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:lock",
        json={
            "command_id": "observed-snapshot-lock",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "confirm_high_risk_cost": True,
        },
    ).json()
    active = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{locked['id']}:activate",
        json={
            "command_id": "observed-snapshot-activate",
            "expected_contract_hash": locked["contract_hash"],
        },
    ).json()
    return project, decision, active


def test_decision_change_impact_estimates_only_active_frozen_dag_cost(client: TestClient) -> None:
    project, decision, snapshot = create_observed_active_snapshot(client)
    before_project = client.get(f"/api/v1/projects/{project['id']}").json()
    with SessionLocal() as session:
        persisted_project = session.get(Project, project["id"])
        active_snapshot_id = persisted_project.active_snapshot_id
        cost_event_count = len(list(session.scalars(select(CostEvent).where(
            CostEvent.project_id == project["id"]
        ))))
        project_event_count = len(list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"]
        ))))
        assert len(list(session.scalars(select(WorkItem).where(WorkItem.project_id == project["id"])))) == 0

    response = client.post(
        f"/api/v1/projects/{project['id']}/decisions/{decision['id']}/change-impact-analyses",
        json={
            "command_id": "active-decision-change-impact",
            "actor_id": "cost-reviewer",
            "proposed_value": "cinematic",
        },
    )
    assert response.status_code == 201
    analysis = response.json()
    assert analysis["active_snapshot_id"] == snapshot["id"]
    assert analysis["status"] == "completed"
    assert analysis["estimated_work_count"] == 6
    assert analysis["cost_status"] == "estimated"
    assert analysis["estimated_cost"] == pytest.approx(0.9)
    assert analysis["currency"] == "CNY"
    estimated_targets = [item for item in analysis["targets"] if item["included_in_estimate"]]
    assert len(estimated_targets) == 6
    assert {item["record_type"] for item in estimated_targets} == {"dag_node"}
    assert sum(item["estimated_cost"] for item in estimated_targets) == pytest.approx(0.9)
    assert analysis["target_counts"]["dag_node"] == 7
    assert analysis["target_counts"]["snapshot"] == 1

    after_project = client.get(f"/api/v1/projects/{project['id']}").json()
    assert after_project["status"] == before_project["status"]
    with SessionLocal() as session:
        assert session.get(Project, project["id"]).active_snapshot_id == active_snapshot_id
        assert len(list(session.scalars(select(CostEvent).where(
            CostEvent.project_id == project["id"]
        )))) == cost_event_count
        assert len(list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"]
        )))) == project_event_count + 1
        assert len(list(session.scalars(select(WorkItem).where(WorkItem.project_id == project["id"])))) == 0
        stored = session.get(DecisionChangeImpactAnalysis, analysis["id"])
        assert stored.analysis_hash == analysis["analysis_hash"]


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
                session.add(QCReport(
                    project_id=project["id"],
                    snapshot_id=snapshot["id"],
                    asset_id=asset.id,
                    report_number=1,
                    ruleset_version="test-editor-qc.v1",
                    status="passed",
                    analyzer="test",
                ))
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
        "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
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

    duplicate_impact_response = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={"command_id": "production-impact-command-duplicate", **selection},
    )
    assert duplicate_impact_response.status_code == 201
    duplicate_impact = duplicate_impact_response.json()
    assert duplicate_impact["id"] != impact["id"]
    assert duplicate_impact["snapshot_contract_hash"] == impact["snapshot_contract_hash"]
    assert duplicate_impact["snapshot_contract_hash"] == snapshot["contract_hash"]

    duplicate_snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "production-snapshot-command-duplicate",
            "impact_analysis_id": duplicate_impact["id"],
            "analysis_hash": duplicate_impact["analysis_hash"],
            "confirm_contract_scope": True,
        },
    )
    assert duplicate_snapshot.status_code == 409
    assert duplicate_snapshot.headers["x-error-code"] == "PRODUCTION_SNAPSHOT_DUPLICATE"
    assert duplicate_snapshot.json()["detail"] == "相同制作方案已保存为制作方案 1，不能重复创建。"
    preparation = client.get(f"/api/v1/projects/{project['id']}/production-preparation").json()
    assert [item["id"] for item in preparation["snapshots"]] == [snapshot["id"]]


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
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "keyframe_image")]["id"]),
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
            "shot_workflow_assignments": production_workflow_assignments(plan, components[("workflow_slot", "keyframe_image")]["id"], components[("workflow_slot", "first_frame_video")]["id"]),
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
    execution_positions = {item["dag_node_id"]: index for index, item in enumerate(execution["work_items"])}
    for edge in activated.json()["edges"]:
        assert execution_positions[edge["parent_node_id"]] < execution_positions[edge["child_node_id"]]
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
    image_node_count = sum(node["kind"] == "generate_keyframe" for node in activated["nodes"])
    for _ in range(image_node_count):
        assert process_one("test-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    image_items = [item for item in execution["work_items"] if item["kind"] == "generate_keyframe"]
    later_items = [item for item in execution["work_items"] if item["kind"] != "generate_keyframe"]
    assert all(item["status"] == "completed" for item in image_items)
    assert all(item["status"] == "waiting_phase" for item in later_items)
    assert execution["phases"][0]["status"] == "review_required"
    assert execution["phases"][1]["status"] == "waiting_image_approval"
    assert execution["project_status"] == "producing"
    assert execution["snapshot"]["status"] == "submitted"
    assert process_one("test-worker") is False
    assert all(item["attempts"][0]["provider_task_id"] is None for item in image_items)
    assert all(item["attempts"][0]["response_manifest"]["media_created"] is False for item in image_items)
    with SessionLocal() as session:
        cost_events = list(session.scalars(select(CostEvent).where(CostEvent.snapshot_id == snapshot["id"])))
        assert all(item.kind == "estimated" and item.status == "confirmed" for item in cost_events)


def test_rejected_image_blocks_snapshot_and_can_return_to_production_preparation(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "reject-image-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "reject-image-submit-0001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, _ = attach_local_provider_output(project, snapshot, 480, 848)
    response_hash = hashlib.sha256(json.dumps(
        response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json={
            "command_id": "reject-image-register-01",
            "output_index": 0,
            "expected_response_manifest_hash": response_hash,
        },
    ).json()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
        json={"command_id": "reject-image-verify-0001", "expected_row_version": asset["row_version"]},
    ).json()

    rejected = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:reject",
        json={
            "command_id": "reject-image-review-0001",
            "expected_row_version": asset["row_version"],
            "rationale": "同一镜头的器具、机位和构图不连续",
        },
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "archived"
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    assert execution["project_status"] == "blocked"
    assert execution["snapshot"]["status"] == "execution_blocked"
    assert next(row for row in execution["work_items"] if row["id"] == item["id"])["status"] == "completed"
    assert all(
        row["status"] == "cancelled"
        for row in execution["work_items"]
        if row["id"] != item["id"]
    )

    closed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:close-blocked-production",
        json={
            "command_id": "reject-image-close-0001",
            "expected_contract_hash": snapshot["contract_hash"],
            "confirm_return_to_production_preparation": True,
        },
    )
    assert closed.status_code == 200
    assert closed.json()["project_status"] == "contract_ready"
    assert closed.json()["closed_snapshot_status"] == "superseded"


def test_image_phase_requires_exact_approved_assets_before_releasing_video(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "phase-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "phase-submit-command-0001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    ).json()
    image_phase = submitted["phases"][0]
    assert image_phase["total_count"] == 3
    assert all(
        item["status"] == "waiting_phase"
        for item in submitted["work_items"]
        if item["kind"] != "generate_keyframe"
    )

    premature = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:approve-image-phase",
        json={
            "command_id": "phase-premature-command-1",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_image_node_ids": image_phase["expected_node_ids"],
            "approved_asset_ids": ["asset_missing"],
            "confirm_release_video_phase": True,
        },
    )
    assert premature.status_code == 409
    assert premature.headers["x-error-code"] == "IMAGE_PHASE_WORK_INCOMPLETE"

    approved_asset_ids = []
    for index in range(image_phase["total_count"]):
        item, response_manifest, _ = attach_local_provider_output(project, snapshot, 480, 848, node_index=index)
        response_hash = hashlib.sha256(json.dumps(
            response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        asset = client.post(
            f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
            json={
                "command_id": f"phase-register-command-{index:03d}",
                "output_index": 0,
                "expected_response_manifest_hash": response_hash,
            },
        ).json()
        asset = client.post(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
            json={"command_id": f"phase-verify-command-{index:04d}", "expected_row_version": asset["row_version"]},
        ).json()
        candidate = client.post(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}:run-qc",
            json={"command_id": f"phase-qc-command-{index:08d}", "expected_row_version": asset["row_version"]},
        ).json()
        pending = next(
            row for row in client.get(f"/api/v1/projects/{project['id']}/quality-review").json()["assets"]
            if row["id"] == asset["id"]
        )
        approved = client.post(
            f"/api/v1/projects/{project['id']}/assets/{asset['id']}:approve",
            json={
                "command_id": f"phase-approve-command-{index:03d}",
                "expected_row_version": pending["row_version"],
                "qc_report_candidate_id": candidate["id"],
                "rationale": "该关键帧符合当前分镜合同。",
            },
        ).json()
        approved_asset_ids.append(approved["id"])

    ready = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    image_phase = ready["phases"][0]
    assert image_phase["status"] == "ready_to_release"
    assert image_phase["approved_count"] == image_phase["total_count"]
    mismatch = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:approve-image-phase",
        json={
            "command_id": "phase-mismatch-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_image_node_ids": image_phase["expected_node_ids"],
            "approved_asset_ids": approved_asset_ids[:-1],
            "confirm_release_video_phase": True,
        },
    )
    assert mismatch.status_code == 409
    assert mismatch.headers["x-error-code"] == "IMAGE_PHASE_ASSET_LIST_MISMATCH"

    command = {
        "command_id": "phase-release-command-0001",
        "expected_contract_hash": snapshot["contract_hash"],
        "expected_image_node_ids": image_phase["expected_node_ids"],
        "approved_asset_ids": approved_asset_ids,
        "confirm_release_video_phase": True,
    }
    released = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:approve-image-phase",
        json=command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:approve-image-phase",
        json=command,
    )
    assert released.status_code == 200
    execution = released.json()
    assert replayed.status_code == 200
    assert execution["snapshot"]["image_phase_approved_at"]
    assert execution["snapshot"]["image_phase_approval_manifest"]["schema_version"] == "image-phase-approval.v1"
    assert execution["phases"][0]["status"] == "approved"
    assert all(
        item["status"] == "queued"
        for item in execution["work_items"]
        if item["kind"] != "generate_keyframe"
    )
    released_asset = next(
        item for item in client.get(f"/api/v1/projects/{project['id']}/quality-review").json()["assets"]
        if item["id"] == approved_asset_ids[0]
    )
    assert released_asset["approval_revocation"]["allowed"] is False
    assert released_asset["approval_revocation"]["blocker_code"] == "IMAGE_PHASE_ALREADY_RELEASED"
    revoke = client.post(
        f"/api/v1/projects/{project['id']}/assets/{released_asset['id']}:revoke-approval",
        json={
            "command_id": "phase-revoke-command-0001",
            "expected_row_version": released_asset["row_version"],
            "rationale": "图片阶段已放行，不应允许直接撤销。",
        },
    )
    assert revoke.status_code == 409
    assert revoke.headers["x-error-code"] == "IMAGE_PHASE_ALREADY_RELEASED"
    assert process_one("test-worker") is True
    after_first_video = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    completed_videos = [
        item for item in after_first_video["work_items"]
        if item["kind"] == "generate_i2v_clip" and item["status"] == "completed"
    ]
    assert len(completed_videos) == 1


class FakePersistedExternalAdapter:
    adapter_kind = "fake_external"
    display_name = "Fake RunningHub"
    external = True
    execution_enabled = True
    requires_credential = True
    supported_work_kinds = frozenset({"generate_keyframe", "generate_i2v_clip"})

    def __init__(self) -> None:
        self.submit_count = 0
        self.poll_count = 0

    def execute(self, request: ProviderExecutionRequest) -> dict:
        raise AssertionError("External adapter execute() must not be used")

    def submit(self, request: ProviderExecutionRequest) -> ProviderSubmission:
        self.submit_count += 1
        return ProviderSubmission("persisted-task-1", {"schema_version": "fake-submission.v1"})

    def poll(self, request: ProviderExecutionRequest, provider_task_id: str) -> ProviderPollResult:
        assert provider_task_id == "persisted-task-1"
        self.poll_count += 1
        if self.poll_count == 1:
            return ProviderPollResult("running", {"schema_version": "fake-poll.v1", "remote_status": "RUNNING"})
        return ProviderPollResult("succeeded", fake_provider_response(provider_task_id))


class FakeRejectedExternalAdapter(FakePersistedExternalAdapter):
    adapter_kind = "runninghub"

    def submit(self, request: ProviderExecutionRequest) -> ProviderSubmission:
        self.submit_count += 1
        raise ProviderAdapterError(
            "RUNNINGHUB_SUBMISSION_REJECTED",
            "RunningHub 账户余额不足，请充值后重新创建并提交制作方案。",
            {
                "schema_version": "runninghub-submission-rejection.v1",
                "provider": "runninghub",
                "provider_code": "416",
                "message": "TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET",
            },
        )


class FakePollValidationExternalAdapter(FakePersistedExternalAdapter):
    adapter_kind = "runninghub"

    def poll(self, request: ProviderExecutionRequest, provider_task_id: str) -> ProviderPollResult:
        self.poll_count += 1
        raise ProviderAdapterError(
            "RUNNINGHUB_OUTPUT_MIME_INVALID",
            "RunningHub 返回的目标文件格式 image/webp 不在本次冻结存储策略的允许列表中。",
            {
                "schema_version": "runninghub-output-validation.v1",
                "provider": "runninghub",
                "provider_task_id": provider_task_id,
                "remote_status": "SUCCESS",
                "expected_media_type": "image",
                "provider_result_index": 0,
                "detected_mime_type": "image/webp",
                "allowed_mime_types": ["image/png"],
            },
        )


def fake_provider_response(provider_task_id: str) -> dict:
    content = solid_png(480, 848)
    relative = f"providers/fake/{provider_task_id}.png"
    path = TEST_RUNTIME / "assets" / "providers" / "fake" / f"{provider_task_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return {
        "schema_version": "provider-response.v1",
        "media_created": True,
        "outputs": [{
            "uri": f"runtime://assets/{relative}",
            "storage_backend": "local",
            "asset_type": "image",
            "role": "provider_output",
            "mime_type": "image/png",
            "content_hash": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        }],
    }


class FakeCapacityExternalAdapter(FakePersistedExternalAdapter):
    adapter_kind = "fake_external"

    def submit(self, request: ProviderExecutionRequest) -> ProviderSubmission:
        self.submit_count += 1
        return ProviderSubmission(
            f"capacity-task-{self.submit_count}",
            {"schema_version": "fake-submission.v1"},
        )

    def poll(self, request: ProviderExecutionRequest, provider_task_id: str) -> ProviderPollResult:
        assert provider_task_id.startswith("capacity-task-")
        self.poll_count += 1
        if self.poll_count == 1:
            return ProviderPollResult("running", {"schema_version": "fake-poll.v1", "remote_status": "RUNNING"})
        return ProviderPollResult("succeeded", fake_provider_response(provider_task_id))


def test_external_worker_persists_task_id_and_resumes_poll_without_resubmit(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="fake_external")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "external-persist-activate", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "external-persist-submit",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    adapter = FakePersistedExternalAdapter()
    registry = ProviderAdapterRegistry((adapter,))

    assert process_one("external-worker", registry) is True
    with SessionLocal() as session:
        attempt = session.scalar(select(WorkAttempt).where(WorkAttempt.provider_task_id == "persisted-task-1"))
        item = session.get(WorkItem, attempt.work_item_id)
        assert attempt.state == "submitted"
        assert item.status == "in_progress"
        item.available_at = item.created_at
        session.commit()

    assert process_one("external-worker-after-restart", registry) is True
    assert adapter.submit_count == 1
    assert adapter.poll_count == 1
    with SessionLocal() as session:
        attempt = session.scalar(select(WorkAttempt).where(WorkAttempt.provider_task_id == "persisted-task-1"))
        item = session.get(WorkItem, attempt.work_item_id)
        assert attempt.state == "submitted"
        item.available_at = item.created_at
        session.commit()

    assert process_one("external-worker-after-second-restart", registry) is True
    assert adapter.submit_count == 1
    assert adapter.poll_count == 2
    with SessionLocal() as session:
        attempt = session.scalar(select(WorkAttempt).where(WorkAttempt.provider_task_id == "persisted-task-1"))
        item = session.get(WorkItem, attempt.work_item_id)
        assert attempt.state == "completed"
        assert item.status == "completed"
        asset = session.scalar(select(Asset).where(Asset.work_attempt_id == attempt.id))
        assert asset is not None
        assert asset.state == "verified"
        assert asset.content_hash == attempt.response_manifest["outputs"][0]["content_hash"]
        assert (asset.width, asset.height) == (480, 848)
    quality = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    registered = next(asset for asset in quality["assets"] if asset["work_attempt_id"] == attempt.id)
    assert registered["state"] == "verified"
    assert registered["content_hash"]
    assert registered["width"] == 480
    assert registered["height"] == 848


def test_external_worker_waits_for_provider_capacity_before_submitting_next_item(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="fake_external")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "capacity-activate-command", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "capacity-submit-command",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    adapter = FakeCapacityExternalAdapter()
    registry = ProviderAdapterRegistry((adapter,))

    assert process_one("capacity-worker-first", registry) is True
    assert adapter.submit_count == 1
    assert process_one("capacity-worker-full", registry) is False
    assert adapter.submit_count == 1
    with SessionLocal() as session:
        image_items = list(session.scalars(
            select(WorkItem)
            .where(
                WorkItem.snapshot_id == snapshot["id"],
                WorkItem.kind == "generate_keyframe",
            )
            .order_by(WorkItem.priority, WorkItem.created_at, WorkItem.id)
        ))
        assert [item.status for item in image_items].count("in_progress") == 1
        assert [item.status for item in image_items].count("queued") == 2
        active = next(item for item in image_items if item.status == "in_progress")
        active.available_at = active.created_at
        session.commit()

    assert process_one("capacity-worker-poll-running", registry) is True
    assert process_one("capacity-worker-still-full", registry) is False
    assert adapter.submit_count == 1
    with SessionLocal() as session:
        active = session.scalar(select(WorkItem).where(
            WorkItem.snapshot_id == snapshot["id"],
            WorkItem.kind == "generate_keyframe",
            WorkItem.status == "in_progress",
        ))
        assert active is not None
        active.available_at = active.created_at
        session.commit()

    assert process_one("capacity-worker-poll-complete", registry) is True
    assert process_one("capacity-worker-next", registry) is True
    assert adapter.submit_count == 2
    with SessionLocal() as session:
        image_statuses = list(session.scalars(select(WorkItem.status).where(
            WorkItem.snapshot_id == snapshot["id"],
            WorkItem.kind == "generate_keyframe",
        )))
        assert image_statuses.count("completed") == 1
        assert image_statuses.count("in_progress") == 1
        assert image_statuses.count("queued") == 1


def test_external_worker_persists_explicit_submission_rejection_evidence_without_retry(
    client: TestClient,
) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "external-reject-activate", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "external-reject-submit",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    adapter = FakeRejectedExternalAdapter()
    registry = ProviderAdapterRegistry((adapter,))

    assert process_one("external-reject-worker", registry) is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    attempt = next(
        item["attempts"][-1]
        for item in execution["work_items"]
        if item["status"] == "blocked"
    )
    assert attempt["error_code"] == "RUNNINGHUB_SUBMISSION_REJECTED"
    assert "余额不足" in attempt["error_detail"]
    assert attempt["response_manifest"]["provider_code"] == "416"
    assert adapter.submit_count == 1
    blocked_item = next(item for item in execution["work_items"] if item["status"] == "blocked")
    assert len(blocked_item["attempts"]) == 1


def test_external_worker_persists_poll_output_validation_evidence_without_retry(
    client: TestClient,
) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "external-output-activate", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "external-output-submit",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    adapter = FakePollValidationExternalAdapter()
    registry = ProviderAdapterRegistry((adapter,))

    assert process_one("external-output-submit-worker", registry) is True
    with SessionLocal() as session:
        attempt = session.scalar(select(WorkAttempt).where(WorkAttempt.provider_task_id == "persisted-task-1"))
        item = session.get(WorkItem, attempt.work_item_id)
        item.available_at = item.created_at
        session.commit()

    assert process_one("external-output-poll-worker", registry) is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    attempt = next(item["attempts"][-1] for item in execution["work_items"] if item["status"] == "blocked")
    assert attempt["error_code"] == "RUNNINGHUB_OUTPUT_MIME_INVALID"
    assert attempt["response_manifest"]["remote_status"] == "SUCCESS"
    assert attempt["response_manifest"]["detected_mime_type"] == "image/webp"
    assert adapter.submit_count == 1
    assert adapter.poll_count == 1


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
    assert blocked[0]["attempts"][0]["error_code"] == "EXTERNAL_PROVIDER_EXECUTION_DISABLED"
    assert len(blocked[0]["attempts"]) == 1
    assert blocked[0]["attempts"][0]["provider_task_id"] is None
    state = client.get(f"/api/v1/projects/{project['id']}").json()
    assert state["status"] == "blocked"
    assert state["blocked_from_state"] == "producing"
    assert state["state_reason_code"] == "EXTERNAL_PROVIDER_EXECUTION_DISABLED"
    assert state["blocked_responsible_aggregate_type"] == "work_item"
    assert state["blocked_responsible_aggregate_id"] == blocked[0]["id"]
    assert state["blocked_allowed_commands"] == []
    assert state["blocked_at"] is not None


def test_blocked_production_requires_confirmation_and_returns_to_preparation(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "close-blocked-activate", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "close-blocked-submit",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("close-blocked-worker") is True
    before = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    assert before["project_status"] == "blocked"
    preserved_ids = {
        item["id"] for item in before["work_items"]
        if item["status"] in {"blocked", "completed"}
    }

    unconfirmed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:close-blocked-production",
        json={
            "command_id": "close-blocked-unconfirmed",
            "expected_contract_hash": snapshot["contract_hash"],
            "confirm_return_to_production_preparation": False,
        },
    )
    assert unconfirmed.status_code == 409
    assert unconfirmed.headers["x-error-code"] == "BLOCKED_PRODUCTION_CLOSE_CONFIRMATION_REQUIRED"

    command = {
        "command_id": "close-blocked-confirmed",
        "expected_contract_hash": snapshot["contract_hash"],
        "confirm_return_to_production_preparation": True,
    }
    closed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:close-blocked-production",
        json=command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:close-blocked-production",
        json=command,
    )
    assert closed.status_code == 200
    assert replayed.status_code == 200
    result = closed.json()
    assert replayed.json() == result
    assert result["project_status"] == "contract_ready"
    assert result["closed_snapshot_status"] == "superseded"
    assert result["cancelled_work_item_ids"]

    with SessionLocal() as session:
        stored_project = session.get(Project, project["id"])
        stored_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        items = list(session.scalars(select(WorkItem).where(WorkItem.snapshot_id == snapshot["id"])))
        attempts = list(session.scalars(select(WorkAttempt).where(
            WorkAttempt.work_item_id.in_([item.id for item in items])
        )))
        events = list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"],
            ProjectEvent.event_type == "production.blocked_snapshot_closed.v1",
        )))
        assert stored_project is not None and stored_project.active_snapshot_id is None
        assert stored_project.status == "contract_ready"
        assert stored_snapshot is not None and stored_snapshot.status == "superseded"
        assert {item.id for item in items if item.status in {"blocked", "completed"}} == preserved_ids
        assert {item.id for item in items if item.status == "cancelled"} == set(result["cancelled_work_item_ids"])
        assert all(
            attempt.state == "cancelled"
            for attempt in attempts
            if attempt.work_item_id in result["cancelled_work_item_ids"]
        )
        assert len(events) == 1

    preparation = client.get(
        f"/api/v1/projects/{project['id']}/production-preparation"
    ).json()
    assert preparation["current_snapshot"] is None
    assert preparation["snapshots"][0]["id"] == snapshot["id"]
    assert preparation["snapshots"][0]["status"] == "superseded"
    assert preparation["next_action"]["code"] == "ANALYZE_PRODUCTION_IMPACT"

    repeated_impact = client.post(
        f"/api/v1/projects/{project['id']}/production-impact-analyses",
        json={
            "command_id": "close-blocked-repeat-impact",
            "plan_version_id": snapshot["plan_version_id"],
            "production_config_version_id": snapshot["production_config_version_id"],
            **snapshot["selection"],
        },
    )
    assert repeated_impact.status_code == 201
    repeated_analysis = repeated_impact.json()
    assert repeated_analysis["snapshot_contract_hash"] == snapshot["contract_hash"]

    repeated_snapshot = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots",
        json={
            "command_id": "close-blocked-repeat-snapshot",
            "impact_analysis_id": repeated_analysis["id"],
            "analysis_hash": repeated_analysis["analysis_hash"],
            "confirm_contract_scope": True,
        },
    )
    assert repeated_snapshot.status_code == 201
    replacement = repeated_snapshot.json()
    assert replacement["snapshot_number"] == 2
    assert replacement["status"] == "preparing"
    assert replacement["contract_hash"] == snapshot["contract_hash"]

    repeated_preparation = client.get(
        f"/api/v1/projects/{project['id']}/production-preparation"
    ).json()
    assert repeated_preparation["current_snapshot"]["id"] == replacement["id"]
    assert [item["id"] for item in repeated_preparation["snapshots"]] == [
        replacement["id"],
        snapshot["id"],
    ]
    assert repeated_preparation["snapshots"][1]["status"] == "superseded"


def test_blocked_production_cannot_close_while_provider_attempt_is_active(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "active-close-activate", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "active-close-submit",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("active-close-worker") is True
    with SessionLocal() as session:
        active_item = session.scalar(select(WorkItem).where(
            WorkItem.snapshot_id == snapshot["id"],
            WorkItem.status == "queued",
        ))
        assert active_item is not None
        active_attempt = session.get(WorkAttempt, active_item.current_attempt_id)
        assert active_attempt is not None
        active_item.status = "in_progress"
        active_attempt.state = "submitted"
        session.commit()

    denied = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:close-blocked-production",
        json={
            "command_id": "active-close-denied",
            "expected_contract_hash": snapshot["contract_hash"],
            "confirm_return_to_production_preparation": True,
        },
    )
    assert denied.status_code == 409
    assert denied.headers["x-error-code"] == "BLOCKED_PRODUCTION_STILL_EXECUTING"


def test_project_control_exposes_exact_production_route_cost_and_blocker(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "control-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "control-submit-command-001",
            "expected_contract_hash": activated["contract_hash"],
            "expected_estimated_cost": activated["estimated_cost"],
            "expected_currency": activated["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202
    assert process_one("control-test-worker") is True

    response = client.get(f"/api/v1/projects/{project['id']}/control-center")
    assert response.status_code == 200
    control = response.json()
    assert control["persisted_status"] == "blocked"
    assert control["state_reason_code"] == "EXTERNAL_PROVIDER_EXECUTION_DISABLED"
    assert control["blocked_from_state"] == "producing"
    assert control["blocked_responsible_aggregate_type"] == "work_item"
    assert control["evaluated_stage"] == "production"
    assert control["active_plan_version"] == 1
    assert control["active_snapshot_status"] == "execution_blocked"
    active_plan = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()["active_plan"]
    assert control["production_basis"]["requirement"]["id"] == active_plan["requirement_version_id"]
    assert control["production_basis"]["creative_brief"] == active_plan["creative_brief"]
    assert control["production_basis"]["plan"] == {
        "id": active_plan["id"],
        "version_number": active_plan["version_number"],
        "contract_schema_version": active_plan["contract_schema_version"],
        "shot_count": len(active_plan["shots"]),
        "confirmed_at": active_plan["confirmed_at"],
        "confirmed_by": active_plan["confirmed_by"],
    }
    assert control["work_counts"]["blocked"] == 1
    assert control["blocker_count"] >= 1
    work_blocker = next(item for item in control["blockers"] if item["source_type"] == "work_item")
    assert work_blocker["code"] == "EXTERNAL_PROVIDER_EXECUTION_DISABLED"
    assert work_blocker["affected_node_keys"]
    blocked_route = next(item for item in control["routes"] if item["attempt_state"] == "blocked")
    assert control["blocked_responsible_aggregate_id"] == blocked_route["work_item_id"]
    assert blocked_route["provider"] == "mock_visual"
    assert blocked_route["adapter_kind"] == "runninghub"
    assert blocked_route["provider_workflow_id"] == "mock-workflow-not-executable"
    assert blocked_route["provider_task_id"] is None
    assert control["next_action"]["code"] == "VIEW_PRODUCTION_BLOCKERS"
    assert control["next_action"]["path"] == f"/production?project={project['id']}"
    assert control["costs"] == [{
        "currency": "CNY",
        "estimated_confirmed": 0.9,
        "charged_confirmed": 0.0,
        "adjusted_confirmed": 0.0,
        "refunded_confirmed": 0.0,
        "pending_event_count": 0,
    }]
    assert any(event["event_type"] == "production.work_finished.v1" for event in control["recent_events"])
    recent_event = control["recent_events"][0]
    assert recent_event["event_id"].startswith("event_")
    assert recent_event["aggregate_type"]
    assert recent_event["aggregate_id"]
    assert recent_event["correlation_id"]
    assert recent_event["actor_type"]
    assert recent_event["actor_id"]
    assert recent_event["schema_version"] == 1

    with SessionLocal() as session:
        before_ledger_read = (
            len(list(session.scalars(select(ProjectEvent).where(ProjectEvent.project_id == project["id"])))),
            len(list(session.scalars(select(CostEvent).where(CostEvent.project_id == project["id"])))),
        )
    first_ledger = client.get(f"/api/v1/projects/{project['id']}/audit-ledger?limit=2")
    assert first_ledger.status_code == 200
    first_page = first_ledger.json()
    assert first_page["project_id"] == project["id"]
    assert first_page["event_limit"] == 2
    assert first_page["before_sequence"] is None
    assert len(first_page["events"]) == 2
    assert first_page["events"][0]["sequence"] > first_page["events"][1]["sequence"]
    assert first_page["has_more_events"] is True
    assert first_page["next_before_sequence"] == first_page["events"][-1]["sequence"]
    assert first_page["cost_summaries"] == control["costs"]
    priced_nodes = [node for node in activated["nodes"] if node["estimated_cost"] is not None]
    assert len(first_page["cost_events"]) == len(priced_nodes)
    assert {event["kind"] for event in first_page["cost_events"]} == {"estimated"}
    assert {event["status"] for event in first_page["cost_events"]} == {"confirmed"}
    assert round(sum(event["amount"] for event in first_page["cost_events"]), 6) == 0.9

    second_ledger = client.get(
        f"/api/v1/projects/{project['id']}/audit-ledger"
        f"?limit=2&before_sequence={first_page['next_before_sequence']}"
    ).json()
    assert all(
        event["sequence"] < first_page["next_before_sequence"]
        for event in second_ledger["events"]
    )
    assert not ({event["event_id"] for event in first_page["events"]}
                & {event["event_id"] for event in second_ledger["events"]})
    with SessionLocal() as session:
        after_ledger_read = (
            len(list(session.scalars(select(ProjectEvent).where(ProjectEvent.project_id == project["id"])))),
            len(list(session.scalars(select(CostEvent).where(CostEvent.project_id == project["id"])))),
        )
    assert after_ledger_read == before_ledger_read


def test_user_can_exactly_retry_one_failed_production_work_item(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "retry-work-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "retry-work-submit-001",
            "expected_contract_hash": activated["contract_hash"],
            "expected_estimated_cost": activated["estimated_cost"],
            "expected_currency": activated["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    with SessionLocal() as session:
        item = session.scalar(select(WorkItem).where(
            WorkItem.snapshot_id == snapshot["id"],
            WorkItem.status == "queued",
        ))
        attempt = session.get(WorkAttempt, item.current_attempt_id)
        manifest = json.loads(json.dumps(attempt.request_manifest))
        manifest["provider"].pop("credential_ref", None)
        manifest["provider"]["api_key"] = "test-runninghub-key"
        fingerprint = hashlib.sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        item.payload = manifest
        item.request_fingerprint = fingerprint
        attempt.request_manifest = manifest
        attempt.request_fingerprint = fingerprint
        session.commit()
    assert process_one("retry-work-blocking-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    blocked = next(item for item in execution["work_items"] if item["status"] == "blocked")
    failed = blocked["attempts"][-1]
    payload = {
        "command_id": "retry-work-command-001",
        "actor_id": "local-user",
        "expected_contract_hash": snapshot["contract_hash"],
        "failed_attempt_id": failed["id"],
        "expected_request_fingerprint": blocked["request_fingerprint"],
        "confirm_additional_cost": True,
    }

    retried = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}"
        f"/work-items/{blocked['id']}:retry",
        json=payload,
    )
    assert retried.status_code == 202
    result = retried.json()
    assert result["project_status"] == "producing"
    assert result["snapshot"]["status"] == "submitted"
    target = next(item for item in result["work_items"] if item["id"] == blocked["id"])
    assert target["status"] == "queued"
    assert target["error"] is None
    assert target["row_version"] == blocked["row_version"] + 1
    assert len(target["attempts"]) == 2
    retry_attempt = target["attempts"][-1]
    assert retry_attempt["attempt_number"] == 2
    assert retry_attempt["trigger"] == "user_confirmed_retry"
    assert retry_attempt["state"] == "created"
    assert retry_attempt["request_fingerprint"] == failed["request_fingerprint"]
    assert retry_attempt["request_manifest"] == failed["request_manifest"]
    assert target["current_attempt_id"] == retry_attempt["id"]

    repeated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}"
        f"/work-items/{blocked['id']}:retry",
        json=payload,
    )
    assert repeated.status_code == 202
    repeated_target = next(item for item in repeated.json()["work_items"] if item["id"] == blocked["id"])
    assert len(repeated_target["attempts"]) == 2

    with SessionLocal() as session:
        old_attempt = session.get(WorkAttempt, failed["id"])
        assert old_attempt.state == "blocked"
        retry_cost = session.scalar(select(CostEvent).where(
            CostEvent.work_attempt_id == retry_attempt["id"],
        ))
        assert retry_cost is not None
        assert retry_cost.status == "confirmed"
        assert retry_cost.amount > 0
        assert any(
            event.event_type == "production.work_retry_authorized.v1"
            for event in session.scalars(select(ProjectEvent).where(ProjectEvent.project_id == project["id"]))
        )


def test_dependency_retry_batch_freezes_scope_cost_and_retries_exact_requests(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "batch-retry-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "batch-retry-submit-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    with SessionLocal() as session:
        items = list(session.scalars(
            select(WorkItem).where(WorkItem.snapshot_id == snapshot["id"]).order_by(WorkItem.created_at)
        ))[:2]
        for item in items:
            attempt = session.get(WorkAttempt, item.current_attempt_id)
            item.status = "blocked"
            item.error = "TEST_EXPLICIT_FAILURE"
            attempt.state = "blocked"
            attempt.error_code = "TEST_EXPLICIT_FAILURE"
        stored_project = session.get(Project, project["id"])
        stored_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        stored_project.status = "blocked"
        stored_project.blocked_from_state = "producing"
        stored_snapshot.status = "execution_blocked"
        session.commit()
        root_ids = [item.id for item in items]
    analyzed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:analyze",
        json={
            "command_id": "batch-retry-analyze-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "root_work_item_ids": root_ids,
        },
    )
    assert analyzed.status_code == 201, analyzed.text
    batch = analyzed.json()
    assert set(batch["retry_work_item_ids"]) == set(root_ids)
    assert batch["estimated_cost"] > 0
    assert batch["manifest"]["schema_version"] == "production-retry-batch.v1"
    replayed_analysis = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:analyze",
        json={
            "command_id": "batch-retry-analyze-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "root_work_item_ids": root_ids,
        },
    )
    assert replayed_analysis.status_code == 201
    assert replayed_analysis.json()["id"] == batch["id"]
    denied = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:authorize",
        json={
            "command_id": "batch-retry-denied-001",
            "retry_batch_id": batch["id"],
            "expected_analysis_hash": batch["analysis_hash"],
            "expected_retry_work_item_ids": batch["retry_work_item_ids"],
            "expected_request_fingerprints": batch["request_fingerprints"],
            "expected_estimated_cost": batch["estimated_cost"],
            "expected_currency": batch["currency"],
            "confirm_additional_cost": False,
        },
    )
    assert denied.status_code == 409
    authorized = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:authorize",
        json={
            "command_id": "batch-retry-authorize-01",
            "retry_batch_id": batch["id"],
            "expected_analysis_hash": batch["analysis_hash"],
            "expected_retry_work_item_ids": batch["retry_work_item_ids"],
            "expected_request_fingerprints": batch["request_fingerprints"],
            "expected_estimated_cost": batch["estimated_cost"],
            "expected_currency": batch["currency"],
            "confirm_additional_cost": True,
        },
    )
    assert authorized.status_code == 202, authorized.text
    result = authorized.json()
    retried = [item for item in result["work_items"] if item["id"] in root_ids]
    assert all(item["status"] == "queued" for item in retried)
    assert all(item["attempts"][-1]["trigger"] == "user_confirmed_dependency_retry" for item in retried)
    assert all(len(item["attempts"]) == 2 for item in retried)
    replayed_authorization = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:authorize",
        json={
            "command_id": "batch-retry-authorize-01",
            "retry_batch_id": batch["id"],
            "expected_analysis_hash": batch["analysis_hash"],
            "expected_retry_work_item_ids": batch["retry_work_item_ids"],
            "expected_request_fingerprints": batch["request_fingerprints"],
            "expected_estimated_cost": batch["estimated_cost"],
            "expected_currency": batch["currency"],
            "confirm_additional_cost": True,
        },
    )
    assert replayed_authorization.status_code == 202
    replayed_items = [
        item for item in replayed_authorization.json()["work_items"]
        if item["id"] in root_ids
    ]
    assert all(len(item["attempts"]) == 2 for item in replayed_items)


def test_dependency_retry_batch_includes_zero_cost_local_timeline_descendant(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "local-descendant-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "local-descendant-submit-001",
            "expected_contract_hash": activated["contract_hash"],
            "expected_estimated_cost": activated["estimated_cost"],
            "expected_currency": activated["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202, submitted.text
    with SessionLocal() as session:
        nodes = list(session.scalars(select(DAGNode).where(DAGNode.snapshot_id == snapshot["id"])))
        root_node = next(node for node in nodes if node.kind == "generate_keyframe")
        timeline_node = next(node for node in nodes if node.kind == "assemble_timeline_contract")
        root_item = session.scalar(select(WorkItem).where(WorkItem.dag_node_id == root_node.id))
        timeline_item = session.scalar(select(WorkItem).where(WorkItem.dag_node_id == timeline_node.id))
        assert root_item is not None
        assert timeline_item is not None
        for item in (root_item, timeline_item):
            attempt = session.get(WorkAttempt, item.current_attempt_id)
            assert attempt is not None
            item.status = "blocked"
            item.error = "TEST_EXPLICIT_FAILURE"
            attempt.state = "blocked"
            attempt.error_code = "TEST_EXPLICIT_FAILURE"
        stored_project = session.get(Project, project["id"])
        stored_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        stored_project.status = "blocked"
        stored_project.blocked_from_state = "producing"
        stored_snapshot.status = "execution_blocked"
        session.commit()
        root_item_id = root_item.id
        timeline_item_id = timeline_item.id
        root_cost = root_node.estimated_cost

    analyzed = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:analyze",
        json={
            "command_id": "local-descendant-analyze-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "root_work_item_ids": [root_item_id],
        },
    )
    assert analyzed.status_code == 201, analyzed.text
    batch = analyzed.json()
    assert set(batch["retry_work_item_ids"]) == {root_item_id, timeline_item_id}
    retry_items = {item["work_item_id"]: item for item in batch["manifest"]["retry_items"]}
    assert retry_items[timeline_item_id]["kind"] == "assemble_timeline_contract"
    assert retry_items[timeline_item_id]["estimated_cost"] == 0
    assert batch["estimated_cost"] == pytest.approx(root_cost)

    authorized = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}/retry-batches:authorize",
        json={
            "command_id": "local-descendant-authorize-001",
            "retry_batch_id": batch["id"],
            "expected_analysis_hash": batch["analysis_hash"],
            "expected_retry_work_item_ids": batch["retry_work_item_ids"],
            "expected_request_fingerprints": batch["request_fingerprints"],
            "expected_estimated_cost": batch["estimated_cost"],
            "expected_currency": batch["currency"],
            "confirm_additional_cost": True,
        },
    )
    assert authorized.status_code == 202, authorized.text
    retried = {
        item["id"]: item
        for item in authorized.json()["work_items"]
        if item["id"] in {root_item_id, timeline_item_id}
    }
    assert set(retried) == {root_item_id, timeline_item_id}
    assert all(item["status"] == "queued" for item in retried.values())
    assert all(item["attempts"][-1]["trigger"] == "user_confirmed_dependency_retry" for item in retried.values())
    assert all(len(item["attempts"]) == 2 for item in retried.values())
    timeline_attempt_id = retried[timeline_item_id]["attempts"][-1]["id"]
    with SessionLocal() as session:
        timeline_cost = session.scalar(select(CostEvent).where(CostEvent.work_attempt_id == timeline_attempt_id))
        assert timeline_cost is not None
        assert timeline_cost.provider_operation == "project.timeline"
        assert timeline_cost.amount == 0
        assert timeline_cost.status == "confirmed"


def test_local_subtitle_retry_preserves_exact_request_and_confirms_zero_cost(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "subtitle-retry-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    manifest = {
        "schema_version": "production-work-request.v3",
        "snapshot_id": snapshot["id"],
        "contract_hash": snapshot["contract_hash"],
        "dag_node_id": "",
        "node_key": "project.subtitles",
        "kind": "generate_subtitles",
        "input_contract": {"cues": [{"timeline_in_ms": 0, "timeline_out_ms": 1000, "text": "精确字幕重试"}]},
        "output_contract": {"media_type": "subtitle", "format": "srt"},
        "workflow_slot_version_id": None,
        "provider_config_version_id": None,
        "provider": None,
        "provider_key": "local_subtitle",
        "adapter_kind": "local_subtitle",
        "workflow": None,
        "provider_workflow_id": None,
        "video_spec": None,
        "storage_policy": None,
    }
    with SessionLocal() as session:
        node = DAGNode(
            snapshot_id=snapshot["id"],
            node_key="project.subtitles",
            kind="generate_subtitles",
            input_contract=manifest["input_contract"],
            output_contract=manifest["output_contract"],
            workflow_slot_version_id=None,
            pricing_rule_id=None,
            pricing_quantity=None,
            pricing_unit=None,
            estimated_cost=None,
            currency=snapshot["currency"],
        )
        session.add(node)
        session.flush()
        manifest["dag_node_id"] = node.id
        fingerprint = hashlib.sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        item = WorkItem(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            dag_node_id=node.id,
            kind="generate_subtitles",
            payload=manifest,
            status="blocked",
            error="LOCAL_SUBTITLE_TEST_FAILURE",
            request_fingerprint=fingerprint,
        )
        session.add(item)
        session.flush()
        failed = WorkAttempt(
            work_item_id=item.id,
            attempt_number=1,
            trigger="initial_submission",
            provider="local_subtitle",
            request_fingerprint=fingerprint,
            request_manifest=manifest,
            state="blocked",
            error_code="LOCAL_SUBTITLE_TEST_FAILURE",
            error_detail="模拟本地字幕生成失败。",
        )
        session.add(failed)
        session.flush()
        item.current_attempt_id = failed.id
        stored_project = session.get(Project, project["id"])
        stored_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        stored_project.status = "blocked"
        stored_project.blocked_from_state = "producing"
        stored_snapshot.status = "execution_blocked"
        session.commit()
        item_id = item.id
        failed_id = failed.id
        original_row_version = item.row_version

    response = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}"
        f"/work-items/{item_id}:retry",
        json={
            "command_id": "subtitle-retry-command-001",
            "actor_id": "local-user",
            "expected_contract_hash": activated["contract_hash"],
            "failed_attempt_id": failed_id,
            "expected_request_fingerprint": fingerprint,
            "confirm_additional_cost": True,
        },
    )
    assert response.status_code == 202, response.text
    target = next(item for item in response.json()["work_items"] if item["id"] == item_id)
    assert target["status"] == "queued"
    assert target["row_version"] == original_row_version + 1
    assert len(target["attempts"]) == 2
    retry_attempt = target["attempts"][-1]
    assert retry_attempt["attempt_number"] == 2
    assert retry_attempt["trigger"] == "user_confirmed_retry"
    assert retry_attempt["request_manifest"] == manifest
    assert retry_attempt["request_fingerprint"] == fingerprint
    with SessionLocal() as session:
        assert session.get(WorkAttempt, failed_id).state == "blocked"
        retry_cost = session.scalar(select(CostEvent).where(
            CostEvent.work_attempt_id == retry_attempt["id"],
        ))
        assert retry_cost is not None
        assert retry_cost.provider_operation == "generate_subtitles"
        assert retry_cost.amount == 0
        assert retry_cost.status == "confirmed"


def test_production_retry_rejects_legacy_frozen_provider_contract(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client, adapter_kind="runninghub")
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "legacy-retry-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "legacy-retry-submit-001",
            "expected_contract_hash": activated["contract_hash"],
            "expected_estimated_cost": activated["estimated_cost"],
            "expected_currency": activated["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert process_one("legacy-retry-blocking-worker") is True
    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    blocked = next(item for item in execution["work_items"] if item["status"] == "blocked")
    with SessionLocal() as session:
        item = session.get(WorkItem, blocked["id"])
        failed = session.get(WorkAttempt, item.current_attempt_id)
        manifest = json.loads(json.dumps(failed.request_manifest))
        manifest["provider"].pop("api_key")
        manifest["provider"]["credential_ref"] = "env://LEGACY_RUNNINGHUB_KEY"
        fingerprint = hashlib.sha256(
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        item.payload = manifest
        item.request_fingerprint = fingerprint
        failed.request_manifest = manifest
        failed.request_fingerprint = fingerprint
        session.commit()
        failed_attempt_id = failed.id

    denied = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}"
        f"/work-items/{blocked['id']}:retry",
        json={
            "command_id": "legacy-retry-command-001",
            "actor_id": "local-user",
            "expected_contract_hash": snapshot["contract_hash"],
            "failed_attempt_id": failed_attempt_id,
            "expected_request_fingerprint": fingerprint,
            "confirm_additional_cost": True,
        },
    )
    assert denied.status_code == 409
    assert denied.headers["x-error-code"] == "PRODUCTION_RETRY_REQUEST_CONTRACT_UNSUPPORTED"
    with SessionLocal() as session:
        attempts = list(session.scalars(select(WorkAttempt).where(
            WorkAttempt.work_item_id == blocked["id"],
        )))
        assert len(attempts) == 1


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
    candidate = qc.json()
    assert candidate["status"] == "awaiting_review"
    assert candidate["findings"] == []
    review_view = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    pending_asset = next(row for row in review_view["assets"] if row["id"] == asset["id"])
    assert pending_asset["state"] == "review_required"
    assert "project.timeline" in pending_asset["affected_downstream_node_keys"]

    approved = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:approve",
        json={
            "command_id": "quality-approve-command-001",
            "expected_row_version": pending_asset["row_version"],
            "qc_report_candidate_id": candidate["id"],
            "rationale": "Composition and subject continuity are acceptable.",
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"
    assert approved.json()["review_decisions"][0]["decision"] == "approved"
    assert approved.json()["latest_qc_report"]["analyzer"] == "visual-qc.v1"
    content = client.get(f"/api/v1/projects/{project['id']}/assets/{asset['id']}/content")
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("image/png")
    with SessionLocal() as session:
        assert len(list(session.scalars(select(WorkAttempt).where(WorkAttempt.work_item_id == item["id"])))) == 1
        assert session.scalar(select(AssetReviewDecision).where(AssetReviewDecision.asset_id == asset["id"])) is not None


def test_verified_asset_can_be_approved_by_direct_human_review(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "direct-review-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "direct-review-submit-00001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202
    with SessionLocal() as session:
        node = session.scalar(select(DAGNode).where(
            DAGNode.snapshot_id == snapshot["id"],
            DAGNode.kind == "generate_keyframe",
        ))
        assert node is not None
        asset = Asset(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            dag_node_id=node.id,
            output_index=0,
            asset_type="image",
            role="keyframe",
            uri=f"runtime://assets/{project['id']}/direct-review.png",
            storage_backend="local",
            provider_output_manifest={},
            content_hash="a" * 64,
            mime_type="image/png",
            byte_size=1024,
            width=480,
            height=848,
            state="verified",
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

    approved = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:approve",
        json={
            "command_id": "direct-human-review-approve-001",
            "expected_row_version": 1,
            "rationale": "人工确认画面符合分镜合同",
        },
    )
    assert approved.status_code == 200, approved.text
    result = approved.json()
    assert result["state"] == "approved"
    assert result["latest_qc_report"]["analyzer"] == "human-direct-review"
    assert result["latest_qc_report"]["ruleset_version"] == "human-review.v1"
    assert result["review_decisions"][0]["rationale"] == "人工确认画面符合分镜合同"
    assert result["review_context"]["shot"]["shot_code"]
    assert result["approval_revocation"]["allowed"] is True

    revoked = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:revoke-approval",
        json={
            "command_id": "direct-human-review-revoke-001",
            "expected_row_version": result["row_version"],
            "rationale": "用户需要重新检查这一张素材。",
        },
    )
    assert revoked.status_code == 200
    result = revoked.json()
    assert result["state"] == "verified"
    assert result["approved_at"] is None
    assert [item["decision"] for item in result["review_decisions"]] == ["approved", "approval_revoked"]

    approved_again = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:approve",
        json={
            "command_id": "direct-human-review-approve-002",
            "expected_row_version": result["row_version"],
            "rationale": "重新检查后确认画面符合分镜合同。",
        },
    )
    assert approved_again.status_code == 200
    result = approved_again.json()
    assert result["state"] == "approved"
    assert result["latest_qc_report"]["report_number"] == 2

    with SessionLocal() as session:
        timeline = Timeline(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            version_number=1,
            status="candidate",
            source="manual",
            output_spec={},
            track_config={},
            validation_report=[],
            created_by="local-user",
        )
        session.add(timeline)
        session.flush()
        session.add(TimelineItem(
            timeline_id=timeline.id,
            track_type="main_video",
            sequence_number=1,
            asset_id=asset_id,
            label="Referenced approved asset",
            source_in_ms=0,
            source_out_ms=1000,
            timeline_in_ms=0,
            timeline_out_ms=1000,
            transform={},
        ))
        session.commit()

    referenced = next(
        item for item in client.get(f"/api/v1/projects/{project['id']}/quality-review").json()["assets"]
        if item["id"] == asset_id
    )
    assert referenced["approval_revocation"]["allowed"] is False
    assert referenced["approval_revocation"]["blocker_code"] == "ASSET_REFERENCED_BY_TIMELINE"
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:revoke-approval",
        json={
            "command_id": "direct-human-review-revoke-002",
            "expected_row_version": referenced["row_version"],
            "rationale": "不应绕过时间线引用。",
        },
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "ASSET_REFERENCED_BY_TIMELINE"


def test_quality_review_does_not_report_unexecuted_nodes_as_output_gaps(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "quality-gap-activate-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "quality-gap-submit-00001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202

    execution = client.get(f"/api/v1/projects/{project['id']}/production-execution").json()
    assert any(item["status"] == "waiting_phase" for item in execution["work_items"])
    review = client.get(f"/api/v1/projects/{project['id']}/quality-review")
    assert review.status_code == 200
    assert review.json()["output_gaps"] == []


def test_storyboard_asset_revision_creates_explicit_draft_and_new_plan(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "revision-activate-0001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    submitted = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "revision-submit-00001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    assert submitted.status_code == 202
    with SessionLocal() as session:
        node = session.scalar(select(DAGNode).where(
            DAGNode.snapshot_id == snapshot["id"], DAGNode.shot_id.is_not(None)
        ).order_by(DAGNode.node_key))
        assert node is not None
        asset = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], dag_node_id=node.id,
            output_index=0, asset_type="image", role="keyframe", uri=f"runtime://assets/{project['id']}/revision.png",
            storage_backend="local", provider_output_manifest={}, state="review_required",
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

    command = {
        "command_id": "asset-revision-command-001",
        "actor_id": "local-user",
        "expected_asset_row_version": 1,
        "issue_scope": "storyboard",
        "issue_code": "action_mismatch",
        "rationale": "人物动作与已经确认的分镜目标不一致，需要调整这个镜头。",
    }
    created = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision", json=command
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision", json=command
    )
    assert created.status_code == 201
    result = created.json()
    assert replayed.json()["request"]["id"] == result["request"]["id"]
    assert result["request"]["status"] == "draft_created"
    assert result["request"]["shot_code"]
    assert result["next_action"]["draft_candidate_id"]
    request_read = client.get(
        f"/api/v1/projects/{project['id']}/asset-revision-requests/{result['request']['id']}"
    )
    assert request_read.status_code == 200
    assert request_read.json()["issue_code"] == "action_mismatch"
    assert request_read.json()["rationale"] == command["rationale"]
    duplicate_open = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={**command, "command_id": "asset-revision-command-002"},
    )
    assert duplicate_open.status_code == 409
    assert duplicate_open.headers["x-error-code"] == "STORYBOARD_REVISION_ALREADY_OPEN"
    quality = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    stored_asset = next(item for item in quality["assets"] if item["id"] == asset_id)
    assert stored_asset["revision_requests"][0]["id"] == result["request"]["id"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "producing"

    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    draft = planning["revision_draft"]
    assert draft["status"] == "revision_draft"
    assert planning["revision_context"]["id"] == result["request"]["id"]
    assert planning["active_plan"] is not None
    direct_accept = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{draft['id']}:accept",
        json={
            "command_id": "revision-draft-accept-001",
            "expected_requirement_version_id": planning["active_requirement"]["id"],
            "expected_candidate_row_version": draft["row_version"],
        },
    )
    assert direct_accept.status_code == 409
    assert direct_accept.headers["x-error-code"] == "SHOT_PLAN_NOT_REVIEWABLE"

    target = next(shot for shot in draft["shots"] if shot["shot_code"] == result["request"]["shot_code"])
    revised = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{draft['id']}:revise",
        json={
            "command_id": "revision-draft-patch-001",
            "expected_requirement_version_id": planning["active_requirement"]["id"],
            "expected_candidate_row_version": draft["row_version"],
            "patches": [{
                "target_shot_code": target["shot_code"],
                "changes": {"action": f"{target['action']}，并修正动作节奏"},
            }],
        },
    )
    assert revised.status_code == 201
    candidate = revised.json()
    assert candidate["status"] == "awaiting_review"
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "producing"

    accepted = client.post(
        f"/api/v1/projects/{project['id']}/shot-plan-candidates/{candidate['id']}:accept",
        json={
            "command_id": "revision-candidate-accept-001",
            "expected_requirement_version_id": planning["active_requirement"]["id"],
            "expected_candidate_row_version": candidate["row_version"],
        },
    )
    assert accepted.status_code == 200
    new_plan = accepted.json()
    assert new_plan["version_number"] == planning["active_plan"]["version_number"] + 1
    current_project = client.get(f"/api/v1/projects/{project['id']}").json()
    assert current_project["status"] == "contract_ready"
    with SessionLocal() as session:
        stored_project = session.get(Project, project["id"])
        old_snapshot = session.get(ProductionSnapshot, snapshot["id"])
        request = session.get(AssetRevisionRequest, result["request"]["id"])
        assert stored_project is not None and stored_project.active_snapshot_id is None
        assert old_snapshot is not None and old_snapshot.status == "superseded"
        assert request is not None and request.status == "plan_confirmed"
        assert request.resulting_plan_version_id == new_plan["id"]
    stale_branch = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={**command, "command_id": "asset-revision-stale-plan-001"},
    )
    assert stale_branch.status_code == 409
    assert stale_branch.headers["x-error-code"] == "STORYBOARD_REVISION_PLAN_NOT_ACTIVE"


def test_open_storyboard_revision_can_be_explicitly_cancelled(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    with SessionLocal() as session:
        node = session.scalar(select(DAGNode).where(
            DAGNode.snapshot_id == snapshot["id"], DAGNode.shot_id.is_not(None)
        ).order_by(DAGNode.node_key))
        assert node is not None
        asset = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], dag_node_id=node.id,
            output_index=0, asset_type="image", role="keyframe", uri=f"runtime://assets/{project['id']}/cancel.png",
            storage_backend="local", provider_output_manifest={}, state="review_required",
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id
    created = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            "command_id": "cancel-revision-create-001", "actor_id": "local-user",
            "expected_asset_row_version": 1, "issue_scope": "storyboard",
            "issue_code": "other",
            "rationale": "误选了分镜问题。",
        },
    ).json()
    cancel_command = {
        "command_id": "cancel-revision-command-001", "actor_id": "local-user",
        "reason": "用户确认这不是分镜问题。",
    }
    cancelled = client.post(
        f"/api/v1/projects/{project['id']}/asset-revision-requests/{created['request']['id']}:cancel",
        json=cancel_command,
    )
    replayed = client.post(
        f"/api/v1/projects/{project['id']}/asset-revision-requests/{created['request']['id']}:cancel",
        json=cancel_command,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert replayed.json()["status"] == "cancelled"
    planning = client.get(f"/api/v1/projects/{project['id']}/planning-center").json()
    assert planning["revision_draft"] is None
    assert planning["revision_context"] is None
    assert planning["active_plan"] is not None
    with SessionLocal() as session:
        draft = session.get(ShotPlanCandidate, created["request"]["draft_candidate_id"])
        assert draft is not None and draft.status == "cancelled"


def test_non_shot_asset_requires_explicit_non_storyboard_scope(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    with SessionLocal() as session:
        node = DAGNode(
            snapshot_id=snapshot["id"], node_key="project.manual_output", kind="manual_output",
            shot_id=None, input_contract={}, output_contract={"media_type": "image"},
        )
        session.add(node)
        session.flush()
        asset = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], dag_node_id=node.id,
            output_index=0, asset_type="image", role="manual", uri=f"runtime://assets/{project['id']}/manual.png",
            storage_backend="local", provider_output_manifest={}, state="review_required",
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id
    base = {
        "actor_id": "local-user", "expected_asset_row_version": 1,
        "rationale": "生成效果不符合预期。",
    }
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            **base, "command_id": "non-shot-storyboard-001",
            "issue_scope": "storyboard", "issue_code": "content_mismatch",
        },
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "STORYBOARD_REVISION_SHOT_REQUIRED"
    recorded = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            **base, "command_id": "non-shot-production-001",
            "issue_scope": "production", "issue_code": "visual_artifact", "rationale": "",
        },
    )
    assert recorded.status_code == 201
    assert recorded.json()["request"]["status"] == "recorded"
    assert recorded.json()["request"]["issue_code"] == "visual_artifact"
    assert recorded.json()["request"]["rationale"] == ""
    assert recorded.json()["next_action"]["path"].startswith("/production?")
    incompatible = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            **base, "command_id": "non-shot-incompatible-001",
            "issue_scope": "production", "issue_code": "content_mismatch",
        },
    )
    assert incompatible.status_code == 422
    missing_other_detail = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            **base, "command_id": "non-shot-other-empty-001",
            "issue_scope": "editing", "issue_code": "other", "rationale": "   ",
        },
    )
    assert missing_other_detail.status_code == 422
    editing = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset_id}:request-revision",
        json={
            **base, "command_id": "non-shot-editing-001",
            "issue_scope": "editing", "issue_code": "replace_clip",
            "rationale": "人物动作不对，但用户明确选择在剪辑阶段替换。",
        },
    )
    assert editing.status_code == 201
    assert editing.json()["request"]["issue_scope"] == "editing"
    assert editing.json()["request"]["issue_code"] == "replace_clip"
    with SessionLocal() as session:
        event = session.scalar(select(ProjectEvent).where(
            ProjectEvent.aggregate_id == recorded.json()["request"]["id"],
            ProjectEvent.event_type == "asset.revision_requested.v1",
        ))
        assert event is not None
        assert event.data["issue_code"] == "visual_artifact"


def test_failed_qc_agent_requires_exact_user_confirmed_retry(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "qc-retry-activate-0001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "qc-retry-submit-000001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, _ = attach_local_provider_output(project, snapshot, 480, 848)
    response_hash = hashlib.sha256(json.dumps(response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json={"command_id": "qc-retry-register-0001", "output_index": 0, "expected_response_manifest_hash": response_hash},
    ).json()
    asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:verify",
        json={"command_id": "qc-retry-verify-00001", "expected_row_version": asset["row_version"]},
    ).json()
    gateway = FailOnceQCGateway()
    app.dependency_overrides[get_qc_gateway] = lambda: gateway

    failed = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:run-qc",
        json={"command_id": "qc-retry-run-failed001", "expected_row_version": asset["row_version"]},
    )
    assert failed.status_code == 502
    view = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()
    pending = next(row for row in view["assets"] if row["id"] == asset["id"])
    assert pending["state"] == "verified"
    assert pending["latest_qc_agent_run"]["status"] == "failed"
    failed_run_id = pending["latest_qc_agent_run"]["id"]

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}:run-qc",
        json={"command_id": "qc-retry-run-new-00001", "expected_row_version": pending["row_version"]},
    )
    assert duplicate.status_code == 409
    assert duplicate.headers["x-error-code"] == "QC_FAILED_RUN_REQUIRES_RETRY"

    retried = client.post(
        f"/api/v1/projects/{project['id']}/assets/{asset['id']}/qc-runs/{failed_run_id}:retry",
        json={
            "command_id": "qc-retry-confirmed-0001",
            "failed_agent_run_id": failed_run_id,
            "expected_asset_id": asset["id"],
            "expected_row_version": pending["row_version"],
            "confirm_model_cost": True,
        },
    )
    assert retried.status_code == 200
    assert retried.json()["status"] == "awaiting_review"
    assert gateway.calls == 2
    with SessionLocal() as session:
        runs = list(session.scalars(select(AgentRun).where(AgentRun.project_id == project["id"], AgentRun.agent_role == "qc")))
        assert [run.status for run in runs] == ["failed", "succeeded"]
        assert len(list(session.scalars(select(QCReportCandidate).where(QCReportCandidate.asset_id == asset["id"])))) == 1


def test_contact_sheet_does_not_substitute_a_snapshot_or_create_records(client: TestClient) -> None:
    project = client.post("/api/v1/projects", json={
        "title": "Contact sheet empty state",
        "core_topic": "Read only projection",
        "duration_seconds": 15,
        "aspect_ratio": "9:16",
        "audio_mode": "off",
        "production_profile": {
            "video_motion_strategy": "adaptive",
            "keyframe_strategy": "adaptive",
            "enforcement": "required",
        },
    }).json()
    with SessionLocal() as session:
        before = (
            len(list(session.scalars(select(CommandReceipt)))),
            len(list(session.scalars(select(ProjectEvent)))),
            len(list(session.scalars(select(WorkAttempt)))),
            len(list(session.scalars(select(CostEvent)))),
        )
    response = client.get(f"/api/v1/projects/{project['id']}/contact-sheet")
    assert response.status_code == 200
    view = response.json()
    assert view["snapshot"] is None
    assert view["entries"] == []
    assert view["counts"] == {}
    assert "不会改用最新或历史快照" in view["boundary"]
    with SessionLocal() as session:
        after = (
            len(list(session.scalars(select(CommandReceipt)))),
            len(list(session.scalars(select(ProjectEvent)))),
            len(list(session.scalars(select(WorkAttempt)))),
            len(list(session.scalars(select(CostEvent)))),
        )
    assert after == before


def test_contact_sheet_projects_exact_route_dependencies_entities_and_qc(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    activated = client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:activate",
        json={"command_id": "contact-activate-command-001", "expected_contract_hash": snapshot["contract_hash"]},
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/production-snapshots/{snapshot['id']}:submit",
        json={
            "command_id": "contact-submit-command-001",
            "expected_contract_hash": snapshot["contract_hash"],
            "expected_estimated_cost": snapshot["estimated_cost"],
            "expected_currency": snapshot["currency"],
            "expected_dag_node_ids": [node["id"] for node in activated["nodes"]],
            "confirm_high_risk_submission": True,
        },
    )
    item, response_manifest, _ = attach_local_provider_output(project, snapshot, 480, 848)
    response_hash = hashlib.sha256(json.dumps(
        response_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    image_asset = client.post(
        f"/api/v1/projects/{project['id']}/work-attempts/{item['attempt_id']}/assets",
        json={
            "command_id": "contact-register-command-001",
            "output_index": 0,
            "expected_response_manifest_hash": response_hash,
        },
    ).json()
    image_asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/{image_asset['id']}:verify",
        json={"command_id": "contact-verify-command-001", "expected_row_version": image_asset["row_version"]},
    ).json()
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/assets/{image_asset['id']}:run-qc",
        json={"command_id": "contact-qc-command-001", "expected_row_version": image_asset["row_version"]},
    ).json()
    image_asset = client.get(f"/api/v1/projects/{project['id']}/quality-review").json()["assets"][0]
    image_asset = client.post(
        f"/api/v1/projects/{project['id']}/assets/{image_asset['id']}:approve",
        json={
            "command_id": "contact-approve-command-001",
            "expected_row_version": image_asset["row_version"],
            "qc_report_candidate_id": candidate["id"],
            "rationale": "素材符合当前镜头合同。",
        },
    ).json()
    report = image_asset["latest_qc_report"]

    with SessionLocal() as session:
        image = session.get(Asset, image_asset["id"])
        image_node = session.get(DAGNode, image.dag_node_id)
        edge = session.scalar(select(DependencyEdge).where(
            DependencyEdge.snapshot_id == snapshot["id"],
            DependencyEdge.parent_node_id == image_node.id,
        ))
        video_node = session.get(DAGNode, edge.child_node_id)
        shot = session.get(Shot, image_node.shot_id)
        attachment = Attachment(
            project_id=project["id"],
            original_filename="identity-reference.png",
            mime_type="image/png",
            byte_size=10,
            content_hash=hashlib.sha256(b"identity").hexdigest(),
            storage_path="attachments/identity-reference.png",
        )
        entity = Entity(
            id="contact_character",
            project_id=project["id"],
            entity_type="character",
            display_name="Contact Character",
        )
        session.add_all([attachment, entity])
        session.flush()
        version = EntityVersion(
            project_id=project["id"],
            entity_id=entity.id,
            version_number=1,
            attributes={"identity": "confirmed"},
            source_attachment_id=attachment.id,
        )
        session.add(version)
        session.flush()
        shot.character_entity_version_ids = [version.id]
        video_asset = Asset(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            work_attempt_id=None,
            dag_node_id=video_node.id,
            output_index=0,
            asset_type="video",
            role="shot_clip",
            uri=f"runtime://assets/contact/{video_node.id}.mp4",
            storage_backend="local",
            provider_output_manifest={"registered_for_projection_test": True},
            content_hash=hashlib.sha256(video_node.id.encode()).hexdigest(),
            mime_type="video/mp4",
            byte_size=100,
            width=480,
            height=848,
            duration_ms=shot.duration_ms,
            state="approved",
        )
        session.add(video_asset)
        session.commit()
        video_asset_id = video_asset.id
        entity_version_id = version.id
        before = (
            len(list(session.scalars(select(CommandReceipt)))),
            len(list(session.scalars(select(ProjectEvent)))),
            len(list(session.scalars(select(WorkAttempt)))),
            len(list(session.scalars(select(CostEvent)))),
        )

    response = client.get(f"/api/v1/projects/{project['id']}/contact-sheet")
    assert response.status_code == 200
    view = response.json()
    assert view["snapshot"]["id"] == snapshot["id"]
    assert [entry["number"] for entry in view["entries"]] == list(range(1, len(view["entries"]) + 1))
    image_entry = next(entry for entry in view["entries"] if entry["asset"]["id"] == image_asset["id"])
    video_entry = next(entry for entry in view["entries"] if entry["asset"]["id"] == video_asset_id)
    assert image_entry["route"] == {
        "work_item_id": item["id"],
        "work_item_status": "completed",
        "attempt_id": item["attempt_id"],
        "attempt_number": 1,
        "attempt_state": "completed",
        "provider": "mock_visual",
        "adapter_kind": "mock",
        "provider_workflow_id": "mock-workflow-not-executable",
        "provider_task_id": None,
        "request_fingerprint": image_entry["route"]["request_fingerprint"],
    }
    assert image_entry["asset"]["latest_qc_report"]["id"] == report["id"]
    assert image_entry["shot"]["id"] == video_entry["shot"]["id"]
    assert image_entry["entity_references"][0]["entity_version_id"] == entity_version_id
    dependency = video_entry["dependencies"][0]
    assert dependency["parent_node_id"] == image_entry["node_id"]
    assert [asset["id"] for asset in dependency["registered_assets"]] == [image_asset["id"]]
    assert video_entry["route"] is None
    assert "不推断" in view["boundary"]
    with SessionLocal() as session:
        after = (
            len(list(session.scalars(select(CommandReceipt)))),
            len(list(session.scalars(select(ProjectEvent)))),
            len(list(session.scalars(select(WorkAttempt)))),
            len(list(session.scalars(select(CostEvent)))),
        )
    assert after == before


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


def test_change_continuity_relations_reuse_core_check_for_realtime_rhythm_observation() -> None:
    expected = {
        "time_jump": ("jump-readable", "1×观看后时间跳转清楚、切点节奏自然"),
        "location_change": ("location-readable", "1×观看后地点切换清楚、切点节奏自然"),
        "outfit_change": ("outfit-readable", "1×观看后换装意图清楚、切点节奏自然"),
    }
    for relation, (check_id, label) in expected.items():
        checks = dict(editor_service._CONTINUITY_CHECKS[relation])
        assert len(checks) == 3
        assert checks[check_id] == label
        assert editor_service._continuity_review_mode(check_id) == "action"


def save_fully_reviewed_editor_draft(
    client: TestClient,
    project: dict,
    timeline: dict,
    track_config: dict,
    items: list[dict],
) -> dict:
    item_fields = {
        "track_type", "sequence_number", "asset_id", "label", "gap_reason",
        "source_in_ms", "source_out_ms", "timeline_in_ms", "timeline_out_ms", "transform",
    }
    draft_items = [
        {
            **{key: value for key, value in item.items() if key in item_fields},
            "client_item_id": f"review-{item['track_type']}-{item['sequence_number']}",
        }
        for item in items
    ]
    all_check_ids = {
        "subject", "motion", "eyeline", "jump-readable", "new-information",
        "location-readable", "orientation", "outfit-readable", "reason", "change-readable",
    }
    main_items = sorted(
        (item for item in draft_items if item["track_type"] == "main_video"),
        key=lambda item: item["sequence_number"],
    )
    outcomes = {
        f"{left['client_item_id']}-{right['client_item_id']}": {
            check_id: "passed" for check_id in all_check_ids
        }
        for left, right in zip(main_items, main_items[1:])
        if left.get("asset_id") and right.get("asset_id")
    }
    def boundary_fingerprint(left: dict, right: dict) -> str:
        def item_fingerprint(item: dict, side: str) -> list:
            transform = item.get("transform") or {}
            transition = transform.get("transition_out" if side == "left" else "transition_in") or {}
            return [
                item.get("client_item_id"), item.get("asset_id"),
                item.get("source_in_ms"), item.get("source_out_ms"),
                item.get("timeline_in_ms"), item.get("timeline_out_ms"),
                transform.get("fit"), transition.get("type", "cut"),
                transition.get("duration_ms", 0),
            ]
        return json.dumps(
            [item_fingerprint(left, "left"), item_fingerprint(right, "right")],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    observations = {
        f"{left['client_item_id']}-{right['client_item_id']}": {
            mode: {
                "boundary_fingerprint": boundary_fingerprint(left, right),
                "observed_at": "2026-08-11T06:00:00Z",
                "completed_steps": {
                    "frames": ["left_frame", "right_frame"],
                    "overlay": ["overlay"],
                    "action": ["synchronous_action", "sequential_cut_realtime_context"],
                }[mode],
                "action_sequence_evidence": {
                    "playback_rate": 1,
                    "left_context_ms": min(1000, left["source_out_ms"] - left["source_in_ms"]),
                    "right_context_ms": min(1000, right["source_out_ms"] - right["source_in_ms"]),
                } if mode == "action" else None,
            }
            for mode in ("frames", "overlay", "action")
        }
        for left, right in zip(main_items, main_items[1:])
        if left.get("asset_id") and right.get("asset_id")
    }
    response = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": timeline["snapshot_id"],
            "base_timeline_id": timeline["id"],
            "base_timeline_row_version": timeline["row_version"],
            "track_config": track_config,
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": outcomes,
            "continuity_issue_contexts": {},
            "continuity_observations": observations,
            "candidate_review_sessions": {},
        },
    )
    assert response.status_code == 200
    return response.json()


def create_confirmed_timeline(
    client: TestClient,
    *,
    include_preview_review: bool = True,
) -> tuple[dict, dict, dict]:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    stage = client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "delivery-stage-approve-001", "expected_snapshot_id": snapshot["id"]},
    )
    assert stage.status_code == 200
    timeline = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "delivery-timeline-create-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
            "items": timeline_items_for_assets(video_assets),
        },
    ).json()
    timeline = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:validate",
        json={"command_id": "delivery-timeline-validate-001", "expected_row_version": timeline["row_version"]},
    ).json()
    timeline = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:confirm",
        json={
            "command_id": "delivery-timeline-confirm-001",
            "expected_row_version": timeline["row_version"],
            "expected_contract_hash": timeline["contract_hash"],
            "confirm_delivery_scope": True,
        },
    ).json()
    if include_preview_review:
        with SessionLocal() as session:
            SqlAlchemyEventRepository(session).add(ProjectEvent(
                project_id=project["id"],
                snapshot_id=snapshot["id"],
                event_type="timeline.preview_reviewed.v1",
                aggregate_type="timeline",
                aggregate_id=timeline["id"],
                actor_type="user",
                actor_id="test-reviewer",
                causation_id="delivery-preview-review-fixture",
                message="Test reviewer approved the exact preview.",
                data={
                    "timeline_contract_hash": timeline["contract_hash"],
                    "preview_key": "a" * 64,
                    "preview_content_hash": "b" * 64,
                    "quality_status": "review_required",
                    "quality_check_codes": ["PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED"],
                    "confirmed_manual_checks": {
                        "visual_continuity": True,
                        "subjective_sync": True,
                        "subtitle_readability": True,
                        "warnings": True,
                    },
                },
            ))
            session.commit()
    return project, snapshot, timeline


def synthetic_mp4(width: int, height: int, duration_ms: int) -> bytes:
    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I4s", len(payload) + 8, kind) + payload
    timescale = 1000
    mvhd = box(b"mvhd", b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, timescale, duration_ms))
    tkhd = box(b"tkhd", struct.pack(">II", width << 16, height << 16))
    return box(b"ftyp", b"isom\x00\x00\x02\x00isom") + box(b"moov", mvhd + box(b"trak", tkhd))


def authorize_delivery_attempt(client: TestClient, project: dict, timeline: dict) -> dict:
    response = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-authorize-command-001",
            "actor_id": "test-user",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "external_upload",
            "confirm_delivery_authorization": True,
        },
    )
    assert response.status_code == 201
    return response.json()


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
    assert approved.status_code == 200, approved.text
    assert replayed.json()["project_status"] == "editing"
    assert approved.json()["quality_stage_ready"] is True
    assert next(row for row in client.get("/api/v1/projects").json() if row["id"] == project["id"])["status"] == "editing"

    missing_snap = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-create-missing-snap-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False},
            "items": timeline_items_for_assets(video_assets),
        },
    )
    assert missing_snap.status_code == 422

    create_command = {
        "command_id": "timeline-create-command-001",
        "expected_snapshot_id": snapshot["id"],
        "source": "user",
        "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": False},
        "items": timeline_items_for_assets(video_assets),
    }
    created = client.post(f"/api/v1/projects/{project['id']}/timeline-candidates", json=create_command)
    replayed_candidate = client.post(f"/api/v1/projects/{project['id']}/timeline-candidates", json=create_command)
    assert created.status_code == 201
    timeline = created.json()
    assert replayed_candidate.json()["id"] == timeline["id"]
    assert timeline["status"] == "candidate"
    assert timeline["track_config"]["snap_enabled"] is False

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


def test_editor_assistant_creates_auditable_timeline_candidate_for_manual_confirmation(
    client: TestClient,
) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-agent-stage-approve", "expected_snapshot_id": snapshot["id"]},
    )

    response = client.post(
        f"/api/v1/projects/{project['id']}/editor-assistant:generate",
        json={"command_id": "editor-agent-generate-001", "expected_snapshot_id": snapshot["id"]},
    )

    assert response.status_code == 201
    timeline = response.json()
    assert timeline["source"] == "editor_assistant"
    assert timeline["status"] == "candidate"
    assert timeline["source_agent_run_id"]
    assert timeline["track_config"]["snap_enabled"] is True
    assert [item["asset_id"] for item in timeline["items"]] == [item["id"] for item in video_assets]
    assert all(item["transform"]["editor_assistant"]["selection_reason"] for item in timeline["items"])
    assert all(item["transform"]["editor_assistant"]["qc_report_ids"] for item in timeline["items"])
    workspace = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()
    assert workspace["latest_editor_run"]["status"] == "succeeded"
    assert workspace["next_action"]["code"] == "VALIDATE_TIMELINE"
    assert [shot["sequence_number"] for shot in workspace["shot_sequence"]] == [1, 2, 3]
    assert [shot["shot_code"] for shot in workspace["shot_sequence"]] == ["SH-001", "SH-002", "SH-003"]
    video_workspace_assets = sorted(
        (asset for asset in workspace["available_assets"] if asset["asset_type"] == "video"),
        key=lambda asset: asset["shot_sequence_number"],
    )
    assert [asset["shot_code"] for asset in video_workspace_assets] == ["SH-001", "SH-002", "SH-003"]
    assert [asset["shot_sequence_number"] for asset in video_workspace_assets] == [1, 2, 3]
    persisted_timeline = next(item for item in workspace["timelines"] if item["id"] == timeline["id"])
    assert persisted_timeline["source"] == "editor_assistant"
    assert persisted_timeline["items"][0]["transform"]["editor_assistant"]["selection_reason"]
    assert persisted_timeline["items"][0]["transform"]["editor_assistant"]["qc_report_ids"]
    assert client.get(f"/api/v1/projects/{project['id']}").json()["status"] == "editing"


def test_timeline_revision_freezes_snap_toggle_and_changes_contract_hash(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    stage = client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "timeline-snap-stage-approve-001", "expected_snapshot_id": snapshot["id"]},
    )
    assert stage.status_code == 200
    created = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-snap-create-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": False},
            "items": timeline_items_for_assets(video_assets),
        },
    )
    assert created.status_code == 201
    first_validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{created.json()['id']}:validate",
        json={"command_id": "timeline-snap-validate-001", "expected_row_version": created.json()["row_version"]},
    )
    assert first_validated.status_code == 200
    first = first_validated.json()
    revised_track_config = {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True}
    revised_items = timeline_items_for_assets(video_assets)
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, first, revised_track_config, revised_items,
    )
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{first['id']}:revise",
        json={
            "command_id": "timeline-snap-revise-001",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": first["row_version"],
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "source": "user",
            "track_config": revised_track_config,
            "items": revised_items,
        },
    )
    assert revised.status_code == 201
    second_validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{revised.json()['id']}:validate",
        json={"command_id": "timeline-snap-validate-002", "expected_row_version": revised.json()["row_version"]},
    )
    assert second_validated.status_code == 200
    second = second_validated.json()
    assert first["track_config"]["snap_enabled"] is False
    assert second["track_config"]["snap_enabled"] is True
    assert second["contract_hash"] != first["contract_hash"]
    original = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()["timelines"][-1]
    assert original["id"] == first["id"]
    assert original["track_config"]["snap_enabled"] is False


def test_editor_assistant_adds_the_exact_approved_voiceover_to_audio_track(
    client: TestClient,
) -> None:
    project, snapshot = create_locked_snapshot(client)
    seed_editor_assets(client, project, snapshot)
    with SessionLocal() as session:
        persisted_project = session.get(Project, project["id"])
        persisted_project.audio_mode = "voiceover"
        audio = Asset(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            work_attempt_id=None,
            dag_node_id=None,
            output_index=0,
            asset_type="audio",
            role="voiceover",
            uri=f"runtime://assets/editor/{project['id']}-voiceover.wav",
            storage_backend="local",
            provider_output_manifest={"seeded_for_voiceover_editor_test": True},
            content_hash=hashlib.sha256(project["id"].encode()).hexdigest(),
            mime_type="audio/wav",
            byte_size=100,
            duration_ms=29_000,
            state="approved",
        )
        session.add(audio)
        session.flush()
        session.add(QCReport(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            asset_id=audio.id,
            report_number=1,
            ruleset_version="human-review.v1",
            status="passed",
            analyzer="human",
        ))
        subtitle = Asset(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            work_attempt_id=None,
            dag_node_id=None,
            output_index=0,
            asset_type="subtitle",
            role="voiceover_subtitles",
            uri=f"runtime://assets/editor/{project['id']}-subtitles.srt",
            storage_backend="local",
            provider_output_manifest={"seeded_for_voiceover_editor_test": True},
            content_hash=hashlib.sha256(f"{project['id']}-subtitles".encode()).hexdigest(),
            mime_type="application/x-subrip",
            byte_size=100,
            duration_ms=29_000,
            state="approved",
        )
        session.add(subtitle)
        session.flush()
        session.add(QCReport(
            project_id=project["id"],
            snapshot_id=snapshot["id"],
            asset_id=subtitle.id,
            report_number=1,
            ruleset_version="human-review.v1",
            status="passed",
            analyzer="human",
        ))
        session.commit()
        audio_id = audio.id
        subtitle_id = subtitle.id
    stage = client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-audio-stage-approve", "expected_snapshot_id": snapshot["id"]},
    )
    assert stage.status_code == 200

    response = client.post(
        f"/api/v1/projects/{project['id']}/editor-assistant:generate",
        json={"command_id": "editor-audio-generate-01", "expected_snapshot_id": snapshot["id"]},
    )

    assert response.status_code == 201
    timeline = response.json()
    audio_items = [item for item in timeline["items"] if item["track_type"] == "audio"]
    subtitle_items = [item for item in timeline["items"] if item["track_type"] == "subtitle"]
    assert timeline["track_config"]["audio_enabled"] is True
    assert timeline["track_config"]["subtitle_enabled"] is True
    assert len(audio_items) == 1
    assert audio_items[0]["asset_id"] == audio_id
    assert audio_items[0]["timeline_in_ms"] == 0
    assert audio_items[0]["timeline_out_ms"] == 29_000
    assert audio_items[0]["transform"]["source"] == "frozen_approved_voiceover"
    assert audio_items[0]["transform"]["volume_envelope"] == [
        {"time_ms": 0, "gain_db": 0.0},
        {"time_ms": 29_000, "gain_db": 0.0},
    ]
    assert len(subtitle_items) == 1
    assert subtitle_items[0]["asset_id"] == subtitle_id
    assert subtitle_items[0]["timeline_in_ms"] == 0
    assert subtitle_items[0]["timeline_out_ms"] == 29_000
    assert subtitle_items[0]["transform"]["render"] == "burn_in"
    assert subtitle_items[0]["transform"]["subtitle_cues"] is None
    assert subtitle_items[0]["transform"]["source"] == "frozen_approved_subtitles"
    assert len(subtitle_items[0]["transform"]["qc_report_ids"]) == 1
    validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:validate",
        json={"command_id": "editor-audio-validate-01", "expected_row_version": timeline["row_version"]},
    )
    assert validated.status_code == 200
    assert validated.json()["validation_report"] == []
    revised_items = []
    for item in timeline["items"]:
        draft = {
            key: item[key]
            for key in (
                "track_type", "sequence_number", "asset_id", "label", "gap_reason",
                "source_in_ms", "source_out_ms", "timeline_in_ms", "timeline_out_ms", "transform",
            )
        }
        if item["track_type"] == "subtitle":
            draft["transform"] = {
                **item["transform"],
                "subtitle_cues": [
                    {"sequence": 1, "start_ms": 0, "end_ms": 1800, "text": "逐条修订后的第一句"},
                    {"sequence": 2, "start_ms": 2000, "end_ms": 4200, "text": "第二句\n允许明确换行"},
                ],
            }
        revised_items.append(draft)
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, validated.json(), timeline["track_config"], revised_items,
    )
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:revise",
        json={
            "command_id": "editor-subtitle-cues-revise-01",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": validated.json()["row_version"],
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "source": "user",
            "track_config": timeline["track_config"],
            "items": revised_items,
        },
    )
    assert revised.status_code == 201
    revised_validation = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{revised.json()['id']}:validate",
        json={
            "command_id": "editor-subtitle-cues-validate-01",
            "expected_row_version": revised.json()["row_version"],
        },
    )
    assert revised_validation.status_code == 200
    assert revised_validation.json()["validation_report"] == []
    frozen_subtitle = next(
        item for item in revised_validation.json()["items"] if item["track_type"] == "subtitle"
    )
    assert frozen_subtitle["transform"]["subtitle_cues"][1]["text"] == "第二句\n允许明确换行"
    invalid_items = json.loads(json.dumps(revised_items, ensure_ascii=False))
    invalid_subtitle = next(item for item in invalid_items if item["track_type"] == "subtitle")
    invalid_subtitle["transform"]["subtitle_cues"][1]["start_ms"] = 1500
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, revised_validation.json(), timeline["track_config"], invalid_items,
    )
    invalid_revision = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{revised.json()['id']}:revise",
        json={
            "command_id": "editor-subtitle-cues-revise-invalid-01",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": revised_validation.json()["row_version"],
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "source": "user",
            "track_config": timeline["track_config"],
            "items": invalid_items,
        },
    )
    assert invalid_revision.status_code == 201
    invalid_validation = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{invalid_revision.json()['id']}:validate",
        json={
            "command_id": "editor-subtitle-cues-validate-invalid-01",
            "expected_row_version": invalid_revision.json()["row_version"],
        },
    )
    assert invalid_validation.status_code == 200
    assert "SUBTITLE_CUE_TIMING_INVALID" in {
        row["code"] for row in invalid_validation.json()["validation_report"]
    }


def test_timeline_validation_blocks_unapproved_assets_gaps_and_source_overrun(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-stage-approve-002", "expected_snapshot_id": snapshot["id"]},
    )
    items = timeline_items_for_assets(video_assets)
    items[0]["source_out_ms"] += 1000
    items[0]["transform"]["transition_in"] = {"type": "unknown", "duration_ms": 500}
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
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
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
    assert "VIDEO_TRANSITION_INVALID" in codes
    preview = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{candidate['id']}:render-preview",
        json={
            "command_id": "timeline-invalid-preview-01",
            "expected_row_version": validated["row_version"],
            "expected_contract_hash": validated["contract_hash"],
            "quality_profile": "draft_360p",
        },
    )
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["state"] == "blocked"
    assert preview_payload["content_url"] is None
    assert preview_payload["cached"] is False
    assert "TIMELINE_GAP_UNRESOLVED" in {row["code"] for row in preview_payload["validation_report"]}
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


def test_timeline_validation_blocks_reusing_main_video_to_fill_duration(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-reuse-stage-approve", "expected_snapshot_id": snapshot["id"]},
    )
    items = timeline_items_for_assets(video_assets)
    items[1]["asset_id"] = items[0]["asset_id"]
    items[1]["label"] = items[0]["label"]
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-reuse-create-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
            "items": items,
        },
    ).json()

    validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{candidate['id']}:validate",
        json={"command_id": "timeline-reuse-validate-001", "expected_row_version": candidate["row_version"]},
    ).json()

    issue = next(
        row for row in validated["validation_report"]
        if row["code"] == "TIMELINE_VIDEO_ASSET_REUSE_NOT_ALLOWED"
    )
    assert validated["status"] == "candidate"
    assert issue["path"] == "items.main_video.2"
    assert issue["evidence"]["asset_id"] == video_assets[0]["id"]
    assert issue["evidence"]["previous_sequence_number"] == 1


def test_timeline_preview_renders_cached_low_resolution_without_delivery_side_effects(
    client: TestClient,
    monkeypatch,
) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-preview-stage-approve", "expected_snapshot_id": snapshot["id"]},
    )
    candidate = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "editor-preview-create-001",
            "expected_snapshot_id": snapshot["id"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
            "items": timeline_items_for_assets(video_assets),
        },
    ).json()
    timeline = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{candidate['id']}:validate",
        json={"command_id": "editor-preview-validate-01", "expected_row_version": candidate["row_version"]},
    ).json()
    with SessionLocal() as session:
        assets = [session.get(Asset, row["id"]) for row in video_assets]
        for asset in assets:
            path = resolve_local_asset_path(asset.uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            node_id = Path(asset.uri).stem
            path.write_bytes(node_id.encode())
            assert hashlib.sha256(node_id.encode()).hexdigest() == asset.content_hash
    ffmpeg_path = TEST_RUNTIME / "tools" / "ffmpeg.exe"
    ffmpeg_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_path.write_bytes(b"fixture")
    monkeypatch.setattr(
        editor_service,
        "inspect_local_ffmpeg",
        lambda: FFmpegReadiness(True, None, None, str(ffmpeg_path), "ffmpeg fixture 1.0"),
    )
    render_calls = []

    def fake_render(_renderer, request):
        render_calls.append(request)
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(b"preview-mp4")
        return LocalRenderResult(("ffmpeg",), "", "")

    monkeypatch.setattr(editor_service.LocalFFmpegRenderer, "render", fake_render)
    quality_calls = []
    monkeypatch.setattr(
        editor_service,
        "_preview_quality_report",
        lambda path, _timeline, _ffmpeg_path, duration_ms: quality_calls.append((path, duration_ms)) or {
            "schema_version": "editor-preview-qc.v1",
            "status": "review_required",
            "checks": [{
                "code": "PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED",
                "state": "manual_review",
                "message": "manual review",
                "evidence": {},
            }],
        },
    )
    monkeypatch.setattr(
        editor_service,
        "probe_media",
        lambda _path, _declared_type: {"mime_type": "video/mp4", "width": 360, "height": 640, "duration_ms": 30_000},
    )
    payload = {
        "command_id": "editor-preview-render-001",
        "expected_row_version": timeline["row_version"],
        "expected_contract_hash": timeline["contract_hash"],
        "quality_profile": "draft_360p",
    }
    first = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:render-preview",
        json=payload,
    )
    assert first.status_code == 200
    preview = first.json()
    assert preview["state"] == "ready", preview
    assert preview["cached"] is False
    assert preview["width"] == 360
    assert preview["height"] == 640
    assert preview["content_hash"] == hashlib.sha256(b"preview-mp4").hexdigest()
    assert len(preview["preview_key"]) == 64
    assert preview["quality_report"]["status"] == "review_required"
    assert len(render_calls) == 1
    assert render_calls[0].output_path.name.startswith(f".{preview['preview_key']}.")
    assert render_calls[0].output_path.name.endswith(".tmp.mp4")
    assert not render_calls[0].output_path.exists()
    assert len(quality_calls) == 1
    review_payload = {
        "command_id": "editor-preview-review-001",
        "expected_row_version": timeline["row_version"],
        "expected_contract_hash": timeline["contract_hash"],
        "preview_key": preview["preview_key"],
        "expected_preview_content_hash": preview["content_hash"],
        "confirm_visual_continuity_reviewed": False,
        "confirm_subjective_sync_reviewed": True,
        "confirm_subtitle_readability_reviewed": True,
        "confirm_warnings_reviewed": True,
    }
    missing_manual_review = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:review-preview",
        json=review_payload,
    )
    assert missing_manual_review.status_code == 409
    assert missing_manual_review.headers["x-error-code"] == "PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED"
    reviewed = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:review-preview",
        json={
            **review_payload,
            "command_id": "editor-preview-review-002",
            "confirm_visual_continuity_reviewed": True,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    review = reviewed.json()
    assert review["schema_version"] == "editor-preview-review.v1"
    assert review["timeline_id"] == timeline["id"]
    assert review["preview_key"] == preview["preview_key"]
    assert review["preview_content_hash"] == preview["content_hash"]
    assert review["quality_status"] == "review_required"
    replayed_review = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:review-preview",
        json={
            **review_payload,
            "command_id": "editor-preview-review-002",
            "confirm_visual_continuity_reviewed": True,
        },
    )
    assert replayed_review.status_code == 200
    assert replayed_review.json()["review_id"] == review["review_id"]
    refreshed_workspace = client.get(
        f"/api/v1/projects/{project['id']}/editor-workspace",
    ).json()
    refreshed_timeline = next(
        row for row in refreshed_workspace["timelines"] if row["id"] == timeline["id"]
    )
    assert refreshed_timeline["preview_review"]["review_id"] == review["review_id"]
    assert refreshed_timeline["preview_review"]["preview_content_hash"] == preview["content_hash"]
    cached = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:render-preview",
        json={**payload, "command_id": "editor-preview-render-002"},
    ).json()
    assert cached["state"] == "ready"
    assert cached["cached"] is True
    assert len(render_calls) == 1
    assert len(quality_calls) == 4
    content = client.get(preview["content_url"])
    assert content.status_code == 200
    assert content.content == b"preview-mp4"
    monkeypatch.setattr(
        editor_service,
        "probe_media",
        lambda _path, _declared_type: {"mime_type": "video/mp4", "width": 640, "height": 360, "duration_ms": 30_000},
    )
    invalid_cache = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:render-preview",
        json={**payload, "command_id": "editor-preview-render-003"},
    ).json()
    assert invalid_cache["state"] == "blocked"
    assert invalid_cache["validation_report"][0]["code"] == "PREVIEW_OUTPUT_CONTRACT_INVALID"
    assert client.get(preview["content_url"]).status_code == 404
    with SessionLocal() as session:
        assert session.get(Timeline, timeline["id"]).status == "review"
        review_events = list(session.scalars(select(ProjectEvent).where(
            ProjectEvent.project_id == project["id"],
            ProjectEvent.event_type == "timeline.preview_reviewed.v1",
            ProjectEvent.aggregate_id == timeline["id"],
        )))
        assert len(review_events) == 1
        assert list(session.scalars(select(DeliveryAttempt).where(DeliveryAttempt.project_id == project["id"]))) == []
        assert list(session.scalars(select(WorkItem).where(
            WorkItem.project_id == project["id"],
            WorkItem.kind == "render_delivery",
        ))) == []


def test_preview_quality_report_blocks_audio_failures_and_requires_visual_review(monkeypatch) -> None:
    timeline = SimpleNamespace(track_config={
        "audio_enabled": True,
        "subtitle_enabled": True,
        "audio_mastering": {
            "loudness_target_lufs": -16,
            "true_peak_limit_dbtp": -1,
        },
    })
    monkeypatch.setattr(
        editor_service,
        "_preview_black_segments",
        lambda _path, _ffmpeg: ([{"start_ms": 1000, "end_ms": 1700, "duration_ms": 700}], None),
    )
    monkeypatch.setattr(
        editor_service,
        "measure_program_audio",
        lambda _path, _ffmpeg: {
            "ebur128_status": "measured",
            "integrated_loudness_lufs": -8.0,
            "true_peak_dbtp": 0.0,
        },
    )
    monkeypatch.setattr(
        editor_service,
        "_preview_audio_duration",
        lambda _path, _ffmpeg: {"status": "measured", "duration_ms": 29_000},
    )
    report = editor_service._preview_quality_report(
        Path("preview.mp4"),
        timeline,
        Path("ffmpeg.exe"),
        30_000,
    )
    by_code = {check["code"]: check for check in report["checks"]}
    assert report["status"] == "blocked"
    assert by_code["PREVIEW_BLACK_SEGMENTS_DETECTED"]["state"] == "warning"
    assert by_code["PREVIEW_AUDIO_LOUDNESS_OUT_OF_RANGE"]["state"] == "blocked"
    assert by_code["PREVIEW_AUDIO_TRUE_PEAK_EXCEEDED"]["state"] == "blocked"
    assert by_code["PREVIEW_AUDIO_DURATION_MISMATCH"]["state"] == "blocked"
    assert by_code["PREVIEW_SUBTITLE_VISUAL_REVIEW_REQUIRED"]["state"] == "manual_review"
    assert by_code["PREVIEW_VISUAL_CONTINUITY_REVIEW_REQUIRED"]["state"] == "manual_review"
    assert by_code["PREVIEW_SUBJECTIVE_SYNC_REVIEW_REQUIRED"]["state"] == "manual_review"


def test_preview_quality_report_passes_technical_checks_but_keeps_manual_gate(monkeypatch) -> None:
    timeline = SimpleNamespace(track_config={
        "audio_enabled": True,
        "subtitle_enabled": False,
        "audio_mastering": {
            "loudness_target_lufs": -16,
            "true_peak_limit_dbtp": -1,
        },
    })
    monkeypatch.setattr(editor_service, "_preview_black_segments", lambda _path, _ffmpeg: ([], None))
    monkeypatch.setattr(
        editor_service,
        "measure_program_audio",
        lambda _path, _ffmpeg: {
            "ebur128_status": "measured",
            "integrated_loudness_lufs": -16.2,
            "true_peak_dbtp": -1.1,
        },
    )
    monkeypatch.setattr(
        editor_service,
        "_preview_audio_duration",
        lambda _path, _ffmpeg: {"status": "measured", "duration_ms": 30_100},
    )
    report = editor_service._preview_quality_report(
        Path("preview.mp4"),
        timeline,
        Path("ffmpeg.exe"),
        30_000,
    )
    assert report["status"] == "review_required"
    assert not [check for check in report["checks"] if check["state"] == "blocked"]
    assert {check["code"] for check in report["checks"] if check["state"] == "passed"} >= {
        "PREVIEW_BLACK_FRAME_CHECK_PASSED",
        "PREVIEW_AUDIO_LOUDNESS_PASSED",
        "PREVIEW_AUDIO_TRUE_PEAK_PASSED",
        "PREVIEW_AUDIO_DURATION_PASSED",
    }


def test_preview_quality_report_blocks_when_enabled_audio_cannot_be_measured(monkeypatch) -> None:
    timeline = SimpleNamespace(track_config={"audio_enabled": True, "subtitle_enabled": False})
    monkeypatch.setattr(editor_service, "_preview_black_segments", lambda _path, _ffmpeg: ([], None))
    monkeypatch.setattr(
        editor_service,
        "measure_program_audio",
        lambda _path, _ffmpeg: {"ebur128_status": "analysis_failed", "analyzer_return_code": 1},
    )
    monkeypatch.setattr(
        editor_service,
        "_preview_audio_duration",
        lambda _path, _ffmpeg: {"status": "analysis_failed", "return_code": 1},
    )
    report = editor_service._preview_quality_report(
        Path("preview.mp4"),
        timeline,
        Path("ffmpeg.exe"),
        30_000,
    )
    blocked_codes = {check["code"] for check in report["checks"] if check["state"] == "blocked"}
    assert report["status"] == "blocked"
    assert blocked_codes == {"PREVIEW_AUDIO_ANALYSIS_FAILED", "PREVIEW_AUDIO_DURATION_MISMATCH"}


def test_preview_black_segment_parser_is_deterministic(monkeypatch) -> None:
    result = SimpleNamespace(
        returncode=0,
        stderr=(
            "[blackdetect @ fixture] black_start:0.5 black_end:1.25 black_duration:0.75\n"
            "[blackdetect @ fixture] black_start:5 black_end:5.6 black_duration:0.6"
        ),
    )
    monkeypatch.setattr(editor_service.subprocess, "run", lambda *_args, **_kwargs: result)
    segments, error = editor_service._preview_black_segments(Path("preview.mp4"), Path("ffmpeg.exe"))
    assert error is None
    assert segments == [
        {"start_ms": 500, "end_ms": 1250, "duration_ms": 750},
        {"start_ms": 5000, "end_ms": 5600, "duration_ms": 600},
    ]


def test_timeline_freezes_authorized_looping_bgm_ducking_and_mastering(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    video_assets = seed_editor_assets(client, project, snapshot)
    client.post(
        f"/api/v1/projects/{project['id']}/quality-stage:approve",
        json={"command_id": "editor-bgm-stage-approve", "expected_snapshot_id": snapshot["id"]},
    )
    with SessionLocal() as session:
        voiceover = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], output_index=0,
            asset_type="audio", role="voiceover", uri=f"runtime://assets/editor/{project['id']}-voice.wav",
            storage_backend="local", provider_output_manifest={"test": True},
            content_hash=hashlib.sha256(f"{project['id']}-voice".encode()).hexdigest(),
            mime_type="audio/wav", byte_size=100, duration_ms=15_000, state="approved",
        )
        bgm = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], output_index=1,
            asset_type="audio", role="background_music", uri=f"runtime://assets/editor/{project['id']}-bgm.wav",
            storage_backend="local", provider_output_manifest={"test": True},
            content_hash=hashlib.sha256(f"{project['id']}-bgm".encode()).hexdigest(),
            mime_type="audio/wav", byte_size=100, duration_ms=10_000, state="approved",
        )
        session.add_all([voiceover, bgm])
        session.commit()
        voiceover_id, bgm_id = voiceover.id, bgm.id
    audio_items = [
        {
            "track_type": "audio", "sequence_number": 1, "asset_id": voiceover_id, "label": "旁白",
            "source_in_ms": 0, "source_out_ms": 15_000, "timeline_in_ms": 5_000, "timeline_out_ms": 20_000,
            "transform": {
                "mix": "voiceover", "playback": {"mode": "trim"},
                "volume_envelope": [{"time_ms": 0, "gain_db": 0}, {"time_ms": 15_000, "gain_db": 0}],
            },
        },
        {
            "track_type": "audio", "sequence_number": 2, "asset_id": bgm_id, "label": "BGM",
            "source_in_ms": 0, "source_out_ms": 10_000, "timeline_in_ms": 0, "timeline_out_ms": 30_000,
            "transform": {
                "mix": "background_music", "playback": {"mode": "loop"},
                "rights": {"confirmed": True, "basis": "licensed", "evidence": "项目商业音乐授权单 TEST-001"},
                "ducking": {
                    "enabled": True, "reduction_db": -12, "attack_ms": 200, "release_ms": 500,
                    "regions": [{"start_ms": 5_000, "end_ms": 20_000}],
                },
                "volume_envelope": [{"time_ms": 0, "gain_db": -6}, {"time_ms": 30_000, "gain_db": -6}],
            },
        },
    ]
    invalid_audio_items = json.loads(json.dumps(audio_items))
    invalid_audio_items[1]["transform"]["rights"]["confirmed"] = False
    invalid_audio_items[1]["transform"]["ducking"]["regions"] = []
    track_config = {
        "audio_enabled": True, "subtitle_enabled": False, "snap_enabled": True,
        "audio_mastering": {
            "loudness_target_lufs": -14, "true_peak_limit_dbtp": -1.5, "clipping_control": "limiter",
        },
    }
    created = client.post(
        f"/api/v1/projects/{project['id']}/timeline-candidates",
        json={
            "command_id": "timeline-bgm-create-001", "expected_snapshot_id": snapshot["id"], "source": "user",
            "track_config": track_config,
            "items": [*timeline_items_for_assets(video_assets), *invalid_audio_items],
        },
    )
    assert created.status_code == 201
    timeline = created.json()
    invalid = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:validate",
        json={"command_id": "timeline-bgm-validate-001", "expected_row_version": timeline["row_version"]},
    )
    assert invalid.status_code == 200
    assert {row["code"] for row in invalid.json()["validation_report"]} >= {
        "BGM_RIGHTS_AUTHORIZATION_REQUIRED", "BGM_DUCKING_REGIONS_STALE",
    }
    revised_items = [*timeline_items_for_assets(video_assets), *audio_items]
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, invalid.json(), track_config, revised_items,
    )
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:revise",
        json={
            "command_id": "timeline-bgm-revise-001", "expected_snapshot_id": snapshot["id"],
            "expected_row_version": invalid.json()["row_version"], "source": "user",
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "track_config": track_config,
            "items": revised_items,
        },
    )
    assert revised.status_code == 201
    timeline = revised.json()
    validated = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{timeline['id']}:validate",
        json={"command_id": "timeline-bgm-validate-002", "expected_row_version": timeline["row_version"]},
    )
    assert validated.status_code == 200
    result = validated.json()
    assert result["status"] == "review"
    assert result["validation_report"] == []
    assert result["track_config"]["audio_mastering"] == {
        "loudness_target_lufs": -14.0,
        "true_peak_limit_dbtp": -1.5,
        "clipping_control": "limiter",
    }
    frozen_bgm = next(item for item in result["items"] if item["asset_id"] == bgm_id)
    assert frozen_bgm["transform"]["rights"]["evidence"] == "项目商业音乐授权单 TEST-001"
    assert frozen_bgm["transform"]["ducking"]["regions"] == [{"start_ms": 5_000, "end_ms": 20_000}]


def test_audio_approval_requires_explicit_listening_confirmation(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    with SessionLocal() as session:
        persisted_project = session.get(Project, project["id"])
        persisted_project.status = "producing"
        audio = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], output_index=0,
            asset_type="audio", role="voiceover", uri=f"runtime://assets/editor/{project['id']}-listen.wav",
            storage_backend="local", provider_output_manifest={"test": True},
            content_hash=hashlib.sha256(f"{project['id']}-listen".encode()).hexdigest(),
            mime_type="audio/wav", byte_size=100, duration_ms=1000, state="review_required",
        )
        session.add(audio)
        session.flush()
        report = QCReport(
            project_id=project["id"], snapshot_id=snapshot["id"], asset_id=audio.id,
            report_number=1, ruleset_version="qc-policy.v1", status="review_required",
            analyzer="human-review-required",
        )
        session.add(report)
        session.flush()
        session.add(QCFinding(
            qc_report_id=report.id, code="AUDIO_TECHNICAL_QC_PASSED", severity="passed",
            evidence={"schema_version": "audio-qc.v1"}, contract_field="output_contract.audio",
            disposition="manual_review",
        ))
        session.commit()
        audio_id, row_version, report_id = audio.id, audio.row_version, report.id
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/assets/{audio_id}:approve",
        json={
            "command_id": "audio-listen-block-001", "expected_row_version": row_version,
            "qc_report_id": report_id, "rationale": "尚未试听", "confirm_audio_listened": False,
        },
    )
    assert blocked.status_code == 409
    assert blocked.headers["x-error-code"] == "AUDIO_LISTENING_CONFIRMATION_REQUIRED"
    approved = client.post(
        f"/api/v1/projects/{project['id']}/assets/{audio_id}:approve",
        json={
            "command_id": "audio-listen-approve-01", "expected_row_version": row_version,
            "qc_report_id": report_id, "rationale": "已完整试听并确认内容", "confirm_audio_listened": True,
        },
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"


def test_voice_clone_authorization_is_versioned_hashed_and_revocable(client: TestClient) -> None:
    project, snapshot = create_locked_snapshot(client)
    with SessionLocal() as session:
        sample_hash = hashlib.sha256(f"{project['id']}-authorized-voice".encode()).hexdigest()
        sample = Asset(
            project_id=project["id"], snapshot_id=snapshot["id"], output_index=0,
            asset_type="audio", role="voice_clone_sample",
            uri=f"runtime://assets/voice-clones/{project['id']}.wav",
            storage_backend="local", provider_output_manifest={"authorization_sample": True},
            content_hash=sample_hash, mime_type="audio/wav", byte_size=1000,
            duration_ms=5000, state="approved",
        )
        session.add(sample)
        session.commit()
        sample_id = sample.id
    base = {
        "authorization_key": "brand_founder",
        "sample_asset_id": sample_id,
        "subject_name": "品牌创始人",
        "provider_voice_id": f"cosyvoice-clone-{project['id']}",
        "authorization_basis": "self",
        "authorization_scope": ["tts", "commercial"],
        "consent_evidence": "本人签署的声音复刻与商业视频使用授权，证据编号 CONSENT-001。",
        "authorized_by": "品牌创始人本人",
        "valid_from": "2026-07-01T00:00:00+08:00",
        "expires_at": "2027-07-01T00:00:00+08:00",
    }
    denied = client.post(
        f"/api/v1/projects/{project['id']}/voice-clone-authorizations",
        json={"command_id": "voice-auth-denied-001", **base, "confirm_authority": False},
    )
    assert denied.status_code == 409
    created = client.post(
        f"/api/v1/projects/{project['id']}/voice-clone-authorizations",
        json={"command_id": "voice-auth-create-001", **base, "confirm_authority": True},
    )
    assert created.status_code == 201, created.text
    authorization = created.json()
    assert authorization["version_number"] == 1
    assert authorization["sample_content_hash"] == sample_hash
    assert authorization["status"] == "active"
    assert len(authorization["contract_hash"]) == 64
    listed = client.get(f"/api/v1/projects/{project['id']}/voice-clone-authorizations").json()
    assert [row["id"] for row in listed] == [authorization["id"]]
    revoked = client.post(
        f"/api/v1/projects/{project['id']}/voice-clone-authorizations/{authorization['id']}:revoke",
        json={
            "command_id": "voice-auth-revoke-001",
            "expected_contract_hash": authorization["contract_hash"],
            "reason": "授权主体主动终止后续使用",
            "confirm_revoke": True,
        },
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"


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
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
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
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
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
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
            "items": items,
        },
    )
    assert unlinked.status_code == 409
    assert unlinked.headers["x-error-code"] == "TIMELINE_REVISION_REQUIRED"
    revised_items = list(reversed([
        {**item, "sequence_number": len(items) - index}
        for index, item in enumerate(items)
    ]))
    reviewed_draft = save_fully_reviewed_editor_draft(
        client,
        project,
        first,
        {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
        revised_items,
    )
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{first['id']}:revise",
        json={
            "command_id": "timeline-revision-create-002",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": first["row_version"],
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "source": "user",
            "track_config": {"audio_enabled": False, "subtitle_enabled": False, "snap_enabled": True},
            "items": revised_items,
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


def test_delivery_authorization_and_verified_mp4_complete_project_without_execution(client: TestClient) -> None:
    project, snapshot, timeline = create_confirmed_timeline(client)
    with SessionLocal() as session:
        work_attempt_count = len(list(session.scalars(select(WorkAttempt))))
        cost_event_count = len(list(session.scalars(select(CostEvent))))

    denied = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-authorize-denied-01",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "external_upload",
            "confirm_delivery_authorization": False,
        },
    )
    assert denied.status_code == 409
    assert denied.headers["x-error-code"] == "DELIVERY_AUTHORIZATION_REQUIRED"

    attempt = authorize_delivery_attempt(client, project, timeline)
    replay = authorize_delivery_attempt(client, project, timeline)
    assert replay["id"] == attempt["id"]
    assert attempt["status"] == "authorized"
    assert attempt["request_manifest"]["schema_version"] == "v2.delivery-request.v3"
    assert attempt["request_manifest"]["timeline_contract_hash"] == timeline["contract_hash"]
    assert attempt["request_manifest"]["preview_review"]["schema_version"] == "editor-preview-review.v1"
    assert attempt["request_manifest"]["preview_review"]["timeline_contract_hash"] == timeline["contract_hash"]
    assert attempt["request_manifest"]["output_spec"]["duration_ms"] == 30_000

    second = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-authorize-second-001",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "external_upload",
            "confirm_delivery_authorization": True,
        },
    )
    assert second.status_code == 409
    assert second.headers["x-error-code"] == "DELIVERY_ATTEMPT_EXISTS"

    content = synthetic_mp4(480, 848, 30_000)
    rejected_upload = client.post(
        f"/api/v1/projects/{project['id']}/delivery-attempts/{attempt['id']}/output",
        data={
            "command_id": "delivery-upload-wrong-fp01",
            "actor_id": "test-user",
            "expected_request_fingerprint": "0" * 64,
            "expected_row_version": attempt["row_version"],
        },
        files={"file": ("delivery.mp4", content, "video/mp4")},
    )
    assert rejected_upload.status_code == 409
    assert rejected_upload.headers["x-error-code"] == "DELIVERY_REQUEST_FINGERPRINT_MISMATCH"
    assert not list((TEST_RUNTIME / "uploads" / "delivery").glob("*.upload"))

    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/delivery-attempts/{attempt['id']}/output",
        data={
            "command_id": "delivery-upload-command-001",
            "actor_id": "test-user",
            "expected_request_fingerprint": attempt["request_fingerprint"],
            "expected_row_version": attempt["row_version"],
        },
        files={"file": ("delivery.mp4", content, "video/mp4")},
    )
    assert uploaded.status_code == 201
    attempt = uploaded.json()
    assert attempt["status"] == "output_registered"
    assert attempt["final_asset"]["asset_type"] == "final_delivery"
    assert attempt["final_asset"]["state"] == "created"

    verified = client.post(
        f"/api/v1/projects/{project['id']}/delivery-attempts/{attempt['id']}:verify",
        json={
            "command_id": "delivery-verify-command-001",
            "actor_id": "test-user",
            "expected_row_version": attempt["row_version"],
            "expected_asset_row_version": attempt["final_asset"]["row_version"],
        },
    )
    assert verified.status_code == 200
    result = verified.json()
    assert result["status"] == "verified"
    assert result["final_asset"]["state"] == "verified"
    assert result["final_asset"]["content_hash"] == hashlib.sha256(content).hexdigest()
    assert result["final_asset"]["width"] == 480
    assert result["final_asset"]["height"] == 848
    assert result["final_asset"]["duration_ms"] == 30_000
    workspace = client.get(f"/api/v1/projects/{project['id']}/delivery-workspace").json()
    assert workspace["project_status"] == "completed"
    assert workspace["delivery_asset_id"] == result["final_asset"]["id"]
    assert workspace["confirmed_timeline"]["status"] == "exported"
    download = client.get(f"/api/v1/projects/{project['id']}/assets/{result['final_asset']['id']}/content")
    assert download.status_code == 200
    assert download.content == content
    control = client.get(f"/api/v1/projects/{project['id']}/control-center").json()
    assert control["evaluated_stage"] == "completed"
    assert control["delivery"]["status"] == "verified"
    assert control["next_action"]["code"] == "DOWNLOAD_DELIVERY"
    assert control["next_action"]["path"] == "/editor"
    with SessionLocal() as session:
        assert len(list(session.scalars(select(DeliveryAttempt)))) == 1
        assert len(list(session.scalars(select(WorkAttempt)))) == work_attempt_count
        assert len(list(session.scalars(select(CostEvent)))) == cost_event_count
        assert session.get(Project, project["id"]).active_snapshot_id == snapshot["id"]

    editor = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()
    exported = editor["timelines"][0]
    assert exported["status"] == "exported"
    draft_items = [{
        "client_item_id": item["id"],
        "track_type": item["track_type"],
        "sequence_number": item["sequence_number"],
        "asset_id": item["asset_id"],
        "label": item["label"],
        "gap_reason": item["gap_reason"],
        "source_in_ms": item["source_in_ms"],
        "source_out_ms": item["source_out_ms"],
        "timeline_in_ms": item["timeline_in_ms"],
        "timeline_out_ms": item["timeline_out_ms"],
        "transform": item["transform"],
    } for item in exported["items"]]
    first_boundary_key = f"{draft_items[0]['client_item_id']}-{draft_items[1]['client_item_id']}"
    motion_analysis = {
        "left_change_percent": 1.2,
        "right_change_percent": 2.3,
        "right_minus_left_percentage_points": 1.1,
        "left_grid_change_percent": [1.0] * 9,
        "right_grid_change_percent": [2.0] * 9,
        "right_minus_left_grid_percentage_points": [1.0] * 9,
        "left_centroid": {"x_percent": 25.0, "y_percent": 40.0, "dispersion_percent": 12.0},
        "right_centroid": {"x_percent": 30.0, "y_percent": 45.0, "dispersion_percent": 14.0},
        "left_rhythm_change_percent": [1.0, 1.2],
        "right_rhythm_change_percent": [2.0, 2.3],
        "left_rhythm_centroids": [
            {"x_percent": 24.0, "y_percent": 39.0, "dispersion_percent": 11.0},
            {"x_percent": 25.0, "y_percent": 40.0, "dispersion_percent": 12.0},
        ],
        "right_rhythm_centroids": [
            {"x_percent": 29.0, "y_percent": 44.0, "dispersion_percent": 13.0},
            {"x_percent": 30.0, "y_percent": 45.0, "dispersion_percent": 14.0},
        ],
        "left_centroid_path": {"x_percentage_points": 1.0, "y_percentage_points": 1.0, "distance_percent": 1.4},
        "right_centroid_path": {"x_percentage_points": 1.0, "y_percentage_points": 1.0, "distance_percent": 1.4},
        "centroid_path_continuity": {
            "x_gap_percentage_points": 0.0,
            "y_gap_percentage_points": 0.0,
            "distance_gap_percentage_points": 0.0,
            "angle_degrees": 0.0,
        },
        "left_rhythm_slope_percentage_points": 0.2,
        "right_rhythm_slope_percentage_points": 0.3,
        "right_minus_left_rhythm_slope_percentage_points": 0.1,
    }
    candidate_review_sessions = {
        "stable-review-session": {
            "measured_motion_evidence": {"exact-candidate-source": motion_analysis},
            "comparison_outcomes": {"exact-candidate-source": "shortlisted"},
            "alternative_outcomes": {"transition:fade:200": "kept_baseline"},
        },
    }
    saved_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 12_000,
            "continuity_outcomes": {
                first_boundary_key: {"motion": "needs_adjustment", "subject": "passed"},
            },
            "continuity_issue_contexts": {
                first_boundary_key: [{
                    "check_id": "motion",
                    "check_label": "动作阶段与运动方向自然承接",
                    "mode": "action",
                }],
            },
            "continuity_observations": {
                first_boundary_key: {
                    "frames": {
                        "boundary_fingerprint": json.dumps([
                            [
                                draft_items[0]["client_item_id"], draft_items[0]["asset_id"],
                                draft_items[0]["source_in_ms"], draft_items[0]["source_out_ms"],
                                draft_items[0]["timeline_in_ms"], draft_items[0]["timeline_out_ms"],
                                draft_items[0]["transform"].get("fit"),
                                (draft_items[0]["transform"].get("transition_out") or {}).get("type", "cut"),
                                (draft_items[0]["transform"].get("transition_out") or {}).get("duration_ms", 0),
                            ],
                            [
                                draft_items[1]["client_item_id"], draft_items[1]["asset_id"],
                                draft_items[1]["source_in_ms"], draft_items[1]["source_out_ms"],
                                draft_items[1]["timeline_in_ms"], draft_items[1]["timeline_out_ms"],
                                draft_items[1]["transform"].get("fit"),
                                (draft_items[1]["transform"].get("transition_in") or {}).get("type", "cut"),
                                (draft_items[1]["transform"].get("transition_in") or {}).get("duration_ms", 0),
                            ],
                        ], ensure_ascii=False, separators=(",", ":")),
                        "observed_at": "2026-08-11T06:00:00Z",
                        "completed_steps": ["left_frame", "right_frame"],
                        "action_sequence_evidence": None,
                    },
                },
            },
            "candidate_review_sessions": candidate_review_sessions,
        },
    )
    assert saved_draft.status_code == 200
    assert saved_draft.json()["schema_version"] == "editor-draft-session.v9"
    assert saved_draft.json()["playhead_ms"] == 12_000
    assert saved_draft.json()["continuity_outcomes"] == {
        first_boundary_key: {"motion": "needs_adjustment", "subject": "passed"},
    }
    assert saved_draft.json()["continuity_issue_contexts"] == {
        first_boundary_key: [{
            "check_id": "motion",
            "check_label": "动作阶段与运动方向自然承接",
            "mode": "action",
        }],
    }
    assert saved_draft.json()["candidate_review_sessions"] == candidate_review_sessions
    assert client.get(f"/api/v1/projects/{project['id']}/editor-draft").json()[
        "candidate_review_sessions"
    ] == candidate_review_sessions

    invalid_outcome = json.loads(json.dumps(candidate_review_sessions))
    invalid_outcome["stable-review-session"]["comparison_outcomes"]["exact-candidate-source"] = "recommended"
    invalid_candidate_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": {},
            "continuity_issue_contexts": {},
            "continuity_observations": {},
            "candidate_review_sessions": invalid_outcome,
        },
    )
    assert invalid_candidate_draft.status_code == 422

    invalid_alternative_outcome = json.loads(json.dumps(candidate_review_sessions))
    invalid_alternative_outcome["stable-review-session"]["alternative_outcomes"][
        "transition:fade:200"
    ] = "shortlisted"
    invalid_candidate_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": {},
            "continuity_issue_contexts": {},
            "continuity_observations": {},
            "candidate_review_sessions": invalid_alternative_outcome,
        },
    )
    assert invalid_candidate_draft.status_code == 422

    preserve_candidate_sessions = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": {},
            "continuity_issue_contexts": {},
            "continuity_observations": {},
            "candidate_review_sessions": {},
        },
    )
    assert preserve_candidate_sessions.status_code == 200
    assert preserve_candidate_sessions.json()["candidate_review_sessions"] == candidate_review_sessions

    appended_alternative = json.loads(json.dumps(candidate_review_sessions))
    appended_alternative["stable-review-session"]["measured_motion_evidence"] = {}
    appended_alternative["stable-review-session"]["comparison_outcomes"] = {}
    appended_alternative["stable-review-session"]["alternative_outcomes"] = {
        "roll:42": "kept_baseline",
    }
    appended_candidate_sessions = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": {},
            "continuity_issue_contexts": {},
            "continuity_observations": {},
            "candidate_review_sessions": appended_alternative,
        },
    )
    assert appended_candidate_sessions.status_code == 200
    assert appended_candidate_sessions.json()["candidate_review_sessions"][
        "stable-review-session"
    ]["alternative_outcomes"] == {
        "transition:fade:200": "kept_baseline",
        "roll:42": "kept_baseline",
    }

    invalid_grid = json.loads(json.dumps(candidate_review_sessions))
    invalid_grid["stable-review-session"]["measured_motion_evidence"][
        "exact-candidate-source"
    ]["left_grid_change_percent"] = [1.0] * 8
    invalid_candidate_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": draft_items,
            "playhead_ms": 0,
            "continuity_outcomes": {},
            "continuity_issue_contexts": {},
            "continuity_observations": {},
            "candidate_review_sessions": invalid_grid,
        },
    )
    assert invalid_candidate_draft.status_code == 422
    assert client.get(f"/api/v1/projects/{project['id']}/editor-draft").json()["items"] == draft_items

    stripped_draft_items = [
        {key: value for key, value in item.items() if key != "client_item_id"}
        for item in draft_items
    ]
    blocked_revision = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{exported['id']}:revise",
        json={
            "command_id": "post-delivery-revision-001",
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": exported["row_version"],
            "expected_editor_draft_row_version": appended_candidate_sessions.json()["row_version"],
            "source": "user",
            "track_config": exported["track_config"],
            "items": stripped_draft_items,
        },
    )
    assert blocked_revision.status_code == 409
    assert blocked_revision.headers["x-error-code"] == "TIMELINE_CONTINUITY_REVIEW_INCOMPLETE"
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, exported, exported["track_config"], stripped_draft_items,
    )
    stale_observations = json.loads(json.dumps(reviewed_draft["continuity_observations"]))
    reviewed_first_boundary_key = next(iter(stale_observations))
    stale_observations[reviewed_first_boundary_key]["frames"]["boundary_fingerprint"] = "stale-boundary"
    stale_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": reviewed_draft["items"],
            "playhead_ms": 0,
            "continuity_outcomes": reviewed_draft["continuity_outcomes"],
            "continuity_issue_contexts": {},
            "continuity_observations": stale_observations,
            "candidate_review_sessions": reviewed_draft["candidate_review_sessions"],
        },
    )
    assert stale_draft.status_code == 200
    stale_revision = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{exported['id']}:revise",
        json={
            "command_id": "post-delivery-revision-stale-observation",
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": exported["row_version"],
            "expected_editor_draft_row_version": stale_draft.json()["row_version"],
            "source": "user",
            "track_config": exported["track_config"],
            "items": stripped_draft_items,
        },
    )
    assert stale_revision.status_code == 409
    assert stale_revision.headers["x-error-code"] == "TIMELINE_CONTINUITY_REVIEW_INCOMPLETE"
    incomplete_action_observations = json.loads(json.dumps(reviewed_draft["continuity_observations"]))
    incomplete_action_observations[reviewed_first_boundary_key]["action"]["completed_steps"] = [
        "synchronous_action",
    ]
    incomplete_action_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": reviewed_draft["items"],
            "playhead_ms": 0,
            "continuity_outcomes": reviewed_draft["continuity_outcomes"],
            "continuity_issue_contexts": {},
            "continuity_observations": incomplete_action_observations,
            "candidate_review_sessions": reviewed_draft["candidate_review_sessions"],
        },
    )
    assert incomplete_action_draft.status_code == 200
    incomplete_action_revision = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{exported['id']}:revise",
        json={
            "command_id": "post-delivery-revision-incomplete-action-observation",
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": exported["row_version"],
            "expected_editor_draft_row_version": incomplete_action_draft.json()["row_version"],
            "source": "user",
            "track_config": exported["track_config"],
            "items": stripped_draft_items,
        },
    )
    assert incomplete_action_revision.status_code == 409
    assert incomplete_action_revision.headers["x-error-code"] == "TIMELINE_CONTINUITY_REVIEW_INCOMPLETE"
    insufficient_context_observations = json.loads(json.dumps(reviewed_draft["continuity_observations"]))
    insufficient_context_observations[reviewed_first_boundary_key]["action"]["action_sequence_evidence"] = {
        "playback_rate": 1,
        "left_context_ms": 1,
        "right_context_ms": 1,
    }
    insufficient_context_draft = client.put(
        f"/api/v1/projects/{project['id']}/editor-draft",
        json={
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "base_timeline_id": exported["id"],
            "base_timeline_row_version": exported["row_version"],
            "track_config": exported["track_config"],
            "items": reviewed_draft["items"],
            "playhead_ms": 0,
            "continuity_outcomes": reviewed_draft["continuity_outcomes"],
            "continuity_issue_contexts": {},
            "continuity_observations": insufficient_context_observations,
            "candidate_review_sessions": reviewed_draft["candidate_review_sessions"],
        },
    )
    assert insufficient_context_draft.status_code == 200
    insufficient_context_revision = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{exported['id']}:revise",
        json={
            "command_id": "post-delivery-revision-insufficient-action-context",
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": exported["row_version"],
            "expected_editor_draft_row_version": insufficient_context_draft.json()["row_version"],
            "source": "user",
            "track_config": exported["track_config"],
            "items": stripped_draft_items,
        },
    )
    assert insufficient_context_revision.status_code == 409
    assert insufficient_context_revision.headers["x-error-code"] == "TIMELINE_CONTINUITY_REVIEW_INCOMPLETE"
    reviewed_draft = save_fully_reviewed_editor_draft(
        client, project, exported, exported["track_config"], stripped_draft_items,
    )
    revised = client.post(
        f"/api/v1/projects/{project['id']}/timelines/{exported['id']}:revise",
        json={
            "command_id": "post-delivery-revision-002",
            "actor_id": "test-user",
            "expected_snapshot_id": snapshot["id"],
            "expected_row_version": exported["row_version"],
            "expected_editor_draft_row_version": reviewed_draft["row_version"],
            "source": "user",
            "track_config": exported["track_config"],
            "items": stripped_draft_items,
        },
    )
    assert revised.status_code == 201
    assert revised.json()["status"] == "candidate"
    assert revised.json()["supersedes_timeline_id"] == exported["id"]
    assert revised.json()["continuity_review"]["schema_version"] == "timeline-continuity-review.v6"
    assert revised.json()["continuity_review"]["boundary_count"] == 2
    assert revised.json()["continuity_review_hash"]
    assert revised.json()["continuity_review_hash"] == hashlib.sha256(
        json.dumps(
            revised.json()["continuity_review"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert all(
        check["outcome"] == "passed"
        for boundary in revised.json()["continuity_review"]["boundaries"]
        for check in boundary["checks"]
    )
    assert all(
        check["action_sequence_evidence"] is not None
        if check["observation_mode"] == "action"
        else check["action_sequence_evidence"] is None
        for boundary in revised.json()["continuity_review"]["boundaries"]
        for check in boundary["checks"]
    )
    assert all(
        check["observation_boundary_fingerprint"] and check["observed_at"]
        for boundary in revised.json()["continuity_review"]["boundaries"]
        for check in boundary["checks"]
    )
    assert all(
        check["completed_steps"] == {
            "frames": ["left_frame", "right_frame"],
            "overlay": ["overlay"],
            "action": ["synchronous_action", "sequential_cut_realtime_context"],
        }[check["observation_mode"]]
        for boundary in revised.json()["continuity_review"]["boundaries"]
        for check in boundary["checks"]
    )
    assert client.get(f"/api/v1/projects/{project['id']}/editor-draft").json() is None
    editor = client.get(f"/api/v1/projects/{project['id']}/editor-workspace").json()
    assert editor["project_status"] == "editing"
    assert next(row for row in editor["timelines"] if row["id"] == exported["id"])["status"] == "exported"
    control = client.get(f"/api/v1/projects/{project['id']}/control-center").json()
    assert control["evaluated_stage"] == "editing"
    assert control["delivery"]["status"] == "verified"


def test_delivery_authorization_requires_exact_preview_review(client: TestClient) -> None:
    project, _, timeline = create_confirmed_timeline(client, include_preview_review=False)
    workspace = client.get(f"/api/v1/projects/{project['id']}/delivery-workspace").json()
    assert workspace["preview_review"] is None
    assert workspace["next_action"]["code"] == "REVIEW_TIMELINE_PREVIEW"
    response = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-without-preview-review",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "external_upload",
            "confirm_delivery_authorization": True,
        },
    )
    assert response.status_code == 409
    assert response.headers["x-error-code"] == "DELIVERY_PREVIEW_REVIEW_REQUIRED"
    with SessionLocal() as session:
        assert list(session.scalars(select(DeliveryAttempt).where(
            DeliveryAttempt.project_id == project["id"],
        ))) == []


def test_delivery_verification_blocks_invalid_dimensions_without_retry_or_timeline_mutation(client: TestClient) -> None:
    project, _, timeline = create_confirmed_timeline(client)
    attempt = authorize_delivery_attempt(client, project, timeline)
    content = synthetic_mp4(576, 1024, 30_000)
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/delivery-attempts/{attempt['id']}/output",
        data={
            "command_id": "delivery-upload-invalid-001",
            "expected_request_fingerprint": attempt["request_fingerprint"],
            "expected_row_version": attempt["row_version"],
        },
        files={"file": ("wrong-size.mp4", content, "video/mp4")},
    ).json()
    blocked = client.post(
        f"/api/v1/projects/{project['id']}/delivery-attempts/{attempt['id']}:verify",
        json={
            "command_id": "delivery-verify-invalid-001",
            "expected_row_version": uploaded["row_version"],
            "expected_asset_row_version": uploaded["final_asset"]["row_version"],
        },
    )
    assert blocked.status_code == 200
    result = blocked.json()
    assert result["status"] == "blocked"
    assert result["error_code"] == "DELIVERY_DIMENSIONS_INVALID"
    assert result["error_detail"]["actual"] == [576, 1024]
    assert result["final_asset"]["state"] == "archived"
    assert result["final_asset"]["latest_qc_report"]["status"] == "blocked"
    assert result["final_asset"]["latest_qc_report"]["findings"][0]["code"] == "DELIVERY_DIMENSIONS_INVALID"
    workspace = client.get(f"/api/v1/projects/{project['id']}/delivery-workspace").json()
    assert workspace["project_status"] == "blocked"
    assert workspace["confirmed_timeline"]["status"] == "confirmed"
    denied_second = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-authorize-after-block",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "external_upload",
            "confirm_delivery_authorization": True,
        },
    )
    assert denied_second.status_code == 409
    with SessionLocal() as session:
        assert len(list(session.scalars(select(DeliveryAttempt)))) == 1
        assert session.get(Timeline, timeline["id"]).status == "confirmed"


def _connect_fake_ffmpeg(monkeypatch) -> None:
    monkeypatch.setattr(
        delivery_service,
        "inspect_local_ffmpeg",
        lambda: FFmpegReadiness(
            available=True,
            reason_code=None,
            reason=None,
            executable_path="C:\\test\\ffmpeg.exe",
            version="ffmpeg version test",
        ),
    )


def _write_timeline_input_files(timeline_id: str) -> list[Path]:
    paths: list[Path] = []
    with SessionLocal() as session:
        items = list(session.scalars(
            select(TimelineItem)
            .where(TimelineItem.timeline_id == timeline_id)
            .order_by(TimelineItem.sequence_number)
        ))
        for index, item in enumerate(items):
            asset = session.get(Asset, item.asset_id)
            path = resolve_local_asset_path(asset.uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"local-render-input-{index}".encode()
            path.write_bytes(content)
            asset.content_hash = hashlib.sha256(content).hexdigest()
            paths.append(path)
        session.commit()
    return paths


class FakeDeliveryRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self, request):
        self.calls += 1
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        request.output_path.write_bytes(synthetic_mp4(request.width, request.height, 30_000))
        return LocalRenderResult(
            command=("fake-ffmpeg",),
            stdout_tail="",
            stderr_tail="",
        )


def test_local_ffmpeg_authorization_requires_connected_renderer_without_creating_attempt(client: TestClient, monkeypatch) -> None:
    project, _, timeline = create_confirmed_timeline(client)
    monkeypatch.setattr(
        delivery_service,
        "inspect_local_ffmpeg",
        lambda: FFmpegReadiness(
            available=False,
            reason_code="FFMPEG_PATH_NOT_CONFIGURED",
            reason="未配置 FFmpeg。",
            executable_path=None,
            version=None,
        ),
    )
    with SessionLocal() as session:
        delivery_count = len(list(session.scalars(select(DeliveryAttempt))))
        work_count = len(list(session.scalars(select(WorkItem))))
    response = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-local-unavailable-001",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "local_ffmpeg",
            "confirm_delivery_authorization": True,
        },
    )
    assert response.status_code == 409
    assert response.headers["x-error-code"] == "FFMPEG_PATH_NOT_CONFIGURED"
    with SessionLocal() as session:
        assert len(list(session.scalars(select(DeliveryAttempt)))) == delivery_count
        assert len(list(session.scalars(select(WorkItem)))) == work_count


def test_local_ffmpeg_worker_registers_unverified_output_without_completing_project(client: TestClient, monkeypatch) -> None:
    project, snapshot, timeline = create_confirmed_timeline(client)
    _write_timeline_input_files(timeline["id"])
    _connect_fake_ffmpeg(monkeypatch)
    response = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-local-authorize-001",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "local_ffmpeg",
            "confirm_delivery_authorization": True,
        },
    )
    assert response.status_code == 201
    queued = response.json()
    assert queued["status"] == "queued"
    assert queued["work_item_id"]
    renderer = FakeDeliveryRenderer()
    assert process_one(worker_id="delivery-test-worker", delivery_renderer=renderer)
    assert renderer.calls == 1
    workspace = client.get(f"/api/v1/projects/{project['id']}/delivery-workspace").json()
    attempt = workspace["attempts"][0]
    assert attempt["status"] == "output_registered"
    assert attempt["execution_kind"] == "local_ffmpeg"
    assert attempt["final_asset"]["state"] == "created"
    assert workspace["project_status"] == "delivery_ready"
    with SessionLocal() as session:
        item = session.get(WorkItem, queued["work_item_id"])
        work_attempt = session.get(WorkAttempt, item.current_attempt_id)
        assert item.status == "completed"
        assert work_attempt.state == "completed"
        assert session.get(ProductionSnapshot, snapshot["id"]).status != "execution_blocked"


def test_local_ffmpeg_worker_blocks_changed_input_once_without_running_renderer(client: TestClient, monkeypatch) -> None:
    project, _, timeline = create_confirmed_timeline(client)
    paths = _write_timeline_input_files(timeline["id"])
    _connect_fake_ffmpeg(monkeypatch)
    queued = client.post(
        f"/api/v1/projects/{project['id']}/deliveries:authorize",
        json={
            "command_id": "delivery-local-authorize-change-01",
            "timeline_id": timeline["id"],
            "expected_timeline_contract_hash": timeline["contract_hash"],
            "execution_kind": "local_ffmpeg",
            "confirm_delivery_authorization": True,
        },
    ).json()
    paths[0].write_bytes(b"changed-after-delivery-authorization")
    renderer = FakeDeliveryRenderer()
    assert process_one(worker_id="delivery-test-worker", delivery_renderer=renderer)
    assert renderer.calls == 0
    workspace = client.get(f"/api/v1/projects/{project['id']}/delivery-workspace").json()
    attempt = workspace["attempts"][0]
    assert attempt["status"] == "blocked"
    assert attempt["error_code"] == "LOCAL_RENDER_INPUT_HASH_MISMATCH"
    with SessionLocal() as session:
        assert len(list(session.scalars(select(DeliveryAttempt)))) == 1
        item = session.get(WorkItem, queued["work_item_id"])
        assert item.status == "blocked"
        assert len(list(session.scalars(select(WorkAttempt).where(
            WorkAttempt.work_item_id == item.id
        )))) == 1


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


def test_system_configuration_contract_persists_provider_api_key_as_plain_field(client: TestClient) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0]["api_key"] = "configured-api-key"
    response = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-secret-command-001",
        "configuration": configuration,
    })
    assert response.status_code == 201
    provider = next(
        item for item in response.json()["components"] if item["component_type"] == "provider"
    )
    assert provider["details"]["api_key"] == "configured-api-key"

    configuration = valid_system_configuration()
    configuration["providers"][0]["base_url"] = "https://user:password@provider.invalid/api"
    embedded = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-secret-command-002",
        "configuration": configuration,
    })
    assert embedded.status_code == 422


def test_system_configuration_rejects_unconnected_local_storage_reference(client: TestClient) -> None:
    configuration = valid_system_configuration()
    configuration["storage"]["local_root_ref"] = "v2/runtime/assets"
    rejected = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-invalid-local-storage-create",
        "configuration": configuration,
    })
    assert rejected.status_code == 422

    created = client.post("/api/v1/system-config/versions", json={
        "command_id": "config-valid-local-storage-create",
        "configuration": valid_system_configuration(),
    }).json()
    with SessionLocal() as session:
        storage = session.scalar(select(StoragePolicyVersion).where(
            StoragePolicyVersion.production_config_version_id == created["id"]
        ))
        assert storage is not None
        storage.local_root_ref = "v2/runtime/assets"
        session.commit()

    validated = client.post(
        f"/api/v1/system-config/versions/{created['id']}:validate",
        json={
            "command_id": "config-invalid-local-storage-validate",
            "expected_row_version": created["row_version"],
        },
    )
    assert validated.status_code == 200
    report = validated.json()
    assert report["status"] == "validation_failed"
    assert any(
        item["code"] == "LOCAL_STORAGE_REF_NOT_CONNECTED"
        and item["path"] == "storage.local_root_ref"
        for item in report["validation_report"]
    )


def test_provider_readiness_is_read_only_and_does_not_enable_unregistered_adapter(
    client: TestClient,
) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0].update({
        "provider_key": "runninghub_visual",
        "display_name": "RunningHub",
        "adapter_kind": "runninghub",
        "api_key": "must-never-be-returned",
    })
    configuration["workflow_slots"][0]["provider_key"] = "runninghub_visual"
    created = client.post("/api/v1/system-config/versions", json={
        "command_id": "provider-readiness-create-001",
        "configuration": configuration,
    }).json()
    ready = client.post(
        f"/api/v1/system-config/versions/{created['id']}:validate",
        json={"command_id": "provider-readiness-validate1", "expected_row_version": created["row_version"]},
    ).json()
    published = client.post(
        f"/api/v1/system-config/versions/{created['id']}:publish",
        json={
            "command_id": "provider-readiness-publish1",
            "expected_row_version": ready["row_version"],
            "confirm_high_risk_changes": True,
        },
    )
    assert published.status_code == 200
    response = client.get("/api/v1/system-config/provider-readiness")
    assert response.status_code == 200
    view = response.json()
    assert view["network_probe_performed"] is False
    assert view["external_execution_enabled"] is False
    assert len(view["providers"]) == 1
    provider = view["providers"][0]
    assert provider["provider_display_name"] == "RunningHub"
    assert provider["adapter_kind"] == "runninghub"
    assert provider["adapter_registered"] is True
    assert provider["execution_enabled"] is False
    assert provider["api_key_state"] == "configured"
    assert provider["configuration_ready"] is True
    assert provider["configuration_issue_count"] == 0
    assert provider["configuration_issue_codes"] == []
    assert provider["status"] == "execution_disabled"
    assert provider["next_action"] == "enable_execution"
    serialized = response.text
    assert "must-never-be-returned" not in serialized


def test_provider_readiness_requires_api_key_in_published_provider_configuration(
    client: TestClient,
) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0].update({
        "provider_key": "runninghub_without_key",
        "display_name": "RunningHub",
        "adapter_kind": "runninghub",
        "api_key": None,
    })
    configuration["workflow_slots"][0]["provider_key"] = "runninghub_without_key"
    created = client.post("/api/v1/system-config/versions", json={
        "command_id": "provider-readiness-missing-key-create",
        "configuration": configuration,
    }).json()
    ready = client.post(
        f"/api/v1/system-config/versions/{created['id']}:validate",
        json={"command_id": "provider-readiness-missing-key-validate", "expected_row_version": created["row_version"]},
    ).json()
    published = client.post(
        f"/api/v1/system-config/versions/{created['id']}:publish",
        json={
            "command_id": "provider-readiness-missing-key-publish",
            "expected_row_version": ready["row_version"],
            "confirm_high_risk_changes": True,
        },
    )
    assert published.status_code == 200

    provider = client.get("/api/v1/system-config/provider-readiness").json()["providers"][0]
    assert provider["api_key_state"] == "missing"
    assert provider["status"] == "credential_not_ready"
    assert provider["next_action"] == "configure_credential"


class _CosyVoiceValidationTransport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def synthesize(self, url, api_key, payload, timeout, max_bytes):
        self.calls.append((url, api_key, payload, timeout, max_bytes))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(24000)
            target.writeframes(b"\x00\x00" * 2400)
        return {
            "request_id": "cosyvoice-validation-request-001",
            "usage": {"characters": 13},
        }, buffer.getvalue()


def test_cosyvoice_validation_workspace_and_paid_evidence_are_auditable(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "true")
    config = publish_visual_production_configuration(
        client,
        with_pricing=True,
        with_voiceover=True,
        command_prefix="cosyvoice-validation-config",
    )
    workspace_response = client.get(
        "/api/v1/system-config/cosyvoice-validation",
        params={"configuration_id": config["id"]},
    )
    assert workspace_response.status_code == 200, workspace_response.text
    workspace = workspace_response.json()
    assert workspace["preflight"]["status"] == "ready_for_paid_validation"
    assert workspace["preflight"]["network_probe_performed"] is False
    assert workspace["preflight"]["audio_contract"] == {
        "sample_rate": 24000,
        "channels": 1,
        "format": "wav",
        "voice_key": "steady_male",
        "provider_voice_id": "longxiaocheng",
        "speaking_rate": 1.0,
        "volume": 50,
    }
    assert workspace["validation_runs"] == []
    assert "test-cosyvoice-key" not in workspace_response.text

    preflight = workspace["preflight"]
    transport = _CosyVoiceValidationTransport()
    payload = CosyVoicePaidValidationCommand(
        command_id="cosyvoice-validation-paid-001",
        actor_id="local-user",
        configuration_id=config["id"],
        expected_config_hash=preflight["configuration"]["config_hash"],
        validation_text="片场 V2 配音连接验收。",
        expected_validation_text_sha256=preflight["validation_text"]["sha256"],
        confirm_paid_call=True,
    )
    with SessionLocal() as session:
        result = execute_cosyvoice_paid_validation(
            session,
            payload,
            transport=transport,
        )
    assert result["status"] == "passed"
    assert result["network_probe_performed"] is True
    assert result["request_id"] == "cosyvoice-validation-request-001"
    assert result["usage"] == {"characters": 13}
    assert result["output"]["mime_type"] == "audio/wav"
    assert result["output"]["sample_rate"] == 24000
    assert result["output"]["channels"] == 1
    assert transport.calls[0][2]["input"] == {
        "text": "片场 V2 配音连接验收。",
        "voice": "longxiaocheng",
        "rate": 1.0,
        "volume": 50,
        "format": "wav",
        "sample_rate": 24000,
    }

    with SessionLocal() as session:
        replayed = execute_cosyvoice_paid_validation(
            session,
            payload,
            transport=transport,
        )
        stored = session.get(CosyVoiceValidationRun, result["id"])
        assert stored is not None
        assert stored.status == "passed"
        assert stored.request_id == "cosyvoice-validation-request-001"
        assert stored.output["content_hash"] == result["output"]["content_hash"]
    assert replayed["id"] == result["id"]
    assert len(transport.calls) == 1

    refreshed = client.get(
        "/api/v1/system-config/cosyvoice-validation",
        params={"configuration_id": config["id"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["validation_runs"][0]["id"] == result["id"]
    assert "test-cosyvoice-key" not in refreshed.text


def test_provider_readiness_reports_historical_runninghub_contract_without_mutation(
    client: TestClient,
) -> None:
    configuration = valid_system_configuration()
    configuration["providers"][0].update({
        "provider_key": "runninghub_visual",
        "display_name": "RunningHub",
        "adapter_kind": "runninghub",
        "api_key": "historical-test-key",
    })
    configuration["workflow_slots"][0]["provider_key"] = "runninghub_visual"
    created = client.post("/api/v1/system-config/versions", json={
        "command_id": "provider-readiness-historical-create",
        "configuration": configuration,
    }).json()
    ready = client.post(
        f"/api/v1/system-config/versions/{created['id']}:validate",
        json={"command_id": "provider-readiness-historical-validate", "expected_row_version": created["row_version"]},
    ).json()
    published = client.post(
        f"/api/v1/system-config/versions/{created['id']}:publish",
        json={
            "command_id": "provider-readiness-historical-publish",
            "expected_row_version": ready["row_version"],
            "confirm_high_risk_changes": True,
        },
    ).json()
    workflow_id = next(
        item["id"] for item in published["components"] if item["component_type"] == "workflow_slot"
    )
    legacy_bindings = [{
        "node_id": "prompt",
        "field_path": "text",
        "value_source": "{{prompt}}",
        "value_type": "string",
        "required": True,
    }]
    with SessionLocal() as session:
        workflow = session.get(WorkflowSlotVersion, workflow_id)
        assert workflow is not None
        workflow.node_info_list = legacy_bindings
        session.commit()

    response = client.get("/api/v1/system-config/provider-readiness")
    assert response.status_code == 200
    provider = response.json()["providers"][0]
    assert provider["configuration_ready"] is False
    assert provider["configuration_issue_count"] == 2
    assert provider["configuration_issue_codes"] == [
        "RUNNINGHUB_NODE_SOURCE_UNSUPPORTED",
        "RUNNINGHUB_VISUAL_PROMPT_BINDING_COUNT_INVALID",
    ]
    assert provider["status"] == "configuration_not_ready"
    assert provider["next_action"] == "revise_configuration"

    with SessionLocal() as session:
        workflow = session.get(WorkflowSlotVersion, workflow_id)
        assert workflow is not None
        assert workflow.node_info_list == legacy_bindings
    assert "must-never-be-returned" not in response.text


def test_spa_entry_disables_caching(client: TestClient) -> None:
    response = client.get("/editor")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
