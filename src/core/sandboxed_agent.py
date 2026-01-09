"""
Sandboxed agent entry point.

This module is executed inside the bubblewrap sandbox. It receives
execution parameters via environment variable and runs the Claude
Code agent with the session's workspace as the working directory.

The sandbox provides:
- Isolated PID namespace (ps only shows sandbox processes)
- Restricted filesystem (only /session, /skills, system binaries)
- Automatic subprocess inheritance (all children are sandboxed)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

# Configure logging early
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)

logger = logging.getLogger(__name__)


def get_agent_params() -> dict[str, Any]:
    """
    Get agent parameters from environment.
    
    Parameters are passed via SANDBOXED_AGENT_PARAMS environment variable
    as a JSON string.
    
    Returns:
        Parsed parameters dictionary.
        
    Raises:
        RuntimeError: If parameters are missing or invalid.
    """
    params_json = os.environ.get("SANDBOXED_AGENT_PARAMS")
    if not params_json:
        raise RuntimeError(
            "SANDBOXED_AGENT_PARAMS environment variable not set. "
            "This module must be run inside the sandbox."
        )
    
    try:
        return json.loads(params_json)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid SANDBOXED_AGENT_PARAMS JSON: {e}")


async def run_agent_internal(
    params: dict[str, Any],
    session_dir: Path,
) -> dict[str, Any]:
    """
    Run the Claude Code agent with the given parameters.
    
    This is the core execution function that runs inside the sandbox.
    
    Args:
        params: Agent execution parameters.
        session_dir: Session directory path (inside sandbox: /session).
        
    Returns:
        Agent result dictionary.
    """
    # Import agent modules (available via /src mount)
    from claude_code_sdk import ClaudeCodeAgent, ClaudeAgentOptions
    
    from .permission_profiles import PermissionManager, load_permission_profile
    from .permissions import create_permission_callback
    from .schemas import AgentResult, TaskStatus
    from .sessions import SessionManager
    
    session_id = params["session_id"]
    task = params["task"]
    model = params.get("model", "claude-sonnet-4-20250514")
    max_turns = params.get("max_turns", 50)
    system_prompt = params.get("system_prompt")
    resume_id = params.get("resume_id")
    fork_session = params.get("fork_session", False)
    timeout_seconds = params.get("timeout_seconds")
    enable_skills = params.get("enable_skills", True)
    role = params.get("role", "default")
    
    logger.info(f"SANDBOXED AGENT: Starting session {session_id}")
    logger.info(f"SANDBOXED AGENT: Task: {task[:100]}...")
    logger.info(f"SANDBOXED AGENT: Model: {model}, Max turns: {max_turns}")
    
    # Workspace is the working directory
    workspace_dir = session_dir / "workspace"
    
    # Load permission profile
    # Note: In sandbox, config is not accessible, use simplified permissions
    permission_manager = _create_sandbox_permission_manager(workspace_dir)
    
    # Create permission callback
    can_use_tool = create_permission_callback(
        permission_manager=permission_manager,
    )
    
    # Build tools list
    tools = [
        "Read", "Write", "Edit", "MultiEdit",
        "Bash", "Glob", "Grep", "LS",
        "Task", "TodoRead", "TodoWrite",
    ]
    
    if enable_skills:
        tools.append("Skill")
    
    # Build agent options
    options = ClaudeAgentOptions(
        system_prompt=system_prompt or _build_default_system_prompt(role),
        model=model,
        max_turns=max_turns,
        permission_mode=None,  # Use can_use_tool callback
        tools=tools,
        cwd=str(workspace_dir),
        can_use_tool=can_use_tool,
        env={"CLAUDE_CONFIG_DIR": str(session_dir)},
        resume=resume_id,
        fork_session=fork_session,
    )
    
    # Run the agent
    try:
        agent = ClaudeCodeAgent()
        
        # Execute with timeout if specified
        if timeout_seconds:
            result = await asyncio.wait_for(
                agent.run(task, options),
                timeout=timeout_seconds,
            )
        else:
            result = await agent.run(task, options)
        
        # Write result to file for parent process
        result_data = {
            "status": "COMPLETE",
            "session_id": session_id,
            "output": _extract_output(result),
        }
        
        result_file = workspace_dir / "agent_result.json"
        result_file.write_text(json.dumps(result_data, indent=2))
        
        logger.info(f"SANDBOXED AGENT: Completed successfully")
        return result_data
        
    except asyncio.TimeoutError:
        logger.error(f"SANDBOXED AGENT: Timeout after {timeout_seconds}s")
        return {"status": "ERROR", "error": f"Timeout after {timeout_seconds}s"}
    except Exception as e:
        logger.error(f"SANDBOXED AGENT: Error: {e}")
        return {"status": "ERROR", "error": str(e)}


def _create_sandbox_permission_manager(workspace_dir: Path) -> Any:
    """
    Create a minimal permission manager for sandboxed execution.
    
    Since the sandbox already restricts filesystem access, the permission
    manager just needs to allow operations within the workspace.
    """
    from .permission_profiles import (
        PermissionManager,
        ToolPermissions,
        WorkspacePermissions,
    )
    
    # Allow all tools (sandbox handles actual restrictions)
    tools = ToolPermissions(
        enabled=[
            "Read", "Write", "Edit", "MultiEdit",
            "Bash", "Glob", "Grep", "LS",
            "Task", "Skill", "TodoRead", "TodoWrite",
        ],
        disabled=[],
        permission_checked=["Read", "Write", "Edit", "MultiEdit", "Bash"],
    )
    
    # Allow workspace access (sandbox enforces the actual limits)
    workspace = WorkspacePermissions(
        allow=[
            "Read(./**)",
            "Write(./**)",
            "Edit(./**)",
            "MultiEdit(./**)",
            "Bash(*)",
            "Glob(*)",
            "Grep(*)",
            "LS(*)",
        ],
        deny=[],
    )
    
    return PermissionManager(
        name="sandbox",
        tools=tools,
        workspace=workspace,
    )


def _build_default_system_prompt(role: str) -> str:
    """Build a default system prompt for the agent."""
    return f"""You are an AI assistant running in a secure sandbox environment.

Your working directory is /session/workspace. You can read and write files here.
Skills are available at /skills (read-only).

You have access to standard tools: Read, Write, Edit, Bash, etc.

Complete the user's task efficiently and provide clear output.
"""


def _extract_output(result: Any) -> str:
    """Extract output from agent result."""
    if hasattr(result, "output"):
        return str(result.output)
    if hasattr(result, "text"):
        return str(result.text)
    return str(result)


def main() -> int:
    """
    Main entry point for sandboxed agent.
    
    Called when running: python -m src.core.sandboxed_agent
    
    Returns:
        Exit code (0 for success, non-zero for errors).
    """
    try:
        # Get parameters from environment
        params = get_agent_params()
        
        # Session directory is /session inside sandbox
        session_dir = Path("/session")
        
        if not session_dir.exists():
            logger.error("Session directory /session does not exist")
            return 1
        
        # Run the agent
        result = asyncio.run(run_agent_internal(params, session_dir))
        
        # Print result to stdout for parent process
        print(json.dumps(result))
        
        return 0 if result.get("status") == "COMPLETE" else 1
        
    except Exception as e:
        logger.error(f"SANDBOXED AGENT FATAL: {e}")
        print(json.dumps({"status": "ERROR", "error": str(e)}))
        return 1


def run() -> None:
    """Alternative entry point for direct invocation."""
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
