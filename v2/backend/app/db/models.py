from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
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
