# Security Architecture

**Last verified:** 2026-01-09

---

## System Overview

Agentum implements a defense-in-depth security model combining multiple isolation layers: permission-based tool access control, bubblewrap (bwrap) filesystem sandboxing, session-scoped workspace isolation, and JWT-based API authentication. The architecture assumes an untrusted agent that may attempt to escape sandbox constraints.

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
│                              PERMISSION LAYER                                   │
│                                                                                 │
│   ┌───────────────────┐    ┌─────────────────┐    ┌───────────────────────┐    │
│   │  Permission       │    │  Allow/Deny     │    │  Tool Whitelist/      │    │
│   │  Manager          │───>│  Pattern        │───>│  Blacklist            │    │
│   │  (permissions.yaml)    │  Matching       │    │  Enforcement          │    │
│   └───────────────────┘    └─────────────────┘    └───────────┬───────────┘    │
│                                                                │                │
└────────────────────────────────────────────────────────────────┼────────────────┘
                                                                 │
                                                                 v
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              HOOK PIPELINE                                      │
│                                                                                 │
│   PreToolUse Hooks (executed in order):                                         │
│   ┌───────────────────┐    ┌───────────────────┐    ┌────────────────────┐     │
│   │  Absolute Path    │───>│  Permission       │───>│  Dangerous         │     │
│   │  Block Hook       │    │  Check Hook       │    │  Command Hook      │     │
│   └───────────────────┘    └───────────────────┘    └─────────┬──────────┘     │
│                                                                │                │
│   Note: Sandbox wrapping occurs inside Permission Check Hook   │                │
│         when Bash commands are allowed (not a separate hook)   │                │
│                                                                │                │
└────────────────────────────────────────────────────────────────┼────────────────┘
                                                                 │
                                                                 v
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          BUBBLEWRAP SANDBOX (bwrap)                             │
│                                                                                 │
│   ┌───────────────────────────────────────────────────────────────────────┐    │
│   │                      ISOLATED NAMESPACE                               │    │
│   │                                                                       │    │
│   │   /workspace (rw)  ←── Session-specific workspace mount               │    │
│   │   /skills (ro)     ←── Skills directory (read-only)                   │    │
│   │   /usr, /lib, /bin ←── System binaries (read-only)                    │    │
│   │   /tmp (tmpfs)     ←── Ephemeral scratch space (100MB)                │    │
│   │                                                                       │    │
│   │   PID namespace isolation (--unshare-pid)                             │    │
│   │   UTS namespace isolation (--unshare-uts)                             │    │
│   │   IPC namespace isolation (--unshare-ipc)                             │    │
│   │   Environment cleared (--clearenv)                                    │    │
│   │   Process dies with parent (--die-with-parent)                        │    │
│   │   New session (--new-session)                                         │    │
│   │                                                                       │    │
│   └───────────────────────────────────────────────────────────────────────┘    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Security Layers

### Layer 1: JWT Authentication

**Mechanism:** HS256 JWT tokens

```
AUTHENTICATION FLOW
===================

Client                    API                     AuthService
  │                        │                          │
  │── POST /auth/token ───>│                          │
  │                        │── get_or_create_user ───>│
  │                        │<── (user, token) ────────│
  │<── 200 + JWT ──────────│                          │
  │                        │                          │
  │── GET /sessions ───────>│                          │
  │   [+ Bearer token]     │── validate_token ───────>│
  │                        │<── user_id ──────────────│
  │                        │── check ownership ───────>│
  │<── 200 + data ─────────│                          │
```

**Token Structure:**
```python
payload = {
    "sub": user_id,       # UUID (36 chars)
    "exp": expiry,        # UTC timestamp
    "iat": issued_at,     # UTC timestamp
    "type": "access"      # Token type
}
```

| Parameter | Value | Notes |
|-----------|-------|-------|
| Algorithm | HS256 | HMAC-SHA256 |
| Expiry | 7 days | JWT_EXPIRY_HOURS = 168 |
| Secret | 256-bit | Auto-generated, persisted to `config/secrets.yaml` |

**Secret Management:**
- Auto-generated on first startup if not present
- Stored in `config/secrets.yaml` (gitignored)
- Loaded once per AuthService instance

---

### Layer 2: Session Ownership

**Enforcement Point:** API route handlers via `get_current_user_id` dependency

