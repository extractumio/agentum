#!/usr/bin/env python3
"""
Entry point for Agentum.

This is a wrapper that imports from src/core/agent.py.
"""
import sys
from pathlib import Path

# Add src to path FIRST so we can import config
_src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(_src_dir))
sys.path.insert(0, str(_src_dir / "core"))

# Load .env from central config location
from config import ENV_FILE

if ENV_FILE.exists():
    from dotenv import load_dotenv
    import os
    result = load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("ANTHROPIC_API_KEY", "")

from agent import main

if __name__ == "__main__":
    sys.exit(main())
