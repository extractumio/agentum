# Database Usage Architecture

## Overview

Agentum uses a dual-storage architecture combining SQLite for structured data persistence and file-based storage for Claude SDK compatibility. Redis is included in Docker Compose for future distributed deployment but is not currently utilized.

---

## SQLite Database

### Purpose

SQLite serves as the primary relational database for:

1. **Session Management** - Tracking agent execution state and metadata
2. **Event Persistence** - Storing SSE events for replay and recovery
3. **User Authentication** - Managing user identities (currently anonymous JWT-based)

### Location

- **Development**: `Project/data/agentum.db`
- **Docker**: `/data/agentum.db` (mounted volume)

### Schema

```
┌─────────────────────────────────────────────────────────────────┐
│                          USERS                                   │
├─────────────────────────────────────────────────────────────────┤
│ id          │ VARCHAR(36)  │ Primary Key (UUID)                 │
│ type        │ VARCHAR(20)  │ "anonymous" or future auth types   │
│ created_at  │ DATETIME     │ Account creation timestamp         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ 1:N
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                         SESSIONS                                 │
├─────────────────────────────────────────────────────────────────┤
│ id              │ VARCHAR(50)  │ Primary Key (YYYYMMDD_HHMMSS_hex) │
│ user_id         │ VARCHAR(36)  │ Foreign Key → users.id           │
│ status          │ VARCHAR(20)  │ pending/running/complete/failed  │
│ task            │ TEXT         │ User task description            │
│ model           │ VARCHAR(100) │ Claude model name                │
│ working_dir     │ TEXT         │ Agent working directory          │
│ created_at      │ DATETIME     │ Session creation timestamp       │
│ updated_at      │ DATETIME     │ Last update timestamp            │
│ completed_at    │ DATETIME     │ Completion timestamp (nullable)  │
│ num_turns       │ INTEGER      │ Number of agent turns            │
│ duration_ms     │ INTEGER      │ Execution duration (nullable)    │
│ total_cost_usd  │ FLOAT        │ API cost in USD (nullable)       │
│ cancel_requested│ BOOLEAN      │ Cancellation flag                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ 1:N
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                          EVENTS                                  │
├─────────────────────────────────────────────────────────────────┤
│ id         │ INTEGER     │ Primary Key (auto-increment)         │
│ session_id │ VARCHAR(50) │ Foreign Key → sessions.id (indexed)  │
│ sequence   │ INTEGER     │ Event ordering number (indexed)      │
│ event_type │ VARCHAR(50) │ Event type (message, tool_use, etc.) │
│ data       │ TEXT        │ JSON-serialized event payload        │
│ timestamp  │ DATETIME    │ Event timestamp                      │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
┌─────────────────┐     ┌───────────────────┐     ┌─────────────────┐
│   API Request   │────▶│  SessionService   │────▶│ SQLite Database │
│ (Create Session)│     │  (session_service │     │   (sessions)    │
└─────────────────┘     │       .py)        │     └─────────────────┘
                        └───────────────────┘              │
                                 │                         │
                                 ▼                         │
                        ┌───────────────────┐              │
                        │   File Storage    │◀─────────────┘
                        │  (sessions/*/)    │   Synchronized
                        └───────────────────┘   (atomic ops)

┌─────────────────┐     ┌───────────────────┐     ┌─────────────────┐
│  Agent Events   │────▶│  EventService     │────▶│ SQLite Database │
│ (SSE Streaming) │     │ (event_service.py)│     │    (events)     │
└─────────────────┘     └───────────────────┘     └─────────────────┘
```

### Key Operations

| Operation | Service | Description |
|-----------|---------|-------------|
| Create Session | `session_service.create_session()` | Atomic DB + file creation |
| Update Session | `session_service.update_session()` | Status, turns, cost updates |
| Record Event | `event_service.record_event()` | Persist SSE event to DB |
| List Events | `event_service.list_events()` | Fetch events for replay |
| Session Recovery | `session_service.cleanup_stale_sessions()` | Fix sessions after restart |

