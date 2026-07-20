from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..creation.contracts import AgentRunRead, CommandContext, RequirementVersionRead
from ..revision.contracts import AssetRevisionRequestRead


class GenerateBrief(CommandContext):
    expected_requirement_version_id: str


class RetryBrief(CommandContext):
    expected_requirement_version_id: str
    failed_agent_run_id: str = Field(min_length=1, max_length=48)
    confirm_model_cost: bool


class ReviseBrief(CommandContext):
    expected_requirement_version_id: str
    revision_instruction: str = Field(min_length=1, max_length=4000)
    confirm_model_cost: bool


class DecideBrief(CommandContext):
    expected_requirement_version_id: str
    reason: str | None = Field(default=None, max_length=500)


class GenerateShotPlan(CommandContext):
    expected_requirement_version_id: str
    creative_brief_candidate_id: str


class RetryShotPlan(CommandContext):
    expected_requirement_version_id: str
    failed_agent_run_id: str = Field(min_length=1, max_length=48)
    confirm_model_cost: bool


class DecideShotPlan(CommandContext):
    expected_requirement_version_id: str
    expected_candidate_row_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class StartShotPlanRevision(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_plan_version_id: str = Field(min_length=1, max_length=48)


class CancelShotPlanRevision(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_candidate_row_version: int = Field(ge=1)


class ShotContractPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    shot_code: str | None = Field(default=None, min_length=1, max_length=32)
    sequence_number: int | None = Field(default=None, ge=1)
    duration_ms: int | None = Field(default=None, gt=0)
    narrative_beat_code: str | None = Field(default=None, pattern=r"^BEAT_[0-9]{2,3}$")
    brief_segment_codes: list[str] | None = None
    continuity_group_id: str | None = Field(default=None, pattern=r"^CONT-[0-9]{3}$")
    continuity_relation: Literal["same_moment", "time_jump", "location_change", "outfit_change"] | None = None
    action_count: Literal[1] | None = None
    shot_purpose: Literal["establish", "develop", "demonstrate", "contrast", "transition", "resolve"] | None = None
    framing: Literal["extreme_close_up", "close_up", "medium", "full", "wide"] | None = None
    camera_angle: Literal["eye_level", "high", "low", "top_down", "over_shoulder"] | None = None
    camera_motion: Literal["locked", "pan", "tilt", "dolly", "tracking", "handheld"] | None = None
    subject_motion: Literal["none", "subtle", "moderate", "significant"] | None = None
    scene_entity_version_id: str | None = Field(default=None, max_length=48)
    character_entity_version_ids: list[str] | None = None
    outfit_entity_version_ids: list[str] | None = None
    product_entity_version_ids: list[str] | None = None
    primary_reference_entity_version_id: str | None = Field(default=None, max_length=48)
    face_visibility: Literal["required", "optional", "not_visible"] | None = None
    face_subject_entity_version_ids: list[str] | None = None
    text_policy: Literal["forbidden", "allowed", "required"] | None = None
    required_on_screen_text: str | None = Field(default=None, min_length=1, max_length=1000)
    audio_requirement: Literal["off", "lip_motion_only", "configured"] | None = None
    composition: str | None = Field(default=None, min_length=1, max_length=500)
    action: str | None = Field(default=None, min_length=1, max_length=1000)
    visual_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, min_length=1, max_length=2000)
    new_information: str | None = Field(default=None, min_length=1, max_length=1000)
    generation_requirements: dict[str, bool] | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("at least one structured shot field is required")
        nullable = {"scene_entity_version_id", "primary_reference_entity_version_id", "negative_prompt", "continuity_group_id", "required_on_screen_text"}
        invalid_nulls = [field for field in self.model_fields_set if field not in nullable and getattr(self, field) is None]
        if invalid_nulls:
            raise ValueError(f"fields cannot be null: {', '.join(sorted(invalid_nulls))}")
        for field in ("brief_segment_codes", "character_entity_version_ids", "outfit_entity_version_ids", "product_entity_version_ids", "face_subject_entity_version_ids"):
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


class ReviseShotPlanWithDirector(CommandContext):
    model_config = ConfigDict(extra="forbid")
    expected_requirement_version_id: str
    expected_candidate_row_version: int = Field(ge=1)
    selected_shot_codes: list[str] = Field(min_length=1, max_length=200)
    revision_instruction: str = Field(min_length=1, max_length=4000)
    confirm_model_cost: bool

    @model_validator(mode="after")
    def selected_shots_are_unique(self):
        if len(self.selected_shot_codes) != len(set(self.selected_shot_codes)):
            raise ValueError("selected shot codes must be unique")
        return self


class CreativeBriefCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requirement_version_id: str
    agent_run_id: str
    supersedes_candidate_id: str | None
    revision_number: int
    source: str
    status: str
    brief: dict[str, Any]
    field_sources: dict[str, Any]
    validation_errors: list[dict[str, Any]]
    created_by: str
    created_at: datetime
    decided_at: datetime | None


class ShotContract(BaseModel):
    shot_code: str
    sequence_number: int
    duration_ms: int
    narrative_beat_code: str | None = None
    brief_segment_codes: list[str]
    continuity_group_id: str | None = None
    continuity_relation: str
    action_count: int = 1
    shot_purpose: str
    framing: str
    camera_angle: str
    camera_motion: str
    subject_motion: str
    scene_entity_version_id: str | None
    character_entity_version_ids: list[str]
    outfit_entity_version_ids: list[str]
    product_entity_version_ids: list[str] = Field(default_factory=list)
    primary_reference_entity_version_id: str | None = None
    face_visibility: str
    face_subject_entity_version_ids: list[str]
    text_policy: str
    required_on_screen_text: str | None
    audio_requirement: str = "off"
    composition: str
    action: str
    visual_prompt: str | None = None
    negative_prompt: str | None = None
    new_information: str
    generation_requirements: dict[str, bool]


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
    source_attachment_id: str | None
    source_mime_type: str | None
    source_attachment_verified: bool


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
    revision_draft: ShotPlanCandidateRead | None
    revision_context: AssetRevisionRequestRead | None
    active_plan: PlanVersionRead | None
    brief_history: list[CreativeBriefCandidateRead]
    shot_plan_history: list[ShotPlanCandidateRead]
    plan_history: list[PlanVersionRead]
    latest_planner_run: AgentRunRead | None
    latest_director_run: AgentRunRead | None
    entity_versions: list[EntityVersionSummary]
    next_action: PlanningNextAction
