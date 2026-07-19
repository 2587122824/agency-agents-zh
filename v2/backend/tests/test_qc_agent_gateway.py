from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

TEST_DATABASE = Path(__file__).resolve().parent / "test_studio.db"
TEST_RUNTIME = Path(__file__).resolve().parent / "test_runtime"
os.environ["V2_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["V2_RUNTIME_ROOT"] = str(TEST_RUNTIME)

from v2.backend.app.creation.agent_gateway import AgentGatewayError
from v2.backend.app.providers.credentials import EnvironmentCredentialResolver
from v2.backend.app.quality.agent_gateway import ConfiguredQCGateway, QCSelection


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class FakeTransport:
    def __init__(self, output: object) -> None:
        content = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        self.response = {"id": "qc-request-1", "choices": [{"message": {"content": content}}], "usage": {"total_tokens": 21}}
        self.calls: list[dict] = []

    def create_chat_completion(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.response


def selection() -> QCSelection:
    return QCSelection(
        "production_config_1", "model_config_qc_1", "provider_config_1", "Vision Provider", "Visual QC",
        "vision-model", "https://api.example.test/v1", "env://TEST_QC_KEY", 30,
        "qc-agent-input.v1", "qc-agent-prompt.v1", "qc-report-candidate.v1", 2000, {"temperature": 0.1},
        ("vision_analysis",),
    )


def manifest(*, face_visibility: str = "required") -> dict:
    ref = "dag_node.node_1.input_contract.shot.face_visibility"
    return {
        "contract_version": "qc-agent-input.v1",
        "project_id": "project_1",
        "asset": {"id": "asset_1", "content_hash": "a" * 64, "asset_type": "image", "mime_type": "image/png", "width": 480, "height": 848, "duration_ms": None},
        "media_probe_id": "media-probe:" + "a" * 64,
        "snapshot_id": "snapshot_1",
        "dag_node_id": "node_1",
        "shot_code": "SH-001",
        "shot_contract": {"face_visibility": face_visibility, "text_policy": "forbidden", "motion_requirement": "static"},
        "entity_reference_asset_ids": [],
        "deterministic_checks": [{"id": "file-contract:asset_1", "status": "passed", "ruleset_version": "v2.file-contract.v1"}],
        "qc_policy_version": "qc-policy.v1",
        "contract_reference_catalog": [ref],
        "system_config_version": "production_config_1",
    }


def output() -> dict:
    return {
        "overall_recommendation": "review_required",
        "findings": [{
            "finding_code": "IDENTITY_SIMILARITY_UNCERTAIN",
            "category": "identity",
            "severity": "medium",
            "confidence": 0.78,
            "summary": "人物外观需要与参考进一步比对。",
            "evidence": [{"kind": "asset", "asset_id": "asset_1", "region": None}],
            "contract_refs": ["dag_node.node_1.input_contract.shot.face_visibility"],
            "suggested_review_action": "compare_reference",
        }],
        "analyzer_version": "visual-qc.v1",
    }


def gateway_for(value: object) -> tuple[ConfiguredQCGateway, FakeTransport]:
    transport = FakeTransport(value)
    gateway = ConfiguredQCGateway(
        transport=transport,
        credential_resolver=EnvironmentCredentialResolver({"TEST_QC_KEY": "secret"}, {"TEST_QC_KEY"}),
    )
    return gateway, transport


def test_qc_gateway_sends_exact_manifest_and_image_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    path = tmp_path / "asset.png"
    path.write_bytes(PNG)
    gateway, transport = gateway_for(output())

    result = gateway.invoke(selection(), manifest(), path)

    assert result.output.findings[0].finding_code == "IDENTITY_SIMILARITY_UNCERTAIN"
    assert len(transport.calls) == 1
    content = transport.calls[0]["payload"]["messages"][1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "qc-agent-input.v1" in content[0]["text"]


@pytest.mark.parametrize("mutate", [
    lambda value: value["findings"][0].update({"contract_refs": ["unknown.contract"]}),
    lambda value: value["findings"][0]["evidence"][0].update({"asset_id": "asset_other"}),
    lambda value: value.update({"overall_recommendation": "passed"}),
])
def test_qc_gateway_rejects_invalid_candidate_without_retry(monkeypatch, tmp_path: Path, mutate) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    path = tmp_path / "asset.png"
    path.write_bytes(PNG)
    value = output()
    mutate(value)
    gateway, transport = gateway_for(value)

    with pytest.raises(AgentGatewayError):
        gateway.invoke(selection(), manifest(), path)

    assert len(transport.calls) == 1


def test_qc_gateway_rejects_face_missing_when_face_is_not_required(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "true")
    path = tmp_path / "asset.png"
    path.write_bytes(PNG)
    value = output()
    value["findings"][0].update({"finding_code": "FACE_MISSING"})
    gateway, transport = gateway_for(value)

    with pytest.raises(AgentGatewayError) as raised:
        gateway.invoke(selection(), manifest(face_visibility="not_visible"), path)

    assert raised.value.code == "QC_OUTPUT_CONTRACT_INVALID"
    assert len(transport.calls) == 1
