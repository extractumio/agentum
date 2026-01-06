"""
Event streaming fanout for SSE.

Provides per-session subscriber queues with backpressure handling.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, DefaultDict, Set

logger = logging.getLogger(__name__)


class EventHub:
    """Fan out events to multiple subscribers per session."""

    def __init__(self, max_queue_size: int = 500) -> None:
        self._subscribers: DefaultDict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)

    async def publish(self, session_id: str, event: dict[str, Any]) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(session_id, set()))

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                    logger.warning(
                        "Dropping oldest event for session %s due to backpressure",
                        session_id,
                    )
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "Dropping event for session %s due to full queue",
                    session_id,
                )


class EventSinkQueue:
    """Adapter to present EventHub as an asyncio.Queue-like sink."""

    def __init__(self, hub: EventHub, session_id: str) -> None:
        self._hub = hub
        self._session_id = session_id

    async def put(self, event: dict[str, Any]) -> None:
        await self._hub.publish(self._session_id, event)

    def put_nowait(self, event: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.put(event))
