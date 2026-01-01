#!/usr/bin/env python3
"""
Core integration tests for the Agentum agent.

Tests that the agent can execute a simple task successfully
and produces valid output.yaml and agent.jsonl files.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest
import yaml


# Path configuration
TESTS_DIR: Path = Path(__file__).parent
INPUT_DIR: Path = TESTS_DIR / "input"
AGENT_DIR: Path = TESTS_DIR.parent.parent
AGENT_PY: Path = AGENT_DIR / "agent.py"
SESSIONS_DIR: Path = AGENT_DIR / "sessions"


@pytest.fixture
def task_file() -> Path:
    """Path to the test task file."""
    return INPUT_DIR / "task.md"


@pytest.fixture
def user_profile() -> Path:
    """Path to the permissive user profile for testing."""
    return INPUT_DIR / "permissions.user.permissive.yaml"


def find_latest_session_dir() -> Optional[Path]:
    """
    Find the most recently created session directory.
    
    Returns:
        Path to the latest session directory, or None if not found.
    """
    if not SESSIONS_DIR.exists():
        return None
    
    session_dirs = [
        d for d in SESSIONS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]
    
    if not session_dirs:
        return None
    
    # Sort by directory name (contains timestamp)
    session_dirs.sort(key=lambda d: d.name, reverse=True)
    return session_dirs[0]


class TestAgentCore:
    """Core agent integration tests."""

    @pytest.mark.integration
    def test_agent_executes_task_successfully(
        self,
        task_file: Path,
        user_profile: Path,
    ) -> None:
        """
        Test that the agent can execute a task and produce valid output.
        
        This test:
        1. Runs the agent with the test task and permissive profile
        2. Verifies output.yaml contains status=COMPLETE and non-empty output
        3. Verifies agent.jsonl last record has valid subtype, is_error=false, non-empty result
        """
        # Verify test files exist
        assert task_file.exists(), f"Task file not found: {task_file}"
        assert user_profile.exists(), f"User profile not found: {user_profile}"

        # Run the agent
        cmd = [
            sys.executable,
            str(AGENT_PY),
            "--task-file", str(task_file),
            "--user-profile", str(user_profile),
            "--timeout", "120",
            "--max-turns", "20",
        ]

        env = os.environ.copy()
        # Ensure we have the API key (check common variable names)
        if "ANTHROPIC_API_KEY" not in env:
            # Check for alternative key names
            alt_key = env.get("CLOUDLINUX_ANTHROPIC_API_KEY")
            if alt_key:
                env["ANTHROPIC_API_KEY"] = alt_key
        
        assert "ANTHROPIC_API_KEY" in env, (
            "ANTHROPIC_API_KEY or CLOUDLINUX_ANTHROPIC_API_KEY "
            "environment variable must be set"
        )

        result = subprocess.run(
            cmd,
            cwd=str(AGENT_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,  # 3 minute timeout for the test
        )

        # Log output for debugging
        print(f"STDOUT:\n{result.stdout}")
        print(f"STDERR:\n{result.stderr}")

        # Agent should exit with code 0 for COMPLETE status
        assert result.returncode == 0, (
            f"Agent exited with code {result.returncode}\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )

        # Find the session directory
        session_dir = find_latest_session_dir()
        assert session_dir is not None, "No session directory found"

        # Verify output.yaml
        output_yaml_path = session_dir / "workspace" / "output.yaml"
        assert output_yaml_path.exists(), (
            f"output.yaml not found in session workspace: {output_yaml_path}"
        )

        with open(output_yaml_path, "r") as f:
            output_data = yaml.safe_load(f)

        assert output_data is not None, "output.yaml is empty or invalid YAML"
        assert output_data.get("status") == "COMPLETE", (
            f"Expected status=COMPLETE, got: {output_data.get('status')}"
        )
        assert output_data.get("output"), (
            f"Expected non-empty output field, got: {output_data.get('output')!r}"
        )

        # Verify agent.jsonl
        agent_jsonl_path = session_dir / "agent.jsonl"
        assert agent_jsonl_path.exists(), (
            f"agent.jsonl not found in session: {agent_jsonl_path}"
        )

        # Read all lines and get the last record
        with open(agent_jsonl_path, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) > 0, "agent.jsonl is empty"

        # Parse the last record
        last_record = json.loads(lines[-1])

        # Verify last record has valid subtype
        assert "subtype" in last_record, (
            f"Last record missing 'subtype' field: {last_record}"
        )
        subtype = last_record.get("subtype")
        # Valid subtypes for final record include "success", "result", etc.
        assert subtype is not None and subtype != "", (
            f"Expected non-empty subtype, got: {subtype!r}"
        )

        # Verify is_error is false
        is_error = last_record.get("is_error")
        assert is_error is False, (
            f"Expected is_error=False, got: {is_error!r}"
        )

        # Verify result is non-empty
        result_field = last_record.get("result")
        assert result_field is not None and result_field != "", (
            f"Expected non-empty result, got: {result_field!r}"
        )

        print(f"\n✓ Session completed successfully: {session_dir.name}")
        print(f"  Status: {output_data.get('status')}")
        print(f"  Output: {output_data.get('output')[:100]}...")
        print(f"  Subtype: {subtype}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

