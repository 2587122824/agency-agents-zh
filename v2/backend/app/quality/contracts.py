from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class QualityCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class RegisterAttemptAsset(QualityCommand):
    output_index: int = Field(ge=0)
    expected_response_manifest_hash: str = Field(min_length=64, max_length=64)


class VerifyAsset(QualityCommand):
    expected_row_version: int = Field(ge=1)


class RunAssetQC(QualityCommand):
    expected_row_version: int = Field(ge=1)


class ReviewAsset(QualityCommand):
    expected_row_version: int = Field(ge=1)
    qc_report_id: str
    rationale: str = Field(min_length=1, max_length=1000)


class QCFindingRead(BaseModel):
    id: str
    code: str
    severity: str
    evidence: dict
    contract_field: str | None
    disposition: str
    created_at: datetime


class QCReportRead(BaseModel):
    id: str
    report_number: int
    ruleset_version: str
    status: str
    analyzer: str
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: str | None
    findings: list[QCFindingRead]


class AssetReviewDecisionRead(BaseModel):
    id: str
    decision: str
    rationale: str
    actor_id: str
    created_at: datetime


class AssetRead(BaseModel):
    id: str
    project_id: str
    snapshot_id: str
    work_attempt_id: str | None
    dag_node_id: str | None
    node_key: str | None
    output_index: int
    asset_type: str
    role: str
    uri: str
    storage_backend: str
    content_hash: str | None
    mime_type: str | None
    byte_size: int | None
    width: int | None
    height: int | None
    duration_ms: int | None
    state: str
    row_version: int
    created_at: datetime
    verified_at: datetime | None
    approved_at: datetime | None
    archived_at: datetime | None
    latest_qc_report: QCReportRead | None
    review_decisions: list[AssetReviewDecisionRead]
    affected_downstream_node_keys: list[str]


class QualityReviewView(BaseModel):
    project_id: str
    project_status: str
    active_snapshot_id: str | None
    assets: list[AssetRead]
    output_gaps: list[dict]
    counts: dict[str, int]
    stage_ready: bool
    next_action: dict
