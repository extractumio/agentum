"""
Core agent implementation for Agentum.

This module contains the main agent execution logic using the Claude Agent SDK.
"""
import asyncio
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional, Union

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
)
from jinja2 import Environment, FileSystemLoader

# Import paths from central config
from config import (
    AGENT_DIR,
    PROMPTS_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    SKILLS_DIR,
)
from exceptions import (
    AgentError,
    MaxTurnsExceededError,
    ServerError,
    SessionIncompleteError,
)
from schemas import (
    AgentConfig,
    AgentResult,
    LLMMetrics,
    OutputSchema,
    SessionInfo,
    TaskStatus,
    TokenUsage,
)
from sessions import SessionManager
from skills import SkillManager
from tracer import ExecutionTracer, TracerBase, NullTracer
from trace_processor import TraceProcessor
from permissions import create_permission_callback, PermissionDenialTracker
from permission_profiles import ProfiledPermissionManager, ProfileType

logger = logging.getLogger(__name__)

# Jinja environment for templates
_jinja_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _filter_startswith(items: list[str], prefix: str) -> list[str]:
    """Jinja2 filter to select strings starting with a prefix."""
    return [item for item in items if item.startswith(prefix)]


def _filter_contains(items: list[str], value: str) -> bool:
    """Jinja2 filter to check if a list contains a value."""
    return value in items


# Register custom filters
_jinja_env.filters["select_startswith"] = _filter_startswith
_jinja_env.filters["contains"] = _filter_contains


