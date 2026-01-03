#!/usr/bin/env python3
"""
CLI Entry point for Agentum (direct execution).

This script runs the agent directly without going through the HTTP API.
It imports from src/core/agent.py and executes tasks locally.

Configuration is loaded from config/agent.yaml and config/secrets.yaml.

For HTTP-based execution via the API, use agent_http.py instead.
"""
import sys
from pathlib import Path

# Add project root to sys.path so that 'src' can be imported as a package
_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "tools"))

from src.core.agent import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
