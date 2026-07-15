from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from v2.backend.app.db.models import Decision, Project, ProjectEvent, WorkItem, utc_now
from v2.backend.app.db.session import Base
from v2.backend.app.repositories import (
    SqlAlchemyCommandRepository,
    SqlAlchemyDecisionRepository,
    SqlAlchemyEventRepository,
    SqlAlchemyProjectRepository,
)


def test_project_repository_contract() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            repository = SqlAlchemyProjectRepository(session)
            earlier = Project(
                title="Earlier",
                core_topic="Repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=utc_now() - timedelta(minutes=1),
            )
            later = Project(
                title="Later",
                core_topic="Repository contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
                updated_at=utc_now(),
            )
            repository.add(earlier)
            repository.add(later)
            repository.flush()
            work_item = WorkItem(project_id=later.id, kind="contract_validation", payload={})
            repository.add_work_item(work_item)
            session.commit()

            assert [project.id for project in repository.list_recent()] == [later.id, earlier.id]
            loaded = repository.get(later.id, with_workspace=True)
            assert loaded is not None
            assert [item.id for item in loaded.work_items] == [work_item.id]
            assert repository.get("project_missing", with_workspace=True) is None
    finally:
        engine.dispose()


def test_event_repository_contract_preserves_project_cursor_order_and_limit() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            events = SqlAlchemyEventRepository(session)
            first = Project(
                title="First",
                core_topic="Event contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Event isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            events.add(ProjectEvent(project_id=first.id, event_type="first", message="first"))
            events.add(ProjectEvent(project_id=second.id, event_type="other", message="other"))
            events.add(ProjectEvent(project_id=first.id, event_type="second", message="second"))
            session.commit()

            rows = events.list_after(first.id, 0, limit=1)
            assert [row.event_type for row in rows] == ["first"]
            remaining = events.list_after(first.id, rows[0].sequence, limit=100)
            assert [row.event_type for row in remaining] == ["second"]
    finally:
        engine.dispose()


def test_decision_repository_contract_scopes_keys_and_ids_to_project() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            decisions = SqlAlchemyDecisionRepository(session)
            first = Project(
                title="First",
                core_topic="Decision contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Decision isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            first_decision = Decision(project_id=first.id, key="visual_style", label="Style")
            second_decision = Decision(project_id=second.id, key="visual_style", label="Other style")
            decisions.add(first_decision)
            decisions.add(second_decision)
            decisions.flush()
            session.commit()

            assert decisions.get_by_key(first.id, "visual_style").id == first_decision.id  # type: ignore[union-attr]
            assert decisions.get_by_key(second.id, "visual_style").id == second_decision.id  # type: ignore[union-attr]
            assert decisions.get_for_project(first.id, second_decision.id) is None
            assert decisions.get_by_key(first.id, "missing") is None
    finally:
        engine.dispose()


def test_command_repository_contract_preserves_receipt_and_project_isolation() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as session:
            projects = SqlAlchemyProjectRepository(session)
            commands = SqlAlchemyCommandRepository(session)
            first = Project(
                title="First",
                core_topic="Command contract",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            second = Project(
                title="Second",
                core_topic="Command isolation",
                duration_seconds=10,
                aspect_ratio="9:16",
                audio_mode="off",
            )
            projects.add(first)
            projects.add(second)
            projects.flush()
            commands.add(first.id, "command-001", "message.add", "message", "message-001")
            commands.add(second.id, "command-001", "asset.verify", "asset", "asset-001")
            session.commit()

            first_receipt = commands.get(first.id, "command-001")
            second_receipt = commands.get(second.id, "command-001")
            assert first_receipt is not None
            assert first_receipt.command_type == "message.add"
            assert first_receipt.result_type == "message"
            assert first_receipt.result_id == "message-001"
            assert second_receipt is not None
            assert second_receipt.command_type == "asset.verify"
            assert commands.get(first.id, "missing") is None
    finally:
        engine.dispose()
