import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.trace_processor import TraceProcessor
from src.core.tracer import EventingTracer, NullTracer
from src.services import event_service
from claude_agent_sdk.types import StreamEvent


@pytest.mark.asyncio
async def test_eventing_tracer_streams_without_header_leak() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="session-1")

    tracer.on_message("```\n---\nstatus: COMPLETE\n", is_partial=True)
    await asyncio.sleep(0)
    assert queue.empty()

    tracer.on_message("---\n```\n\nHello", is_partial=True)
    await asyncio.sleep(0)

    partial_event = await queue.get()
    assert partial_event["type"] == "message"
    assert partial_event["data"]["text"] == "Hello"
    assert partial_event["data"]["is_partial"] is True

    tracer.on_message("", is_partial=False)
    await asyncio.sleep(0)

    final_event = await queue.get()
    assert final_event["data"]["is_partial"] is False
    assert final_event["data"]["full_text"] == "Hello"
    assert final_event["data"]["structured_status"] == "COMPLETE"


@pytest.mark.asyncio
async def test_record_event_skips_partial_and_persists_full_text(
    test_engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr(event_service, "AsyncSessionLocal", async_session)

    await event_service.record_event(
        {
            "type": "message",
            "data": {"text": "partial", "is_partial": True},
            "timestamp": datetime.now(timezone.utc),
            "sequence": 1,
            "session_id": "session-2",
        }
    )
    await event_service.record_event(
        {
            "type": "message",
            "data": {
                "text": "",
                "full_text": "Full message",
                "is_partial": False,
            },
            "timestamp": datetime.now(timezone.utc),
            "sequence": 2,
            "session_id": "session-2",
        }
    )

    events = await event_service.list_events("session-2")
    assert len(events) == 1
    assert events[0]["data"]["text"] == "Full message"


@pytest.mark.asyncio
async def test_trace_processor_emits_stream_events() -> None:
    queue: asyncio.Queue = asyncio.Queue()
    tracer = EventingTracer(NullTracer(), event_queue=queue, session_id="session-3")
    processor = TraceProcessor(tracer)

    processor.process_message(
        StreamEvent(
            uuid="1",
            session_id="session-3",
            event={"type": "message_start", "message": {"usage": {}}},
        )
    )
    processor.process_message(
        StreamEvent(
            uuid="2",
            session_id="session-3",
            event={
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Stream"},
            },
        )
    )
    processor.process_message(
        StreamEvent(
            uuid="3",
            session_id="session-3",
            event={"type": "message_stop", "usage": {}},
        )
    )

    await asyncio.sleep(0)
    first_event = await queue.get()
    assert first_event["data"]["is_partial"] is True
    assert first_event["data"]["text"] == "Stream"

    final_event = await queue.get()
    assert final_event["data"]["is_partial"] is False
    assert final_event["data"]["full_text"] == "Stream"
