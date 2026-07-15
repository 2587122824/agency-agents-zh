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


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("work"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="work_items")


class ProjectEvent(Base):
    __tablename__ = "project_events"

    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(String(500))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Message(Base):
    __tablename__ = "creation_messages"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("message"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="user")
    content: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("creation_messages.id"), nullable=True)
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
    prompt_contract_version: Mapped[str] = mapped_column(String(48), default="creative.v1")
    output_schema_version: Mapped[str] = mapped_column(String(48), default="requirement-candidate.v1")
    parsed_candidate_id: Mapped[str | None] = mapped_column(String(48), nullable=True)
    raw_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RequirementCandidate(Base):
    __tablename__ = "requirement_candidates"

    id: Mapped[str] = mapped_column(String(48), primary_key=True, default=lambda: new_id("candidate"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    base_requirement_version_id: Mapped[str] = mapped_column(ForeignKey("requirement_versions.id"), index=True)
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
