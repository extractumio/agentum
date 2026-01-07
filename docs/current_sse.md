# SSE (Server-Sent Events) Implementation

This document describes the current SSE implementation for real-time event streaming between the Agentum backend and the web frontend.

## Overview

Agentum uses Server-Sent Events (SSE) to stream real-time execution events from the backend to the frontend during agent task execution. This enables live updates of tool calls, messages, errors, and completion status without polling.

```
┌─────────────┐         POST /sessions/run          ┌─────────────┐
│   Frontend  │────────────────────────────────────▶│   Backend   │
│   (React)   │                                     │  (FastAPI)  │
│             │◀────────────────────────────────────│             │
│             │   { session_id, status: "running" } │             │
│             │                                     │             │
│             │    GET /sessions/{id}/events        │             │
│             │────────────────────────────────────▶│             │
│             │                                     │             │
│             │◀═══════════════════════════════════ │             │
│             │         SSE Event Stream            │             │
│             │   (agent_start, tool_*, message,    │             │
│             │    agent_complete, error, etc.)     │             │
└─────────────┘                                     └─────────────┘
```

---

## Session and Task Lifecycle

### 1. Task Initiation

**New Session Flow:**
1. User enters task in the frontend input field
2. Frontend calls `POST /api/v1/sessions/run` with task and config
3. Backend creates a session record in the database (status: `pending`)
4. Backend creates file-based session folder (via `SessionManager`)
5. Backend persists `user_message` event for replay
6. Backend starts the agent in a background asyncio task via `agent_runner.start_task()`
7. Backend updates session status to `running`
8. Backend returns `{ session_id, status: "running" }`
9. Frontend immediately opens an SSE connection to `GET /sessions/{id}/events`

**Session Continuation Flow:**
1. User enters follow-up task while a previous session exists
2. Frontend calls `POST /api/v1/sessions/{id}/task` with new task
3. Backend checks session status:
   - If `cancelled` and not resumable → returns HTTP 400
   - If resumable → prepends resume context to task
4. Backend starts agent with `resume_session_id` pointing to itself
5. Frontend opens SSE connection to stream new events

### 2. SSE Connection Establishment

The frontend uses the native browser `EventSource` API:

```typescript
const url = `${baseUrl}/api/v1/sessions/${sessionId}/events?token=${token}`;
const source = new EventSource(url);
```

**Authentication:** Token is passed via query parameter (EventSource limitation - cannot set headers).

**Backend Endpoint:** `GET /sessions/{id}/events`
- Validates token and session ownership
- Gets or creates an event queue for the session
- Returns `StreamingResponse` with `text/event-stream` media type

### 3. Event Queue Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             AgentRunner                                       │
│                                                                               │
│  _running_tasks: dict[session_id, asyncio.Task]                              │
│                                                                               │
│  ┌─────────────────┐   emit_event()   ┌─────────────────────────────────────┐│
│  │   EventingTracer│─────────────────▶│           EventHub                  ││
│  │   (tracer.py)   │                  │   (pub/sub fanout per session)      ││
│  └────────┬────────┘                  │                                     ││
│           │                           │  ┌─────────┐  ┌─────────┐           ││
│           │ persist (final only)      │  │Queue[1] │  │Queue[2] │  ...      ││
│           ▼                           │  └────┬────┘  └────┬────┘           ││
│  ┌─────────────────┐                  └───────┼────────────┼────────────────┘│
│  │  EventService   │                          │            │                 │
│  │ (DB persistence)│                          ▼            ▼                 │
│  └─────────────────┘                     SSE Client 1  SSE Client 2          │
│                                                                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Key Components:**

1. **EventingTracer** (`tracer.py`): Wraps the base tracer and emits structured events; handles streaming text buffering and structured output parsing
2. **EventHub** (`event_stream.py`): Pub/sub fanout that manages multiple subscriber queues per session with backpressure handling
3. **EventService** (`event_service.py`): Persists final events to the database (partial messages are **not** persisted)
4. **AgentRunner** (`agent_runner.py`): Manages background task execution and coordinates event flow
5. **SSE Generator** (`sessions.py`): Async generator that reads from subscriber queue and yields SSE-formatted data

### Event Persistence Strategy

| Event Type | Streamed to Frontend | Persisted to DB |
|------------|---------------------|-----------------|
| Partial `message` (`is_partial: true`) | ✅ Yes | ❌ No |
| Final `message` (`is_partial: false`) | ✅ Yes (with `full_text`) | ✅ Yes |
| `tool_start`, `tool_complete` | ✅ Yes | ✅ Yes |
| `agent_start`, `agent_complete` | ✅ Yes | ✅ Yes |
| `error`, `cancelled` | ✅ Yes | ✅ Yes |

