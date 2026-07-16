from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ControlNextAction(BaseModel):
    code: str
    label: str
    path: str
    incurs_production_cost: bool = False
    confirmation_level: str = "none"


class ProjectControlSummary(BaseModel):
    project_id: str
    title: str
    core_topic: str
    duration_seconds: int
    aspect_ratio: str
    audio_mode: str
    persisted_status: str
    state_row_version: int
    state_changed_at: datetime
    state_actor_type: str
    state_changed_by: str
    state_trigger: str
    state_reason_code: str | None
    blocked_from_state: str | None
    blocked_responsible_aggregate_type: str | None
    blocked_responsible_aggregate_id: str | None
    blocked_allowed_commands: list[str]
    blocked_at: datetime | None
    evaluated_stage: str
    stage_label: str
    active_plan_version: int | None
    active_snapshot_number: int | None
    active_snapshot_status: str | None
    work_counts: dict[str, int]
    asset_counts: dict[str, int]
    blocker_count: int
    latest_event_at: datetime | None
    updated_at: datetime
    next_action: ControlNextAction


class ControlBlocker(BaseModel):
    source_type: str
    source_id: str
    code: str
    message: str
    evidence: dict
    affected_node_keys: list[str]


class ControlCostCurrency(BaseModel):
    currency: str
    estimated_confirmed: float
    charged_confirmed: float
    adjusted_confirmed: float
    refunded_confirmed: float
    pending_event_count: int


class ControlRoute(BaseModel):
    work_item_id: str
    work_item_status: str
    node_key: str | None
    attempt_id: str
    attempt_number: int
    attempt_state: str
    provider: str
    adapter_kind: str | None
    provider_workflow_id: str | None
    provider_task_id: str | None
    request_fingerprint: str
    error_code: str | None


class ControlEvent(BaseModel):
    sequence: int
    event_type: str
    message: str
    data: dict
    created_at: datetime


class ProjectControlView(ProjectControlSummary):
    active_plan: dict | None
    active_snapshot: dict | None
    delivery: dict | None
    costs: list[ControlCostCurrency]
    blockers: list[ControlBlocker]
    routes: list[ControlRoute]
    recent_events: list[ControlEvent]