```python
# Session ownership check pattern (user_id passed to query filter)
session = await session_service.get_session(
    db=db,
    session_id=session_id,
    user_id=user_id,  # Filters query to only return user's sessions
)

if not session:
    raise HTTPException(404, "Session not found")  # Unauthorized = not found
```

**Database Model:**
```
┌──────────────┐       ┌──────────────┐
│    User      │ 1───* │   Session    │
├──────────────┤       ├──────────────┤
│ id (PK)      │       │ id (PK)      │
│ type         │       │ user_id (FK) │
│ created_at   │       │ status       │
└──────────────┘       │ task         │
                       │ working_dir  │
                       │ ...          │
                       └──────────────┘
```

---

### Layer 3: Permission Rules

**Configuration:** `config/permissions.yaml`

**Pattern Matching:** Glob-style patterns with tool-specific syntax

```yaml
session_workspace:
  description: Patterns for session-specific workspace permissions.
  allow:
    # Session workspace access
    - Read({workspace}/**)
    - Write({workspace}/**)
    - Edit({workspace}/**)
    - MultiEdit({workspace}/**)
    # Relative paths from cwd (which is set to workspace)
    - Read(./**)
    - Write(./**)
    - Edit(./**)
    - MultiEdit(./**)
    # Skills directory read access
    - Read(./skills/**)
    - Read(/skills/**)
    # Allow all Bash commands (relies on sandbox isolation)
    - Bash(*)
    - Glob(*)
    - Grep(*)
    - LS(*)
    
  deny:
    # Block writing to skills
    - Write(/skills/**)
    - Write(./skills/**)
    # Block absolute paths and parent traversal
    - Read(/**)
    - Write(/**)
    - Read(../**)
    - Write(../**)
    # Block dangerous commands
    - Bash(kill *)
    - Bash(systemctl *)
    - Bash(mount *)
    # ... (see full list in permissions.yaml)
```

**Permission Check Flow:**

```
Tool Call ───> Security Check ───> Pattern Match ───> Execute/Deny
                    │                    │
                    │                    └── Check Order:
                    │                        1. Allow rules (explicit permit)
                    │                        2. Deny rules (explicit block)
                    │                        3. Default: deny
                    │
                    └── Security violations bypass pattern matching
                        and trigger immediate interrupt
```

> **Note:** Allow rules are checked FIRST. This allows specific allow patterns
> (e.g., `Write({workspace}/**)`) to take precedence over generic deny patterns
> (e.g., `Write(/**)`), enabling fine-grained workspace access control.

**Security Violation Detection:**
```python
# Immediate interrupt on sandbox bypass attempts (checked before pattern matching)
if tool_input.get("dangerouslyDisableSandbox"):
    return PermissionResultDeny(
        behavior="deny",
        message="Security violation: sandbox bypass attempted",
        interrupt=True  # Stop agent execution immediately
    )
```

---

### Layer 4: Hook Pipeline

**PreToolUse Hooks (execution order):**

| Order | Hook | Purpose | Scope |
|-------|------|---------|-------|
| 1 | `absolute_path_block_hook` | Denies absolute paths or `..` traversal | File tools (Read, Write, Edit, etc.) |
| 2 | `permission_hook` | Checks allow/deny rules + wraps Bash in bwrap | All permission_checked tools |
| 3 | `dangerous_command_hook` | Blocks high-risk shell commands via regex | Bash only |

**Hook Pipeline Implementation:**
```python
# From permissions.py create_permission_hooks()
manager = HooksManager()

# 1. Block absolute paths FIRST (enforce relative paths policy)
manager.add_pre_tool_hook(create_absolute_path_block_hook())

# 2. Permission checking (includes sandbox wrapping for Bash)
manager.add_pre_tool_hook(permission_hook)

# 3. Block dangerous commands (regex patterns)
manager.add_pre_tool_hook(dangerous_hook, matcher="Bash")
```

> **Note:** Sandbox wrapping (bubblewrap) is integrated into the permission hook,
> not a separate hook. When a Bash command is allowed, the permission callback
> wraps it in bwrap before execution.

**Absolute Path Block:**
```python
if path.is_absolute() or ".." in path.parts:
    return HookResult(
        permission_decision="deny",
        permission_reason="Absolute paths and parent traversal are prohibited",
        interrupt=True
    )
```

