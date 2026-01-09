"""
Agent-level bubblewrap sandbox runner.

Wraps the entire Claude Code agent process in a bubblewrap sandbox,
providing automatic subprocess inheritance. All child processes
(Bash commands, Python scripts, etc.) inherit the sandbox restrictions.

This replaces per-command wrapping with process-level sandboxing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SandboxMount(BaseModel):
    """Mount configuration for the sandbox."""
    
    source: str = Field(description="Host path to mount")
    target: str = Field(description="Path inside sandbox")
    mode: str = Field(default="ro", description="Mount mode: ro or rw")


class SandboxNetworkConfig(BaseModel):
    """Network policy configuration for sandboxed tools (backward compatibility)."""

    enabled: bool = Field(default=False, description="Allow network access")
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Whitelisted domains for WebFetch/WebSearch",
    )
    allow_localhost: bool = Field(
        default=False,
        description="Allow access to localhost or private network ranges",
    )


class SandboxEnvironment(BaseModel):
    """Environment configuration for sandboxed agent."""
    
    home: str = Field(default="/session/workspace", description="HOME inside sandbox")
    path: str = Field(default="/usr/bin:/bin", description="PATH inside sandbox")
    claude_config_dir: str = Field(default="/session", description="CLAUDE_CONFIG_DIR")
    clear_env: bool = Field(default=True, description="Clear environment vars")
    additional_vars: dict[str, str] = Field(default_factory=dict)


class SandboxConfig(BaseModel):
    """Configuration for agent-level sandbox."""
    
    enabled: bool = Field(default=True, description="Enable bwrap sandbox")
    bwrap_path: str = Field(default="bwrap", description="Path to bubblewrap binary")
    
    # Backward compatibility with old sandbox.py
    # These are kept for permission_profiles.py compatibility
    file_sandboxing: bool = Field(default=True, description="Enable file isolation (legacy)")
    network_sandboxing: bool = Field(default=True, description="Enable network filtering (legacy)")
    
    # Legacy compatibility fields (not used in new architecture)
    use_tmpfs_root: bool = Field(default=True, description="Legacy: mount empty tmpfs at /")
    static_mounts: dict = Field(default_factory=dict, description="Legacy: static mount config")
    session_mounts: dict = Field(default_factory=dict, description="Legacy: session mount config")
    dynamic_mounts: list = Field(default_factory=list, description="Legacy: dynamic mounts")
    writable_paths: list[str] = Field(default_factory=list, description="Legacy: writable paths")
    readonly_paths: list[str] = Field(default_factory=list, description="Legacy: readonly paths")
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig, description="Legacy: network config")
    
    # Namespace isolation
    unshare_pid: bool = Field(default=True, description="Isolate PID namespace")
    unshare_ipc: bool = Field(default=True, description="Isolate IPC namespace")
    unshare_uts: bool = Field(default=True, description="Isolate UTS namespace")
    
    # Standard mounts (read-only system paths)
    system_mounts: list[SandboxMount] = Field(default_factory=lambda: [
        SandboxMount(source="/usr", target="/usr", mode="ro"),
        SandboxMount(source="/lib", target="/lib", mode="ro"),
        SandboxMount(source="/bin", target="/bin", mode="ro"),
    ])
    
    # Additional read-only mounts (e.g., skills)
    readonly_mounts: list[SandboxMount] = Field(default_factory=list)
    
    # Environment configuration
    environment: SandboxEnvironment = Field(default_factory=SandboxEnvironment)
    
    # Temp filesystem size
    tmpfs_size: str = Field(default="100M", description="Size of /tmp tmpfs")
    
    def resolve(self, placeholders: dict[str, str]) -> "SandboxConfig":
        """
        Resolve placeholders in mount paths.
        
        This method provides backward compatibility with the old sandbox.py.
        In the new architecture, paths are resolved at runtime by
        SandboxedAgentRunner, so this just returns self.
        
        Args:
            placeholders: Dict of placeholder names to values.
            
        Returns:
            Self (no resolution needed in new architecture).
        """
        # The new architecture handles path resolution in SandboxedAgentRunner
        # Just return self for backward compatibility
        return self


@dataclass
class SandboxedAgentParams:
    """Parameters passed to the sandboxed agent process."""
    
    session_id: str
    task: str
    model: str
    max_turns: int
    system_prompt: Optional[str] = None
    resume_id: Optional[str] = None
    fork_session: bool = False
    timeout_seconds: Optional[int] = None
    enable_skills: bool = True
    role: str = "default"
    output_format: str = "yaml"
    
    def to_json(self) -> str:
        """Serialize to JSON for passing to subprocess."""
        return json.dumps({
            "session_id": self.session_id,
            "task": self.task,
            "model": self.model,
            "max_turns": self.max_turns,
            "system_prompt": self.system_prompt,
            "resume_id": self.resume_id,
            "fork_session": self.fork_session,
            "timeout_seconds": self.timeout_seconds,
            "enable_skills": self.enable_skills,
            "role": self.role,
            "output_format": self.output_format,
        })
    
    @classmethod
    def from_json(cls, data: str) -> "SandboxedAgentParams":
        """Deserialize from JSON."""
        parsed = json.loads(data)
        return cls(**parsed)


@dataclass
class SandboxedAgentResult:
    """Result from sandboxed agent execution."""
    
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    result_file: Optional[Path] = None
    error: Optional[str] = None


class SandboxedAgentRunner:
    """
    Runs Claude Code agent inside a bubblewrap sandbox.
    
    The entire agent process is wrapped in bwrap, providing:
    - Isolated PID namespace (ps only shows sandbox processes)
    - Restricted filesystem view (only session dir + system binaries)
    - Automatic subprocess inheritance
    
    All child processes spawned by the agent (Bash commands, Python scripts)
    inherit the sandbox restrictions automatically.
    """
    
    def __init__(
        self,
        config: SandboxConfig,
        sessions_dir: Path,
        skills_dir: Path,
        src_dir: Optional[Path] = None,
    ) -> None:
        """
        Initialize the sandbox runner.
        
        Args:
            config: Sandbox configuration.
            sessions_dir: Base directory for sessions (host path).
            skills_dir: Skills library directory (host path).
            src_dir: Source code directory for agent modules (host path).
        """
        self._config = config
        self._sessions_dir = sessions_dir
        self._skills_dir = skills_dir
        self._src_dir = src_dir or Path("/src")
    
    @property
    def config(self) -> SandboxConfig:
        """Get sandbox configuration."""
        return self._config
    
    def _check_bwrap_available(self) -> bool:
        """Check if bubblewrap is available."""
        return shutil.which(self._config.bwrap_path) is not None
    
    def build_bwrap_command(
        self,
        session_dir: Path,
        agent_params: SandboxedAgentParams,
        python_executable: str = "python3",
    ) -> list[str]:
        """
        Build the bwrap command to run the agent in sandbox.
        
        Args:
            session_dir: Session directory to mount (contains workspace/).
            agent_params: Parameters for the agent execution.
            python_executable: Python interpreter to use.
            
        Returns:
            Complete bwrap command as list of arguments.
        """
        config = self._config
        cmd: list[str] = [config.bwrap_path]
        
        # Namespace isolation
        if config.unshare_pid:
            cmd.append("--unshare-pid")
        if config.unshare_ipc:
            cmd.append("--unshare-ipc")
        if config.unshare_uts:
            cmd.append("--unshare-uts")
        
        # Process management
        cmd.extend([
            "--die-with-parent",
            "--new-session",
        ])
        
        # Session directory (rw) - agent's isolated world
        cmd.extend(["--bind", str(session_dir), "/session"])
        
        # System mounts (ro)
        for mount in config.system_mounts:
            source = Path(mount.source)
            if source.exists():
                if mount.mode == "rw":
                    cmd.extend(["--bind", mount.source, mount.target])
                else:
                    cmd.extend(["--ro-bind", mount.source, mount.target])
        
        # Handle /lib64 symlink if it exists (common on Linux)
        if Path("/lib64").exists():
            cmd.extend(["--ro-bind", "/lib64", "/lib64"])
        
        # Skills directory (ro)
        if self._skills_dir.exists():
            cmd.extend(["--ro-bind", str(self._skills_dir), "/skills"])
        
        # Source code for agent modules (ro)
        # Required so the sandboxed Python can import agent code
        if self._src_dir.exists():
            cmd.extend(["--ro-bind", str(self._src_dir), "/src"])
        
        # Additional readonly mounts
        for mount in config.readonly_mounts:
            source = Path(mount.source)
            if source.exists():
                cmd.extend(["--ro-bind", mount.source, mount.target])
        
        # Virtual filesystems
        cmd.extend(["--proc", "/proc"])
        cmd.extend(["--dev", "/dev"])
        cmd.extend(["--tmpfs", f"/tmp:size={config.tmpfs_size}"])
        
        # Environment
        if config.environment.clear_env:
            cmd.append("--clearenv")
        
        cmd.extend(["--setenv", "HOME", config.environment.home])
        cmd.extend(["--setenv", "PATH", config.environment.path])
        cmd.extend(["--setenv", "CLAUDE_CONFIG_DIR", config.environment.claude_config_dir])
        cmd.extend(["--setenv", "PYTHONPATH", "/src"])
        
        # Pass agent params via environment
        cmd.extend(["--setenv", "SANDBOXED_AGENT_PARAMS", agent_params.to_json()])
        
        # Additional environment variables
        for key, value in config.environment.additional_vars.items():
            cmd.extend(["--setenv", key, value])
        
        # Working directory
        cmd.extend(["--chdir", "/session/workspace"])
        
        # End of bwrap args, start of command
        cmd.append("--")
        
        # The sandboxed agent entry point
        cmd.extend([
            python_executable,
            "-m", "src.core.sandboxed_agent",
        ])
        
        return cmd
    
    async def run_agent(
        self,
        session_dir: Path,
        params: SandboxedAgentParams,
        timeout: Optional[int] = None,
        on_stdout: Optional[callable] = None,
        on_stderr: Optional[callable] = None,
    ) -> SandboxedAgentResult:
        """
        Run the agent inside the sandbox.
        
        Args:
            session_dir: Session directory (must exist with workspace/).
            params: Agent execution parameters.
            timeout: Overall timeout in seconds.
            on_stdout: Optional callback for stdout lines.
            on_stderr: Optional callback for stderr lines.
            
        Returns:
            SandboxedAgentResult with execution outcome.
        """
        if not self._config.enabled:
            logger.warning("Sandbox is disabled - running agent without isolation")
            return await self._run_unsandboxed(session_dir, params, timeout)
        
        if not self._check_bwrap_available():
            error_msg = (
                f"Bubblewrap not found at '{self._config.bwrap_path}'. "
                "Cannot run agent without sandbox for security reasons."
            )
            logger.error(error_msg)
            return SandboxedAgentResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=error_msg,
                error=error_msg,
            )
        
        # Ensure workspace directory exists
        workspace_dir = session_dir / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        
        # Build the bwrap command
        cmd = self.build_bwrap_command(session_dir, params)
        
        logger.info(f"SANDBOX: Starting agent in sandbox for session {params.session_id}")
        logger.debug(f"SANDBOX CMD: {' '.join(cmd[:20])}...")
        
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(session_dir),
            )
            
            # Collect output
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []
            
            async def read_stream(stream, lines_list, callback):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    lines_list.append(decoded)
                    if callback:
                        callback(decoded)
            
            # Read stdout and stderr concurrently
            if timeout:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(
                            read_stream(process.stdout, stdout_lines, on_stdout),
                            read_stream(process.stderr, stderr_lines, on_stderr),
                        ),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"SANDBOX: Timeout after {timeout}s, killing process")
                    process.kill()
                    await process.wait()
                    return SandboxedAgentResult(
                        success=False,
                        exit_code=124,
                        stdout="".join(stdout_lines),
                        stderr="".join(stderr_lines) + f"\nTimeout after {timeout}s",
                        error=f"Agent timed out after {timeout} seconds",
                    )
            else:
                await asyncio.gather(
                    read_stream(process.stdout, stdout_lines, on_stdout),
                    read_stream(process.stderr, stderr_lines, on_stderr),
                )
            
            await process.wait()
            exit_code = process.returncode or 0
            
            logger.info(f"SANDBOX: Agent exited with code {exit_code}")
            
            # Check for result file
            result_file = session_dir / "workspace" / "agent_result.json"
            
            return SandboxedAgentResult(
                success=exit_code == 0,
                exit_code=exit_code,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines),
                result_file=result_file if result_file.exists() else None,
            )
            
        except Exception as e:
            logger.error(f"SANDBOX: Error running agent: {e}")
            return SandboxedAgentResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=str(e),
                error=str(e),
            )
    
    async def _run_unsandboxed(
        self,
        session_dir: Path,
        params: SandboxedAgentParams,
        timeout: Optional[int] = None,
    ) -> SandboxedAgentResult:
        """
        Run agent without sandbox (fallback, logs warning).
        
        This should only be used when sandbox is explicitly disabled
        in configuration for development/debugging.
        """
        logger.warning("SANDBOX DISABLED: Running agent without isolation!")
        
        # Import here to avoid circular imports
        from .sandboxed_agent import run_agent_internal
        
        try:
            result = await run_agent_internal(params, session_dir)
            return SandboxedAgentResult(
                success=True,
                exit_code=0,
                stdout=json.dumps(result) if result else "",
                stderr="",
            )
        except Exception as e:
            return SandboxedAgentResult(
                success=False,
                exit_code=1,
                stdout="",
                stderr=str(e),
                error=str(e),
            )


def get_default_sandbox_config() -> SandboxConfig:
    """Get default sandbox configuration."""
    return SandboxConfig()


def create_sandbox_runner(
    sessions_dir: Path,
    skills_dir: Path,
    config: Optional[SandboxConfig] = None,
) -> SandboxedAgentRunner:
    """
    Create a sandbox runner with default or custom configuration.
    
    Args:
        sessions_dir: Base directory for sessions.
        skills_dir: Skills library directory.
        config: Optional custom sandbox configuration.
        
    Returns:
        Configured SandboxedAgentRunner instance.
    """
    return SandboxedAgentRunner(
        config=config or get_default_sandbox_config(),
        sessions_dir=sessions_dir,
        skills_dir=skills_dir,
    )
