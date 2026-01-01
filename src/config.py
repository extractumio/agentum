"""
Global configuration for Agentum.

This module defines the AGENT_DIR constant which is the root directory
of the AGENT project. All file-based operations should reference paths
relative to this directory.

Usage:
    from config import AGENT_DIR, ENV_FILE, LOGS_DIR, SESSIONS_DIR, ...
"""
from pathlib import Path

# AGENT_DIR is the root of the AGENT project
# config.py is at AGENT/src/config.py, so parent.parent = AGENT/
AGENT_DIR: Path = Path(__file__).parent.parent.resolve()

# Standard directories
LOGS_DIR: Path = AGENT_DIR / "logs"
SESSIONS_DIR: Path = AGENT_DIR / "sessions"
CONFIG_DIR: Path = AGENT_DIR / "config"
SKILLS_DIR: Path = AGENT_DIR / "skills"
PROMPTS_DIR: Path = AGENT_DIR / "prompts"

# Configuration files
ENV_FILE: Path = AGENT_DIR / ".env"
PERMISSIONS_FILE: Path = CONFIG_DIR / "permissions.json"

# Source directories
SRC_DIR: Path = AGENT_DIR / "src"
CORE_DIR: Path = SRC_DIR / "core"


def ensure_dirs() -> None:
    """Ensure all required directories exist."""
    for dir_path in [LOGS_DIR, SESSIONS_DIR, CONFIG_DIR, SKILLS_DIR]:
        dir_path.mkdir(parents=True, exist_ok=True)

