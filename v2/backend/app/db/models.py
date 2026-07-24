from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("project"))
    title: Mapped[str] = mapped_column(String(160))
    core_topic: Mapped[str] = mapped_column(String(500))
    duration_seconds: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(16))
    audio_mode: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    state_actor_type: Mapped[str] = mapped_column(String(16), default="system")
    state_changed_by: Mapped[str] = mapped_column(String(80), default="system")
    state_trigger: Mapped[str] = mapped_column(String(80), default="project_created")
    state_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocked_from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blocked_responsible_aggregate_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    blocked_responsible_aggregate_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    blocked_allowed_commands: Mapped[list] = mapped_column(JSON, default=list)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    archived_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    active_snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("production_snapshots.id"), nullable=True, index=True)
    delivery_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    decisions: Mapped[list[Decision]] = relationship(back_populates="project", cascade="all, delete-orphan")
    work_items: Mapped[list[WorkItem]] = relationship(back_populates="project", cascade="all, delete-orphan")
    messages: Mapped[list[Message]] = relationship(back_populates="project", cascade="all, delete-orphan")
    requirement_versions: Mapped[list[RequirementVersion]] = relationship(back_populates="project", cascade="all, delete-orphan")
    requirement_candidates: Mapped[list[RequirementCandidate]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("decision"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(160))
    value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    source: Mapped[str] = mapped_column(String(24), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="decisions")


class DecisionChangeImpactAnalysis(Base):
    __tablename__ = "decision_change_impact_analyses"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("decision_impact"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decisions.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[str] = mapped_column(String(40), default="observed_lineage_with_active_cost")
    current_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    observed_manifest_ids: Mapped[list] = mapped_column(JSON, default=list)
    target_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    estimated_work_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_status: Mapped[str] = mapped_column(String(32), default="not_applicable")
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    analysis_hash: Mapped[str] = mapped_column(String(64), index=True)
    active_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("production_snapshots.id"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DecisionChangeImpactTarget(Base):
    __tablename__ = "decision_change_impact_targets"
    __table_args__ = (UniqueConstraint("analysis_id", "record_type", "record_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("impact_target"))
    analysis_id: Mapped[str] = mapped_column(
        ForeignKey("decision_change_impact_analyses.id"), index=True
    )
    record_type: Mapped[str] = mapped_column(String(40), index=True)
    record_id: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[str] = mapped_column(String(200))
    record_status: Mapped[str] = mapped_column(String(40))
    authority: Mapped[str] = mapped_column(String(24))
    impact_kind: Mapped[str] = mapped_column(String(32), default="review_candidate")
    reason_code: Mapped[str] = mapped_column(String(80), default="OBSERVED_DECISION_LINEAGE")
    included_in_estimate: Mapped[bool] = mapped_column(Boolean, default=False)
    estimated_work_units: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (UniqueConstraint("snapshot_id", "dag_node_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("work"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("production_snapshots.id"), nullable=True, index=True)
    dag_node_id: Mapped[str | None] = mapped_column(ForeignKey("dag_nodes.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    current_attempt_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="work_items")


class WorkAttempt(Base):
    __tablename__ = "work_attempts"
    __table_args__ = (
        UniqueConstraint("work_item_id", "attempt_number"),
        UniqueConstraint("provider", "provider_task_id"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("attempt"))
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(120))
    provider_task_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    request_manifest: Mapped[dict] = mapped_column(JSON)
    response_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    execution_lock_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    execution_lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("work_attempt_id", "output_index"),
        UniqueConstraint("storage_backend", "uri"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("asset"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    work_attempt_id: Mapped[str | None] = mapped_column(ForeignKey("work_attempts.id"), nullable=True, index=True)
    dag_node_id: Mapped[str | None] = mapped_column(ForeignKey("dag_nodes.id"), nullable=True, index=True)
    output_index: Mapped[int] = mapped_column(Integer)
    asset_type: Mapped[str] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(80))
    uri: Mapped[str] = mapped_column(String(500))
    storage_backend: Mapped[str] = mapped_column(String(40))
    provider_output_manifest: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QCReport(Base):
    __tablename__ = "qc_reports"
    __table_args__ = (UniqueConstraint("asset_id", "report_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("qc_report"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    report_number: Mapped[int] = mapped_column(Integer)
    ruleset_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32), index=True)
    analyzer: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(48), nullable=True)


class QCFinding(Base):
    __tablename__ = "qc_findings"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("qc_finding"))
    qc_report_id: Mapped[str] = mapped_column(ForeignKey("qc_reports.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(24))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    contract_field: Mapped[str | None] = mapped_column(String(160), nullable=True)
    disposition: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QCReportCandidate(Base):
    __tablename__ = "qc_report_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("qc_candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_review", index=True)
    overall_recommendation: Mapped[str] = mapped_column(String(32))
    findings: Mapped[list] = mapped_column(JSON, default=list)
    analyzer_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssetReviewDecision(Base):
    __tablename__ = "asset_review_decisions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("asset_review"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    qc_report_id: Mapped[str] = mapped_column(ForeignKey("qc_reports.id"), index=True)
    decision: Mapped[str] = mapped_column(String(24))
    rationale: Mapped[str] = mapped_column(String(1000))
    actor_id: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssetRevisionRequest(Base):
    __tablename__ = "asset_revision_requests"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("asset_revision"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), index=True)
    shot_id: Mapped[str | None] = mapped_column(ForeignKey("shots.id"), nullable=True, index=True)
    shot_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    issue_scope: Mapped[str] = mapped_column(String(24), index=True)
    issue_code: Mapped[str] = mapped_column(String(40), index=True)
    rationale: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    source_asset_state: Mapped[str] = mapped_column(String(32))
    source_asset_row_version: Mapped[int] = mapped_column(Integer)
    affected_downstream_node_keys: Mapped[list] = mapped_column(JSON, default=list)
    draft_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("shot_plan_candidates.id"), nullable=True, index=True
    )
    resulting_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("shot_plan_candidates.id"), nullable=True, index=True
    )
    resulting_plan_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_versions.id"), nullable=True, index=True
    )
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Timeline(Base):
    __tablename__ = "timelines"
    __table_args__ = (UniqueConstraint("project_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("timeline"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    supersedes_timeline_id: Mapped[str | None] = mapped_column(ForeignKey("timelines.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True, index=True)
    output_spec: Mapped[dict] = mapped_column(JSON)
    track_config: Mapped[dict] = mapped_column(JSON)
    validation_report: Mapped[list] = mapped_column(JSON, default=list)
    contract_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TimelineItem(Base):
    __tablename__ = "timeline_items"
    __table_args__ = (UniqueConstraint("timeline_id", "track_type", "sequence_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("timeline_item"))
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"), index=True)
    track_type: Mapped[str] = mapped_column(String(24), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(160))
    gap_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_in_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_out_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeline_in_ms: Mapped[int] = mapped_column(Integer)
    timeline_out_ms: Mapped[int] = mapped_column(Integer)
    transform: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    __table_args__ = (UniqueConstraint("timeline_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("delivery"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    timeline_id: Mapped[str] = mapped_column(ForeignKey("timelines.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="authorized", index=True)
    execution_kind: Mapped[str] = mapped_column(String(32))
    request_manifest: Mapped[dict] = mapped_column(JSON)
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    work_item_id: Mapped[str | None] = mapped_column(ForeignKey("work_items.id"), nullable=True, unique=True)
    final_asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True, unique=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    render_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    render_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    output_registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectEvent(Base):
    __tablename__ = "project_events"
    __table_args__ = (
        UniqueConstraint("event_id"),
        UniqueConstraint("project_id", "project_sequence"),
    )

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(48), default=lambda: new_id("event"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    project_sequence: Mapped[int] = mapped_column(Integer)
    snapshot_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    aggregate_type: Mapped[str] = mapped_column(String(40), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(80), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(80), index=True)
    actor_type: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("outbox"))
    event_id: Mapped[str] = mapped_column(ForeignKey("project_events.event_id"), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    topic: Mapped[str] = mapped_column(String(80), default="project.events")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("conversation"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    started_by: Mapped[str] = mapped_column(String(48), default="local-user")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Message(Base):
    __tablename__ = "creation_messages"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("message"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    conversation_session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    content: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("creation_messages.id"), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="messages")


class RequirementVersion(Base):
    __tablename__ = "requirement_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("requirement"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    fields: Mapped[dict] = mapped_column(JSON)
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    candidate_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(48), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    project: Mapped[Project] = relationship(back_populates="requirement_versions")


class AgentInputManifest(Base):
    __tablename__ = "agent_input_manifests"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("manifest"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"))
    message_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision_ids: Mapped[list] = mapped_column(JSON, default=list)
    attachment_binding_ids: Mapped[list] = mapped_column(JSON, default=list)
    system_config_version: Mapped[str] = mapped_column(String(48), default="v2.creation.mock.v1")
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("agent_run"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_role: Mapped[str] = mapped_column(String(40), default="creative")
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)
    input_manifest_id: Mapped[str] = mapped_column(ForeignKey("agent_input_manifests.id"))
    model_provider: Mapped[str] = mapped_column(String(40), default="mock")
    model_name: Mapped[str] = mapped_column(String(80), default="deterministic-creative-v1")
    production_config_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    model_config_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    provider_config_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    prompt_contract_version: Mapped[str] = mapped_column(String(48), default="creative.v1")
    output_schema_version: Mapped[str] = mapped_column(String(48), default="requirement-candidate.v1")
    parsed_candidate_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    parsed_proposal_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    raw_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    token_usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CreativeTurnProposal(Base):
    __tablename__ = "creative_turn_proposals"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("cproposal"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), unique=True, index=True)
    assistant_message_id: Mapped[str] = mapped_column(ForeignKey("creation_messages.id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    suggestion_sets: Mapped[list] = mapped_column(JSON, default=list)
    explicit_updates: Mapped[list] = mapped_column(JSON, default=list)
    creative_diagnosis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    clarifying_question: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prompt_contract_version: Mapped[str] = mapped_column(String(48))
    output_schema_version: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CreativeSuggestionSelection(Base):
    __tablename__ = "creative_suggestion_selections"
    __table_args__ = (UniqueConstraint("proposal_id", "suggestion_set_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("cselection"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("creative_turn_proposals.id"), index=True)
    suggestion_set_id: Mapped[str] = mapped_column(String(48))
    option_id: Mapped[str] = mapped_column(String(48))
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("requirement_candidates.id"), nullable=True)
    selected_by: Mapped[str] = mapped_column(String(48))
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RequirementCandidate(Base):
    __tablename__ = "requirement_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    conversation_session_id: Mapped[str] = mapped_column(ForeignKey("conversation_sessions.id"), index=True)
    base_requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    supersedes_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("requirement_candidates.id"), nullable=True, index=True
    )
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_review", index=True)
    fields: Mapped[dict] = mapped_column(JSON)
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    change_summary: Mapped[list] = mapped_column(JSON, default=list)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="requirement_candidates")


class ClarificationRequest(Base):
    __tablename__ = "clarification_requests"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("clarification"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("requirement_candidates.id"), nullable=True)
    base_requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    field_key: Mapped[str] = mapped_column(String(120))
    reason_code: Mapped[str] = mapped_column(String(80), default="REQUIRED_FIELD_MISSING")
    question: Mapped[str] = mapped_column(String(500))
    options: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    resolution: Mapped[object | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("attachment"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    message_id: Mapped[str | None] = mapped_column(ForeignKey("creation_messages.id"), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120))
    byte_size: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(500))
    verification_status: Mapped[str] = mapped_column(String(32), default="verified")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AttachmentBinding(Base):
    __tablename__ = "attachment_bindings"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("binding"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    attachment_id: Mapped[str] = mapped_column(ForeignKey("attachments.id"), index=True)
    binding_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entity_version_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="confirmed")
    confirmed_by: Mapped[str] = mapped_column(String(48), default="user")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CommandReceipt(Base):
    __tablename__ = "command_receipts"
    __table_args__ = (UniqueConstraint("project_id", "command_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("command"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    command_id: Mapped[str] = mapped_column(String(80))
    command_type: Mapped[str] = mapped_column(String(80))
    result_type: Mapped[str] = mapped_column(String(80))
    result_id: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("project_id", "id"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EntityVersion(Base):
    __tablename__ = "entity_versions"
    __table_args__ = (UniqueConstraint("entity_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("entity_version"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("entities.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="confirmed")
    source_attachment_id: Mapped[str | None] = mapped_column(ForeignKey("attachments.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str] = mapped_column(String(48), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CreativeBriefCandidate(Base):
    __tablename__ = "creative_brief_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("brief_candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"))
    supersedes_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("creative_brief_candidates.id"), nullable=True, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32), default="planner_agent")
    status: Mapped[str] = mapped_column(String(32), default="awaiting_review", index=True)
    brief: Mapped[dict] = mapped_column(JSON)
    field_sources: Mapped[dict] = mapped_column(JSON, default=dict)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(48), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShotPlanCandidate(Base):
    __tablename__ = "shot_plan_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("shot_candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    creative_brief_candidate_id: Mapped[str] = mapped_column(ForeignKey("creative_brief_candidates.id"))
    agent_run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"), nullable=True)
    supersedes_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("shot_plan_candidates.id"), nullable=True, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(32), default="director_agent")
    status: Mapped[str] = mapped_column(String(32), default="awaiting_review", index=True)
    shots: Mapped[list] = mapped_column(JSON)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(48), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProductionPlanCandidate(Base):
    __tablename__ = "production_plan_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("production_plan_candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), index=True)
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    video_spec_version_id: Mapped[str] = mapped_column(ForeignKey("video_spec_versions.id"), index=True)
    agent_run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_review", index=True)
    proposed_assignments: Mapped[list] = mapped_column(JSON)
    confirmed_assignments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str] = mapped_column(String(48), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("project_id", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("plan"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
    shot_plan_candidate_id: Mapped[str] = mapped_column(ForeignKey("shot_plan_candidates.id"))
    status: Mapped[str] = mapped_column(String(24), default="confirmed", index=True)
    creative_brief: Mapped[dict] = mapped_column(JSON)
    contract_schema_version: Mapped[str] = mapped_column(String(48), default="shot-plan.v1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    confirmed_by: Mapped[str] = mapped_column(String(48), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (
        UniqueConstraint("plan_version_id", "shot_code"),
        UniqueConstraint("plan_version_id", "sequence_number"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("shot"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), index=True)
    shot_code: Mapped[str] = mapped_column(String(32))
    sequence_number: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    narrative_beat_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    brief_segment_codes: Mapped[list] = mapped_column(JSON, default=list)
    continuity_group_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    continuity_relation: Mapped[str] = mapped_column(String(32))
    action_count: Mapped[int] = mapped_column(Integer, default=1)
    shot_purpose: Mapped[str] = mapped_column(String(32))
    framing: Mapped[str] = mapped_column(String(32))
    camera_angle: Mapped[str] = mapped_column(String(32))
    camera_motion: Mapped[str] = mapped_column(String(32))
    subject_motion: Mapped[str] = mapped_column(String(32))
    scene_entity_version_id: Mapped[str | None] = mapped_column(ForeignKey("entity_versions.id"), nullable=True)
    character_entity_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    outfit_entity_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    product_entity_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    primary_reference_entity_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("entity_versions.id"), nullable=True
    )
    face_visibility: Mapped[str] = mapped_column(String(24))
    face_subject_entity_version_ids: Mapped[list] = mapped_column(JSON, default=list)
    text_policy: Mapped[str] = mapped_column(String(24))
    required_on_screen_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_requirement: Mapped[str] = mapped_column(String(24), default="off")
    composition: Mapped[str] = mapped_column(String(500))
    action: Mapped[str] = mapped_column(String(1000))
    visual_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    guide_frame_prompts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_information: Mapped[str] = mapped_column(String(1000))
    generation_requirements: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductionImpactAnalysis(Base):
    __tablename__ = "production_impact_analyses"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("impact"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), index=True)
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    pricing_catalog_version_id: Mapped[str | None] = mapped_column(ForeignKey("pricing_catalog_versions.id"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_confirmation", index=True)
    selection: Mapped[dict] = mapped_column(JSON)
    manifest: Mapped[dict] = mapped_column(JSON)
    analysis_hash: Mapped[str] = mapped_column(String(64), index=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list)
    execution_blockers: Mapped[list] = mapped_column(JSON, default=list)
    estimated_call_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_status: Mapped[str] = mapped_column(String(32), default="not_configured")
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductionSnapshot(Base):
    __tablename__ = "production_snapshots"
    __table_args__ = (UniqueConstraint("project_id", "snapshot_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("snapshot"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), index=True)
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    pricing_catalog_version_id: Mapped[str | None] = mapped_column(ForeignKey("pricing_catalog_versions.id"), nullable=True, index=True)
    impact_analysis_id: Mapped[str] = mapped_column(ForeignKey("production_impact_analyses.id"), unique=True)
    snapshot_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="preparing", index=True)
    audio_mode: Mapped[str] = mapped_column(String(24))
    output_spec: Mapped[dict] = mapped_column(JSON)
    selection: Mapped[dict] = mapped_column(JSON)
    contract: Mapped[dict] = mapped_column(JSON)
    contract_hash: Mapped[str] = mapped_column(String(64), index=True)
    estimated_call_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_status: Mapped[str] = mapped_column(String(32), default="not_configured")
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    execution_blockers: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image_phase_required: Mapped[bool] = mapped_column(Boolean, default=False)
    image_phase_approval_manifest: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    image_phase_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    image_phase_approved_by: Mapped[str | None] = mapped_column(String(48), nullable=True)


class SnapshotEntityVersion(Base):
    __tablename__ = "snapshot_entity_versions"
    __table_args__ = (UniqueConstraint("snapshot_id", "entity_version_id", "role"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("snapshot_entity"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    entity_version_id: Mapped[str] = mapped_column(ForeignKey("entity_versions.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DAGNode(Base):
    __tablename__ = "dag_nodes"
    __table_args__ = (UniqueConstraint("snapshot_id", "node_key"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("dag_node"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(80), index=True)
    shot_id: Mapped[str | None] = mapped_column(ForeignKey("shots.id"), nullable=True, index=True)
    input_contract: Mapped[dict] = mapped_column(JSON)
    output_contract: Mapped[dict] = mapped_column(JSON)
    workflow_slot_version_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_slot_versions.id"), nullable=True)
    pricing_rule_id: Mapped[str | None] = mapped_column(ForeignKey("pricing_rules.id"), nullable=True)
    pricing_quantity: Mapped[float | None] = mapped_column(nullable=True)
    pricing_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(String(12), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (UniqueConstraint("snapshot_id", "parent_node_id", "child_node_id", "input_slot"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("dag_edge"))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    parent_node_id: Mapped[str] = mapped_column(ForeignKey("dag_nodes.id"), index=True)
    child_node_id: Mapped[str] = mapped_column(ForeignKey("dag_nodes.id"), index=True)
    dependency_type: Mapped[str] = mapped_column(String(24))
    input_slot: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductionConfigVersion(Base):
    __tablename__ = "production_config_versions"
    __table_args__ = (UniqueConstraint("config_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("production_config"))
    config_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    supersedes_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("production_config_versions.id"), nullable=True
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_report: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(48), default="local-user")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ProviderConfigVersion(Base):
    __tablename__ = "provider_config_versions"
    __table_args__ = (UniqueConstraint("provider_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("provider_config"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    provider_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    adapter_kind: Mapped[str] = mapped_column(String(80))
    region: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_url: Mapped[str] = mapped_column(String(500))
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer)
    max_concurrency: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelConfigVersion(Base):
    __tablename__ = "model_config_versions"
    __table_args__ = (UniqueConstraint("config_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("model_config"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    config_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    agent_role: Mapped[str] = mapped_column(String(24))
    provider_config_version_id: Mapped[str] = mapped_column(ForeignKey("provider_config_versions.id"))
    provider_model_id: Mapped[str] = mapped_column(String(200))
    input_contract_version: Mapped[str] = mapped_column(String(80))
    output_schema_version: Mapped[str] = mapped_column(String(80))
    prompt_contract_version: Mapped[str] = mapped_column(String(80))
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sampling: Mapped[dict] = mapped_column(JSON, default=dict)
    capability_tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoSpecVersion(Base):
    __tablename__ = "video_spec_versions"
    __table_args__ = (UniqueConstraint("spec_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("video_spec"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    spec_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    aspect_ratio: Mapped[str] = mapped_column(String(16))
    fps: Mapped[int] = mapped_column(Integer)
    duration_min_seconds: Mapped[int] = mapped_column(Integer)
    duration_max_seconds: Mapped[int] = mapped_column(Integer)
    frame_count_rule: Mapped[dict] = mapped_column(JSON)
    container: Mapped[str] = mapped_column(String(24))
    video_codec: Mapped[str] = mapped_column(String(40))
    pixel_format: Mapped[str] = mapped_column(String(40))
    bitrate_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    safe_crop: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowSlotVersion(Base):
    __tablename__ = "workflow_slot_versions"
    __table_args__ = (UniqueConstraint("slot_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("workflow_slot"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    slot_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    operation_kind: Mapped[str] = mapped_column(String(80), index=True)
    provider_config_version_id: Mapped[str] = mapped_column(ForeignKey("provider_config_versions.id"))
    provider_workflow_id: Mapped[str] = mapped_column(String(200))
    provider_workflow_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model_config_version_id: Mapped[str | None] = mapped_column(ForeignKey("model_config_versions.id"), nullable=True)
    input_schema_version: Mapped[str] = mapped_column(String(80))
    output_schema_version: Mapped[str] = mapped_column(String(80))
    node_info_list: Mapped[list] = mapped_column(JSON)
    supported_video_spec_ids: Mapped[list] = mapped_column(JSON, default=list)
    capability_tags: Mapped[list] = mapped_column(JSON, default=list)
    validation_status: Mapped[str] = mapped_column(String(32), default="not_validated")
    validation_report: Mapped[list] = mapped_column(JSON, default=list)
    tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AudioConfigVersion(Base):
    __tablename__ = "audio_config_versions"
    __table_args__ = (UniqueConstraint("config_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("audio_config"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    config_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    supported_modes: Mapped[list] = mapped_column(JSON)
    tts_workflow_slot_version_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_slot_versions.id"), nullable=True)
    default_voice_entity_version_id: Mapped[str | None] = mapped_column(ForeignKey("entity_versions.id"), nullable=True)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(24))
    speaking_rate_range: Mapped[dict] = mapped_column(JSON)
    loudness_target: Mapped[float | None] = mapped_column(nullable=True)
    temporary_upload_policy_version_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StoragePolicyVersion(Base):
    __tablename__ = "storage_policy_versions"
    __table_args__ = (UniqueConstraint("policy_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("storage_policy"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    policy_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    backend_kind: Mapped[str] = mapped_column(String(24))
    region_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bucket_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    credential_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    allowed_mime_types: Mapped[list] = mapped_column(JSON)
    max_file_size_bytes: Mapped[int] = mapped_column(Integer)
    public_url_policy: Mapped[str] = mapped_column(String(40))
    lifecycle_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_root_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PricingCatalogVersion(Base):
    __tablename__ = "pricing_catalog_versions"
    __table_args__ = (UniqueConstraint("catalog_key", "version_number"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("pricing_catalog"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    catalog_key: Mapped[str] = mapped_column(String(120), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160))
    currency: Mapped[str] = mapped_column(String(12))
    confirmation_threshold: Mapped[float] = mapped_column()
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PricingRule(Base):
    __tablename__ = "pricing_rules"
    __table_args__ = (UniqueConstraint("pricing_catalog_version_id", "workflow_slot_version_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("pricing_rule"))
    pricing_catalog_version_id: Mapped[str] = mapped_column(ForeignKey("pricing_catalog_versions.id"), index=True)
    provider_config_version_id: Mapped[str] = mapped_column(ForeignKey("provider_config_versions.id"), index=True)
    workflow_slot_version_id: Mapped[str] = mapped_column(ForeignKey("workflow_slot_versions.id"), index=True)
    operation_kind: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(32))
    unit_price: Mapped[float] = mapped_column()
    minimum_charge: Mapped[float | None] = mapped_column(nullable=True)
    estimated_runtime_seconds: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CostEvent(Base):
    __tablename__ = "cost_events"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("cost_event"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("production_snapshots.id"), index=True)
    work_attempt_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    provider: Mapped[str] = mapped_column(String(120))
    provider_operation: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[float] = mapped_column()
    currency: Mapped[str] = mapped_column(String(12))
    provider_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProductionConfigComponent(Base):
    __tablename__ = "production_config_components"
    __table_args__ = (UniqueConstraint("production_config_version_id", "component_type", "component_version_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("config_component"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    component_type: Mapped[str] = mapped_column(String(40), index=True)
    component_version_id: Mapped[str] = mapped_column(String(48), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConfigurationReference(Base):
    __tablename__ = "configuration_references"
    __table_args__ = (UniqueConstraint("production_config_version_id", "ref_type", "ref_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("config_reference"))
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    ref_type: Mapped[str] = mapped_column(String(24))
    ref_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConfigurationCommandReceipt(Base):
    __tablename__ = "configuration_command_receipts"
    __table_args__ = (UniqueConstraint("command_id"),)

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("config_command"))
    command_id: Mapped[str] = mapped_column(String(80))
    command_type: Mapped[str] = mapped_column(String(80))
    result_type: Mapped[str] = mapped_column(String(80))
    result_id: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConfigurationEvent(Base):
    __tablename__ = "configuration_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    production_config_version_id: Mapped[str] = mapped_column(ForeignKey("production_config_versions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[str] = mapped_column(String(48))
    command_id: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
