from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class RetryAssetQC(QualityCommand):
    failed_agent_run_id: str
    expected_asset_id: str
    expected_row_version: int = Field(ge=1)
    confirm_model_cost: bool


class ReviewAsset(QualityCommand):
    expected_row_version: int = Field(ge=1)
    qc_report_candidate_id: str | None = None
    qc_report_id: str | None = None
    rationale: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def at_most_one_review_source(self):
        if self.qc_report_candidate_id is not None and self.qc_report_id is not None:
            raise ValueError("at most one quality review source is allowed")
        return self


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


class QCReportCandidateFindingRead(BaseModel):
    finding_code: str
    category: str
    severity: str
    confidence: float
    summary: str
    evidence: list[dict]
    contract_refs: list[str]
    suggested_review_action: str


class QCReportCandidateRead(BaseModel):
    id: str
    asset_id: str
    agent_run_id: str
    status: str
    overall_recommendation: str
    findings: list[QCReportCandidateFindingRead]
    analyzer_version: str
    created_at: datetime
    decided_at: datetime | None


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
    latest_qc_candidate: QCReportCandidateRead | None
    latest_qc_agent_run: dict | None
    review_decisions: list[AssetReviewDecisionRead]
    affected_downstream_node_keys: list[str]
    revision_requests: list[dict[str, Any]]
    review_context: dict[str, Any]


class QualityReviewView(BaseModel):
    project_id: str
    project_status: str
    active_snapshot_id: str | None
    assets: list[AssetRead]
    output_gaps: list[dict]
    counts: dict[str, int]
    stage_ready: bool
    next_action: dict
