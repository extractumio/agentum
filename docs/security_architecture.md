# Security Architecture

**Last verified:** 2026-01-09

---

## System Overview

Agentum implements a defense-in-depth security model with **agent-level bubblewrap sandboxing** at its core. The entire Claude Code agent process runs inside a bwrap sandbox, and all child processes (Bash commands, Python scripts, etc.) automatically inherit the sandbox restrictions.

Key security layers:
1. **JWT Authentication** - API access control
2. **Session Ownership** - User-session isolation
3. **Permission Rules** - Tool allow/deny patterns (UX guidance)
4. **Agent-Level Sandbox** - Process-level isolation with automatic inheritance

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                               API GATEWAY (FastAPI)                             │
│                                                                                 │
│   ┌───────────────┐    ┌────────────────┐    ┌──────────────────────┐          │
│   │  POST /auth   │    │  Bearer Token  │    │  Session Ownership   │          │
│   │  /token       │───>│  Validation    │───>│  Verification        │          │
│   └───────────────┘    └────────────────┘    └──────────┬───────────┘          │
│                                                          │                      │
└──────────────────────────────────────────────────────────┼──────────────────────┘
                                                           │
                                                           v
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TASK RUNNER                                        │
│                                                                                 │
│   ┌───────────────────┐    ┌─────────────────┐    ┌───────────────────────┐    │
│   │  Load Security    │    │  Build Bwrap    │    │  Spawn Sandboxed      │    │
│   │  Config           │───>│  Command        │───>│  Agent Process        │    │
│   │  (security.yaml)  │    │                 │    │                       │    │
│   └───────────────────┘    └─────────────────┘    └───────────┬───────────┘    │
│                                                                │                │
└────────────────────────────────────────────────────────────────┼────────────────┘
                                                                 │
                                                                 v
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     BUBBLEWRAP SANDBOX (bwrap)                                  │
│                                                                                 │
│   ┌───────────────────────────────────────────────────────────────────────┐    │
│   │                      ISOLATED NAMESPACE                               │    │
│   │                                                                       │    │
│   │   /session (rw)   ←── Entire session directory mounted                │    │
│   │   ├── workspace/  ←── Agent cwd (writable)                            │    │
│   │   ├── .claude.json                                                    │    │
│   │   ├── projects/   ←── SDK internal                                    │    │
│   │   ├── todos/      ←── SDK todos                                       │    │
│   │   └── ...                                                             │    │
│   │   /skills (ro)    ←── Skills library (read-only)                      │    │
│   │   /src (ro)       ←── Agent modules (read-only)                       │    │
│   │   /usr, /lib, /bin←── System binaries (read-only)                     │    │
│   │   /tmp (tmpfs)    ←── Ephemeral scratch space                         │    │
│   │                                                                       │    │
│   │   NOT MOUNTED (invisible/inaccessible):                               │    │
│   │   ├── /etc        ←── No passwd, shadow, hosts                        │    │
│   │   ├── /home       ←── No home directories                             │    │
│   │   ├── /config     ←── No secrets.yaml                                 │    │
│   │   ├── /data       ←── No database                                     │    │
│   │   ├── /sessions/* ←── No other sessions                               │    │
│   │   └── /logs       ←── No application logs                             │    │
│   │                                                                       │    │
│   │   Claude Code Agent                                                   │    │
│   │   ├── Bash("ps aux") → Only sees sandbox processes (4-5 total)        │    │
│   │   ├── Bash("cat /etc/passwd") → "No such file or directory"           │    │
│   │   ├── Read("./output.txt") → Works (in /session/workspace)            │    │
│   │   └── Python subprocess → Inherits all restrictions                   │    │
│   │                                                                       │    │
│   └───────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
│   Namespace Isolation:                                                          │
│   ├── PID namespace (--unshare-pid): ps only shows sandbox processes           │
│   ├── UTS namespace (--unshare-uts): Hostname isolation                        │
│   ├── IPC namespace (--unshare-ipc): IPC isolation                             │
│   ├── --die-with-parent: Cleanup on parent exit                                │
│   ├── --new-session: TTY isolation                                             │
│   └── --clearenv: Clean environment                                            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Key Security Improvement: Subprocess Inheritance

The previous architecture wrapped individual Bash commands in bwrap, which had critical flaws:
- Claude Code SDK could directly access `/etc/passwd` via Read tool
- Each command required separate wrapping
- Subprocess inheritance didn't work

**New architecture** wraps the entire agent process:

```
OLD (broken):
Docker Container
└── Agent Process (unsandboxed)
    ├── bwrap bash "cmd1"  ← wrapped per command
    ├── bwrap bash "cmd2"  ← wrapped per command
    └── Agent can still read /etc/passwd directly!

NEW (fixed):
Docker Container
└── bwrap [...] -- python sandboxed_agent.py
    └── Claude Code Agent (sandboxed)
        ├── Bash tool → inherits sandbox
        ├── Read tool → only /session accessible
        └── Python subprocess → inherits sandbox
```

---

## Security Layers

### Layer 1: JWT Authentication

**Mechanism:** HS256 JWT tokens

| Parameter | Value | Notes |
|-----------|-------|-------|
| Algorithm | HS256 | HMAC-SHA256 |
| Expiry | 7 days | JWT_EXPIRY_HOURS = 168 |
| Secret | 256-bit | Auto-generated, persisted to `config/secrets.yaml` |

### Layer 2: Session Ownership

**Enforcement Point:** API route handlers via `get_current_user_id` dependency

- Sessions are owned by users
- API queries filter by `user_id`
- Unauthorized access returns 404 (not 403) to prevent enumeration

### Layer 3: Permission Rules

**Configuration:** `config/permissions.yaml`

Permission rules provide **UX guidance** rather than security enforcement:
- When a tool is denied, the agent receives helpful error messages
- Security is enforced by the sandbox at the kernel level

```yaml
session_workspace:
  allow:
    - Read({workspace}/**)
    - Write({workspace}/**)
    - Bash(*)
    # ...
  deny:
    - Write(/skills/**)
    # Deny rules now provide friendly messages
    # Actual blocking is done by sandbox
```

### Layer 4: Agent-Level Sandbox (Primary Security)

**Technology:** [bubblewrap](https://github.com/containers/bubblewrap)

**Configuration:** `config/security.yaml`

```yaml
sandbox:
  enabled: true
  bwrap_path: "bwrap"
  
  # Namespace isolation
  unshare_pid: true   # ps only sees sandbox processes
  unshare_ipc: true
  unshare_uts: true
  
  # System mounts (read-only)
  system_mounts:
    - source: "/usr"
      target: "/usr"
      mode: "ro"
    - source: "/lib"
      target: "/lib"
      mode: "ro"
    - source: "/bin"
      target: "/bin"
      mode: "ro"
  
  # Environment inside sandbox
  environment:
    home: "/session/workspace"
    path: "/usr/bin:/bin"
    claude_config_dir: "/session"
    clear_env: true
```

---

## Session Directory Access

The sandbox mounts the session directory at `/session`:

| Path | Access | Purpose |
|------|--------|---------|
| `/session/workspace/` | rw | Agent cwd, output files |
| `/session/.claude.json` | rw | SDK feature flags |
| `/session/agent.jsonl` | rw | Execution log |
| `/session/projects/` | rw | SDK internal |
| `/session/todos/` | rw | SDK todo management |
| `/session/plans/` | rw | SDK plans |
| `/session/debug/` | rw | Debug output |
| `/session/session_info.json` | ro | Session metadata |

---

## Blocked Operations

### By Sandbox (Kernel-Level Enforcement)

| Operation | Result | Reason |
|-----------|--------|--------|
| `cat /etc/passwd` | "No such file or directory" | `/etc` not mounted |
| `ps aux` | Only 4-5 processes visible | PID namespace isolation |
| `ls /home` | "No such file or directory" | `/home` not mounted |
| `cat /config/secrets.yaml` | "No such file or directory" | `/config` not mounted |
| `ls /sessions/other_session` | "No such file or directory" | Other sessions not mounted |
| `echo test > /usr/test.txt` | "Read-only file system" | `/usr` is read-only |

### Subprocess Inheritance

All restrictions apply to subprocesses:

```
Claude Code
└── Bash("python script.py")
    └── script.py runs in sandbox
        └── subprocess.run("cat /etc/passwd")
            └── BLOCKED by sandbox
```

---

## Network Filtering

**Configuration:** `config/security.yaml`

```yaml
network:
  mode: "whitelist"
  allowed_domains:
    - "api.anthropic.com"
    - "pypi.org"
    - "files.pythonhosted.org"
  allow_localhost: true
```

Network filtering is applied at container level via:
1. **DNS blocking** - `/etc/hosts` entries redirect blocked domains to localhost
2. **iptables rules** - Whitelist only allowed IPs (when kernel supports)

---

## Docker Configuration

**docker-compose.yml:**

```yaml
services:
  agentum-api:
    cap_add:
      - SYS_ADMIN  # Required for bwrap namespace operations
    security_opt:
      - no-new-privileges:true
    # Note: apparmor:unconfined and seccomp:unconfined removed
    # Agent-level bwrap sandbox provides isolation
```

---

## Implementation Files

| File | Purpose |
|------|---------|
| `src/core/sandbox_runner.py` | `SandboxedAgentRunner` class - builds and runs bwrap command |
| `src/core/sandboxed_agent.py` | Entry point for agent inside sandbox |
| `src/core/network_filter.py` | DNS/iptables network filtering |
| `src/core/task_runner.py` | Orchestrates sandboxed vs direct execution |
| `config/security.yaml` | Sandbox and network configuration |

---

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRUSTED ZONE                               │
│                                                                 │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │  API Server     │    │  Task Runner    │                   │
│   │  (FastAPI)      │    │                 │                   │
│   └─────────────────┘    └─────────────────┘                   │
│                                                                 │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │  Session        │    │  Sandbox        │                   │
│   │  Manager        │    │  Runner         │                   │
│   └─────────────────┘    └─────────────────┘                   │
│                                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ Trust Boundary (bwrap)
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                     UNTRUSTED ZONE                              │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                   BUBBLEWRAP SANDBOX                    │  │
│   │                                                         │  │
│   │   ┌─────────────────────────────────────────────────┐   │  │
│   │   │              Claude Code Agent                  │   │  │
│   │   │                                                 │   │  │
│   │   │   All tool execution, file operations, bash     │   │  │
│   │   │   commands, and subprocesses run inside         │   │  │
│   │   │   sandbox with inherited restrictions           │   │  │
│   │   │                                                 │   │  │
│   │   └─────────────────────────────────────────────────┘   │  │
│   │                                                         │  │
│   └─────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Threat Mitigation

| Threat | Mitigation |
|--------|------------|
| Session hijacking | JWT validation + session ownership check |
| Path traversal | Sandbox only mounts specific paths |
| Sandbox escape via `dangerouslyDisableSandbox` | Immediate deny + interrupt in permission callback |
| File system access outside session | bwrap mount restrictions (paths not mounted) |
| Process discovery | PID namespace isolation |
| Environment variable leakage | `--clearenv` flag |
| Network reconnaissance | Network filtering (DNS blocking + iptables) |
| Skill tampering | Skills mounted read-only |
| Subprocess escape | All children inherit sandbox restrictions |

---

## Verified Test Results

Tests run in Docker on macOS (Docker Desktop with linuxkit):

```bash
# Test: ps aux only shows sandbox processes
$ bwrap --unshare-pid [...] -- bash -c "ps aux | wc -l"
4  # Only bwrap, bash, ps visible (not 50+ host processes)

# Test: /etc/passwd not accessible
$ bwrap [...] -- cat /etc/passwd
cat: /etc/passwd: No such file or directory

# Test: Workspace writable
$ bwrap [...] --bind /workspace /workspace -- echo test > /workspace/test.txt
SUCCESS

# Test: System paths read-only
$ bwrap [...] -- echo test > /usr/test.txt
bash: /usr/test.txt: Read-only file system

# Test: Nested subprocess inherits restrictions
$ bwrap [...] -- bash -c "bash -c 'cat /etc/passwd'"
cat: /etc/passwd: No such file or directory
```

---

## Removed Components

The following were removed as part of the sandbox architecture refactor:

| Component | Reason for Removal |
|-----------|-------------------|
| `config/security/dangerous_patterns/*.yaml` (16 files) | Replaced by sandbox kernel-level enforcement |
| `src/core/dangerous_patterns_loader.py` | No longer needed |
| `src/core/sandbox.py` (old) | Replaced by `sandbox_runner.py` |
| `create_dangerous_command_hook()` | Patterns replaced by sandbox |
| `create_sandbox_execution_hook()` | Per-command wrapping replaced by agent-level |
| `DANGEROUS_COMMAND_PATTERNS` constant | No longer needed |

---

## Configuration Reference

### config/security.yaml

```yaml
sandbox:
  enabled: true
  bwrap_path: "bwrap"
  unshare_pid: true
  unshare_ipc: true
  unshare_uts: true
  tmpfs_size: "100M"
  
  system_mounts:
    - source: "/usr"
      target: "/usr"
      mode: "ro"
    # ...
  
  environment:
    home: "/session/workspace"
    path: "/usr/bin:/bin"
    claude_config_dir: "/session"
    clear_env: true

network:
  mode: "whitelist"
  allowed_domains:
    - "api.anthropic.com"
    - "pypi.org"
  allow_localhost: true
```

### config/secrets.yaml

```yaml
anthropic_api_key: sk-ant-...  # Required
jwt_secret: <auto-generated>   # Optional, auto-created
```

---

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| bwrap not installed | Agent startup fails with error message |
| Missing mount source | Sandbox may fail at runtime, warning logged |
| Sandbox disabled in config | Agent runs unsandboxed (logs warning) |
| JWT secret missing | Auto-generated on startup |
| Database unavailable | Retry with exponential backoff |

---

## Audit Points

| Event | Log Level | Log Message |
|-------|-----------|-------------|
| Sandbox start | INFO | `SANDBOX: Starting agent in sandbox for session {id}` |
| Sandbox disabled | WARNING | `Sandbox is disabled - running agent without isolation` |
| bwrap not found | ERROR | `Bubblewrap not found at '{path}'` |
| Sandbox timeout | WARNING | `SANDBOX: Timeout after {n}s, killing process` |
| Permission check | INFO | `PERMISSION CHECK: {tool_name}` |
| Security violation | WARNING | `SECURITY: sandbox bypass attempted` |

---

## Known Limitations

1. **Network namespace in Docker**: `--unshare-net` may not work in all Docker configurations
2. **No user namespace**: bwrap runs as same user; root inside sandbox = root outside
3. **Skills directory shared**: Skills accessible to all sessions (by design)
4. **JWT single-secret**: No key rotation mechanism
5. **SQLite limitations**: Single-writer; may need PostgreSQL for high concurrency
