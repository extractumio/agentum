#!/usr/bin/env python3
"""
Entry point for Agentum.

This is a wrapper that imports from src/core/agent.py.
Configuration is loaded from config/agent.yaml and config/secrets.yaml.
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
