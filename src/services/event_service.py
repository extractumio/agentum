"""
Event persistence service for Agentum.

Stores SSE events in the database for replay and recovery.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select

from ..db.database import AsyncSessionLocal
from ..db.models import Event

logger = logging.getLogger(__name__)


async def record_event(event: dict[str, Any]) -> None:
    """Persist a structured event to the database."""
    session_id = event.get("session_id") or event.get("data", {}).get("session_id")
    if not session_id:
        logger.warning("Skipping event without session_id: %s", event.get("type"))
        return

    timestamp_raw = event.get("timestamp")
    timestamp = None
    if isinstance(timestamp_raw, datetime):
        timestamp = timestamp_raw
    elif isinstance(timestamp_raw, str):
        try:
            timestamp = datetime.fromisoformat(timestamp_raw)
        except ValueError:
            timestamp = None
    if timestamp is None:
        timestamp = datetime.utcnow()

    payload = event.get("data", {})
    async with AsyncSessionLocal() as db:
        db_event = Event(
            session_id=session_id,
            sequence=int(event.get("sequence") or 0),
            event_type=str(event.get("type") or "unknown"),
            data=json.dumps(payload, default=str),
            timestamp=timestamp,
        )
        db.add(db_event)
        await db.commit()


async def list_events(
    session_id: str,
    after_sequence: Optional[int] = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """List persisted events for a session in sequence order."""
    async with AsyncSessionLocal() as db:
        query = select(Event).where(Event.session_id == session_id)
        if after_sequence is not None:
            query = query.where(Event.sequence > after_sequence)
        query = query.order_by(Event.sequence.asc()).limit(limit)
        result = await db.execute(query)
        events = list(result.scalars().all())

    return [
        {
            "type": event.event_type,
            "data": json.loads(event.data) if event.data else {},
            "timestamp": event.timestamp.isoformat(),
            "sequence": event.sequence,
            "session_id": event.session_id,
        }
        for event in events
    ]


async def get_last_sequence(session_id: str) -> int:
    """Get the latest sequence number for a session."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.max(Event.sequence)).where(Event.session_id == session_id)
        )
        last = result.scalar_one_or_none()
        return int(last or 0)
