"""
Unified Task Runner for Agentum.

Provides a single entry point for agent task execution that is used by both
CLI (src/core/agent.py) and HTTP API (src/services/agent_runner.py).

This module extracts the common execution logic to ensure consistent behavior
across all entry points and reduces code duplication.

The task runner supports two execution modes:
1. **Sandboxed mode** (default): Agent runs inside bwrap sandbox with isolated
   filesystem and PID namespace. All subprocesses inherit restrictions.
2. **Direct mode**: Agent runs in the same process (for development/debugging).

Usage:
    from src.core.task_runner import execute_agent_task
    from src.core.schemas import TaskExecutionParams

    params = TaskExecutionParams(
        task="Your task description",
        working_dir=Path("/path/to/dir"),
        tracer=ExecutionTracer(),  # or BackendConsoleTracer()
    )
    result = await execute_agent_task(params)
"""
import json
import logging
from pathlib import Path
from typing import Optional

import yaml

from ..config import (
    AGENT_DIR,
    CONFIG_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    SKILLS_DIR,
    AgentConfigLoader,
)
from .agent_core import ClaudeAgent
from .permission_profiles import PermissionManager
from .sandbox_runner import (
    SandboxConfig,
    SandboxedAgentParams,
    SandboxedAgentRunner,
    create_sandbox_runner,
)
from .schemas import AgentConfig, AgentResult, TaskExecutionParams, TaskStatus
from .sessions import SessionManager
from .tracer import NullTracer, TracerBase

logger = logging.getLogger(__name__)


def load_security_config() -> Optional[SandboxConfig]:
    """
    Load sandbox configuration from config/security.yaml.
    
    Returns:
        SandboxConfig if file exists and is valid, None otherwise.
    """
    security_config_path = CONFIG_DIR / "security.yaml"
    
    if not security_config_path.exists():
        logger.warning(f"Security config not found at {security_config_path}")
        return None
    
    try:
        with open(security_config_path, "r") as f:
            config_data = yaml.safe_load(f)
        
        sandbox_data = config_data.get("sandbox", {})
        return SandboxConfig(**sandbox_data)
    except Exception as e:
        logger.error(f"Failed to load security config: {e}")
        return None