**Algorithm Notes:**
- Streaming events are emitted as partial chunks to the UI for real-time feedback
- Only **final** messages (with `full_text` field) are persisted for replay
- The `EventingTracer` buffers streaming text and extracts structured output headers before emitting

### 4. Session Execution with Plan (TodoWrite)

When the agent uses the `TodoWrite` tool to create a task plan, the frontend extracts and displays todos from tool calls:

```
Agent message -> tool_start (TodoWrite) -> tool_complete
  -> UI extracts todos from tool calls
  -> UI attaches todos to last agent message
  -> Subsequent tool calls continue updating tool sections
```

**Algorithm Notes:**
- The UI derives the Todo list from `TodoWrite` tool calls in the current user-turn segment
- Todos are displayed with status indicators: ✓ completed, → in_progress, ○ pending, ✗ cancelled
- The Todo status follows the **terminal status** of the last agent message in the segment

### 5. Task Termination

A task terminates when:

1. **Successful Completion:** Agent emits `agent_complete` event with status
2. **Error:** Agent emits `error` event  
3. **Cancellation:** User cancels via `POST /sessions/{id}/cancel`, agent emits `cancelled` event
4. **Timeout:** Backend detects idle queue while task is no longer running

---

## Event Types

### Core Event Types

| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `agent_start` | Agent begins execution | `session_id`, `model`, `tools`, `skills`, `task` |
| `user_message` | User message recorded | `text` |
| `thinking` | Agent extended thinking | `text` |
| `message` | Agent text response | `text`, `is_partial` |
| `tool_start` | Before tool execution | `tool_name`, `tool_input`, `tool_id` |
| `tool_complete` | After tool execution | `tool_name`, `tool_id`, `result`, `duration_ms`, `is_error` |
| `output_display` | Task output parsed | `output`, `error`, `comments`, `result_files`, `status` |
| `agent_complete` | Task finished | `status`, `num_turns`, `duration_ms`, `total_cost_usd`, `usage`, `model` |
| `error` | Error occurred | `message`, `error_type` |
| `cancelled` | Task was cancelled | `message`, `resumable` |

### Additional Event Types

| Event Type | When Emitted | Key Data Fields |
|------------|--------------|-----------------|
| `profile_switch` | Permission profile changed | `profile_type`, `profile_name`, `tools` |
| `hook_triggered` | Hook executed | `hook_event`, `tool_name`, `decision`, `message` |
| `conversation_turn` | Turn completed | `turn_number`, `prompt_preview`, `response_preview`, `duration_ms`, `tools_used` |
| `session_connect` | Session connected | `session_id` |
| `session_disconnect` | Session disconnected | `session_id`, `total_turns`, `total_duration_ms` |

### Terminal Events

The following events signal that the SSE stream should close:

- `agent_complete`
- `error`
- `cancelled`

When the frontend receives any of these, it closes the EventSource connection.

---

## Event Structure

Each event follows this JSON structure:

```json
{
  "type": "tool_start",
  "data": {
    "tool_name": "Read",
    "tool_input": { "file_path": "/path/to/file.py" },
    "tool_id": "tool_123"
  },
  "timestamp": "2026-01-05T12:34:56.789Z",
  "sequence": 42
}
```

**Fields:**
- `type`: Event type identifier
- `data`: Event-specific payload
- `timestamp`: ISO 8601 UTC timestamp
- `sequence`: Monotonically increasing event sequence number

### SSE Wire Format

Events are sent in SSE format:

```
id: 42
data: {"type":"tool_start","data":{...},"timestamp":"...","sequence":42}

```

**Note:** Each event block ends with double newline. The `id` field matches the sequence number.

---

## Backend Implementation Details

### Event Generation Flow

1. **Tracer Wrapper** (`EventingTracer`):
   ```python
   def emit_event(self, event_type: str, data: dict) -> None:
       event = {
           "type": event_type,
           "data": data,
           "timestamp": datetime.now(timezone.utc).isoformat(),
           "sequence": self._sequence,
       }
       self._event_queue.put_nowait(event)
   ```

2. **SSE Generator** (FastAPI endpoint):
   ```python
   async def event_generator():
       while True:
           try:
               event = await asyncio.wait_for(queue.get(), timeout=30.0)
           except asyncio.TimeoutError:
               yield ": heartbeat\n\n"  # Keep connection alive
               continue
           
           payload = json.dumps(event, default=str)
           yield f"id: {event.get('sequence')}\n"
           yield f"data: {payload}\n\n"
           
           if event.get("type") in ("agent_complete", "error", "cancelled"):
               break
   ```

### Heartbeat Mechanism

