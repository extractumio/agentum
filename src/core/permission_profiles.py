"""
Permission Profiles for Agentum.

Implements a two-profile permission system:
- System Profile: Used for agent initialization/finalization operations
  (loading sessions, reading prompts, saving logs, etc.)
- User Profile: Used during user task execution (restricted access)

This allows the agent to have full access for system operations while
being sandboxed during user task execution.

Usage:
    from permission_profiles import ProfiledPermissionManager

    # Load profiles from default locations
    manager = ProfiledPermissionManager()

    # Start with system profile (for initialization)
    manager.activate_system_profile()

    # Switch to user profile (for task execution)
    manager.activate_user_profile()

    # Check permissions against current profile
    if manager.is_allowed("Read(/path/to/file)"):
        ...

    # Switch back to system profile (for cleanup)
    manager.activate_system_profile()
"""
import json
import logging
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

from config import CONFIG_DIR
from permission_config import (
    PermissionConfig,
    PermissionConfigManager,
    PermissionMode,
    PermissionRules,
    ToolsConfig,
)

# Import TracerBase for type hints (avoid circular import)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from tracer import TracerBase

logger = logging.getLogger(__name__)


class ProfileType(StrEnum):
    """
    Types of permission profiles.
    """
    SYSTEM = "system"
    USER = "user"


class ExtendedToolsConfig(BaseModel):
    """
    Extended tool configuration including permission checking settings.
    """
    enabled: list[str] = Field(
        default_factory=list,
        description="List of enabled tools"
    )
    disabled: list[str] = Field(
        default_factory=list,
        description="List of disabled tools"
    )
    permission_checked: list[str] = Field(
        default_factory=list,
        description="""
Tools that require permission callback checks.
These tools are available but each use is validated against permission rules."""
    )


class ExtendedPermissionRules(BaseModel):
    """
    Extended permission rules including allowed directories.
    Used for system profile.
    """
    allow: list[str] = Field(
        default_factory=list,
        description="List of allowed tool patterns"
    )
    deny: list[str] = Field(
        default_factory=list,
        description="List of denied tool patterns"
    )
    ask: list[str] = Field(
        default_factory=list,
        description="List of patterns requiring confirmation"
    )
    allowed_dirs: list[str] = Field(
        default_factory=list,
        description="Directories accessible in this profile"
    )


class SessionWorkspaceConfig(BaseModel):
    """
    Session-specific workspace permissions.
    
    Defines patterns for dynamically generating session-specific
    permission rules. Use {workspace} placeholder for the workspace path.
    """
    description: str = Field(
        default="",
        description="Description of the session workspace configuration"
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Allow patterns. Supports {workspace} placeholder."
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Deny patterns."
    )
    allowed_dirs: list[str] = Field(
        default_factory=list,
        description="Allowed directories. Supports {workspace} placeholder."
    )


class PermissionProfile(BaseModel):
    """
    A complete permission profile with all settings.
    
    Simplified structure:
    - System profile uses 'permissions' for static rules
    - User profile uses 'session_workspace' for dynamic session-specific rules
    """
    name: str = Field(
        description="Profile name (e.g., 'system', 'user')"
    )
    description: str = Field(
        default="",
        description="Human-readable description of the profile"
    )
    defaultMode: PermissionMode = Field(
        default=PermissionMode.DEFAULT,
        description="Permission mode for this profile"
    )
    tools: ExtendedToolsConfig = Field(
        default_factory=ExtendedToolsConfig,
        description="Tool configuration including enabled/disabled/permission_checked"
    )
    permissions: Optional[ExtendedPermissionRules] = Field(
        default=None,
        description="Static permission rules (used by system profile)"
    )
    session_workspace: Optional[SessionWorkspaceConfig] = Field(
        default=None,
        description="Dynamic session permissions (used by user profile)"
    )


class ProfileNotFoundError(Exception):
    """Raised when a required permission profile file is not found."""
    pass


