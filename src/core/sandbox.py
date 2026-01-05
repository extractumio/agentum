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
    ) -> list[str]:
        """Build a bubblewrap command for the given command args."""
        config = self._config
        cmd = [
            config.bwrap_path,
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
        ]

        if config.use_tmpfs_root:
            cmd.extend(["--tmpfs", "/"])

        cmd.extend(["--proc", "/proc", "--dev", "/dev"])

        for mount in list(config.static_mounts.values()) + list(config.session_mounts.values()):
            cmd.extend(_mount_args(mount))

        for mount in config.dynamic_mounts:
            cmd.extend(_mount_args(mount))

        if config.file_sandboxing:
            if allow_network or not config.network_sandboxing:
                cmd.append("--share-net")
            else:
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
