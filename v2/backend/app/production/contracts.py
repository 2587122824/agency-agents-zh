from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class ShotWorkflowAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_code: str = Field(min_length=1, max_length=80)
    keyframe_workflow_slot_version_id: str | None = None
    video_workflow_slot_version_id: str


class AnalyzeProductionImpact(ProductionCommand):
    plan_version_id: str
    production_config_version_id: str
    video_spec_version_id: str
    shot_workflow_assignments: list[ShotWorkflowAssignment] = Field(min_length=1)
    tts_workflow_slot_version_id: str | None = None
    pricing_catalog_version_id: str | None = None


class CreateProductionSnapshot(ProductionCommand):
    impact_analysis_id: str
    analysis_hash: str = Field(min_length=64, max_length=64)
    confirm_contract_scope: bool


class LockProductionSnapshot(ProductionCommand):
    expected_contract_hash: str = Field(min_length=64, max_length=64)
    expected_estimated_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    expected_currency: str = Field(pattern=r"^[A-Z]{3,12}$")
    confirm_high_risk_cost: bool


class ActivateProductionSnapshot(ProductionCommand):
    expected_contract_hash: str = Field(min_length=64, max_length=64)


class SubmitProduction(ProductionCommand):
    expected_contract_hash: str = Field(min_length=64, max_length=64)
    expected_estimated_cost: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    expected_currency: str = Field(pattern=r"^[A-Z]{3,12}$")
    expected_dag_node_ids: list[str] = Field(min_length=1)
    confirm_high_risk_submission: bool


class ImpactAnalysisRead(BaseModel):
    id: str
    project_id: str
    plan_version_id: str
    production_config_version_id: str
    pricing_catalog_version_id: str | None
    status: str
    selection: dict
    manifest: dict
    analysis_hash: str
    snapshot_contract_hash: str
    validation_errors: list[dict]
    execution_blockers: list[dict]
    estimated_call_count: int
    cost_status: str
    estimated_cost: float | None
    currency: str | None
    created_by: str
    created_at: datetime


class DAGNodeRead(BaseModel):
    id: str
    node_key: str
    kind: str
    shot_id: str | None
    input_contract: dict
    output_contract: dict
    workflow_slot_version_id: str | None
    pricing_rule_id: str | None
    pricing_quantity: float | None
    pricing_unit: str | None
    estimated_cost: float | None
    currency: str | None


class DependencyEdgeRead(BaseModel):
    id: str
    parent_node_id: str
    child_node_id: str
    dependency_type: str
    input_slot: str | None


class ProductionSnapshotRead(BaseModel):
    id: str
    project_id: str
    plan_version_id: str
    production_config_version_id: str
    pricing_catalog_version_id: str | None
    impact_analysis_id: str
    snapshot_number: int
    status: str
    audio_mode: str
    output_spec: dict
    selection: dict
    contract: dict
    contract_hash: str
    estimated_call_count: int
    cost_status: str
    estimated_cost: float | None
    currency: str | None
    execution_blockers: list[dict]
    created_by: str
    created_at: datetime
    locked_at: datetime | None
    activated_at: datetime | None
    entity_versions: list[dict]
    nodes: list[DAGNodeRead]
    edges: list[DependencyEdgeRead]


class WorkAttemptRead(BaseModel):
    id: str
    work_item_id: str
    attempt_number: int
    trigger: str
    provider: str
    provider_task_id: str | None
    request_fingerprint: str
    request_manifest: dict
    response_manifest: dict | None
    state: str
    execution_lock_owner: str | None
    execution_lock_expires_at: datetime | None
    submitted_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    error_detail: str | None
    created_at: datetime


class ExecutionWorkItemRead(BaseModel):
    id: str
    project_id: str
    snapshot_id: str
    dag_node_id: str
    node_key: str
    kind: str
    status: str
    error: str | None
    priority: int
    request_fingerprint: str
    current_attempt_id: str
    available_at: datetime
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: list[WorkAttemptRead]


class ProductionExecutionView(BaseModel):
    project_id: str
    project_status: str
    active_snapshot_id: str | None
    snapshot: ProductionSnapshotRead | None
    work_items: list[ExecutionWorkItemRead]
    blockers: list[dict]


class PublishedConfigChoice(BaseModel):
    id: str
    config_key: str
    version_number: int
    display_name: str
    video_specs: list[dict]
    workflow_slots: list[dict]
    pricing_catalogs: list[dict]


class ProductionPreparationView(BaseModel):
    project_id: str
    active_plan_id: str | None
    audio_mode: str
    published_configurations: list[PublishedConfigChoice]
    analyses: list[ImpactAnalysisRead]
    snapshots: list[ProductionSnapshotRead]
    next_action: dict
