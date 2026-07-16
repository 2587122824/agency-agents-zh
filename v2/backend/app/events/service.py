from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Protocol

from sqlalchemy.orm import Session

from ..db.models import ProjectEvent, utc_now
from ..db.session import SessionLocal
from ..repositories import SqlAlchemyEventRepository, SqlAlchemyOutboxRepository


class EventSink(Protocol):
    def publish(self, topic: str, envelope: dict) -> None: ...


def event_envelope(event: ProjectEvent) -> dict:
    return {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "project_id": event.project_id,
        "snapshot_id": event.snapshot_id,
        "sequence": event.project_sequence,
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "actor": {"type": event.actor_type, "id": event.actor_id},
        "occurred_at": event.created_at.isoformat(),
        "schema_version": event.schema_version,
        "payload": event.data,
        "message": event.message,
    }


def publish_pending_outbox(session: Session, sink: EventSink, *, limit: int = 100) -> int:
    """Publish one explicit batch. Failures are raised and never retried here."""
    events = SqlAlchemyEventRepository(session)
    outbox = SqlAlchemyOutboxRepository(session)
    published = 0
    for message in outbox.list_pending(limit=limit):
        event = events.get_by_event_id(message.event_id)
        if event is None:
            raise RuntimeError(f"Outbox message {message.id} references a missing event.")
        sink.publish(message.topic, event_envelope(event))
        if not outbox.mark_published(message.id, published_at=utc_now()):
            raise RuntimeError(f"Outbox message {message.id} changed before publication was recorded.")
        session.commit()
        published += 1
    return published


def serialize_event(event: ProjectEvent) -> str:
    payload = event_envelope(event)
    return f"id: {event.project_sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def project_event_stream(project_id: str, after: int = 0) -> AsyncIterator[str]:
    cursor = after
    idle_ticks = 0
    while True:
        with SessionLocal() as session:
            events = SqlAlchemyEventRepository(session).list_after(project_id, cursor, limit=100)
        if events:
            idle_ticks = 0
            for event in events:
                cursor = event.project_sequence
                yield serialize_event(event)
        else:
            idle_ticks += 1
            if idle_ticks >= 15:
                idle_ticks = 0
                yield ": keep-alive\n\n"
        await asyncio.sleep(1)