class ProfiledPermissionManager:
    """
    Manages permission profiles for system and user operations.

    Supports switching between profiles during agent lifecycle:
    1. System profile for initialization (loading sessions, prompts)
    2. User profile for task execution (sandboxed)
    3. System profile for finalization (saving sessions, logs)

    Usage:
        manager = ProfiledPermissionManager()

        # Initialize with system permissions
        manager.activate_system_profile()
        session = load_session(...)

        # Execute task with user permissions
        manager.activate_user_profile()
        result = agent.run(task)

        # Save results with system permissions
        manager.activate_system_profile()
        save_session(...)
    """

    # Default filenames (YAML preferred, JSON for backwards compatibility)
    SYSTEM_PROFILE_BASE = "permissions.system"
    USER_PROFILE_BASE = "permissions.user"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml", ".json"]

    def __init__(
        self,
        system_profile_path: Optional[Path] = None,
        user_profile_path: Optional[Path] = None
    ) -> None:
        """
        Initialize the profiled permission manager.

        Args:
            system_profile_path: Path to system profile file.
                               Defaults to AGENT/config/permissions.system.yaml
                               (falls back to .json if .yaml not found).
            user_profile_path: Path to user profile file.
                              Defaults to AGENT/config/permissions.user.yaml
                              (falls back to .json if .yaml not found).
        """
        self._system_profile_path = (
            system_profile_path or self._find_profile_file(self.SYSTEM_PROFILE_BASE)
        )
        self._user_profile_path = (
            user_profile_path or self._find_profile_file(self.USER_PROFILE_BASE)
        )

        self._system_profile: Optional[PermissionProfile] = None
        self._user_profile: Optional[PermissionProfile] = None
        self._user_profile_base: Optional[PermissionProfile] = None  # Template
        self._active_profile_type: ProfileType = ProfileType.SYSTEM
        self._active_profile: Optional[PermissionProfile] = None

        # Session context for dynamic permission generation
        self._session_id: Optional[str] = None
        self._workspace_path: Optional[str] = None
        self._workspace_absolute_path: Optional[Path] = None

        # Internal config manager for permission checking
        self._config_manager = PermissionConfigManager()

        # Optional tracer for profile switch notifications
        self._tracer: Optional["TracerBase"] = None

    def set_tracer(self, tracer: "TracerBase") -> None:
        """
        Set the tracer for profile switch notifications.

        Args:
            tracer: TracerBase instance to notify on profile switches.
        """
        self._tracer = tracer

    def _find_profile_file(self, base_name: str) -> Path:
        """
        Find a profile file by base name, trying extensions in order.

        Tries .yaml first, then .yml, then .json for backwards compatibility.

        Args:
            base_name: Base filename without extension (e.g., "permissions.user").

        Returns:
            Path to the first existing file, or path with .yaml extension
            if no file exists (for error reporting).
        """
        for ext in self.SUPPORTED_EXTENSIONS:
            path = CONFIG_DIR / f"{base_name}{ext}"
            if path.exists():
                return path
        # Return default .yaml path if nothing exists
        return CONFIG_DIR / f"{base_name}.yaml"

    def _notify_profile_switch(self) -> None:
        """Notify tracer about profile switch if tracer is set."""
        if self._tracer is None or self._active_profile is None:
            return

        tools = self.get_enabled_tools()
        allow_count = 0
        deny_count = 0
        if self._active_profile.permissions is not None:
            allow_count = len(self._active_profile.permissions.allow)
            deny_count = len(self._active_profile.permissions.deny)

        # Get the path of the loaded profile
        if self._active_profile_type == ProfileType.SYSTEM:
            profile_path = str(self._system_profile_path)
        else:
            profile_path = str(self._user_profile_path)

        self._tracer.on_profile_switch(
            profile_type=self._active_profile_type.value,
            profile_name=self._active_profile.name,
            tools=tools,
            allow_rules_count=allow_count,
            deny_rules_count=deny_count,
            profile_path=profile_path
        )

    def _load_profile(self, path: Path) -> PermissionProfile:
        """
        Load a permission profile from file (YAML or JSON).

        Supports both YAML (.yaml, .yml) and JSON (.json) formats.
        Format is determined by file extension.

        Args:
            path: Path to profile file.

        Returns:
            Loaded PermissionProfile.

        Raises:
            ProfileNotFoundError: If profile file does not exist.
            ValueError: If profile file is invalid.
        """
        if not path.exists():
            raise ProfileNotFoundError(
                f"Permission profile not found: {path}\n"
                f"Create the profile file manually or copy from templates.\n"
                f"Supported formats: .yaml, .yml, .json"
            )

        try:
            with path.open("r", encoding="utf-8") as f:
                # Determine format by extension
                suffix = path.suffix.lower()
                if suffix in (".yaml", ".yml"):
                    data = yaml.safe_load(f)
                elif suffix == ".json":
                    data = json.load(f)
                else:
                    # Try YAML first (more permissive parser)
                    data = yaml.safe_load(f)

            profile = PermissionProfile.model_validate(data)
            logger.info(f"Loaded permission profile from {path}")
            return profile
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse YAML profile {path}: {e}")
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON profile {path}: {e}")
        except Exception as e:
            raise ValueError(f"Failed to load profile {path}: {e}")

    def _ensure_profiles_loaded(self) -> None:
        """Ensure both profiles are loaded from config files."""
        if self._system_profile is None:
            self._system_profile = self._load_profile(self._system_profile_path)
        if self._user_profile_base is None:
            self._user_profile_base = self._load_profile(self._user_profile_path)
        if self._user_profile is None:
            # Use base profile if no session context is set
            self._user_profile = self._user_profile_base

    def reload_profiles(self) -> None:
        """Force reload both profiles from files."""
        self._system_profile = None
        self._user_profile = None
        self._user_profile_base = None
        self._ensure_profiles_loaded()
        # Re-apply session context if set
        if self._session_id and self._workspace_path:
            self._build_session_user_profile()
        # Re-activate current profile to update config manager
        if self._active_profile_type == ProfileType.SYSTEM:
            self.activate_system_profile()
        else:
            self.activate_user_profile()

    def set_session_context(
        self,
        session_id: str,
        workspace_path: str,
        workspace_absolute_path: Optional[Path] = None
    ) -> None:
        """
        Set the session context for dynamic permission generation.

        This creates a session-specific user profile that restricts
        the agent to only its own workspace folder.

        Args:
            session_id: The session ID (e.g., "20250130_123456_abc12345").
            workspace_path: Path to the session workspace, relative to AGENT_DIR
                           (e.g., "./sessions/20250130_123456_abc12345/workspace").
            workspace_absolute_path: Absolute path to the workspace directory.
                           Used for resolving relative paths in permission matching.

        Example:
            manager.set_session_context(
                session_id="20250130_123456_abc12345",
                workspace_path="./sessions/20250130_123456_abc12345/workspace",
                workspace_absolute_path=Path("/full/path/to/workspace")
            )
            # Now agent can only access this specific workspace
        """
        self._session_id = session_id
        self._workspace_path = workspace_path
        self._workspace_absolute_path = workspace_absolute_path
        self._ensure_profiles_loaded()
        self._build_session_user_profile()
        
        # Update config manager with workspace context for path resolution
        if workspace_absolute_path is not None:
            self._config_manager.set_working_directory(workspace_absolute_path)
        
        logger.info(
            f"Session context set: session_id={session_id}, "
            f"workspace={workspace_path}"
        )

    def _build_session_user_profile(self) -> None:
        """
        Build a session-specific user profile.

        Creates a modified copy of the base user profile with
        session-specific permission rules from the session_workspace config.
        Uses patterns defined in permissions.user.yaml, replacing {workspace}
        placeholder with the actual workspace path.
        """
        if self._user_profile_base is None or self._workspace_path is None:
            return

        base = self._user_profile_base
        workspace = self._workspace_path

        # Check if session_workspace config exists
        if base.session_workspace is None:
            logger.warning(
                "No session_workspace config in user profile, "
                "using base profile without session-specific rules"
            )
            self._user_profile = base
            return

        ws_config = base.session_workspace

        # Build session-specific allow rules from config
        # Replace {workspace} placeholder with actual workspace path
        session_allow: list[str] = []
        for pattern in ws_config.allow:
            session_allow.append(pattern.replace("{workspace}", workspace))

        # Build session-specific deny rules from config
        session_deny: list[str] = []
        for pattern in ws_config.deny:
            session_deny.append(pattern.replace("{workspace}", workspace))

        # Build session-specific allowed_dirs from config
        session_allowed_dirs: list[str] = []
        for pattern in ws_config.allowed_dirs:
            session_allowed_dirs.append(pattern.replace("{workspace}", workspace))

        # Create session-specific profile with permissions for the config manager
        self._user_profile = PermissionProfile(
            name=f"user:{self._session_id}",
            description=(
                f"Session-specific user profile. "
                f"Sandboxed to workspace: {workspace}"
            ),
            defaultMode=base.defaultMode,
            tools=base.tools.model_copy(),
            permissions=ExtendedPermissionRules(
                allow=session_allow,
                deny=session_deny,
                ask=[],
                allowed_dirs=session_allowed_dirs,
            ),
        )

        logger.info(
            f"Built session-specific user profile: {self._user_profile.name}"
        )

    def clear_session_context(self) -> None:
        """
        Clear the session context and revert to base user profile.

        Call this when the session is complete to reset permissions.
        """
        self._session_id = None
        self._workspace_path = None
        self._workspace_absolute_path = None
        if self._user_profile_base is not None:
            self._user_profile = self._user_profile_base
        # Clear working directory from config manager
        self._config_manager.clear_working_directory()
        logger.info("Session context cleared, reverted to base user profile")

    @property
    def system_profile(self) -> PermissionProfile:
        """Get the system profile."""
        self._ensure_profiles_loaded()
        return self._system_profile  # type: ignore

    @property
    def user_profile(self) -> PermissionProfile:
        """Get the user profile."""
        self._ensure_profiles_loaded()
        return self._user_profile  # type: ignore

    @property
    def active_profile(self) -> PermissionProfile:
        """Get the currently active profile."""
        if self._active_profile is None:
            self.activate_system_profile()
        return self._active_profile  # type: ignore

    @property
    def active_profile_type(self) -> ProfileType:
        """Get the type of the currently active profile."""
        return self._active_profile_type

    def activate_system_profile(self) -> PermissionProfile:
        """
        Activate the system profile.

        Used for agent initialization and finalization operations
        that require broader file access.

        Returns:
            The activated system profile.
        """
        self._ensure_profiles_loaded()
        self._active_profile_type = ProfileType.SYSTEM
        self._active_profile = self._system_profile
        self._update_config_manager()

        # Log profile activation with details
        profile_name = self._active_profile.name if self._active_profile else "system"
        allow_count = 0
        deny_count = 0
        if self._active_profile and self._active_profile.permissions:
            allow_count = len(self._active_profile.permissions.allow)
            deny_count = len(self._active_profile.permissions.deny)
        logger.info(
            f"PROFILE SWITCH: Activated SYSTEM profile '{profile_name}' "
            f"(allow={allow_count}, deny={deny_count})"
        )

        # Notify tracer about profile switch
        self._notify_profile_switch()

        return self._active_profile  # type: ignore

    def activate_user_profile(self) -> PermissionProfile:
        """
        Activate the user profile.

        Used for task execution with restricted permissions.

        Returns:
            The activated user profile.
        """
        self._ensure_profiles_loaded()
        self._active_profile_type = ProfileType.USER
        self._active_profile = self._user_profile
        self._update_config_manager()

        # Log profile activation with details
        profile_name = self._active_profile.name if self._active_profile else "user"
        allow_count = 0
        deny_count = 0
        if self._active_profile and self._active_profile.permissions:
            allow_count = len(self._active_profile.permissions.allow)
            deny_count = len(self._active_profile.permissions.deny)
        logger.info(
            f"PROFILE SWITCH: Activated USER profile '{profile_name}' "
            f"(allow={allow_count}, deny={deny_count})"
        )

        # Notify tracer about profile switch
        self._notify_profile_switch()

        return self._active_profile  # type: ignore

    def _update_config_manager(self) -> None:
        """Update the internal config manager with active profile settings."""
        import time

        if self._active_profile is None:
            return

        # Convert profile to PermissionConfig for the config manager
        # Map ExtendedPermissionRules to PermissionRules
        perm_rules = PermissionRules()
        if self._active_profile.permissions is not None:
            perm_rules = PermissionRules(
                allow=self._active_profile.permissions.allow,
                deny=self._active_profile.permissions.deny,
                ask=self._active_profile.permissions.ask,
            )
        
        # Map ExtendedToolsConfig to ToolsConfig
        tools_config = ToolsConfig(
            enabled=self._active_profile.tools.enabled,
            disabled=self._active_profile.tools.disabled,
        )
        
        config = PermissionConfig(
            defaultMode=self._active_profile.defaultMode,
            permissions=perm_rules,
            tools=tools_config,
        )
        self._config_manager._config = config
        # Set _last_modified to prevent reload from file
        # This ensures our manually set config is used
        self._config_manager._last_modified = time.time()
        # Also clear _config_path to prevent file-based reload
        self._config_manager._config_path = None

    def is_allowed(self, tool_call: str) -> bool:
        """
        Check if a tool call is allowed by the current profile.

        Args:
            tool_call: Tool call string, e.g., "Read(/path/to/file)"

        Returns:
            True if allowed, False if denied.
        """
        if self._active_profile is None:
            self.activate_system_profile()
        return self._config_manager.is_tool_allowed(tool_call)

    def needs_confirmation(self, tool_call: str) -> bool:
        """
        Check if a tool call requires confirmation.

        Args:
            tool_call: Tool call string.

        Returns:
            True if confirmation needed.
        """
        return self._config_manager.needs_confirmation(tool_call)

    def get_enabled_tools(self) -> list[str]:
        """Get list of enabled tools for current profile."""
        profile = self.active_profile
        enabled = set(profile.tools.enabled)
        disabled = set(profile.tools.disabled)
        return list(enabled - disabled)

    def get_permission_checked_tools(self) -> set[str]:
        """
        Get tools that require permission callback checks.

        These tools are available but each use is validated.

        Returns:
            Set of tool names that need permission checks.
        """
        return set(self.active_profile.tools.permission_checked)

    def get_disabled_tools(self) -> set[str]:
        """
        Get tools that are completely disabled.

        These tools cannot be used at all.

        Returns:
            Set of tool names that are disabled.
        """
        return set(self.active_profile.tools.disabled)

    def get_pre_approved_tools(self) -> list[str]:
        """
        Get tools that are pre-approved (no permission check needed).

        Returns:
            List of tool names that don't require permission checks.
        """
        enabled = set(self.get_enabled_tools())
        permission_checked = self.get_permission_checked_tools()
        disabled = self.get_disabled_tools()
        return list(enabled - permission_checked - disabled)

    def get_allowed_dirs(self) -> list[str]:
        """
        Get directories accessible in the current profile.

        Returns:
            List of directory paths (relative to working dir).
        """
        profile = self.active_profile
        if profile.permissions is not None:
            return list(profile.permissions.allowed_dirs)
        return []

    def save_profile(
        self,
        profile: PermissionProfile,
        target_path: Optional[Path] = None
    ) -> Path:
        """
        Save a permission profile to file (YAML or JSON).

        Format is determined by file extension. Defaults to YAML.

        Args:
            profile: Profile to save.
            target_path: Path to save to. If None, uses default path
                        based on profile name.

        Returns:
            Path where profile was saved.
        """
        if target_path is None:
            if profile.name == "system":
                target_path = self._system_profile_path
            else:
                target_path = self._user_profile_path

        target_path.parent.mkdir(parents=True, exist_ok=True)

        data = profile.model_dump(mode="json")
        suffix = target_path.suffix.lower()

        with target_path.open("w", encoding="utf-8") as f:
            if suffix in (".yaml", ".yml"):
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            else:
                json.dump(data, f, indent=2)

        logger.info(f"Saved profile '{profile.name}' to {target_path}")
        return target_path

    def validate_profiles_exist(self) -> tuple[Path, Path]:
        """
        Validate that both profile files exist.

        Returns:
            Tuple of (system_profile_path, user_profile_path).

        Raises:
            ProfileNotFoundError: If either profile file is missing.
        """
        missing = []
        if not self._system_profile_path.exists():
            missing.append(f"System profile: {self._system_profile_path}")
        if not self._user_profile_path.exists():
            missing.append(f"User profile: {self._user_profile_path}")

        if missing:
            raise ProfileNotFoundError(
                "Missing permission profile files:\n  " +
                "\n  ".join(missing) +
                "\n\nCreate these files manually or copy from templates."
            )

        return self._system_profile_path, self._user_profile_path


def validate_profile_files(config_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """
    Validate that permission profile files exist.

    Args:
        config_dir: Directory containing profiles.
                   Defaults to AGENT/config/.

    Returns:
        Tuple of (system_profile_path, user_profile_path).

    Raises:
        ProfileNotFoundError: If profile files are missing.
    """
    if config_dir is None:
        config_dir = CONFIG_DIR

    manager = ProfiledPermissionManager(
        system_profile_path=config_dir / ProfiledPermissionManager.SYSTEM_PROFILE_FILENAME,
        user_profile_path=config_dir / ProfiledPermissionManager.USER_PROFILE_FILENAME
    )
    return manager.validate_profiles_exist()