### Robustness Features

- **Retry Logic**: Automatic retry with exponential backoff for transient failures
- **Atomic Operations**: Database + file system changes rolled back on failure
- **Timeout Protection**: 10-second timeout on database operations
- **Sequence Validation**: Event ordering enforced via sequence numbers
- **Session ID Validation**: Regex validation to prevent path traversal attacks

---

## Redis (Future Use)

### Current Status

Redis is included in `docker-compose.yml` but **not actively used**. No Redis client code exists in the codebase.

```yaml
redis:
  image: redis:7-alpine
  command: redis-server --save "" --appendonly "no"
  ports:
    - "127.0.0.1:46379:6379"
```

### Intended Future Use

Redis is reserved for production deployment scenarios:

| Feature | Description |
|---------|-------------|
| **Pub/Sub Events** | Cross-instance SSE event distribution |
| **Session Locking** | Distributed locks for multi-instance deployments |
| **Rate Limiting** | API request throttling |
| **Caching** | Prompt template caching, model responses |

### Local Development

> **Redis is NOT required for local development.** Event streaming uses in-memory queues.

---

## Event Streaming Architecture

### In-Memory Event Hub (Current)

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   EventingTracer│────▶│    EventHub     │────▶│ SSE Subscribers │
│   (agent runs)  │     │ (in-memory)     │     │ (HTTP clients)  │
└─────────────────┘     └─────────────────┘     └─────────────────┘
         │                      │
         │                      │
         ▼                      ▼
┌─────────────────┐     ┌─────────────────┐
│  EventService   │     │  Backpressure   │
│ (SQLite persist)│     │   Handling      │
└─────────────────┘     └─────────────────┘
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `EventHub` | `event_stream.py` | Fan-out events to multiple SSE subscribers |
| `EventSinkQueue` | `event_stream.py` | Adapter for tracer to push events |
| `EventService` | `event_service.py` | Persist events to SQLite |
| `EventingTracer` | `tracer.py` | Emit structured events during agent execution |

### Event Types

| Type | Description | Persisted |
|------|-------------|-----------|
| `agent_start` | Session initialization | ✅ |
| `message` | Assistant/user messages | ✅ (non-partial) |
| `tool_use` | Tool invocation | ✅ |
| `tool_result` | Tool execution result | ✅ |
| `agent_complete` | Session completed | ✅ |
| `error` | Error occurred | ✅ |
| `cancelled` | Session cancelled | ✅ |

---

## Dual Storage Pattern

### Why Two Storage Systems?

| Storage | Purpose |
|---------|---------|
| **SQLite** | Fast queries, cross-session operations, event replay |
| **File-based** | Claude SDK compatibility, JSONL logs, session artifacts |

### Synchronization

The `SessionService` maintains consistency between both systems:

1. **Creation**: File-based session created first, then DB record
2. **Rollback**: On DB failure, file-based session is cleaned up
3. **Updates**: DB updated for status/metrics, files for SDK-specific data

### File-based Session Structure

```
sessions/
└── YYYYMMDD_HHMMSS_hexchars/
    ├── session_info.json     # Session metadata
    ├── agent.jsonl           # SDK conversation log
    ├── workspace/            # Agent working files
    ├── shell-snapshots/      # Terminal state snapshots
    ├── todos/                # Task tracking
    └── debug/                # Debug information
```

---

## Configuration

### Database Path

Configured in `config/api.yaml`:

```yaml
database:
  # SQLite database path (relative to Project root)
  path: data/agentum.db
```

### Connection Settings

Defined in `src/db/database.py`:

```python
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)
```

---

## Future Considerations

1. **PostgreSQL Migration** - Replace SQLite for high-concurrency production
2. **Redis Integration** - Implement pub/sub for multi-instance deployments
3. **Event Archival** - Move old events to cold storage
4. **Database Migrations** - Implement Alembic for schema evolution

