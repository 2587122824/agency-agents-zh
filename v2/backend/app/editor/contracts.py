from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EditorCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=8, max_length=80)
    actor_id: str = Field(default="local-user", min_length=1, max_length=48)


class ApproveQualityStage(EditorCommand):
    expected_snapshot_id: str


class GenerateEditorTimeline(EditorCommand):
    expected_snapshot_id: str


class RetryEditorTimeline(EditorCommand):
    failed_agent_run_id: str
    confirm_model_cost: bool


class AudioMasteringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    loudness_target_lufs: float = Field(default=-16.0, ge=-24, le=-9)
    true_peak_limit_dbtp: float = Field(default=-1.0, ge=-3, le=-0.1)
    clipping_control: Literal["limiter"] = "limiter"


class TimelineTrackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    audio_enabled: bool
    subtitle_enabled: bool
    pixels_per_second: int = Field(default=60, ge=20, le=400)
    snap_interval_ms: int = Field(default=100, ge=10, le=1000)
    audio_mastering: AudioMasteringConfig = Field(default_factory=AudioMasteringConfig)


class TimelineItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track_type: Literal["main_video", "audio", "subtitle"]
    sequence_number: int = Field(ge=1)
    asset_id: str | None = None
    label: str = Field(min_length=1, max_length=160)
    gap_reason: str | None = Field(default=None, max_length=500)
    source_in_ms: int | None = Field(default=None, ge=0)
    source_out_ms: int | None = Field(default=None, ge=0)
    timeline_in_ms: int = Field(ge=0)
    timeline_out_ms: int = Field(gt=0)
    transform: dict = Field(default_factory=dict)


class CreateTimelineCandidate(EditorCommand):
    expected_snapshot_id: str
    source: Literal["user", "editor_assistant"]
    source_agent_run_id: str | None = None
    track_config: TimelineTrackConfig
    items: list[TimelineItemInput] = Field(min_length=1, max_length=500)


class ReviseTimelineCandidate(CreateTimelineCandidate):
    expected_row_version: int = Field(ge=1)


class ValidateTimeline(EditorCommand):
    expected_row_version: int = Field(ge=1)


class ConfirmTimeline(EditorCommand):
    expected_row_version: int = Field(ge=1)
    expected_contract_hash: str = Field(min_length=64, max_length=64)
    confirm_delivery_scope: bool


class RenderTimelinePreview(EditorCommand):
    expected_row_version: int = Field(ge=1)
    expected_contract_hash: str = Field(min_length=64, max_length=64)
    quality_profile: Literal["draft_360p"] = "draft_360p"


class ReviewTimelinePreview(EditorCommand):
    expected_row_version: int = Field(ge=1)
    expected_contract_hash: str = Field(min_length=64, max_length=64)
    preview_key: str = Field(min_length=64, max_length=64)
    expected_preview_content_hash: str = Field(min_length=64, max_length=64)
    confirm_visual_continuity_reviewed: bool
    confirm_subjective_sync_reviewed: bool
    confirm_subtitle_readability_reviewed: bool
    confirm_warnings_reviewed: bool


class TimelinePreviewReviewRead(BaseModel):
    schema_version: Literal["editor-preview-review.v1"] = "editor-preview-review.v1"
    review_id: str
    timeline_id: str
    timeline_contract_hash: str
    preview_key: str
    preview_content_hash: str
    quality_status: Literal["review_required", "passed"]
    quality_check_codes: list[str]
    reviewed_by: str
    reviewed_at: datetime


class TimelinePreviewRead(BaseModel):
    schema_version: Literal["editor-preview.v1"] = "editor-preview.v1"
    state: Literal["blocked", "ready"]
    timeline_id: str
    timeline_version_number: int
    timeline_contract_hash: str
    quality_profile: Literal["draft_360p"]
    width: int
    height: int
    fps: int
    duration_ms: int
    cached: bool
    preview_key: str | None = None
    content_url: str | None = None
    content_hash: str | None = None
    byte_size: int | None = None
    validation_report: list[dict] = Field(default_factory=list)
    quality_report: dict | None = None


class TimelineItemRead(BaseModel):
    id: str
    track_type: str
    sequence_number: int
    asset_id: str | None
    asset_state: str | None
    asset_type: str | None
    asset_duration_ms: int | None
    label: str
    gap_reason: str | None
    source_in_ms: int | None
    source_out_ms: int | None
    timeline_in_ms: int
    timeline_out_ms: int
    transform: dict


class TimelineRead(BaseModel):
    id: str
    project_id: str
    snapshot_id: str
    version_number: int
    supersedes_timeline_id: str | None
    status: str
    source: str
    source_agent_run_id: str | None
    output_spec: dict
    track_config: TimelineTrackConfig
    validation_report: list[dict]
    contract_hash: str | None
    row_version: int
    created_by: str
    created_at: datetime
    validated_at: datetime | None
    confirmed_at: datetime | None
    items: list[TimelineItemRead]


class EditorAssetRead(BaseModel):
    id: str
    snapshot_id: str
    dag_node_id: str | None
    node_key: str | None
    asset_type: str
    role: str
    duration_ms: int | None
    width: int | None
    height: int | None
    state: str
    content_hash: str | None


class EditorWorkspaceView(BaseModel):
    project_id: str
    project_title: str
    project_status: str
    active_snapshot_id: str | None
    duration_ms: int
    aspect_ratio: str
    audio_mode: str
    quality_stage_ready: bool
    quality_output_gaps: list[dict]
    available_assets: list[EditorAssetRead]
    timelines: list[TimelineRead]
    latest_editor_run: dict | None
    next_action: dict
