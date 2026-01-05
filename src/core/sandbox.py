"""
Sandbox configuration and execution helpers for Agentum.

Defines the sandbox configuration schema used by permission profiles and
provides a Bubblewrap-based command wrapper for tool execution.
"""
from __future__ import annotations

import logging
import shlex
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class SandboxMount(BaseModel):
    """Mount configuration for the sandbox."""

    source: str = Field(description="Host path to mount")
    target: str = Field(description="Path inside sandbox")
    mode: str = Field(default="ro", description="Mount mode: ro or rw")

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = (value or "ro").lower()
        if normalized not in {"ro", "rw"}:
            raise ValueError("Sandbox mount mode must be 'ro' or 'rw'")
        return normalized

    def resolve(self, placeholders: dict[str, str]) -> "SandboxMount":
        return SandboxMount(
            source=_resolve_placeholders(self.source, placeholders),
            target=_resolve_placeholders(self.target, placeholders),
            mode=self.mode,
        )


class SandboxNetworkConfig(BaseModel):
    """Network policy configuration for sandboxed tools."""

    enabled: bool = Field(default=False, description="Allow network access")
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Whitelisted domains for WebFetch/WebSearch",
    )
    allow_localhost: bool = Field(
        default=False,
        description="Allow access to localhost or private network ranges",
    )

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def normalize_domains(cls, value: list[str] | None) -> list[str]:
        if not value:
            return []
        return [domain.strip().lower() for domain in value if domain]


class SandboxEnvConfig(BaseModel):
    """Environment configuration for sandboxed tools."""

    home: str = Field(default="/workspace", description="HOME inside sandbox")
    path: str = Field(default="/usr/bin:/bin", description="PATH inside sandbox")
    clear_env: bool = Field(default=True, description="Clear environment vars")


class SandboxConfig(BaseModel):
    """Complete sandbox configuration."""

    enabled: bool = Field(default=True, description="Enable bubblewrap sandbox")
    file_sandboxing: bool = Field(
        default=True,
        description="Use bubblewrap for file system isolation",
    )
    network_sandboxing: bool = Field(
        default=True,
        description="Enforce network policy for WebFetch/WebSearch",
    )
    bwrap_path: str = Field(default="bwrap", description="Path to bubblewrap")
    use_tmpfs_root: bool = Field(default=True, description="Mount empty tmpfs at /")
    static_mounts: dict[str, SandboxMount] = Field(default_factory=dict)
    session_mounts: dict[str, SandboxMount] = Field(default_factory=dict)
    dynamic_mounts: list[SandboxMount] = Field(default_factory=list)
    network: SandboxNetworkConfig = Field(default_factory=SandboxNetworkConfig)
    environment: SandboxEnvConfig = Field(default_factory=SandboxEnvConfig)
    writable_paths: list[str] = Field(default_factory=list)
    readonly_paths: list[str] = Field(default_factory=list)

    @field_validator("dynamic_mounts", mode="before")
    @classmethod
    def normalize_dynamic_mounts(cls, value: list[SandboxMount] | None) -> list[SandboxMount]:
        if not value:
            return []
        return value

    def resolve(self, placeholders: dict[str, str]) -> "SandboxConfig":
        def resolve_mounts(mounts: dict[str, SandboxMount]) -> dict[str, SandboxMount]:
            return {
                key: mount.resolve(placeholders)
                for key, mount in mounts.items()
            }

        return SandboxConfig(
            enabled=self.enabled,
            file_sandboxing=self.file_sandboxing,
            network_sandboxing=self.network_sandboxing,
            bwrap_path=_resolve_placeholders(self.bwrap_path, placeholders),
            use_tmpfs_root=self.use_tmpfs_root,
            static_mounts=resolve_mounts(self.static_mounts),
            session_mounts=resolve_mounts(self.session_mounts),
            dynamic_mounts=[mount.resolve(placeholders) for mount in self.dynamic_mounts],
            network=self.network,
            environment=self.environment,
            writable_paths=[
                _resolve_placeholders(path, placeholders)
                for path in self.writable_paths
            ],
            readonly_paths=[
                _resolve_placeholders(path, placeholders)
                for path in self.readonly_paths
            ],
        )


