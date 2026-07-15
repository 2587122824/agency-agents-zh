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
from v2.backend.app.db.models import RequirementVersion
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
            "entity_version_id": "char_main_v1",
        },
    )
    assert bound.status_code == 201
    assert bound.json()["confirmed_by"] == "local-user"


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
