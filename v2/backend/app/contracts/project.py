from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ProjectStatus = Literal[
    "draft", "collecting_requirements", "decision_required", "planning", "plan_review",
    "contract_ready", "production_ready", "producing", "quality_review", "editing",
    "delivery_ready", "blocked", "completed", "cancelled",
    "confirmed", "queued", "in_progress", "review_required",
]
DecisionStatus = Literal["pending", "resolved"]
AudioMode = Literal["off", "voiceover"]


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    core_topic: str = Field(min_length=1, max_length=500)
    duration_seconds: int = Field(ge=5, le=3600)
    aspect_ratio: Literal["9:16", "16:9", "1:1"]
    audio_mode: AudioMode


class ArchiveProject(BaseModel):
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=80)
    expected_row_version: int = Field(ge=1)
    confirm_archive: Literal[True]


class RestoreProject(BaseModel):
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=80)
    expected_row_version: int = Field(ge=1)


class ProjectRead(ProjectCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: ProjectStatus
    row_version: int
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
    archived_at: datetime | None
    archived_by: str | None
    created_at: datetime
    updated_at: datetime


class DecisionCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,119}$")
    label: str = Field(min_length=1, max_length=160)
    value: Any | None = None
    status: DecisionStatus = "pending"


class DecisionResolve(BaseModel):
    value: Any


class DecisionRead(DecisionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    source: Literal["user", "system"]
    created_at: datetime
    resolved_at: datetime | None


class WorkItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    snapshot_id: str | None
    dag_node_id: str | None
    kind: str
    payload: dict
    status: str
    error: str | None
    priority: int
    request_fingerprint: str | None
    current_attempt_id: str | None
    available_at: datetime
    row_version: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProjectDetail(ProjectRead):
    decisions: list[DecisionRead]
    work_items: list[WorkItemRead]


class QueueRequest(BaseModel):
    kind: Literal["contract_validation"] = "contract_validation"
