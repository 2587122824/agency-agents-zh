from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ..db.models import ProjectEvent
from ..db.session import SessionLocal
from ..repositories import SqlAlchemyEventRepository


def serialize_event(event: ProjectEvent) -> str:
    payload = {
        "sequence": event.sequence,
        "project_id": event.project_id,
        "type": event.event_type,
        "message": event.message,
        "data": event.data,
        "created_at": event.created_at.isoformat(),
    }
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def project_event_stream(project_id: str, after: int = 0) -> AsyncIterator[str]:
    cursor = after
    idle_ticks = 0
    while True:
        with SessionLocal() as session:
            events = SqlAlchemyEventRepository(session).list_after(project_id, cursor, limit=100)
        if events:
            idle_ticks = 0
            for event in events:
                cursor = event.sequence
                yield serialize_event(event)
        else:
            idle_ticks += 1
            if idle_ticks >= 15:
                idle_ticks = 0
                yield ": keep-alive\n\n"
        await asyncio.sleep(1)
