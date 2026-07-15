from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"

from v2.backend.app.main import app
from v2.backend.app.db.session import engine
from v2.backend.app.workers.worker import process_one


@pytest.fixture()
def client():
    TEST_DATABASE.unlink(missing_ok=True)
    with TestClient(app) as test_client:
        yield test_client
    engine.dispose()
    TEST_DATABASE.unlink(missing_ok=True)


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
