#!/usr/bin/env python3
"""
Agentum Entry Point.

This script runs the Agentum agent with proper package imports.

Usage:
    python run_agent.py --task "Your task description" --dir /path/to/project
    
    # Or make executable and run directly:
    ./run_agent.py --task "List all files" --dir .
    
    # Alternative: run as module from Project directory:
    python -m src.core --task "Your task" --dir .
"""
import runpy
import sys
from pathlib import Path

if __name__ == "__main__":
    # Add the project directory to path for proper package resolution
    # This allows 'src' to be the top-level package with 'src.core' as subpackage
    project_dir = Path(__file__).parent.resolve()
    
    if str(project_dir) not in sys.path:
        sys.path.insert(0, str(project_dir))
    
    # Run the src.core package as a module
    runpy.run_module("src.core", run_name="__main__", alter_sys=True)

