# Agentum Current Architecture

## Executive Summary

Agentum is a Claude Code SDK-based AI agent platform that provides two execution modes:
1. **CLI Mode** (`agent_cli.py`) - Direct execution with console tracing
2. **HTTP Mode** (`agent_http.py`) - REST API-based execution through FastAPI backend

Both modes share a common core that handles agent execution, permissions, sessions, and skills. This document describes the current implementation, component interfaces, control flow, and implementation status.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Entry Points: CLI vs HTTP](#2-entry-points-cli-vs-http)
3. [Core Components](#3-core-components)
4. [Control Flow Diagrams](#4-control-flow-diagrams)
5. [Component Interfaces](#5-component-interfaces)
6. [Implementation Status](#6-implementation-status)
7. [File Structure Reference](#7-file-structure-reference)

---

## 1. Architecture Overview

### 1.1 High-Level Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ENTRY POINTS                                      │
│                                                                              │
│   ┌────────────────────┐              ┌────────────────────┐                │
│   │   agent_cli.py     │              │   agent_http.py    │                │
│   │   (Direct CLI)     │              │   (HTTP Client)    │                │
│   └─────────┬──────────┘              └─────────┬──────────┘                │
│             │                                   │                            │
│             │ Imports & Executes                │ HTTP Requests to           │
│             │                                   │                            │
│             ▼                                   ▼                            │
│   ┌────────────────────┐              ┌────────────────────┐                │
│   │  src/core/agent.py │              │  src/api/main.py   │                │
│   │  (CLI Entry)       │              │  (FastAPI App)     │                │
│   └─────────┬──────────┘              └─────────┬──────────┘                │
│             │                                   │                            │
└─────────────┼───────────────────────────────────┼────────────────────────────┘
              │                                   │
              │ TaskExecutionParams               │ TaskExecutionParams
              │ + ExecutionTracer                 │ + BackendConsoleTracer
              ▼                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      UNIFIED TASK RUNNER LAYER                               │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                     execute_agent_task()                           │     │
│   │                    (src/core/task_runner.py)                       │     │
│   │                                                                    │     │
│   │  • Loads AgentConfigLoader                                         │     │
│   │  • Loads PermissionManager from profile                            │     │
│   │  • Builds AgentConfig with merged overrides                        │     │
│   │  • Creates and runs ClaudeAgent                                    │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                   │                                          │
└───────────────────────────────────┼──────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          SHARED CORE LAYER                                   │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                        ClaudeAgent                                 │     │
│   │                    (src/core/agent_core.py)                        │     │
│   │                                                                    │     │
│   │  • Builds ClaudeAgentOptions for SDK                              │     │
│   │  • Manages session lifecycle                                       │     │
│   │  • Renders Jinja2 prompts                                          │     │
│   │  • Processes SDK messages via TraceProcessor                       │     │
│   │  • Handles permission callbacks                                    │     │
│   │  • Manages checkpoints                                             │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│             │         │           │            │            │                │
│             ▼         ▼           ▼            ▼            ▼                │
│   ┌─────────┐  ┌───────────┐  ┌─────────┐  ┌───────┐  ┌──────────┐          │
│   │Sessions │  │Permissions│  │ Skills  │  │Schemas│  │  Tracer  │          │
│   │Manager  │  │ Manager   │  │ Manager │  │       │  │          │          │
│   └─────────┘  └───────────┘  └─────────┘  └───────┘  └──────────┘          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SDK LAYER                                    │
│                                                                              │
│   ┌───────────────────────────────────────────────────────────────────┐     │
│   │                   Claude Agent SDK (claude_agent_sdk)              │     │
│   │                                                                    │     │
│   │  • ClaudeSDKClient - async context manager                         │     │
│   │  • ClaudeAgentOptions - configuration                              │     │
│   │  • Tool execution (Read, Write, Edit, Bash, etc.)                  │     │
│   │  • Permission callbacks                                            │     │
│   │  • MCP server integration                                          │     │
│   └───────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Key Architectural Decisions

| Decision | Description |
|----------|-------------|
| **Dual Entry Points** | CLI for direct execution, HTTP client for API-based execution |
| **Unified Task Runner** | Both entry points use `execute_agent_task()` from `task_runner.py` |
| **Shared Core** | Task runner uses `ClaudeAgent` from `agent_core.py` |
| **File-based Sessions** | Sessions stored in `sessions/` directory with JSON/YAML files |
| **Dual Storage (HTTP)** | SQLite for fast queries + file-based for SDK compatibility |
| **Permission Profiles** | YAML-based permission configuration loaded once at startup |
| **Jinja2 Templates** | System/user prompts rendered from templates in `prompts/` |
| **Tracer Pattern** | Console output via `TracerBase` implementations |
| **SSE Event Streaming** | Real-time execution events via Server-Sent Events (Stage 2) |

---

## 2. Entry Points: CLI vs HTTP

### 2.1 Comparison Matrix

| Aspect | CLI (`agent_cli.py`) | HTTP (`agent_http.py`) |
|--------|---------------------|------------------------|
| **Execution** | Direct, synchronous | Background async task |
| **Task Runner** | `execute_agent_task()` | `execute_agent_task()` |
| **Console Output** | `ExecutionTracer` (rich, interactive) | `EventingTracer` wrapping `BackendConsoleTracer` |
| **Real-time Events** | Direct tracer output | SSE streaming via `/sessions/{id}/events` |
| **Session Storage** | File-based only | SQLite + File-based |
| **Authentication** | None | JWT Bearer Token |
| **Configuration** | YAML files + CLI args | YAML files + API request body |
| **Result Delivery** | stdout + output file | SSE stream + fallback to polling |
| **Special Commands** | `--show-tools`, `--init-permissions` | N/A |

### 2.2 Entry Point Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CLI MODE                                          │
│                                                                              │
│   agent_cli.py  →  src/core/agent.py:main()  →  execute_task()              │
│        │                    │                         │                      │
│        │                    ▼                         ▼                      │
│        │           Build TaskExecutionParams    execute_agent_task()         │
│        │           + ExecutionTracer                  │                      │
│        │                                              ▼                      │
│        │                                       ClaudeAgent.run()             │
│        │                                       → Console output              │
│        ▼                                                                     │
│   Returns exit code 0/1                                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                           HTTP MODE                                          │
│                                                                              │
│   agent_http.py (client)  ─HTTP→  src/api/main.py (server)                  │
│        │                                  │                                  │
│        │                                  ▼                                  │
│        │                    POST /api/v1/sessions/run                        │
│        │                                  │                                  │
│        │                                  ▼                                  │
│        │                    SessionService.create_session()                  │
│        │                                  │                                  │
│        │                                  ▼                                  │
│        │                    AgentRunner.start_task()                         │
│        │                          (background)                               │
│        │                                  │                                  │
│        │                                  ▼                                  │
│        │                    Build TaskExecutionParams                        │
│        │                    + EventingTracer(BackendConsoleTracer)           │
│        │                                  │                                  │
│        │                                  ▼                                  │
│        │                    execute_agent_task()                             │
│        │                    → Linear console + file log + SSE events         │
│        │                                                                     │
│        ├─ SSE: GET /sessions/{id}/events  ─────────────────────────────────▶│
│        │   (Real-time event stream)      ◀─────────────────────────────────│
│        │   ├─ agent_start                                                    │
│        │   ├─ tool_start / tool_complete                                     │
│        │   ├─ thinking / message                                             │
│        │   └─ agent_complete                                                 │
│        │                                                                     │
│   [Fallback] Poll: GET /sessions/{id}  ←───────────────────────────────────│
│   Result: GET /sessions/{id}/result                                          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Shared CLI Argument Definitions

Both entry points use `cli_common.py` for argument parsing:

```python
# Shared argument groups (from cli_common.py):
add_task_arguments()       # --task, --task-file
add_directory_arguments()  # --dir, --add-dir
add_session_arguments()    # --resume, --fork-session, --list-sessions
add_config_override_arguments()  # --model, --max-turns, --timeout, --no-skills
add_permission_arguments() # --profile, --permission-mode
add_role_argument()        # --role
add_output_arguments()     # --output, --json
add_logging_arguments()    # --log-level

# CLI-specific (agent_cli.py only):
add_cli_arguments()        # --config, --secrets, --set, --show-tools, etc.

# HTTP-specific (agent_http.py only):
add_http_arguments()       # --poll-interval, --no-wait, --api-url
```

---

## 3. Core Components

### 3.1 Component Dependency Graph

```
┌──────────────────┐          ┌──────────────────────┐
│TaskExecutionParams│         │ execute_agent_task() │
│  (schemas.py)     │────────▶│  (task_runner.py)    │
└──────────────────┘          └──────────┬───────────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
         ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
         │ AgentConfigLoader│ │ PermissionManager│ │   TracerBase     │
         │  (config.py)     │ │(permission_prof.)│ │  (tracer.py)     │
         └────────┬─────────┘ └──────────────────┘ └──────────────────┘
                  │
                  ▼
         ┌──────────────────────┐
         │    AgentConfig       │
         │   (schemas.py)       │
         └──────────┬───────────┘
                    │
                    ▼
         ┌──────────────────────┐
         │     ClaudeAgent      │
         │  (agent_core.py)     │
         └──────────┬───────────┘
                                         │
         ┌───────────────────────────────┼───────────────────────────────┐
         │                               │                               │
         ▼                               ▼                               ▼
┌──────────────────┐          ┌──────────────────────┐       ┌────────────────────┐
│ SessionManager   │          │  PermissionManager   │       │   SkillManager     │
│  (sessions.py)   │          │(permission_profiles) │       │   (skills.py)      │
└────────┬─────────┘          └──────────┬───────────┘       └────────┬───────────┘
         │                               │                            │
         │                               │                            │
         ▼                               ▼                            ▼
┌──────────────────┐          ┌──────────────────────┐       ┌────────────────────┐
│  SessionInfo     │          │  PermissionProfile   │       │      Skill         │
│  (schemas.py)    │          │(permission_profiles) │       │   (skills.py)      │
└──────────────────┘          └──────────────────────┘       └────────────────────┘
                                         │
                                         ▼
                              ┌──────────────────────┐
                              │ PermissionConfig     │
                              │(permission_config.py)│
                              └──────────────────────┘
```

### 3.2 Component Descriptions

| Component | File | Responsibility |
|-----------|------|----------------|
| **execute_agent_task** | `task_runner.py` | Unified entry point for CLI and HTTP execution |
| **TaskExecutionParams** | `schemas.py` | Dataclass for unified execution parameters |
| **ClaudeAgent** | `agent_core.py` | Main agent execution orchestrator |
| **AgentConfigLoader** | `config.py` | Loads `agent.yaml` + `secrets.yaml` |
| **PermissionManager** | `permission_profiles.py` | Loads permission profile, manages tool access |
| **SessionManager** | `sessions.py` | File-based session CRUD, checkpoints |
| **SkillManager** | `skills.py` | Loads skills from `skills/` directory |
| **TraceProcessor** | `trace_processor.py` | Processes SDK messages for tracing |
| **TracerBase** | `tracer.py` | Abstract interface for execution tracing |
| **ExecutionTracer** | `tracer.py` | Rich interactive CLI output |
| **BackendConsoleTracer** | `tracer.py` | Linear timestamped backend output + logging |
| **EventingTracer** | `tracer.py` | Tracer wrapper that emits events to asyncio queue for SSE |
| **OutputSchema** | `schemas.py` | Defines `output.yaml` structure |

### 3.3 Configuration Files

```
config/
├── agent.yaml          # Agent configuration (model, max_turns, etc.)
├── secrets.yaml        # API keys (ANTHROPIC_API_KEY)
├── permissions.yaml    # Permission profile (tools, allow/deny rules)
└── api.yaml           # API server configuration (host, port, CORS)
```

---

## 4. Control Flow Diagrams

### 4.1 CLI Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CLI EXECUTION FLOW                                    │
└─────────────────────────────────────────────────────────────────────────────┘

agent_cli.py::main()
         │
         ▼
src/core/agent.py::main()
         │
         ├──────────────────────────────────────────────────────────────┐
         │                                                              │
         ▼                                                              │
[Parse Arguments]                                                       │
         │                                                              │
         ▼                                                              │
[Handle Special Commands?]──YES──▶ show_tools() / list_sessions() / ...│
         │                                   │                          │
         NO                                  │                          │
         │                                   ▼                          │
         ▼                              return 0                        │
[Load Configuration]                                                    │
    ├─ AgentConfigLoader.load()                                         │
    ├─ Set ANTHROPIC_API_KEY env var                                   │
    └─ Validate required fields                                         │
         │                                                              │
         ▼                                                              │
[Load Permission Profile]                                               │
    └─ PermissionManager(profile_path)                                  │
         │                                                              │
         ▼                                                              │
[Build AgentConfig]                                                     │
    ├─ Merge YAML config + CLI overrides                               │
    ├─ Get allowed_tools from profile                                   │
    └─ Get auto_checkpoint_tools from profile                           │
         │                                                              │
         ▼                                                              │
execute_task(args, config_loader)                                       │
         │                                                              │
         ▼                                                              │
[Build TaskExecutionParams]                                             │
    ├─ task, working_dir, model, max_turns, ...                         │
    ├─ tracer=ExecutionTracer (rich interactive)                        │
    └─ enable_skills=False if --no-skills                               │
         │                                                              │
         ▼                                                              │
execute_agent_task(params, config_loader)                               │
         │  [task_runner.py - unified entry point]                      │
         ▼                                                              │
[Task Runner Logic]                                                     │
    ├─ Load/Apply config + params overrides                             │
    ├─ Create PermissionManager                                         │
    ├─ Build AgentConfig                                                │
    └─ Create ClaudeAgent with tracer                                   │
         │                                                              │
         ▼                                                              │
ClaudeAgent.run()                                          │
         │                                                              │
         ├────────────────────────────────────────────────────────────────
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ClaudeAgent._execute()                                │
│                                                                              │
│  1. Create/Resume Session (SessionManager)                                   │
│  2. Set session context (PermissionManager.set_session_context)              │
│  3. Copy skills to workspace (if enabled)                                    │
│  4. Load system prompt template (prompts/system.j2)                          │
│  5. Build user prompt template (prompts/user.j2)                             │
│  6. Build ClaudeAgentOptions:                                                │
│     ├─ system_prompt, model, max_turns                                       │
│     ├─ tools, allowed_tools, disallowed_tools                                │
│     ├─ can_use_tool callback (permission checking)                           │
│     ├─ mcp_servers (agentum system tools)                                    │
│     └─ cwd, add_dirs, env                                                    │
│  7. Execute SDK:                                                             │
│     ├─ async with ClaudeSDKClient(options) as client:                        │
│     │     await client.query(user_prompt)                                    │
│     │     async for message in client.receive_response():                    │
│     │         trace_processor.process_message(message)                       │
│     │         checkpoint_tracker.process_message(message)                    │
│  8. Parse output.yaml from workspace                                         │
│  9. Cleanup session (remove skills folder)                                   │
│ 10. Return AgentResult                                                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
         │
         ▼
[Format Result]
    ├─ JSON output (--json flag)
    └─ Formatted text (default)
         │
         ▼
[Return Exit Code]
    ├─ 0 if status == COMPLETE
    └─ 1 otherwise
```

### 4.2 HTTP Execution Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        HTTP EXECUTION FLOW                                   │
└─────────────────────────────────────────────────────────────────────────────┘

                    CLIENT SIDE                │              SERVER SIDE
                                               │
agent_http.py::main()                          │   src/api/main.py (FastAPI)
         │                                     │
         ▼                                     │
[Parse Arguments]                              │
[Load API Config from api.yaml]                │
         │                                     │
         ▼                                     │
[Authenticate]                                 │
    POST /api/v1/auth/token                    │
    {"user_id": "cli-user"}             ──────▶│   routes/auth.py
         │                                     │   ├─ Create/get User in DB
         ▼                                     │   └─ Return JWT token
    Store JWT token                      ◀─────│
         │                                     │
         ▼                                     │
[Build Request Data]                           │
    RunTaskRequest:                            │
    ├─ task                                    │
    ├─ working_dir                             │
    ├─ additional_dirs                         │
    ├─ resume_session_id                       │
    ├─ fork_session                            │
    └─ config (model, max_turns, etc.)         │
         │                                     │
         ▼                                     │
    POST /api/v1/sessions/run            ──────▶│   routes/sessions.py::run_task()
         │                                     │         │
         │                                     │         ▼
         │                                     │   [Create Session in DB]
         │                                     │   SessionService.create_session()
         │                                     │         │
         │                                     │         ▼
         │                                     │   [Build TaskParams]
         │                                     │   build_task_params()
         │                                     │         │
         │                                     │         ▼
         │                                     │   [Start Background Task]
         │                                     │   AgentRunner.start_task(params)
         │                                     │         │
         │                                     │         └─────────────────┐
         ▼                                     │                           │
    Receive session_id                   ◀─────│   Return TaskStartedResponse
         │                                     │                           │
         │                                     │                           ▼
         │                                     │   ┌─────────────────────────────────┐
         │                                     │   │ BACKGROUND TASK                 │
         │                                     │   │                                 │
         │                                     │   │ AgentRunner._run_agent()        │
         │                                     │   │     │                           │
         │                                     │   │     ▼                           │
         │                                     │   │ [Build TaskExecutionParams]     │
         │                                     │   │   tracer=BackendConsoleTracer() │
         │                                     │   │     │                           │
         │                                     │   │     ▼                           │
         │                                     │   │ execute_agent_task(params)      │
         │                                     │   │   [task_runner.py]              │
         │                                     │   │     │                           │
         │                                     │   │     ▼                           │
         │                                     │   │ ClaudeAgent.run()               │
         │                                     │   │ → Linear console + file log    │
         │                                     │   │     │                           │
         │                                     │   │     ▼                           │
         │                                     │   │ Update DB status                │
         │                                     │   └─────────────────────────────────┘
         │                                     │
         ▼                                     │
[Poll for Completion]                          │
    LOOP:                                      │
    GET /api/v1/sessions/{id}            ──────▶│   routes/sessions.py::get_session()
         │                                     │   └─ Return SessionResponse
         ▼                                     │
    Check status                         ◀─────│
    IF status in (completed, failed, cancelled)│
        BREAK                                  │
    SLEEP poll_interval                        │
         │                                     │
         ▼                                     │
[Get Result]                                   │
    GET /api/v1/sessions/{id}/result     ──────▶│   routes/sessions.py::get_result()
         │                                     │   ├─ Get session from DB
         │                                     │   ├─ Parse output.yaml
         ▼                                     │   └─ Return ResultResponse
    Display/Write Result                 ◀─────│
         │                                     │
         ▼                                     │
    Return exit code                           │
```

### 4.3 Permission Checking Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     PERMISSION CHECKING FLOW                                 │
└─────────────────────────────────────────────────────────────────────────────┘

Claude SDK attempts tool use
         │
         ▼
ClaudeAgentOptions.can_use_tool callback
         │
         ▼
create_permission_callback()
    (from permissions.py)
         │
         ▼
┌────────────────────────────────────────────┐
│ Is it a system tool (agentum:*)?           │
│   YES → PermissionResultAllow (always)     │
└────────────────────────────────────────────┘
         │ NO
         ▼
┌────────────────────────────────────────────┐
│ Check dangerouslyDisableSandbox?           │
│   YES → PermissionResultDeny(interrupt)    │
└────────────────────────────────────────────┘
         │ NO
         ▼
┌────────────────────────────────────────────┐
│ Build tool_call string                     │
│   e.g., "Bash(git status)"                 │
└────────────────────────────────────────────┘
         │
         ▼
PermissionManager.is_allowed(tool_call)
         │
         ├──────────────────────────────────────────┐
         │                                          │
         ▼ ALLOWED                                  ▼ DENIED
┌────────────────────┐               ┌─────────────────────────────────┐
│PermissionResultAllow│               │ Track denial count             │
└────────────────────┘               │ Build actionable denial message │
                                     │                                 │
                                     ▼                                 │
                           ┌──────────────────────────────────────────┐│
                           │ should_interrupt = denial_count >=3 OR   ││
                           │                    security_violation    ││
                           └──────────────────────────────────────────┘│
                                     │                                 │
                                     ▼                                 │
                           ┌────────────────────────────────────────┐  │
                           │ PermissionResultDeny(                  │  │
                           │   message=actionable_guidance,         │  │
                           │   interrupt=should_interrupt)          │  │
                           └────────────────────────────────────────┘  │
                                     │                                 │
                                     └─────────────────────────────────┘
```

---

## 5. Component Interfaces

### 5.1 Unified Task Runner Interface

```python
# TaskExecutionParams (schemas.py)
@dataclass
class TaskExecutionParams:
    """Unified parameters for agent task execution."""
    task: str
    working_dir: Optional[Path] = None
    session_id: Optional[str] = None           # Pre-generated (for HTTP)
    resume_session_id: Optional[str] = None
    fork_session: bool = False
    
    # Config overrides (from agent.yaml if not specified)
    model: Optional[str] = None
    max_turns: Optional[int] = None
    timeout_seconds: Optional[int] = None
    permission_mode: Optional[str] = None
    profile_path: Optional[Path] = None
    role: Optional[str] = None
    additional_dirs: list[str] = field(default_factory=list)
    enable_skills: Optional[bool] = None
    enable_file_checkpointing: Optional[bool] = None
    
    # Tracer (CLI: ExecutionTracer, HTTP: BackendConsoleTracer)
    tracer: Optional[TracerBase] = None

# execute_agent_task (task_runner.py)
async def execute_agent_task(
    params: TaskExecutionParams,
    config_loader: Optional[AgentConfigLoader] = None,
) -> AgentResult:
    """
    Single entry point for agent task execution.
    
    Used by both CLI and HTTP entry points for consistent behavior.
    Handles config loading, permission profiles, and ClaudeAgent creation.
    """
```

### 5.2 ClaudeAgent Interface

```python
class ClaudeAgent:
    def __init__(
        self,
        config: AgentConfig,                    # Required configuration
        sessions_dir: Path = SESSIONS_DIR,      # Session storage
        logs_dir: Path = LOGS_DIR,              # Log files
        skills_dir: Path = None,                # Custom skills directory
        tracer: Union[TracerBase, bool] = True, # Console output tracer
        permission_manager: PermissionManager,  # Required permission manager
    ) -> None

    async def run(
        self,
        task: str,                              # Task description
        system_prompt: Optional[str] = None,    # Custom system prompt
        parameters: Optional[dict] = None,      # Template parameters
        resume_session_id: Optional[str] = None,# Resume existing session
        fork_session: bool = False,             # Fork when resuming
        timeout_seconds: Optional[int] = None,  # Override timeout
        session_id: Optional[str] = None,       # Pre-generated session ID (for API)
    ) -> AgentResult

    async def run_with_timeout(...) -> AgentResult  # Alias for run()
    async def compact(session_id: str) -> dict      # Compact conversation history
    
    # Checkpoint management
    def list_checkpoints(session_id: str) -> list[Checkpoint]
    def get_checkpoint(session_id, checkpoint_id, index) -> Optional[Checkpoint]
    def create_checkpoint(session_id, uuid, description) -> Checkpoint
    async def rewind_to_checkpoint(session_id, checkpoint_id, index) -> dict
```

### 5.3 SessionManager Interface

```python
class SessionManager:
    def __init__(self, sessions_dir: Path) -> None
    
    # Session CRUD
    def create_session(working_dir: str, session_id: Optional[str]) -> SessionInfo
    def load_session(session_id: str) -> SessionInfo
    def update_session(session_info, status, resume_id, num_turns, ...) -> SessionInfo
    def list_sessions() -> list[SessionInfo]
    
    # Workspace management
    def get_session_dir(session_id: str) -> Path
    def get_workspace_dir(session_id: str) -> Path
    def get_output_file(session_id: str) -> Path
    def get_log_file(session_id: str) -> Path
    
    # Skills workspace
    def copy_skill_to_workspace(session_id, skill_name, source_dir) -> Path
    def cleanup_workspace_skills(session_id) -> None
    
    # Output parsing
    def parse_output(session_id: str) -> dict
    
    # Checkpoint management
    def add_checkpoint(session_info, uuid, type, ...) -> Checkpoint
    def list_checkpoints(session_id) -> list[Checkpoint]
    def get_checkpoint(session_id, checkpoint_id, index) -> Optional[Checkpoint]
    def clear_checkpoints_after(session_info, uuid) -> int
```

### 5.4 PermissionManager Interface (permission_profiles.py)

```python
class PermissionManager:
    def __init__(self, profile_path: Optional[Path] = None) -> None
    
    # Profile management
    def activate() -> PermissionProfile
    def reload_profile() -> None
    
    # Session context (for workspace sandboxing)
    def set_session_context(session_id, workspace_path, workspace_absolute) -> None
    def clear_session_context() -> None
    
    # Permission checking
    def is_allowed(tool_call: str) -> bool
    def needs_confirmation(tool_call: str) -> bool
    
    # Tool access
    def get_enabled_tools() -> list[str]
    def get_permission_checked_tools() -> set[str]
    def get_disabled_tools() -> set[str]
    def get_pre_approved_tools() -> list[str]
    def get_allowed_dirs() -> list[str]
    
    # Tracing
    def set_tracer(tracer: TracerBase) -> None
```

### 5.5 TracerBase Interface

```python
class TracerBase(ABC):
    @abstractmethod
    def on_agent_start(session_id, model, tools, working_dir, skills, task) -> None
    
    @abstractmethod
    def on_tool_start(tool_name, tool_input, tool_id) -> None
    
    @abstractmethod
    def on_tool_complete(tool_name, tool_id, result, duration_ms, is_error) -> None
    
    @abstractmethod
    def on_thinking(thinking_text: str) -> None
    
    @abstractmethod
    def on_message(text: str, is_partial: bool) -> None
    
    @abstractmethod
    def on_error(error_message: str, error_type: str) -> None
    
    @abstractmethod
    def on_agent_complete(status, num_turns, duration_ms, cost, result, ...) -> None
    
    @abstractmethod
    def on_output_display(output, error, comments, result_files, status) -> None
    
    @abstractmethod
    def on_profile_switch(profile_type, profile_name, tools, allow_count, deny_count) -> None
    
    @abstractmethod
    def on_hook_triggered(hook_event, tool_name, decision, message) -> None
    
    @abstractmethod
    def on_conversation_turn(turn, prompt, response, duration, tools) -> None
    
    @abstractmethod
    def on_session_connect(session_id) -> None
    
    @abstractmethod
    def on_session_disconnect(session_id, total_turns, total_duration_ms) -> None
```

**Implementations:**
- `ExecutionTracer` - Full console output with colors, spinners, boxes (CLI)
- `BackendConsoleTracer` - Linear timestamped output + Python logging (HTTP backend)
- `EventingTracer` - Wrapper tracer that emits structured events to asyncio queue (SSE streaming)
- `QuietTracer` - Minimal output (errors and completion only)
- `NullTracer` - No output (for testing)

### 5.6 API Service Interfaces

```python
# AgentRunner (services/agent_runner.py)
class AgentRunner:
    async def start_task(params: TaskParams) -> None
    async def cancel_task(session_id: str) -> bool
    def is_running(session_id: str) -> bool
    def get_result(session_id: str) -> Optional[dict]
    def get_event_queue(session_id: str) -> Optional[asyncio.Queue]
    def get_or_create_event_queue(session_id: str) -> asyncio.Queue
    def clear_event_queue(session_id: str) -> None

# SessionService (services/session_service.py)
class SessionService:
    async def create_session(db, user_id, task, working_dir, model) -> Session
    async def get_session(db, session_id, user_id) -> Optional[Session]
    async def list_sessions(db, user_id, limit, offset) -> tuple[list[Session], int]
    async def update_session(db, session, status, ...) -> Session
    def get_session_output(session_id: str) -> dict
    def get_session_info(session_id: str) -> dict
```

### 5.7 SSE Event Streaming Architecture

**EventingTracer** - Wrapper pattern for real-time event emission:

```python
class EventingTracer(TracerBase):
    """Tracer wrapper that emits structured events to an asyncio queue."""
    
    def __init__(
        self,
        tracer: TracerBase,                    # Wrapped tracer (BackendConsoleTracer)
        event_queue: Optional[asyncio.Queue],  # SSE event queue
    ) -> None
    
    def emit_event(event_type: str, data: dict[str, Any]) -> None
    # All TracerBase methods (on_agent_start, on_tool_start, etc.)
    # Call wrapped tracer + emit structured event to queue
```

**Event Structure:**
```json
{
  "type": "agent_start | tool_start | tool_complete | thinking | message | error | agent_complete | ...",
  "data": {
    // Event-specific data
  },
  "timestamp": "2026-01-04T12:34:56.789Z",
  "sequence": 123
}
```

**SSE Endpoint Flow:**
1. Client connects to `GET /sessions/{id}/events?token={jwt}`
2. Server validates token and retrieves/creates event queue
3. Server streams events as they're emitted by `EventingTracer`
4. Heartbeat sent every 30s if no events (`: heartbeat\n\n`)
5. Stream ends on `agent_complete`, `error`, or `cancelled` events
6. Queue cleanup when task completes and stream ends

**Fallback Strategy:**
- HTTP client first attempts SSE streaming
- If SSE fails (network issues, old HTTP proxy), falls back to polling
- CLI always uses direct `ExecutionTracer` (no SSE needed)

---

## 6. Implementation Status

### 6.1 Implemented Features

| Feature | CLI | HTTP | Notes |
|---------|-----|------|-------|
| Task execution | ✅ | ✅ | Both use ClaudeAgent.run() |
| Session creation | ✅ | ✅ | File-based + SQLite (HTTP) |
| Session resumption | ✅ | ✅ | Via --resume / resume_session_id |
| Session forking | ✅ | ✅ | Via --fork-session flag |
| List sessions | ✅ | ✅ | --list-sessions / GET /sessions |
| Permission profiles | ✅ | ✅ | YAML-based configuration |
| Skills system | ✅ | ✅ | Markdown + optional scripts |
| File checkpointing | ✅ | ✅ | Auto-checkpoints on Write/Edit |
| Checkpoint rewind | ✅ | ❌ | API endpoint not exposed |
| Console tracing | ✅ | N/A | ExecutionTracer for CLI |
| SSE event streaming | N/A | ✅ | Real-time events via /sessions/{id}/events |
| JSON output | ✅ | ✅ | --json flag / API response |
| Config overrides | ✅ | ✅ | CLI args / request body |
| Task cancellation | ❌ | ✅ | POST /sessions/{id}/cancel |
| Authentication | N/A | ✅ | JWT Bearer tokens |
| MCP system tools | ✅ | ✅ | agentum:write_output |

### 6.2 Missing / Planned Features

| Feature | Status | Notes |
|---------|--------|-------|
| SSE streaming | ✅ **Completed (Stage 2)** | Real-time execution events via SSE |
| Web terminal UI | 🔄 Planned (Stage 3) | React-based terminal |
| Multi-agent context sharing | 🔄 Future | Shared session access |
| Session archival to DB | 🔄 Future | PostgreSQL session storage |
| Docker deployment | 🔄 Future | Containerized deployment |
| Version endpoint | ❌ Not implemented | `<yyyymmdd>-<commit>` format |
| PostgreSQL migration | 🔄 Future | Replace SQLite |
| Webhook notifications | ❌ Not implemented | Event callbacks |

### 6.3 Known Gaps

1. **Checkpoint rewind not exposed via API** - Only available through CLI/direct ClaudeAgent use
2. **No session archival** - Sessions remain in file system indefinitely
3. **SQLite limitations** - Single-file database, not suitable for high concurrency
4. **No distributed session sharing** - Sessions are file-local, no cross-instance access

---

## 7. File Structure Reference

```
Project/
├── agent_cli.py              # CLI entry point (thin wrapper)
├── agent_http.py             # HTTP client (polls API)
├── config/
│   ├── agent.yaml            # Agent configuration
│   ├── api.yaml              # API server configuration
│   ├── permissions.yaml      # Permission profile
│   └── secrets.yaml          # API keys
├── prompts/
│   ├── system.j2             # System prompt template
│   ├── user.j2               # User prompt template
│   ├── roles/
│   │   └── default.md        # Default role definition
│   └── modules/              # Prompt template modules
├── sessions/                 # Session storage
│   └── {session_id}/
│       ├── session_info.json # Session metadata
│       ├── agent.jsonl       # SDK message log
│       └── workspace/
│           ├── output.yaml   # Agent output
│           └── skills/       # Copied skills (during execution)
├── skills/                   # Available skills
│   └── {skill_name}/
│       ├── {skill_name}.md   # Skill description
│       └── scripts/          # Optional scripts
├── src/
│   ├── __init__.py
│   ├── config.py             # Configuration loading
│   ├── api/                  # FastAPI application
│   │   ├── __init__.py
│   │   ├── main.py           # App factory
│   │   ├── deps.py           # Dependency injection
│   │   ├── models.py         # Pydantic request/response models
│   │   └── routes/
│   │       ├── auth.py       # POST /auth/token
│   │       ├── health.py     # GET /health
│   │       └── sessions.py   # /sessions/* endpoints
│   ├── core/                 # Core agent logic
│   │   ├── __init__.py
│   │   ├── agent.py          # CLI entry point logic
│   │   ├── agent_core.py     # ClaudeAgent implementation
│   │   ├── cli_common.py     # Shared CLI arguments
│   │   ├── constants.py      # UI constants
│   │   ├── exceptions.py     # Custom exceptions
│   │   ├── hooks.py          # SDK hooks
│   │   ├── logging_config.py # Logging setup
│   │   ├── output.py         # Output formatting
│   │   ├── permission_config.py   # Permission configuration
│   │   ├── permission_profiles.py # PermissionManager
│   │   ├── permissions.py    # Permission callback factory
│   │   ├── schemas.py        # Pydantic data models + TaskExecutionParams
│   │   ├── sessions.py       # SessionManager
│   │   ├── skills.py         # SkillManager
│   │   ├── task_runner.py    # Unified execute_agent_task()
│   │   ├── tasks.py          # Task loading
│   │   ├── tool_utils.py     # Tool helper functions
│   │   ├── trace_processor.py # SDK message processor
│   │   └── tracer.py         # TracerBase + ExecutionTracer + BackendConsoleTracer
│   ├── db/                   # Database layer
│   │   ├── __init__.py
│   │   ├── database.py       # SQLAlchemy setup
│   │   └── models.py         # User, Session models
│   └── services/             # Business logic
│       ├── __init__.py
│       ├── agent_runner.py   # Background task runner
│       ├── auth_service.py   # JWT authentication
│       └── session_service.py # Session CRUD service
├── tools/                    # MCP tools
│   └── agentum/
│       └── system_write_output/  # agentum:write_output tool
└── tests/                    # Test suites
```

---

## Summary

Agentum provides a well-structured dual-entry architecture where:

1. **Unified Task Runner** (`task_runner.py`) provides a single entry point for both CLI and HTTP
2. **CLI Mode** provides direct execution with rich interactive console output (`ExecutionTracer`)
3. **HTTP Mode** provides API-based execution with linear console + file logging (`BackendConsoleTracer`)

The key components (ClaudeAgent, SessionManager, PermissionManager, SkillManager) are designed with clear interfaces and separation of concerns. The tracer pattern allows different output strategies for CLI vs API modes, while the unified task runner ensures consistent behavior across both entry points.

**Architecture Benefits:**
- Single source of truth for task execution logic
- Easy to add new entry points (WebSocket, gRPC, etc.)
- Consistent behavior guaranteed across CLI and HTTP
- ~40% code reduction in entry points through unification

**Next Steps (per TODO_AND_MISSING.md):**
- ✅ Stage 2: SSE streaming for real-time execution events (COMPLETED)
- Stage 3: Build React web terminal UI
- Future: Multi-agent context sharing, PostgreSQL migration, Docker deployment

---

## 8. Recent Changes (Branch: codex/add-sse-real-time-event-streaming)

### Overview
This branch implements **Stage 2: Real-time SSE Event Streaming**, enabling the HTTP client to receive live execution events instead of polling for completion.

### Key Changes

#### 1. New EventingTracer Component (`src/core/tracer.py`)
- **Purpose**: Wrapper tracer that emits structured events to an asyncio queue while preserving original tracer behavior
- **Pattern**: Decorator pattern - wraps `BackendConsoleTracer` and calls both wrapped tracer + event emission
- **Event Structure**: JSON events with `type`, `data`, `timestamp`, and monotonic `sequence` number
- **Integration**: All 14 tracer methods emit corresponding events (agent_start, tool_start, tool_complete, thinking, message, error, agent_complete, etc.)

#### 2. SSE Endpoint (`src/api/routes/sessions.py`)
- **Route**: `GET /sessions/{session_id}/events?token={jwt}`
- **Authentication**: Token passed via query parameter (EventSource API limitation)
- **Streaming**: Server-Sent Events (text/event-stream) with async generator
- **Features**:
  - 30-second heartbeat to keep connection alive
  - Automatic stream termination on completion/error/cancellation
  - Queue cleanup after stream ends
  - Proper SSE formatting with `data:` prefix and `id:` sequence

#### 3. AgentRunner Event Queue Management (`src/services/agent_runner.py`)
- **New Methods**:
  - `get_or_create_event_queue()` - Ensure queue exists before streaming
  - `clear_event_queue()` - Cleanup after stream ends
- **Integration**: Creates `EventingTracer(BackendConsoleTracer, event_queue)` instead of plain `BackendConsoleTracer`
- **Error Handling**: Emits `cancelled` and `error` events for proper client notification

#### 4. HTTP Client SSE Streaming (`agent_http.py`)
- **New Functions**:
  - `stream_events()` - Connect to SSE endpoint and parse events
  - `apply_sse_event()` - Dispatch events to `ExecutionTracer` for rich console output
- **User Experience**: HTTP client now has same rich interactive output as CLI mode
- **Fallback**: Gracefully falls back to polling if SSE fails (network issues, old proxies)
- **Implementation**: Uses stdlib `urllib.request` for SSE streaming (no external dependencies)

#### 5. Test Suite (`tools/test_sse.py`)
- Comprehensive integration test validating:
  - Event sequence monotonicity
  - All required event types received
  - Proper timestamps on all events
  - Metrics in `agent_complete` event
- Can be run standalone to verify SSE functionality

### Architectural Impact

**Before (Stage 1):**
```
HTTP Client → Poll /sessions/{id} every N seconds → Get status → Parse result
```

**After (Stage 2):**
```
HTTP Client → Stream /sessions/{id}/events → Receive real-time events → Rich console output
              ↓ (fallback if SSE fails)
              Poll /sessions/{id} (legacy path)
```

### Benefits Achieved

1. **Real-time Feedback**: HTTP client now shows live tool execution, thinking, and messages
2. **Consistent UX**: CLI and HTTP modes now provide identical user experience
3. **Code Reuse**: `ExecutionTracer` logic reused for HTTP client (no duplication)
4. **Backward Compatible**: Polling still works as fallback
5. **Production Ready**: Proper error handling, heartbeats, and connection management
6. **No Breaking Changes**: Existing API endpoints unchanged

### Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `agent_http.py` | +150 | SSE client implementation with fallback |
| `src/api/routes/sessions.py` | +68 | SSE endpoint with event streaming |
| `src/core/tracer.py` | +312 | EventingTracer wrapper class |
| `src/services/agent_runner.py` | +40 | Event queue management |
| `tools/test_sse.py` | +93 (new) | Integration test suite |

**Total**: ~660 lines added, implementing complete Stage 2 functionality.

### Testing

Run the integration test to verify SSE streaming:

```bash
# Start the backend server
cd Project
uvicorn src.api.main:app --host 0.0.0.0 --port 40080

# In another terminal, run the test
python tools/test_sse.py
```

Expected output:
- All event types received (agent_start, tool_start, tool_complete, agent_complete)
- Monotonic sequence numbers starting at 1
- All events have timestamps
- Metrics in agent_complete event