class SandboxExecutor:
    """Build bubblewrap commands for sandboxed execution."""

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config

    @property
    def config(self) -> SandboxConfig:
        return self._config

    def build_bwrap_command(
        self,
        command: Iterable[str],
        allow_network: bool,
        nested_container: bool = True,
    ) -> list[str]:
        """
        Build a bubblewrap command for the given command args.

        Args:
            command: Command arguments to execute inside sandbox.
            allow_network: Whether to allow network access.
            nested_container: If True, use flags compatible with running
                inside Docker (avoids pivot_root issues).
        """
        config = self._config

        # Base command - avoid flags that cause pivot_root in Docker
        cmd = [config.bwrap_path]

        # In Docker, we need to be careful about namespace operations
        # Use --unshare-user --unshare-pid for basic isolation
        # but avoid --unshare-all which requires pivot_root
        if nested_container:
            cmd.extend([
                "--unshare-pid",
                "--unshare-uts",
                "--unshare-ipc",
            ])
        else:
            cmd.append("--unshare-all")

        cmd.extend([
            "--die-with-parent",
            "--new-session",
        ])

        # For nested containers: don't try to create a new root filesystem
        # Instead, bind-mount everything explicitly and block access to
        # sensitive paths by NOT mounting them
        if config.use_tmpfs_root and not nested_container:
            cmd.extend(["--tmpfs", "/"])
        else:
            # In Docker, create an isolated filesystem view
            # Start with tmpfs at /tmp for scratch space
            cmd.extend(["--tmpfs", "/tmp:size=100M"])

        cmd.extend(["--proc", "/proc", "--dev", "/dev"])

        for mount in list(config.static_mounts.values()) + list(config.session_mounts.values()):
            cmd.extend(_mount_args(mount))

        for mount in config.dynamic_mounts:
            cmd.extend(_mount_args(mount))

        # Network isolation - only if not in nested container
        if not allow_network and config.network_sandboxing and not nested_container:
            cmd.append("--unshare-net")

        if config.environment.clear_env:
            cmd.append("--clearenv")

        cmd.extend(["--setenv", "HOME", config.environment.home])
        cmd.extend(["--setenv", "PATH", config.environment.path])
        cmd.extend(["--chdir", config.environment.home])

        cmd.append("--")
        cmd.extend(list(command))

        return cmd

    def wrap_shell_command(self, command: str, allow_network: bool) -> str:
        """Wrap a shell command string in a bubblewrap invocation."""
        wrapped = self.build_bwrap_command(
            ["bash", "-lc", command],
            allow_network=allow_network,
        )
        return shlex.join(wrapped)

    def validate_mount_sources(self) -> list[str]:
        """Return a list of missing mount sources for diagnostics."""
        missing = []
        mounts = list(self._config.static_mounts.values()) + list(self._config.session_mounts.values())
        mounts += list(self._config.dynamic_mounts)
        for mount in mounts:
            if not Path(mount.source).exists():
                missing.append(mount.source)
        return missing


def _resolve_placeholders(value: str, placeholders: dict[str, str]) -> str:
    resolved = value
    for key, replacement in placeholders.items():
        resolved = resolved.replace(f"{{{key}}}", replacement)
    return resolved


def _mount_args(mount: SandboxMount) -> list[str]:
    if mount.mode == "rw":
        return ["--bind", mount.source, mount.target]
    return ["--ro-bind", mount.source, mount.target]


async def execute_sandboxed_command(
    executor: SandboxExecutor,
    command: str,
    allow_network: bool = False,
    timeout: int = 300,
) -> tuple[int, str, str]:
    """
    Execute a shell command inside the bubblewrap sandbox.

    This is the core sandboxed execution function that wraps any command
    in bubblewrap with the configured mounts and isolation.

    Args:
        executor: SandboxExecutor with resolved mount configuration.
        command: Shell command to execute inside the sandbox.
        allow_network: Whether to allow network access.
        timeout: Command timeout in seconds.

    Returns:
        Tuple of (exit_code, stdout, stderr).
    """
    import asyncio

    # Build the bwrap command
    bwrap_cmd = executor.build_bwrap_command(
        ["bash", "-c", command],
        allow_network=allow_network,
    )

    logger.info(f"SANDBOX EXEC: {' '.join(bwrap_cmd[:10])}...")
    logger.debug(f"SANDBOX FULL CMD: {' '.join(bwrap_cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *bwrap_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )

        exit_code = process.returncode or 0
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        logger.info(f"SANDBOX RESULT: exit={exit_code}, stdout_len={len(stdout)}")
        return exit_code, stdout, stderr

    except asyncio.TimeoutError:
        logger.warning(f"SANDBOX TIMEOUT: Command timed out after {timeout}s")
        if process:
            process.kill()
        return 124, "", f"Command timed out after {timeout} seconds"
    except Exception as e:
        logger.error(f"SANDBOX ERROR: {e}")
        return 1, "", str(e)
