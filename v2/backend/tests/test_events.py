from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from v2.backend.app.db.models import OutboxMessage, Project, ProjectEvent
from v2.backend.app.db.session import Base
from v2.backend.app.events.service import event_envelope, publish_pending_outbox
from v2.backend.app.repositories import SqlAlchemyEventRepository


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: list[tuple[str, dict]] = []

    def publish(self, topic: str, envelope: dict) -> None:
        if self.fail:
            raise RuntimeError("sink unavailable")
        self.records.append((topic, envelope))


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def test_event_and_outbox_are_written_with_project_local_sequence() -> None:
    engine = _engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            first = Project(title="First", core_topic="One", duration_seconds=10, aspect_ratio="9:16", audio_mode="off")
            second = Project(title="Second", core_topic="Two", duration_seconds=10, aspect_ratio="9:16", audio_mode="off")
            session.add_all([first, second])
            session.flush()
            repository = SqlAlchemyEventRepository(session)
            repository.add(ProjectEvent(project_id=first.id, event_type="project.created.v1", aggregate_type="project", aggregate_id=first.id, actor_type="user", actor_id="tester", message="First"))
            repository.add(ProjectEvent(project_id=second.id, event_type="project.created.v1", aggregate_type="project", aggregate_id=second.id, actor_type="user", actor_id="tester", message="Second"))
            repository.add(ProjectEvent(project_id=first.id, event_type="project.updated.v1", aggregate_type="project", aggregate_id=first.id, actor_type="user", actor_id="tester", message="First again"))
            session.commit()

            first_events = repository.list_after(first.id, 0)
            assert [event.project_sequence for event in first_events] == [1, 2]
            assert len({event.event_id for event in first_events}) == 2
            assert all(event.correlation_id == event.event_id for event in first_events)
            messages = list(session.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at)))
            assert len(messages) == 3
            assert all(message.status == "pending" for message in messages)
            assert {message.event_id for message in messages} == {
                event.event_id for event in repository.list_after(first.id, 0) + repository.list_after(second.id, 0)
            }
    finally:
        engine.dispose()


def test_outbox_publication_is_explicit_and_does_not_retry_failures() -> None:
    engine = _engine()
    try:
        with Session(engine, expire_on_commit=False) as session:
            project = Project(title="Publish", core_topic="Outbox", duration_seconds=10, aspect_ratio="9:16", audio_mode="off")
            session.add(project)
            session.flush()
            event = ProjectEvent(project_id=project.id, event_type="project.created.v1", aggregate_type="project", aggregate_id=project.id, actor_type="user", actor_id="tester", message="Created", data={"safe": True})
            SqlAlchemyEventRepository(session).add(event)
            session.commit()

            with pytest.raises(RuntimeError, match="sink unavailable"):
                publish_pending_outbox(session, RecordingSink(fail=True))
            message = session.scalar(select(OutboxMessage))
            assert message is not None
            assert message.status == "pending"
            assert message.published_at is None

            sink = RecordingSink()
            assert publish_pending_outbox(session, sink) == 1
            session.refresh(message)
            assert message.status == "published"
            assert message.published_at is not None
            assert len(sink.records) == 1
            topic, envelope = sink.records[0]
            assert topic == "project.events"
            assert envelope == event_envelope(event)
            assert envelope["sequence"] == 1
            assert envelope["actor"] == {"type": "user", "id": "tester"}
            assert publish_pending_outbox(session, sink) == 0
    finally:
        engine.dispose()


def test_event_repository_rejects_unversioned_event_type() -> None:
    engine = _engine()
    try:
        with Session(engine) as session:
            project = Project(title="Invalid", core_topic="Event", duration_seconds=10, aspect_ratio="9:16", audio_mode="off")
            session.add(project)
            session.flush()
            with pytest.raises(ValueError, match="not versioned"):
                SqlAlchemyEventRepository(session).add(ProjectEvent(project_id=project.id, event_type="project.created", aggregate_type="project", aggregate_id=project.id, actor_type="user", actor_id="tester", message="Invalid"))
    finally:
        engine.dispose()