async def execute_agent_task(
    params: TaskExecutionParams,
    config_loader: Optional[AgentConfigLoader] = None,
    use_sandbox: Optional[bool] = None,
) -> AgentResult:
    """
    Execute an agent task with unified logic for CLI and HTTP.

    This is the single entry point for running agent tasks. Both CLI and HTTP
    entry points should call this function with their respective parameters.

    By default, the agent runs inside a bubblewrap sandbox for security.
    All subprocesses (Bash commands, Python scripts) inherit the sandbox
    restrictions automatically.

    Args:
        params: TaskExecutionParams with all execution parameters.
        config_loader: Optional pre-loaded AgentConfigLoader. If not provided,
                       a new one will be created.
        use_sandbox: Override sandbox behavior. If None, uses config setting.

    Returns:
        AgentResult with status, output, metrics, and session info.

    Raises:
        AgentError: If the agent encounters an error during execution.
        Exception: For unexpected errors.
    """
    # 1. Resolve working directory
    if params.working_dir:
        working_dir = params.working_dir.resolve()
    else:
        # Default: AGENT_DIR for API context, cwd for CLI
        # The caller should set working_dir explicitly for their context
        working_dir = AGENT_DIR

    logger.info(f"Working directory: {working_dir}")

    # 2. Load configuration from agent.yaml
    if config_loader is None:
        config_loader = AgentConfigLoader()
    config_data = config_loader.get_config()

    # 3. Apply parameter overrides (params take precedence over config)
    model = params.model or config_data["model"]
    max_turns = (
        params.max_turns if params.max_turns is not None
        else config_data["max_turns"]
    )
    timeout_seconds = (
        params.timeout_seconds if params.timeout_seconds is not None
        else config_data["timeout_seconds"]
    )
    # permission_mode is optional and managed via permissions.yaml
    permission_mode = params.permission_mode or config_data.get("permission_mode")
    role = params.role or config_data["role"]

    # Skills and checkpointing (with override support)
    enable_skills = (
        params.enable_skills if params.enable_skills is not None
        else config_data["enable_skills"]
    )
    enable_file_checkpointing = (
        params.enable_file_checkpointing if params.enable_file_checkpointing is not None
        else config_data["enable_file_checkpointing"]
    )
    max_buffer_size = (
        params.max_buffer_size if params.max_buffer_size is not None
        else config_data.get("max_buffer_size")
    )
    output_format = params.output_format or config_data.get("output_format", "yaml")
    include_partial_messages = (
        params.include_partial_messages if params.include_partial_messages is not None
        else config_data.get("include_partial_messages", False)
    )

    logger.info(f"Config: model={model}, max_turns={max_turns}, timeout={timeout_seconds}s")

    # 4. Load security configuration and determine if sandbox should be used
    security_config = load_security_config()
    
    # Determine sandbox usage
    sandbox_enabled = False
    if use_sandbox is not None:
        sandbox_enabled = use_sandbox
    elif security_config is not None:
        sandbox_enabled = security_config.enabled
    
    # 5. Execute agent (sandboxed or direct)
    # Note: Session management is handled by ClaudeAgent in direct mode
    # and by SandboxedAgentRunner in sandbox mode
    if sandbox_enabled and security_config is not None:
        # For sandbox mode, we need to create/load session
        session_manager = SessionManager(SESSIONS_DIR)
        
        if params.resume_session_id:
            session_info = session_manager.load_session(params.resume_session_id)
            session_id = session_info.session_id
        elif params.session_id:
            # Session already created by caller (agent_runner.py)
            session_id = params.session_id
            session_manager.create_session(
                working_dir=str(working_dir),
                session_id=session_id,
            )
        else:
            session_info = session_manager.create_session(
                working_dir=str(working_dir),
            )
            session_id = session_info.session_id
        
        session_dir = session_manager.get_session_dir(session_id)
        
        logger.info(f"SANDBOX: Executing agent in sandbox for session {session_id}")
        return await _execute_sandboxed(
            params=params,
            config_data=config_data,
            security_config=security_config,
            session_dir=session_dir,
            session_id=session_id,
            model=model,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            enable_skills=enable_skills,
            role=role,
            output_format=output_format,
        )
    else:
        # Direct execution - ClaudeAgent handles session management
        logger.info(f"DIRECT: Executing agent directly")
        return await _execute_direct(
            params=params,
            config_data=config_data,
            model=model,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            permission_mode=permission_mode,
            role=role,
            enable_skills=enable_skills,
            enable_file_checkpointing=enable_file_checkpointing,
            max_buffer_size=max_buffer_size,
            output_format=output_format,
            include_partial_messages=include_partial_messages,
            working_dir=working_dir,
        )


