from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..creation.contracts import CommandContext, RequirementVersionRead


class GenerateBrief(CommandContext):
    expected_requirement_version_id: str


class DecideBrief(CommandContext):
    expected_requirement_version_id: str
    reason: str | None = Field(default=None, max_length=500)


class GenerateShotPlan(CommandContext):
    expected_requirement_version_id: str
    creative_brief_candidate_id: str


class DecideShotPlan(CommandContext):
    expected_requirement_version_id: str
    expected_candidate_row_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class ShotContractPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_code: str | None = Field(default=None, min_length=1, max_length=32)
    sequence_number: int | None = Field(default=None, ge=1)
    duration_ms: int | None = Field(default=None, gt=0)
    shot_type: str | None = Field(default=None, min_length=1, max_length=40)
    scene_entity_version_id: str | None = Field(default=None, max_length=48)
    character_entity_version_ids: list[str] | None = None
    outfit_entity_version_ids: list[str] | None = None
    face_visibility: Literal["required", "optional", "not_visible"] | None = None
    text_policy: Literal["forbidden", "allowed", "required"] | None = None
    motion_requirement: Literal["static", "moderate", "significant"] | None = None
    composition: str | None = Field(default=None, min_length=1, max_length=500)
    action: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("at least one structured shot field is required")
        nullable = {"scene_entity_version_id"}
        invalid_nulls = [field for field in self.model_fields_set if field not in nullable and getattr(self, field) is None]
        if invalid_nulls:
            raise ValueError(f"fields cannot be null: {', '.join(sorted(invalid_nulls))}")
        for field in ("character_entity_version_ids", "outfit_entity_version_ids"):
            values = getattr(self, field)
            if values is not None and (len(values) != len(set(values)) or any(not value for value in values)):
                raise ValueError(f"{field} must contain unique non-empty IDs")
        return self


class ShotRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_shot_code: str = Field(min_length=1, max_length=32)
    changes: ShotContractPatch


class ReviseShotPlan(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_requirement_version_id: str
    expected_candidate_row_version: int = Field(ge=1)
    patches: list[ShotRevision] = Field(min_length=1, max_length=200)


class CreativeBriefCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requirement_version_id: str
    agent_run_id: str
    status: str
    brief: dict[str, Any]
    field_sources: dict[str, Any]
    validation_errors: list[dict[str, Any]]
    created_at: datetime
    decided_at: datetime | None


class ShotContract(BaseModel):
    shot_code: str
    sequence_number: int
    duration_ms: int
    shot_type: str
    scene_entity_version_id: str | None
    character_entity_version_ids: list[str]
    outfit_entity_version_ids: list[str]
    face_visibility: str
    text_policy: str
    motion_requirement: str
    composition: str
    action: str


class ShotPlanCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requirement_version_id: str
    creative_brief_candidate_id: str
    agent_run_id: str | None
    supersedes_candidate_id: str | None
    revision_number: int
    source: str
    status: str
    shots: list[ShotContract]
    validation_errors: list[dict[str, Any]]
    row_version: int
    created_by: str
    created_at: datetime
    decided_at: datetime | None


class PlanVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    version_number: int
    requirement_version_id: str
    shot_plan_candidate_id: str
    status: str
    creative_brief: dict[str, Any]
    contract_schema_version: str
    is_active: bool
    confirmed_at: datetime
    confirmed_by: str
    created_at: datetime
    shots: list[ShotContract]


class EntityVersionSummary(BaseModel):
    id: str
    entity_id: str
    entity_type: str
    display_name: str
    version_number: int


class PlanningNextAction(BaseModel):
    code: str
    label: str
    target_ids: list[str] = Field(default_factory=list)
    incurs_model_cost: bool = False
    incurs_production_cost: bool = False


class PlanningCenterView(BaseModel):
    project_id: str
    active_requirement: RequirementVersionRead
    current_brief_candidate: CreativeBriefCandidateRead | None
    accepted_brief_candidate: CreativeBriefCandidateRead | None
    current_shot_candidate: ShotPlanCandidateRead | None
    active_plan: PlanVersionRead | None
    brief_history: list[CreativeBriefCandidateRead]
    shot_plan_history: list[ShotPlanCandidateRead]
    plan_history: list[PlanVersionRead]
    entity_versions: list[EntityVersionSummary]
    next_action: PlanningNextAction