class ClaudeAgent:
    """
    Agentum - Self-Improving Agent.
    
    Executes tasks using the Claude Agent SDK with configurable
    tools, prompts, and execution limits.
    """
    
    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        sessions_dir: Optional[Path] = None,
        logs_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        tracer: Optional[Union[TracerBase, bool]] = True,
        profiled_permission_manager: Optional[ProfiledPermissionManager] = None
    ) -> None:
        """
        Initialize the Claude Agent.
        
        Args:
            config: Agent configuration. Uses defaults if not provided.
            sessions_dir: Directory for sessions. Defaults to AGENT/sessions.
            logs_dir: Directory for logs. Defaults to AGENT/logs.
            skills_dir: Directory for skills. Defaults to AGENT/skills.
            tracer: Execution tracer for console output.
                - True (default): Use ExecutionTracer with default settings.
                - False/None: Disable tracing (NullTracer).
                - TracerBase instance: Use custom tracer.
            profiled_permission_manager: ProfiledPermissionManager for
                system/user profile-based permission checking.
                Required - agent will fail without permission profiles.
        """
        self._config = config or AgentConfig()
        self._sessions_dir = sessions_dir or SESSIONS_DIR
        self._logs_dir = logs_dir or LOGS_DIR
        self._profiled_permission_manager = profiled_permission_manager
        
        # Determine skills directory from parameter, config, or default
        if skills_dir:
            self._skills_dir = skills_dir
        elif self._config.skills_dir:
            self._skills_dir = Path(self._config.skills_dir)
        else:
            self._skills_dir = SKILLS_DIR
        
        self._session_manager = SessionManager(self._sessions_dir)
        self._skill_manager = SkillManager(self._skills_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup tracer
        if tracer is True:
            self._tracer: TracerBase = ExecutionTracer(verbose=True)
        elif tracer is False or tracer is None:
            self._tracer = NullTracer()
        else:
            self._tracer = tracer

        # Track permission denials for proper output generation on interruption
        self._denial_tracker = PermissionDenialTracker()

        # Wire tracer to profiled permission manager for profile switch notifications
        if self._profiled_permission_manager is not None:
            self._profiled_permission_manager.set_tracer(self._tracer)
            # Notify tracer about the current active profile (SYSTEM at init time)
            self._profiled_permission_manager._notify_profile_switch()
    
    @property
    def config(self) -> AgentConfig:
        """Get the agent configuration."""
        return self._config
    
    @property
    def skill_manager(self) -> SkillManager:
        """Get the skill manager."""
        return self._skill_manager
    
    @property
    def tracer(self) -> TracerBase:
        """Get the execution tracer."""
        return self._tracer

    def _copy_skills_to_workspace(self, session_id: str) -> None:
        """
        Copy all available skills to the session's workspace.
        
        This enables skills to read/write files within their own folder
        with full isolation between concurrent sessions.
        
        Args:
            session_id: The session ID for workspace access.
        """
        if not self._config.enable_skills:
            return
        
        skill_names = self._skill_manager.list_skills()
        if not skill_names:
            logger.debug("No skills to copy to workspace")
            return
        
        for skill_name in skill_names:
            try:
                source_dir = self._skill_manager.get_skill_source_dir(skill_name)
                self._session_manager.copy_skill_to_workspace(
                    session_id, skill_name, source_dir
                )
            except Exception as e:
                logger.warning(f"Failed to copy skill '{skill_name}' to workspace: {e}")

    def _cleanup_session(self, session_id: str) -> None:
        """
        Clean up session resources after agent run completes.
        
        Removes copied skills from workspace to save disk space.
        The output.yaml and session metadata are preserved.
        
        Args:
            session_id: The session ID to clean up.
        """
        # Remove skills folder from workspace
        self._session_manager.cleanup_workspace_skills(session_id)
        
        # Clear session context from permission manager
        if self._profiled_permission_manager is not None:
            self._profiled_permission_manager.clear_session_context()
            self._profiled_permission_manager.activate_system_profile()

    def _write_error_output(
        self,
        session_id: str,
        error_msg: str
    ) -> None:
        """
        Write output.yaml with error information when agent is interrupted.

        Ensures proper output format is maintained even when the agent
        is forcibly stopped due to permission denials or other errors.

        Args:
            session_id: The session ID.
            error_msg: The error message to include.
        """
        import yaml as yaml_lib
        output_file = self._session_manager.get_output_file(session_id)

        # Check if output.yaml already exists (agent may have written it)
        if output_file.exists():
            try:
                existing = yaml_lib.safe_load(output_file.read_text())
                if existing is None:
                    existing = {}
                # If agent already wrote a valid output, don't overwrite
                if existing.get("status") in ("COMPLETE", "SUCCESS", "PARTIAL"):
                    return
            except (yaml_lib.YAMLError, IOError):
                pass  # File is invalid/unreadable, we'll overwrite it

        # Generate output from denial tracker if available
        if self._denial_tracker.was_interrupted:
            denial_output = self._denial_tracker.get_error_output()
            output = OutputSchema(
                session_id=session_id,
                status="FAILED",
                error=denial_output.get("error", ""),
                comments=denial_output.get("comments", ""),
                output=denial_output.get("output", ""),
            )
        else:
            output = OutputSchema(
                session_id=session_id,
                status="FAILED",
                error=error_msg,
                comments="",
                output="Agent execution was interrupted before completion.",
            )

        # Ensure output directory exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Write the output file
        try:
            output_file.write_text(output.to_yaml())
            logger.info(f"Wrote error output to {output_file}")
        except IOError as e:
            logger.error(f"Failed to write error output: {e}")
    
    def _build_options(
        self,
        session_info: SessionInfo,
        system_prompt: str,
        trace_processor: Optional[Any] = None
    ) -> ClaudeAgentOptions:
        """
        Build ClaudeAgentOptions for the SDK.
        
        Args:
            session_info: Session information.
            system_prompt: System prompt (required, must not be empty).
            trace_processor: Optional trace processor for permission denial tracking.
        
        Returns:
            ClaudeAgentOptions configured for execution.
        
        Raises:
            AgentError: If required parameters are missing or invalid.
        """
        # Validate required inputs - fail fast
        if not system_prompt or not system_prompt.strip():
            raise AgentError(
                "system_prompt is required and must not be empty. "
                "Load prompts from AGENT/prompts/ before calling _build_options."
            )
        all_tools = list(self._config.allowed_tools)
        if self._config.enable_skills and "Skill" not in all_tools:
            all_tools.append("Skill")
        
        # Permission management: profiled permission manager is required
        if self._profiled_permission_manager is None:
            raise AgentError(
                "ProfiledPermissionManager is required. "
                "Agent cannot run without permission profiles."
            )
        
        # Activate user profile for task execution
        self._profiled_permission_manager.activate_user_profile()
        
        # Get tool configuration from active profile
        permission_checked_tools = self._profiled_permission_manager.get_permission_checked_tools()
        sandbox_disabled_tools = self._profiled_permission_manager.get_disabled_tools()
        
        # Pre-approved tools (no permission check needed)
        allowed_tools = [
            t for t in all_tools 
            if t not in permission_checked_tools and t not in sandbox_disabled_tools
        ]
        
        # Available tools (excluding completely disabled ones)
        available_tools = [
            t for t in all_tools
            if t not in sandbox_disabled_tools
        ]
        
        # Disabled tools list for SDK
        disallowed_tools = list(sandbox_disabled_tools)
        
        active_profile = self._profiled_permission_manager.active_profile
        logger.info(
            f"SANDBOX: Using profile '{active_profile.name}' for task execution"
        )
        logger.info(f"SANDBOX: permission_checked_tools={permission_checked_tools}")
        logger.info(f"SANDBOX: available_tools={available_tools}")
        logger.info(f"SANDBOX: allowed_tools (pre-approved)={allowed_tools}")
        logger.info(f"SANDBOX: disallowed_tools (blocked)={disallowed_tools}")
        
        # Create permission callback using the profiled manager
        # Pass tracer's on_permission_check for tracing (if available)
        # Pass denial tracker to record denials for output generation
        # Pass trace_processor so permission denial shows FAILED status
        on_permission_check = (
            self._tracer.on_permission_check
            if hasattr(self._tracer, 'on_permission_check')
            else None
        )
        # Clear any previous denials before starting new run
        self._denial_tracker.clear()
        can_use_tool = create_permission_callback(
            permission_manager=self._profiled_permission_manager,
            on_permission_check=on_permission_check,
            denial_tracker=self._denial_tracker,
            trace_processor=trace_processor,
        )
        
        all_tools = available_tools
        
        # Build list of accessible directories from the active profile
        working_dir = Path(self._config.working_dir) if self._config.working_dir else AGENT_DIR
        profile_dirs = self._profiled_permission_manager.get_allowed_dirs()
        add_dirs = []
        for dir_path in profile_dirs:
            # Resolve relative paths (e.g., "./input") to absolute paths
            if dir_path.startswith("./"):
                add_dirs.append(str(working_dir / dir_path[2:]))
            elif dir_path.startswith("/"):
                add_dirs.append(dir_path)
            else:
                add_dirs.append(str(working_dir / dir_path))
        logger.info(f"SANDBOX: Profile allowed_dirs={add_dirs}")
        
        session_dir = self._session_manager.get_session_dir(
            session_info.session_id
        )
        
        # Use workspace subdirectory as cwd to prevent reading session logs
        # The workspace only contains files the agent should access (output.yaml)
        workspace_dir = self._session_manager.get_workspace_dir(
            session_info.session_id
        )
        
        logger.info(
            f"SANDBOX: Final ClaudeAgentOptions - "
            f"tools={all_tools}, allowed_tools={allowed_tools}, "
            f"disallowed_tools={disallowed_tools}, "
            f"can_use_tool={'SET' if can_use_tool else 'NONE'}, "
            f"cwd={workspace_dir}"
        )
        
        return ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=self._config.model,
            max_turns=self._config.max_turns,
            tools=all_tools,  # Available tools (excluding disabled)
            allowed_tools=allowed_tools,  # Pre-approved (no permission check)
            disallowed_tools=disallowed_tools,  # Completely blocked tools
            cwd=str(workspace_dir),  # Sandboxed workspace, not session dir
            add_dirs=add_dirs,
            setting_sources=["project"] if self._config.enable_skills else [],
            can_use_tool=can_use_tool,
        )
    
    def _build_user_prompt(
        self,
        task: str,
        session_info: SessionInfo,
        parameters: Optional[dict] = None
    ) -> str:
        """
        Build the user prompt from template.
        
        Args:
            task: The task description.
            session_info: Session information.
            parameters: Additional template parameters.
        
        Returns:
            Rendered user prompt.
        
        Raises:
            AgentError: If user prompt template is missing or invalid.
        """
        # Validate task is provided
        if not task or not task.strip():
            raise AgentError("Task is required and must not be empty")
        
        # Validate user prompt template exists
        user_template_path = PROMPTS_DIR / "user.j2"
        if not user_template_path.exists():
            raise AgentError(
                f"User prompt template not found: {user_template_path}\n"
                f"Create the template file in AGENT/prompts/user.j2"
            )
        
        params = parameters or {}
        workspace_dir = self._session_manager.get_workspace_dir(
            session_info.session_id
        )
        output_file = self._session_manager.get_output_file(
            session_info.session_id
        )
        
        try:
            user_prompt = _jinja_env.get_template("user.j2").render(
                task=task,
                working_dir=self._config.working_dir or str(workspace_dir),
                output_file=str(output_file),
                **params,
            )
        except Exception as e:
            raise AgentError(f"Failed to render user prompt template: {e}")
        
        if not user_prompt or not user_prompt.strip():
            raise AgentError("User prompt is empty after rendering")
        
        return user_prompt
    
    def _validate_response(self, response: Optional[ResultMessage]) -> None:
        """
        Validate the agent response.
        
        Args:
            response: The ResultMessage from the SDK.
        
        Raises:
            SessionIncompleteError: If session did not complete.
            ServerError: If an API error occurred.
            MaxTurnsExceededError: If max turns was exceeded.
        """
        if response is None:
            raise SessionIncompleteError("Session did not complete")
        if response.is_error:
            raise ServerError(f"API error: {response.subtype}")
        if response.subtype == "error_max_turns":
            raise MaxTurnsExceededError(
                f"Exceeded {self._config.max_turns} turns"
            )
    
    async def run(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        parameters: Optional[dict] = None,
        resume_session_id: Optional[str] = None
    ) -> AgentResult:
        """
        Execute the agent with a task.
        
        Args:
            task: The task description.
            system_prompt: Custom system prompt. If None, loads from prompts/system.j2.
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional).
        
        Returns:
            AgentResult with execution outcome.
        
        Raises:
            AgentError: If prompts cannot be loaded or are invalid.
        """
        # Create or resume session first (needed for session-specific permissions)
        if resume_session_id:
            try:
                session_info = self._session_manager.load_session(
                    resume_session_id
                )
                logger.info(f"Resuming session: {resume_session_id}")
            except Exception as e:
                logger.warning(f"Could not resume session: {e}. Creating new.")
                session_info = self._session_manager.create_session(
                    working_dir=self._config.working_dir or str(AGENT_DIR)
                )
        else:
            session_info = self._session_manager.create_session(
                working_dir=self._config.working_dir or str(AGENT_DIR)
            )
        
        # Set session context for session-specific permissions
        # This sandboxes the agent to only its own workspace folder
        if self._profiled_permission_manager is not None:
            workspace_path = f"./sessions/{session_info.session_id}/workspace"
            workspace_absolute = self._session_manager.get_workspace_dir(
                session_info.session_id
            )
            self._profiled_permission_manager.set_session_context(
                session_id=session_info.session_id,
                workspace_path=workspace_path,
                workspace_absolute_path=workspace_absolute
            )
        
        # Copy skills to workspace before execution
        # This enables skills to read/write in their own isolated folder
        self._copy_skills_to_workspace(session_info.session_id)
        
        # Load system prompt from template if not provided
        # Done after session creation so permissions reflect session-specific rules
        if system_prompt is None:
            system_template_path = PROMPTS_DIR / "system.j2"
            if not system_template_path.exists():
                raise AgentError(
                    f"System prompt template not found: {system_template_path}\n"
                    f"Create the template file in AGENT/prompts/system.j2"
                )
            
            # Get skills content if enabled (using workspace-relative paths)
            skills_content = ""
            if self._config.enable_skills:
                skills_content = self._skill_manager.get_all_skills_prompt_for_workspace()
            
            # Build permission profile data for the template
            # Use user profile since that's what will be active during task execution
            # Now includes session-specific paths after set_session_context()
            permissions_data = None
            if self._profiled_permission_manager is not None:
                user_profile = self._profiled_permission_manager.user_profile
                # Get allow/deny/allowed_dirs from permissions if available
                allow_rules: list[str] = []
                deny_rules: list[str] = []
                allowed_dirs: list[str] = []
                if user_profile.permissions is not None:
                    allow_rules = user_profile.permissions.allow
                    deny_rules = user_profile.permissions.deny
                    allowed_dirs = user_profile.permissions.allowed_dirs
                
                permissions_data = {
                    "name": user_profile.name,
                    "description": user_profile.description,
                    "allow": allow_rules,
                    "deny": deny_rules,
                    "enabled_tools": user_profile.tools.enabled,
                    "disabled_tools": user_profile.tools.disabled,
                    "allowed_dirs": allowed_dirs,
                }
            
            # Get workspace directory for template
            workspace_dir = self._session_manager.get_workspace_dir(
                session_info.session_id
            )
            
            try:
                system_prompt = _jinja_env.get_template("system.j2").render(
                    enable_skills=self._config.enable_skills,
                    skills_content=skills_content,
                    permissions=permissions_data,
                    output_yaml_schema=OutputSchema.get_yaml_schema_example(),
                    working_dir=self._config.working_dir or str(workspace_dir),
                )
            except Exception as e:
                raise AgentError(f"Failed to render system prompt template: {e}")
        
        # Validate system prompt is not empty
        if not system_prompt or not system_prompt.strip():
            raise AgentError("System prompt is empty after loading/rendering")
        
        # Create trace processor BEFORE options so it can be passed to
        # permission callback for correct failure status display
        trace_processor = TraceProcessor(self._tracer)
        trace_processor.set_task(task)
        trace_processor.set_model(self._config.model)
        
        # Set cumulative stats if resuming a session
        if session_info.cumulative_usage is not None:
            trace_processor.set_cumulative_stats(
                cost_usd=session_info.cumulative_cost_usd,
                turns=session_info.cumulative_turns,
                tokens=session_info.cumulative_usage.total_tokens,
            )
        
        options = self._build_options(session_info, system_prompt, trace_processor)
        user_prompt = self._build_user_prompt(task, session_info, parameters)
        
        log_file = self._session_manager.get_log_file(session_info.session_id)
        result: Optional[ResultMessage] = None
        
        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(user_prompt)
                
                with log_file.open("w", encoding="utf-8") as f:
                    async for message in client.receive_response():
                        # Write to log file
                        f.write(json.dumps(asdict(message)) + "\n")
                        
                        # Process for console tracing
                        trace_processor.process_message(message)
                        
                        if isinstance(message, ResultMessage):
                            result = message
            
            self._validate_response(result)
            
            # Check if agent was interrupted due to permission denial
            # This happens when interrupt=True is returned from permission callback
            if self._denial_tracker.was_interrupted:
                denial = self._denial_tracker.last_denial
                error_msg = denial.message if denial else "Permission denied"
                self._tracer.on_error(error_msg, error_type="permission_denied")
                self._write_error_output(session_info.session_id, error_msg)
                self._cleanup_session(session_info.session_id)
                
                # Extract metrics even for failed runs
                usage = None
                if result:
                    usage = TokenUsage.from_sdk_usage(result.usage)
                    self._session_manager.update_session(
                        session_info,
                        status=TaskStatus.FAILED,
                        resume_id=result.session_id,
                        num_turns=result.num_turns,
                        duration_ms=result.duration_ms,
                        total_cost_usd=result.total_cost_usd,
                        usage=usage,
                        model=self._config.model,
                    )
                
                output = self._denial_tracker.get_error_output()
                return AgentResult(
                    status=TaskStatus.FAILED,
                    output=output.get("output"),
                    error=output.get("error"),
                    metrics=LLMMetrics(
                        model=self._config.model,
                        duration_ms=result.duration_ms if result else 0,
                        num_turns=result.num_turns if result else 0,
                        session_id=result.session_id if result else None,
                        total_cost_usd=result.total_cost_usd if result else None,
                        usage=usage,
                    ) if result else None,
                    session_info=session_info,
                )
            
            # Normal successful completion
            # Clean up session (remove skills, switch to system profile)
            self._cleanup_session(session_info.session_id)
            
            # Update session with Claude's session ID and metrics
            if result:
                # Extract token usage from result
                usage = TokenUsage.from_sdk_usage(result.usage)
                
                self._session_manager.update_session(
                    session_info,
                    status=TaskStatus.COMPLETE,
                    resume_id=result.session_id,
                    num_turns=result.num_turns,
                    duration_ms=result.duration_ms,
                    total_cost_usd=result.total_cost_usd,
                    usage=usage,
                    model=self._config.model,
                )
            
            output = self._session_manager.parse_output(session_info.session_id)
            
            # Normalize status - agent might write "SUCCESS" but enum expects "COMPLETE"
            raw_status = output.get("status", "COMPLETE").upper()
            if raw_status == "SUCCESS" or raw_status == "DONE":
                raw_status = "COMPLETE"
            
            return AgentResult(
                status=TaskStatus(raw_status),
                output=output.get("output"),
                error=output.get("error"),
                comments=output.get("comments"),
                result_files=output.get("result_files", []),
                metrics=LLMMetrics(
                    model=self._config.model,
                    duration_ms=result.duration_ms,
                    num_turns=result.num_turns,
                    session_id=result.session_id,
                    total_cost_usd=result.total_cost_usd,
                    usage=usage,
                ) if result else None,
                session_info=session_info,
            )
        
        except AgentError as e:
            self._tracer.on_error(str(e), error_type="agent_error")
            self._write_error_output(session_info.session_id, str(e))
            self._cleanup_session(session_info.session_id)
            self._session_manager.update_session(
                session_info, status=TaskStatus.FAILED
            )
            raise
        except asyncio.TimeoutError:
            error_msg = f"Timed out after {self._config.timeout_seconds}s"
            self._tracer.on_error(error_msg, error_type="timeout")
            self._write_error_output(session_info.session_id, error_msg)
            self._cleanup_session(session_info.session_id)
            self._session_manager.update_session(
                session_info, status=TaskStatus.FAILED
            )
            raise AgentError(error_msg)
        except Exception as e:
            self._tracer.on_error(str(e), error_type="error")
            self._write_error_output(session_info.session_id, str(e))
            self._cleanup_session(session_info.session_id)
            self._session_manager.update_session(
                session_info, status=TaskStatus.ERROR
            )
            return AgentResult(
                status=TaskStatus.ERROR,
                error=str(e),
                session_info=session_info,
            )
    
    async def run_with_timeout(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        parameters: Optional[dict] = None,
        resume_session_id: Optional[str] = None
    ) -> AgentResult:
        """
        Execute agent with timeout.
        
        Args:
            task: The task description.
            system_prompt: Custom system prompt (optional).
            parameters: Additional template parameters (optional).
            resume_session_id: Session ID to resume (optional).
        
        Returns:
            AgentResult with execution outcome.
        """
        return await asyncio.wait_for(
            self.run(task, system_prompt, parameters, resume_session_id),
            timeout=self._config.timeout_seconds,
        )