**Dangerous Command Patterns (regex):**
```python
patterns = [
    r"\bkill\b", r"\bpkill\b", r"\bkillall\b",
    r"\bshutdown\b", r"\breboot\b", r"\bpoweroff\b",
    r"\bsystemctl\b", r"\bservice\b",
    r"\bmount\b", r"\bumount\b", r"\bchroot\b",
    r"\biptables\b", r"\bnft\b",
    r"\bss\b", r"\bnetstat\b", r"\blsof\b",
    r"\bps\b", r"\btop\b", r"\bhtop\b",
    r"/proc/", r"/sys/",
    # ... additional patterns
]
```

---

### Layer 5: Bubblewrap Sandbox

**Technology:** [bubblewrap](https://github.com/containers/bubblewrap) - unprivileged sandboxing tool

**Configuration Schema:**
```yaml
sandbox:
  enabled: true
  file_sandboxing: true
  network_sandboxing: true
  bwrap_path: "bwrap"
  use_tmpfs_root: true
```

**Mount Configuration:**

| Mount Type | Source | Target | Mode | Purpose |
|------------|--------|--------|------|---------|
| Static | `/usr` | `/usr` | ro | System binaries |
| Static | `/lib` | `/lib` | ro | System libraries |
| Static | `/bin` | `/bin` | ro | Core utilities |
| Static | `{agent_dir}/skills` | `/skills` | ro | Skills library |
| Session | `{workspace_dir}` | `/workspace` | rw | Agent output |

**Namespace Isolation:**
```bash
bwrap \
  --unshare-pid \      # Process isolation
  --unshare-uts \      # Hostname isolation
  --unshare-ipc \      # IPC isolation
  --die-with-parent \  # Cleanup on parent exit
  --new-session \      # TTY isolation
  --clearenv \         # Clean environment
  --setenv HOME /workspace \
  --setenv PATH /usr/bin:/bin \
  --chdir /workspace \
  -- bash -lc "command"
```

**Docker Compatibility:**
- Uses `--unshare-pid`, `--unshare-uts`, `--unshare-ipc` instead of `--unshare-all`
- Avoids `pivot_root` operations that fail in nested containers
- Creates isolated filesystem view via explicit bind mounts

---

## File System Structure

```
Project/
├── sessions/
│   └── {timestamp}_{uuid}/           # Session directory
│       ├── session_info.json         # Session metadata
│       ├── agent.jsonl               # Agent execution log
│       ├── workspace/                # SANDBOXED OUTPUT AREA
│       │   ├── output.yaml           # Task results
│       │   ├── skills -> ../../../skills  # Symlink to global skills (ro)
│       │   └── ...                   # Agent-created files
│       ├── debug/                    # Debug information
│       ├── projects/                 # Claude SDK internal
│       ├── shell-snapshots/          # Shell state
│       ├── statsig/                  # Feature flags
│       └── todos/                    # Task management
├── skills/                           # Global skills library (ro)
├── config/
│   ├── permissions.yaml              # Permission rules
│   ├── secrets.yaml                  # API keys, JWT secret (gitignored)
│   └── agent.yaml                    # Agent configuration
├── data/
│   └── agentum.db                    # SQLite database
└── logs/
    └── backend.log                   # Application logs
```

**Session ID Format:** `YYYYMMDD_HHMMSS_{uuid8}` (e.g., `20260108_233948_528d507e`)

**Workspace Isolation Principle:**
- Agent CWD is set to `workspace/` subdirectory
- Agent cannot read session logs, metadata, or parent directories
- Skills accessible via symlink `./skills -> ../../../skills` (relative path)
- In sandbox: skills mounted read-only at `/skills` via bwrap

---

## Claude Code SDK Native Security

**Features leveraged from Claude Agent SDK:**

| Feature | How Used |
|---------|----------|
| `allowed_tools` | Pre-approved tools that skip permission callback |
| `disallowed_tools` | Completely blocked tools (agent cannot request) |
| `can_use_tool` callback | Permission check for `permission_checked` tools |
| `cwd` option | Workspace directory as working directory |
| `CLAUDE_CONFIG_DIR` | Session-specific config isolation |

**SDK Permission Modes:**
```python
class PermissionMode(StrEnum):
    DEFAULT = "default"         # Standard prompts
    ACCEPT_EDITS = "acceptEdits" # Auto-accept file edits
    PLAN = "plan"               # Read-only analysis
    BYPASS = "bypassPermissions" # Skip all (dangerous)
```

---

## Blocked Operations

### Bash Commands

Dangerous commands are blocked via **two complementary mechanisms**:

**1. Permission Rules (`permissions.yaml`):**
Pattern-based blocking in the deny list:
```yaml
deny:
  - Bash(kill *)
  - Bash(killall *)
  - Bash(systemctl *)
  - Bash(mount *)
  # ... etc
```

**2. Regex Hook (`dangerous_command_hook`):**
Runtime regex matching for commands that bypass simple pattern matching:

| Command | Regex Pattern | Reason |
|---------|---------------|--------|
| `kill`, `killall`, `pkill` | `\bkill\b`, `\bkillall\b`, `\bpkill\b` | Process manipulation |
| `shutdown`, `reboot`, `poweroff`, `halt` | `\bshutdown\b`, etc. | System control |
| `systemctl`, `service` | `\bsystemctl\b`, `\bservice\b` | Service management |
| `mount`, `umount`, `chroot` | `\bmount\b`, `\bumount\b`, `\bchroot\b` | Filesystem manipulation |
| `iptables`, `ufw`, `nft` | `\biptables\b`, `\bufw\b`, `\bnft\b` | Firewall modification |
| `netstat`, `ss`, `lsof` | `\bnetstat\b`, `\bss\b`, `\blsof\b` | Network introspection |
| `ps`, `top`, `htop` | `\bps\b`, `\btop\b`, `\bhtop\b` | Process introspection |
| `/proc/`, `/sys/` | `/proc/`, `/sys/` | System filesystem access |

> **Defense in Depth:** Both mechanisms work together. Permission rules provide
> first-line pattern matching; regex hooks catch edge cases and command variations.

### Path Access (Hook Enforcement)

| Pattern | Block | Mechanism |
|---------|-------|-----------|
| Absolute paths (`/etc/*`, `/home/*`) | Denied | `absolute_path_block_hook` |
| Parent traversal (`../`) | Always denied | `absolute_path_block_hook` |
| Skills write (`./skills/*`, `/skills/*`) | Denied (read-only) | Permission rules |
| System paths outside mounts | Denied | bwrap sandbox (not mounted) |

---

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────────┐
│                      TRUSTED ZONE                               │
│                                                                 │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │  API Server     │    │  Permission     │                   │
│   │  (FastAPI)      │    │  Manager        │                   │
│   └─────────────────┘    └─────────────────┘                   │
│                                                                 │
│   ┌─────────────────┐    ┌─────────────────┐                   │
│   │  Hook Pipeline  │    │  Session        │                   │
│   │                 │    │  Manager        │                   │
│   └─────────────────┘    └─────────────────┘                   │
│                                                                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ Trust Boundary
                             │
┌────────────────────────────┴────────────────────────────────────┐
│                     UNTRUSTED ZONE                              │
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐  │
│   │                   BUBBLEWRAP SANDBOX                    │  │
│   │                                                         │  │
│   │   ┌─────────────────────────────────────────────────┐   │  │
│   │   │              Claude Agent SDK                   │   │  │
│   │   │                                                 │   │  │
│   │   │   Tool execution, file operations, bash         │   │  │
│   │   │   commands all run inside sandbox               │   │  │
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
| Path traversal | Hook blocks `..` and absolute paths |
| Sandbox escape via `dangerouslyDisableSandbox` | Immediate deny + interrupt |
| File system access outside workspace | bwrap mount restrictions |
| Process escape | PID namespace isolation |
| Environment variable leakage | `--clearenv` flag |
| Network reconnaissance | Introspection commands blocked |
| Skill tampering | Read-only skill mounts |

---

## Configuration Reference

### secrets.yaml
```yaml
anthropic_api_key: sk-ant-...  # Required
jwt_secret: <auto-generated>   # Optional, auto-created
```

### permissions.yaml (security-relevant sections)
```yaml
name: user
defaultMode: default

tools:
  enabled:
    - Read
    - Write
    - Edit
    - MultiEdit
    - Bash
    - Glob
    - Grep
    - LS
    # ... other tools
  disabled: []
  permission_checked:  # Tools requiring permission callback
    - Read
    - Write
    - Edit
    - MultiEdit
    - Bash

session_workspace:
  allow:
    - Read({workspace}/**)
    - Write({workspace}/**)
    - Read(./**)
    - Write(./**)
    - Bash(*)
    # ...
  deny:
    - Write(/skills/**)
    - Read(/**)
    - Write(/**)
    - Bash(kill *)
    # ...

sandbox:
  enabled: true
  file_sandboxing: true
  network_sandboxing: true
  bwrap_path: "bwrap"
  static_mounts:
    skills:
      source: "{agent_dir}/skills"
      target: "/skills"
      mode: ro
    # ... system mounts
  session_mounts:
    workspace:
      source: "{workspace_dir}"
      target: "/workspace"
      mode: rw
  environment:
    home: "/workspace"
    path: "/usr/bin:/bin"
    clear_env: true
```

---

## Failure Modes

| Scenario | Behavior |
|----------|----------|
| bwrap not installed | Sandbox disabled, logs warning |
| Missing mount source | Sandbox may fail at runtime, warning logged |
| JWT secret missing | Auto-generated on startup |
| Permission profile missing | `ProfileNotFoundError` raised |
| Database unavailable | Retry with exponential backoff |

---

## Audit Points

| Event | Log Level | Log Message |
|-------|-----------|-------------|
| Permission check | INFO | `PERMISSION CHECK: {tool_name} with input: {tool_input}` |
| Permission denial | INFO | `PERMISSION DENIAL: {tool_name} denied (count={n}/{max})` |
| Security violation | WARNING | `SECURITY: Model attempted to use dangerouslyDisableSandbox!` |
| Sandbox wrap | INFO | `SANDBOX: Wrapping Bash command in bwrap: {command}...` |
| Profile activation | INFO | `PROFILE: Activated '{name}' (allow={n}, deny={n})` |
| Session creation | INFO | `Created session: {session_id} for user: {user_id}` |
| Dangerous command blocked | WARNING | `Blocked dangerous command: {command}...` |
| Absolute path blocked | WARNING | `Blocked absolute or parent-traversal path: {path}` |

---

## Known Limitations

1. **Network sandboxing incomplete in Docker:** Network namespace isolation (`--unshare-net`) disabled in nested containers
2. **No user namespace:** bwrap runs as same user; root inside sandbox = root outside
3. **Skills directory world-readable:** Skills accessible to any session (by design for sharing)
4. **JWT single-secret:** No key rotation mechanism; requires manual secret update and restart
5. **SQLite limitations:** Single-writer; may need PostgreSQL for high concurrency
6. **Path normalization hook not active:** `create_path_normalization_hook()` exists in `hooks.py` but is not registered in the hook pipeline; absolute paths in workspace are blocked rather than normalized

---

## Critical Security Issue: permission_mode Configuration

**⚠️ CRITICAL:** The `permission_mode` setting in `config/agent.yaml` **MUST NOT BE SET** for security protections to work.

### The Issue

When `permission_mode` is set to any value (including `"default"`), the Claude Agent SDK uses `--permission-prompt-tool stdio`, which is an MCP-based permission system that **completely bypasses**:
- The `can_use_tool` callback
- All permission rules in `permissions.yaml`
- All pre-tool-use hooks (dangerous command patterns, absolute path blocks, etc.)
- Custom permission callbacks

This allows dangerous commands like `ps`, `kill`, `systemctl`, etc. to execute without any checks.

### The Fix

**In `config/agent.yaml`:**
```yaml
# WRONG - This bypasses all security checks:
permission_mode: default

# CORRECT - Remove the field entirely:
# (Field removed - managed via permissions.yaml)
```

**Note:** Permission mode is configured in `permissions.yaml` via the `defaultMode` field, not in `agent.yaml`.

**Code Enforcement:**
- `src/config.py` no longer requires `permission_mode` in agent.yaml
- `src/core/agent_core.py` validates that if present, `permission_mode` must be `None`, and will raise an `AgentError` if set to `"default"`, `"acceptEdits"`, etc.

### Technical Details

- **Source:** [agent_core.py:490-493](../src/core/agent_core.py)
- **SDK Behavior:** When `permission_mode` is set, the SDK invokes the agent with `--permission-prompt-tool stdio`
- **Bypass Mechanism:** The MCP permission system takes precedence over `can_use_tool` callback
- **Security Impact:** All layer 3 (Permission Rules) and layer 4 (Hook Pipeline) protections are bypassed