# Session Management Workflow

This document describes the session lifecycle, how SSE streaming is produced/consumed, and how cancellation/resume are handled in Agentum. It includes text-based flow diagrams, algorithm descriptions, and control-flow notes for happy/failure paths.

## Key Components

- **API**: `src/api/routes/sessions.py`
- **Agent runner**: `src/services/agent_runner.py`
- **Event persistence**: `src/services/event_service.py`
- **Session storage**: `src/services/session_service.py`, `src/core/sessions.py`
- **Tracer + stream processing**: `src/core/tracer.py`, `src/core/trace_processor.py`
- **Web client**: `src/web_terminal_client/src/App.tsx`, `src/web_terminal_client/src/sse.ts`

---

## Session Start (New Session)

**Flow (text diagram)**

```
User UI -> POST /sessions/run
  -> create DB session (status=pending)
  -> create file-based session folder
  -> persist user_message event
  -> agent_runner.start_task(session_id)
  -> update DB status=running
  -> SSE stream begins
```

**Algorithm Notes**
- The DB session record is created first (pending), then the agent is started in the background.
- The **file-based session** (`session_info.json`) is created when the session is created (via `SessionManager`).
- The first user prompt is saved as an event for replay.

---

## Session Normal Execution (No Plan)

**Flow (text diagram)**

```
AgentRunner._run_agent
  -> creates EventingTracer + event queue
  -> execute_agent_task(...)
     -> ClaudeAgent.run(...)
       -> TraceProcessor handles SDK stream
       -> EventingTracer emits:
          - agent_start
          - message (partial)
          - message (final)
          - tool_start / tool_complete
          - metrics_update
          - agent_complete
  -> EventService persists events (final messages only)
  -> DB session updated to terminal status
```

**Algorithm Notes**
- Streaming events are emitted as partial chunks to the UI, but only **final** messages are persisted.
- The final status is derived from `agent_complete` (or `error`/`cancelled`).

---

## Session Execution with Plan (TodoWrite)

**Flow (text diagram)**

```
Agent message -> tool_start (TodoWrite) -> tool_complete
  -> UI extracts todos from tool calls
  -> UI attaches todos to last agent message
  -> Subsequent tool calls continue updating tool sections
```

**Algorithm Notes**
- The UI derives the Todo list from tool calls in the current user-turn segment.
- The Todo status follows the **terminal status** of the last agent message in the segment.

---

## Session Cancellation

**Flow (text diagram)**

```
User UI -> POST /sessions/{id}/cancel
  -> AgentRunner.cancel_task
     -> task.cancel() and status=cancelled
  -> Event emitted: error(cancelled)
  -> DB session status=cancelled
  -> UI receives cancelled/error -> shows failure banner
```

**Algorithm Notes**
- Cancellation emits a terminal event so SSE stream can close deterministically.
- Any in-flight partial stream buffers are cleared by UI on next user message.

---

## Session Resuming

**Flow (text diagram)**

```
User UI -> POST /sessions/{id}/task
  -> API picks task_to_run (request.task or session.task)
  -> resume_from = request.resume_session_id
     or (session has resume_id or num_turns > 0) => resume_from=session_id
  -> AgentRunner.start_task with resume_session_id
  -> ClaudeAgent loads session_info.resume_id
  -> SDK continues from previous context
```

**Algorithm Notes**
- `resume_id` is persisted as soon as `agent_start` is received so cancelled sessions can still resume.
- If the user sends “resume” or any follow-up, the same session is used unless explicitly forked.

---

## Session Reload (History + Replay)

**Flow (text diagram)**

```
User UI -> GET /sessions/{id}
User UI -> GET /sessions/{id}/events/history
  -> UI builds conversation from persisted events
  -> If session is running, SSE re-connects using last sequence
```

**Algorithm Notes**
- Replay uses only persisted events (final messages), so the UI shows clean history without partial chunks.
- On reload, UI uses the last sequence to avoid re-sending already-consumed events.

---

## SSE Events Transport & Processing

**Flow (text diagram)**

```
Backend: EventingTracer -> EventHub -> SSE /events
  -> event_service.record_event (persist final messages only)
Frontend: EventSource -> onmessage
  -> connectSSE
  -> App.handleEvent -> appendEvent
  -> conversation builder merges streaming with final
```

**Algorithm Notes**
- Partial messages are streamed but **not persisted**.
- Final messages include `full_text` for DB persistence.
- The UI merges streamed text into a single message, replacing spinners/tool placeholders.

---

## Failure Path & Error Handling

**Flow (text diagram)**

```
Agent error/timeout/exception
  -> tracer.on_error
  -> error event emitted
  -> session status set to failed
  -> SSE stream closes
  -> UI renders failure banner + error text
```

**Algorithm Notes**
- Errors are terminal events; UI stops reconnect attempts on error/cancelled.
- `agent_complete` status drives border color: green for COMPLETE, red for FAILED/ERROR/CANCELLED.

---

## End-to-End Summary

1. **Start**: Session created (DB + file), agent starts, SSE begins.
2. **Run**: TraceProcessor streams partials, EventingTracer emits SSE events, DB stores final messages.
3. **Cancel**: Agent cancels and emits terminal event; UI reflects cancellation and resets stream state on next user message.
4. **Resume**: Session uses stored `resume_id` to continue from the same context.
5. **Reload**: UI rebuilds history from stored events and resumes SSE if running.