The backend sends SSE heartbeat comments (`: heartbeat\n\n`) every 30 seconds to:
1. Keep the connection alive through proxies
2. Detect if the task has ended while waiting

### Error Handling

**Agent Error Flow:**
```
Agent error/timeout/exception
  -> tracer.on_error emits error event
  -> session status set to failed
  -> SSE stream closes with error event
  -> UI renders failure banner + error text
```

**SSE Streaming Error:**
If the task fails before sending events:
- Backend checks `agent_runner.get_result(session_id)` after timeout
- Synthesizes an error event if result shows failure
- Sends error event to frontend before closing

**Algorithm Notes:**
- Errors are terminal events; UI stops reconnect attempts on `error` or `cancelled`
- `agent_complete` status drives UI styling: green for COMPLETE, red for FAILED/ERROR/CANCELLED

---

## Frontend Implementation Details

### SSE Client (`sse.ts`)

```typescript
export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onReconnecting?: (attempt: number) => void
): () => void
```

**Reconnection Strategy:**
- Max attempts: 5
- Initial delay: 1000ms
- Exponential backoff: delay * 2^(attempt-1)
- Stops reconnecting on terminal events

**Connection Closure:**
When a terminal event is received (`agent_complete`, `error`, `cancelled`):
1. Set `isClosed = true`
2. Close EventSource
3. Reconnection attempts stop

### Event Handling (`App.tsx`)

Events are processed through `handleEvent()`:

```typescript
const handleEvent = useCallback((event: TerminalEvent) => {
  appendEvent(event);  // Add to events array
  
  switch (event.type) {
    case 'agent_start':
      setStatus('running');
      break;
    case 'agent_complete':
      setStatus(normalizeStatus(event.data.status));
      refreshSessions();  // Update session list
      break;
    case 'error':
      setStatus('failed');
      setError(event.data.message);
      break;
    // ...
  }
}, []);
```

### Event Aggregation for Conversation View

The frontend transforms raw events into a conversation structure:

```typescript
type ConversationItem =
  | { type: 'user'; content: string; ... }
  | { type: 'agent_message'; content: string; toolCalls: ToolCallView[]; ... }
  | { type: 'output'; output: string; files: string[]; status: ResultStatus; ... }
```

**Aggregation Logic:**

1. **Tool Grouping**: Sequential `tool_start`/`tool_complete` events are collected into `pendingTools[]`
2. **Message Flush**: When a `message` event arrives, it creates an `agent_message` with accumulated tools
3. **Output Attachment**: `output_display` data is attached to the preceding agent message
4. **Chronological Sorting**: Events are sorted by timestamp to handle out-of-order arrivals

---

## Session State Management

### Status Transitions

```
┌─────────┐   POST /run    ┌─────────┐   agent_complete   ┌──────────┐
│  idle   │───────────────▶│ running │──────────────────▶│ complete │
└─────────┘                └────┬────┘                   └──────────┘
                                │
                                │ error event
                                ▼
                           ┌─────────┐
                           │ failed  │
                           └─────────┘
                                │
                                │ POST /cancel
                                ▼
                          ┌───────────┐
                          │ cancelled │
                          └───────────┘
```

### Cancellation Flow

```
User UI -> POST /sessions/{id}/cancel
  -> AgentRunner.cancel_task
     -> task.cancel() raises CancelledError
     -> Check if session has agent_start event (determines resumability)
  -> Emit cancelled event with resumable flag
  -> DB session status=cancelled
  -> UI receives cancelled event -> shows cancellation state
```

**Algorithm Notes:**
- Cancellation emits a terminal event so SSE stream can close deterministically
- Any in-flight partial stream buffers are cleared by UI on next user message

### Cancellation and Resumability

A cancelled session may or may not be resumable depending on when cancellation occurred:

| Timing | `agent_start` received? | Resumable? |
|--------|------------------------|------------|
| Before agent starts | ❌ No | ❌ No |
| During execution | ✅ Yes | ✅ Yes |
| After completion | N/A | N/A (already done) |

**Resumability is determined by** whether the `agent_start` event was received, which establishes a `resume_id` (Claude session ID).

**The `cancelled` event includes:**
```json
{
  "type": "cancelled",
  "data": {
    "message": "Task was cancelled",
    "resumable": true
  }
}
```

**Frontend behavior:**
- If `resumable: true`: User can continue with follow-up messages
- If `resumable: false`: Next message starts a fresh session

**Backend resume context:** When resuming a cancelled session, the backend prepends context to help the agent understand the interrupted state:

```
[RESUME CONTEXT]
Previous execution was cancelled by user.

Todo state at cancellation:
  ✓ Read file [completed]
  → Process data [in_progress]
  ○ Write output [pending]

Note: Task(s) marked in_progress were interrupted and may be incomplete.
[END RESUME CONTEXT]
```

