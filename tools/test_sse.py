#!/usr/bin/env python3
"""SSE integration tests for Stage 2."""

import asyncio
import json

import httpx

API_BASE = "http://localhost:40080/api/v1"

EXPECTED_EVENT_TYPES = {
    "agent_start",
    "tool_start",
    "tool_complete",
    "agent_complete",
}


async def test_stage2() -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        token_response = await client.post(f"{API_BASE}/auth/token")
        token_response.raise_for_status()
        token = token_response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        session_response = await client.post(
            f"{API_BASE}/sessions",
            headers=headers,
            json={"task": "List current directory", "working_dir": "/tmp"},
        )
        session_response.raise_for_status()
        session_id = session_response.json()["id"]
        print(f"Session: {session_id}")

        start_response = await client.post(
            f"{API_BASE}/sessions/{session_id}/task",
            headers=headers,
            json={"task": "Run: ls -la /tmp | head -5"},
        )
        start_response.raise_for_status()

        print("\nConnecting to SSE stream...")
        events_received: list[dict] = []
        event_types_seen: set[str] = set()

        sse_url = f"{API_BASE}/sessions/{session_id}/events?token={token}"

        async with client.stream("GET", sse_url) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                events_received.append(event)
                event_types_seen.add(event["type"])

                print(
                    f"  [{event['sequence']:3d}] {event['type']}: "
                    f"{str(event['data'])[:60]}..."
                )

                if event["type"] in ("agent_complete", "error", "cancelled"):
                    break

        print(f"\nReceived {len(events_received)} events")
        print(f"Event types: {event_types_seen}")

        missing = EXPECTED_EVENT_TYPES - event_types_seen
        assert not missing, f"Missing event types: {missing}"
        print("  ✓ All required event types received")

        sequences = [e["sequence"] for e in events_received]
        assert sequences == sorted(sequences), "Sequences not monotonic"
        assert sequences[0] == 1, "Sequence should start at 1"
        print("  ✓ Sequences are correct")

        assert all("timestamp" in e for e in events_received)
        print("  ✓ All events have timestamps")

        complete_event = next(
            e for e in events_received if e["type"] == "agent_complete"
        )
        assert "num_turns" in complete_event["data"]
        assert "status" in complete_event["data"]
        print("  ✓ agent_complete has metrics")

        print("\n" + "=" * 50)
        print("ALL STAGE 2 TESTS PASSED ✓")
        print("=" * 50)


if __name__ == "__main__":
    asyncio.run(test_stage2())