async def _execute_sandboxed(
    params: TaskExecutionParams,
    config_data: dict,
    security_config: SandboxConfig,
    session_dir: Path,
    session_id: str,
    model: str,
    max_turns: int,
    timeout_seconds: Optional[int],
    enable_skills: bool,
    role: str,
    output_format: str,
) -> AgentResult:
    """Execute agent inside bwrap sandbox."""
    from datetime import datetime
    
    # Create sandbox runner
    sandbox_runner = create_sandbox_runner(
        sessions_dir=SESSIONS_DIR,
        skills_dir=SKILLS_DIR,
        config=security_config,
    )
    
    # Build agent parameters
    agent_params = SandboxedAgentParams(
        session_id=session_id,
        task=params.task,
        model=model,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        enable_skills=enable_skills,
        role=role,
        output_format=output_format,
    )
    
    # Run agent in sandbox
    start_time = datetime.now()
    result = await sandbox_runner.run_agent(
        session_dir=session_dir,
        params=agent_params,
        timeout=timeout_seconds,
    )
    end_time = datetime.now()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)
    
    # Parse result
    if result.success and result.result_file and result.result_file.exists():
        try:
            result_data = json.loads(result.result_file.read_text())
            return AgentResult(
                status=TaskStatus(result_data.get("status", "COMPLETE")),
                output=result_data.get("output", ""),
                session_id=session_id,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"Failed to parse result file: {e}")
    
    # Handle failure
    if not result.success:
        return AgentResult(
            status=TaskStatus.ERROR,
            output=f"Sandbox execution failed: {result.error or result.stderr}",
            session_id=session_id,
            duration_ms=duration_ms,
        )
    
    # Fallback: try to parse stdout
    try:
        stdout_data = json.loads(result.stdout)
        return AgentResult(
            status=TaskStatus(stdout_data.get("status", "COMPLETE")),
            output=stdout_data.get("output", result.stdout),
            session_id=session_id,
            duration_ms=duration_ms,
        )
    except json.JSONDecodeError:
        return AgentResult(
            status=TaskStatus.COMPLETE if result.exit_code == 0 else TaskStatus.ERROR,
            output=result.stdout or result.stderr,
            session_id=session_id,
            duration_ms=duration_ms,
        )


async def _execute_direct(
    params: TaskExecutionParams,
    config_data: dict,
    model: str,
    max_turns: int,
    timeout_seconds: Optional[int],
    permission_mode: Optional[str],
    role: str,
    enable_skills: bool,
    enable_file_checkpointing: bool,
    max_buffer_size: Optional[int],
    output_format: str,
    include_partial_messages: bool,
    working_dir: Path,
) -> AgentResult:
    """Execute agent directly (no sandbox) - for development/debugging."""
    # Load permission manager from profile
    permission_manager = PermissionManager(profile_path=params.profile_path)

    if params.profile_path:
        logger.info(f"Using profile: {params.profile_path}")

    # Get allowed tools from permission profile
    profile = permission_manager.profile
    profile_tools = profile.tools
    allowed_tools = list(set(profile_tools.enabled) - set(profile_tools.disabled))

    # Get auto_checkpoint_tools from profile (with fallback)
    if profile.checkpointing:
        auto_checkpoint_tools = profile.checkpointing.auto_checkpoint_tools
    else:
        auto_checkpoint_tools = ["Write", "Edit"]  # Reasonable default

    logger.info(f"Allowed tools: {', '.join(allowed_tools)}")
    logger.info(f"Auto checkpoint tools: {', '.join(auto_checkpoint_tools)}")

    # Build AgentConfig
    agent_config = AgentConfig(
        model=model,
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        enable_skills=enable_skills,
        enable_file_checkpointing=enable_file_checkpointing,
        permission_mode=permission_mode,
        role=role,
        auto_checkpoint_tools=auto_checkpoint_tools,
        working_dir=str(working_dir),
        additional_dirs=params.additional_dirs,
        allowed_tools=allowed_tools,
        permissions_config=None,  # Not used with permission profiles
        max_buffer_size=max_buffer_size,
        output_format=output_format,
        include_partial_messages=include_partial_messages,
    )

    # Determine tracer
    tracer: TracerBase = params.tracer if params.tracer else NullTracer()

    logger.info(
        f"Starting agent: model={agent_config.model}, max_turns={agent_config.max_turns}"
    )
    if params.resume_session_id:
        logger.info(
            f"Resuming from session: {params.resume_session_id} "
            f"(fork={params.fork_session})"
        )

    # Create and run agent
    agent = ClaudeAgent(
        config=agent_config,
        sessions_dir=SESSIONS_DIR,
        logs_dir=LOGS_DIR,
        tracer=tracer,
        permission_manager=permission_manager,
    )

    # Execute the agent
    result = await agent.run(
        task=params.task,
        resume_session_id=params.resume_session_id,
        fork_session=params.fork_session,
        session_id=params.session_id,  # Use pre-generated session ID if provided
    )

    return result