### When UI Stops Receiving

The frontend stops receiving SSE events when:

1. **Terminal Event Received**: `agent_complete`, `error`, or `cancelled`
2. **User Cancels**: Calls cancel endpoint, expects `cancelled` event
3. **Connection Error**: After max reconnection attempts (5)
4. **Session Switch**: User selects different session (cleanup function called)
5. **New Task on Completed Session**: Old SSE closed before starting new task

### Cleanup

```typescript
// Stored cleanup function
cleanupRef.current = connectSSE(...);

// Called on:
// - Component unmount
// - Session switch
// - New task start (for session continuation)

if (cleanupRef.current) {
  cleanupRef.current();  // Closes EventSource, clears timeouts
}
```

---

## Session Continuation and Resumption

### Viewing Completed Sessions (History Replay)

When selecting a completed/cancelled session (not running):

1. Frontend fetches session details: `GET /sessions/{id}`
2. Frontend fetches historical events: `GET /sessions/{id}/events/history`
3. UI builds conversation from persisted events
4. Events are displayed chronologically

**No SSE connection is opened** — historical events are fetched once.

**Algorithm Notes:**
- Replay uses only persisted events (final messages), so the UI shows clean history without partial chunks
- If a running session is selected, UI uses SSE to stream new events starting from the last known sequence

### Continuing a Session

When user submits a follow-up message on an existing session:

1. **Check resumability** (for cancelled sessions)
2. Call `POST /sessions/{id}/task` with new message
3. Backend determines `resume_session_id` from session info
4. If cancelled session: backend prepends resume context
5. Open SSE connection for new events

### Session Response Fields

The `GET /sessions/{id}` response includes:

```typescript
interface SessionResponse {
  id: string;
  status: string;  // "running" | "complete" | "failed" | "cancelled"
  resumable?: boolean;  // Only set for cancelled sessions
  // ... other fields
}
```

**`resumable` field logic:**
- `true`: Session has `resume_id`, can be continued
- `false`: Cancelled before agent established session
- `undefined`: Not a cancelled session (always continuable)

---

## HTTP Response Headers

The SSE endpoint returns these headers:

```http
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache, no-transform
Connection: keep-alive
X-Accel-Buffering: no  # Disable nginx buffering
```

---

## Sequence Diagram: Complete Task Flow

```
Frontend                          Backend                           Agent
   │                                 │                                 │
   │──POST /sessions/run─────────────▶                                 │
   │                                 │──create_session()               │
   │                                 │──start_task()──────────────────▶│
   │◀──{ session_id, status }────────│                                 │
   │                                 │                                 │
   │──GET /sessions/{id}/events──────▶                                 │
   │                                 │◀──emit(agent_start)─────────────│
   │◀══agent_start═══════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──emit(tool_start)──────────────│
   │◀══tool_start════════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──emit(tool_complete)───────────│
   │◀══tool_complete═════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──emit(message)─────────────────│
   │◀══message═══════════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──emit(output_display)──────────│
   │◀══output_display════════════════│                                 │
   │                                 │                                 │
   │                                 │◀──emit(agent_complete)──────────│
   │◀══agent_complete════════════════│                                 │
   │                                 │                                 │
   │──close EventSource──────────────│                                 │
   │                                 │──clear_event_queue()            │
   │                                 │──update_session(complete)       │
   │                                 │                                 │
```

---

## File References

| Component | File Path |
|-----------|-----------|
| Backend SSE endpoint | `src/api/routes/sessions.py` |
| Agent runner (task management) | `src/services/agent_runner.py` |
| Event hub (pub/sub fanout) | `src/services/event_stream.py` |
| Event persistence | `src/services/event_service.py` |
| Session storage | `src/services/session_service.py` |
| Eventing tracer | `src/core/tracer.py` (`EventingTracer`) |
| Trace processor | `src/core/trace_processor.py` |
| Frontend SSE client | `src/web_terminal_client/src/sse.ts` |
| Frontend event handling | `src/web_terminal_client/src/App.tsx` |
| Event type definitions | `src/web_terminal_client/src/types.ts` |
| API helpers | `src/web_terminal_client/src/api.ts` |

---

## End-to-End Summary

1. **Start**: Session created (DB + file), agent starts in background, SSE stream begins
2. **Run**: TraceProcessor streams partials to EventingTracer, EventHub fans out to subscribers, EventService stores final messages only
3. **Cancel**: Agent cancels and emits terminal event (`cancelled`); UI reflects cancellation and resets stream state on next user message
4. **Resume**: Session uses stored `resume_id` to continue from the same Claude context
5. **Reload**: UI rebuilds history from stored events via `/events/history` and resumes SSE if session is running




