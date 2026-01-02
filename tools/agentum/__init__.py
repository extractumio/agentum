"""
Agentum system tools package.

Contains system-level tools that are always available to the agent,
regardless of permission settings.
"""
from .system_write_output import (
    SYSTEM_TOOLS,
    create_agentum_mcp_server,
    is_system_tool,
)

__all__ = [
    "SYSTEM_TOOLS",
    "create_agentum_mcp_server",
    "is_system_tool",
]
