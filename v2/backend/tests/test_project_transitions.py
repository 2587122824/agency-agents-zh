from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from v2.backend.app.db.models import Project, ProjectEvent
from v2.backend.app.db.session import Base
from v2.backend.app.orchestration.project_transitions import (
    ProjectStateConflictError,
    ProjectStateTrigger,
    block_project,
    transition_project,
)


def project_row(*, status: str = "draft") -> Project:
    return Project(
        title="State machine test",
        core_topic="Explicit project transitions",
        duration_seconds=15,
        aspect_ratio="9:16",
        audio_mode="off",
        status=status,
    )


@pytest.fixture()
def session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as value:
        yield value
    engine.dispose()


def apply(session: Session, project: Project, trigger: ProjectStateTrigger) -> None:
    transition_project(
        session,
        project,
        trigger,
        actor_type="user",
        actor_id="state-tester",
    )


def test_explicit_lifecycle_uses_one_authoritative_transition_path(session: Session) -> None:
    project = project_row()
    session.add(project)
    session.flush()

    expected = [
        (ProjectStateTrigger.MESSAGE_ADDED, "collecting_requirements", True),
        (ProjectStateTrigger.REQUIREMENT_CONFIRMED, "planning", True),
        (ProjectStateTrigger.BRIEF_CANDIDATE_CREATED, "plan_review", True),
        (ProjectStateTrigger.BRIEF_ACCEPTED, "planning", True),
        (ProjectStateTrigger.SHOT_CANDIDATE_CREATED, "plan_review", True),
        (ProjectStateTrigger.SHOT_CANDIDATE_REVISED, "plan_review", False),
        (ProjectStateTrigger.SHOT_PLAN_ACCEPTED, "contract_ready", True),
        (ProjectStateTrigger.SNAPSHOT_PREPARED, "contract_ready", False),
        (ProjectStateTrigger.SNAPSHOT_LOCKED, "production_ready", True),
        (ProjectStateTrigger.SNAPSHOT_ACTIVATED, "production_ready", False),
        (ProjectStateTrigger.PRODUCTION_SUBMITTED, "producing", True),
        (ProjectStateTrigger.PRODUCTION_PROGRESS, "producing", False),
        (ProjectStateTrigger.PRODUCTION_SETTLED, "quality_review", True),
        (ProjectStateTrigger.QUALITY_RECORDED, "quality_review", False),
        (ProjectStateTrigger.QUALITY_STAGE_APPROVED, "editing", True),
        (ProjectStateTrigger.TIMELINE_CANDIDATE_CREATED, "editing", False),
        (ProjectStateTrigger.TIMELINE_CONFIRMED, "delivery_ready", True),
        (ProjectStateTrigger.DELIVERY_VERIFIED, "completed", True),
    ]
    changed_count = 0
    for trigger, target, changed in expected:
        result = transition_project(
            session,
            project,
            trigger,
            actor_type="user",
            actor_id="state-tester",
        )
        changed_count += int(changed)
        assert result.changed is changed
        assert result.to_state == target
        assert project.status == target
        assert project.row_version == 1 + changed_count

    session.commit()
    events = list(session.scalars(
        select(ProjectEvent)
        .where(ProjectEvent.project_id == project.id, ProjectEvent.event_type == "project.state_changed.v1")
        .order_by(ProjectEvent.sequence)
    ))
    assert len(events) == changed_count
    assert events[0].data["from_state"] == "draft"
    assert events[-1].data["to_state"] == "completed"
    assert events[-1].data["row_version"] == project.row_version


def test_invalid_transition_writes_neither_state_nor_event(session: Session) -> None:
    project = project_row()
    session.add(project)
    session.flush()

    with pytest.raises(ProjectStateConflictError) as caught:
        apply(session, project, ProjectStateTrigger.SNAPSHOT_LOCKED)

    assert caught.value.code == "PROJECT_STATE_TRANSITION_NOT_ALLOWED"
    assert project.status == "draft"
    assert project.row_version == 1
    assert list(session.scalars(select(ProjectEvent))) == []


def test_stale_project_version_cannot_win_atomic_transition(session: Session) -> None:
    project = project_row()
    session.add(project)
    session.flush()
    stale = project_row()
    stale.id = project.id
    stale.status = project.status
    stale.row_version = project.row_version

    apply(session, project, ProjectStateTrigger.MESSAGE_ADDED)

    with pytest.raises(ProjectStateConflictError) as caught:
        apply(session, stale, ProjectStateTrigger.MESSAGE_ADDED)

    assert caught.value.code == "PROJECT_STATE_VERSION_CONFLICT"
    assert project.status == "collecting_requirements"
    assert project.row_version == 2


def test_first_block_is_structured_and_later_diagnostics_do_not_replace_it(session: Session) -> None:
    project = project_row(status="producing")
    session.add(project)
    session.flush()

    first = block_project(
        session,
        project,
        reason_code="PROVIDER_ADAPTER_NOT_CONNECTED",
        responsible_aggregate_type="work_item",
        responsible_aggregate_id="work_001",
        actor_type="system",
        actor_id="worker-1",
        allowed_commands=(),
    )
    assert first.changed is True
    assert project.status == "blocked"
    assert project.blocked_from_state == "producing"
    assert project.state_reason_code == "PROVIDER_ADAPTER_NOT_CONNECTED"
    assert project.blocked_responsible_aggregate_type == "work_item"
    assert project.blocked_responsible_aggregate_id == "work_001"
    assert project.blocked_allowed_commands == []
    assert project.blocked_at is not None
    blocked_row_version = project.row_version

    second = block_project(
        session,
        project,
        reason_code="DEPENDENCY_BLOCKED",
        responsible_aggregate_type="work_item",
        responsible_aggregate_id="work_002",
        actor_type="system",
        actor_id="worker-2",
    )
    assert second.changed is False
    assert project.row_version == blocked_row_version
    assert project.state_reason_code == "PROVIDER_ADAPTER_NOT_CONNECTED"
    assert project.blocked_responsible_aggregate_id == "work_001"

    session.commit()
    events = list(session.scalars(
        select(ProjectEvent)
        .where(ProjectEvent.project_id == project.id)
        .order_by(ProjectEvent.sequence)
    ))
    assert [event.event_type for event in events] == [
        "project.blocked.v1",
        "project.block_diagnostic.v1",
    ]
    assert events[1].data["reason_code"] == "DEPENDENCY_BLOCKED"
    assert events[1].data["responsible_aggregate_id"] == "work_002"


def test_terminal_project_cannot_be_blocked(session: Session) -> None:
    project = project_row(status="completed")
    session.add(project)
    session.flush()

    with pytest.raises(ProjectStateConflictError) as caught:
        block_project(
            session,
            project,
            reason_code="LATE_FAILURE",
            responsible_aggregate_type="work_item",
            responsible_aggregate_id="work_late",
            actor_type="system",
            actor_id="worker-late",
        )

    assert caught.value.code == "PROJECT_TERMINAL_STATE"
    assert project.status == "completed"