async def run_agent(
    task: str,
    profiled_permission_manager: ProfiledPermissionManager,
    working_dir: Optional[str] = None,
    model: Optional[str] = None,
    max_turns: int = 100,
    timeout_seconds: int = 1800,
    allowed_tools: Optional[list[str]] = None,
    enable_skills: bool = False,
    system_prompt: Optional[str] = None,
    parameters: Optional[dict] = None,
    resume_session_id: Optional[str] = None,
    tracer: Optional[Union[TracerBase, bool]] = True
) -> AgentResult:
    """
    Convenience function to run the agent.
    
    Args:
        task: The task description.
        profiled_permission_manager: ProfiledPermissionManager (required).
        working_dir: Working directory for the agent.
        model: Claude model to use.
        max_turns: Maximum number of turns.
        timeout_seconds: Timeout in seconds.
        allowed_tools: List of allowed tools.
        enable_skills: Enable custom skills.
        system_prompt: Custom system prompt.
        parameters: Additional template parameters.
        resume_session_id: Session ID to resume.
        tracer: Execution tracer for console output.
            - True (default): Use ExecutionTracer with default settings.
            - False/None: Disable tracing (NullTracer).
            - TracerBase instance: Use custom tracer.
    
    Returns:
        AgentResult with execution outcome.
    
    Raises:
        AgentError: If permission manager is not provided or prompts are missing.
    """
    config = AgentConfig(
        model=model or os.environ.get(
            "ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"
        ),
        allowed_tools=allowed_tools or ["Read", "Write", "Edit", "Task", "Skill"],
        max_turns=max_turns,
        timeout_seconds=timeout_seconds,
        enable_skills=enable_skills,
        working_dir=working_dir,
        additional_dirs=[],
    )
    
    agent = ClaudeAgent(
        config,
        tracer=tracer,
        profiled_permission_manager=profiled_permission_manager
    )
    return await agent.run_with_timeout(
        task, system_prompt, parameters, resume_session_id
    )

