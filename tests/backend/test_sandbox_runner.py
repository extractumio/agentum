"""
Tests for the agent-level bubblewrap sandbox runner.

These tests verify that the sandbox properly isolates the agent process
and that all restrictions are inherited by child processes.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core.sandbox_runner import (
    SandboxConfig,
    SandboxedAgentParams,
    SandboxedAgentResult,
    SandboxedAgentRunner,
    SandboxEnvironment,
    SandboxMount,
    create_sandbox_runner,
    get_default_sandbox_config,
)


class TestSandboxConfig:
    """Test SandboxConfig model."""

    def test_default_config(self) -> None:
        """Default config should have sensible defaults."""
        config = get_default_sandbox_config()
        
        assert config.enabled is True
        assert config.bwrap_path == "bwrap"
        assert config.unshare_pid is True
        assert config.unshare_ipc is True
        assert config.unshare_uts is True
        assert len(config.system_mounts) == 3  # usr, lib, bin

    def test_custom_config(self) -> None:
        """Custom config should override defaults."""
        config = SandboxConfig(
            enabled=False,
            bwrap_path="/custom/bwrap",
            tmpfs_size="256M",
        )
        
        assert config.enabled is False
        assert config.bwrap_path == "/custom/bwrap"
        assert config.tmpfs_size == "256M"

    def test_environment_config(self) -> None:
        """Environment config should be customizable."""
        env = SandboxEnvironment(
            home="/custom/home",
            path="/custom/bin",
            additional_vars={"MY_VAR": "value"},
        )
        config = SandboxConfig(environment=env)
        
        assert config.environment.home == "/custom/home"
        assert config.environment.path == "/custom/bin"
        assert config.environment.additional_vars["MY_VAR"] == "value"


class TestSandboxedAgentParams:
    """Test SandboxedAgentParams serialization."""

    def test_json_roundtrip(self) -> None:
        """Params should serialize and deserialize correctly."""
        params = SandboxedAgentParams(
            session_id="test-123",
            task="Test task",
            model="claude-sonnet-4-20250514",
            max_turns=50,
            timeout_seconds=300,
            enable_skills=True,
            role="developer",
        )
        
        json_str = params.to_json()
        restored = SandboxedAgentParams.from_json(json_str)
        
        assert restored.session_id == params.session_id
        assert restored.task == params.task
        assert restored.model == params.model
        assert restored.max_turns == params.max_turns
        assert restored.timeout_seconds == params.timeout_seconds
        assert restored.enable_skills == params.enable_skills
        assert restored.role == params.role

    def test_optional_fields(self) -> None:
        """Optional fields should have default values."""
        params = SandboxedAgentParams(
            session_id="test",
            task="task",
            model="model",
            max_turns=10,
        )
        
        assert params.system_prompt is None
        assert params.resume_id is None
        assert params.fork_session is False


class TestSandboxedAgentRunner:
    """Test SandboxedAgentRunner class."""

    @pytest.fixture
    def runner(self, tmp_path: Path) -> SandboxedAgentRunner:
        """Create a runner with test directories."""
        sessions_dir = tmp_path / "sessions"
        skills_dir = tmp_path / "skills"
        sessions_dir.mkdir()
        skills_dir.mkdir()
        
        return SandboxedAgentRunner(
            config=get_default_sandbox_config(),
            sessions_dir=sessions_dir,
            skills_dir=skills_dir,
            src_dir=tmp_path / "src",
        )

    @pytest.fixture
    def session_dir(self, tmp_path: Path) -> Path:
        """Create a test session directory."""
        session = tmp_path / "sessions" / "test-session"
        session.mkdir(parents=True)
        (session / "workspace").mkdir()
        (session / ".claude.json").write_text("{}")
        return session

    def test_build_bwrap_command(
        self,
        runner: SandboxedAgentRunner,
        session_dir: Path,
    ) -> None:
        """bwrap command should include all required flags."""
        params = SandboxedAgentParams(
            session_id="test",
            task="test task",
            model="claude-sonnet-4-20250514",
            max_turns=10,
        )
        
        cmd = runner.build_bwrap_command(session_dir, params)
        
        # Check command starts with bwrap
        assert cmd[0] == "bwrap"
        
        # Check namespace flags
        assert "--unshare-pid" in cmd
        assert "--unshare-ipc" in cmd
        assert "--unshare-uts" in cmd
        assert "--die-with-parent" in cmd
        assert "--new-session" in cmd
        
        # Check session mount
        assert "--bind" in cmd
        session_idx = cmd.index("--bind") + 1
        # Session dir should be mounted at /session
        assert "/session" in cmd
        
        # Check environment is cleared
        assert "--clearenv" in cmd
        
        # Check environment variables are set
        assert "--setenv" in cmd
        assert "HOME" in cmd
        assert "PATH" in cmd
        assert "CLAUDE_CONFIG_DIR" in cmd
        assert "SANDBOXED_AGENT_PARAMS" in cmd
        
        # Check working directory
        assert "--chdir" in cmd
        assert "/session/workspace" in cmd
        
        # Check command ends with python invocation
        assert "python3" in cmd or "python" in cmd
        assert "-m" in cmd
        assert "src.core.sandboxed_agent" in cmd

    def test_bwrap_not_available(
        self,
        runner: SandboxedAgentRunner,
        session_dir: Path,
    ) -> None:
        """Should return error if bwrap is not available."""
        runner._config.bwrap_path = "/nonexistent/bwrap"
        
        params = SandboxedAgentParams(
            session_id="test",
            task="test",
            model="model",
            max_turns=1,
        )
        
        result = asyncio.run(runner.run_agent(session_dir, params))
        
        assert result.success is False
        assert "not found" in result.error.lower()

    def test_sandbox_disabled(
        self,
        runner: SandboxedAgentRunner,
        session_dir: Path,
    ) -> None:
        """Should run without sandbox when disabled."""
        runner._config.enabled = False
        
        params = SandboxedAgentParams(
            session_id="test",
            task="test",
            model="model",
            max_turns=1,
        )
        
        # This will fail since sandboxed_agent.py depends on Claude SDK
        # but we're testing that it attempts unsandboxed execution
        with patch.object(runner, "_run_unsandboxed") as mock_unsandboxed:
            mock_unsandboxed.return_value = SandboxedAgentResult(
                success=True,
                exit_code=0,
                stdout="{}",
                stderr="",
            )
            
            result = asyncio.run(runner.run_agent(session_dir, params))
            
            mock_unsandboxed.assert_called_once()


class TestSandboxIsolation:
    """Integration tests for sandbox isolation.
    
    These tests verify that the sandbox properly restricts access.
    They require bwrap to be installed.
    """

    @pytest.fixture
    def can_run_bwrap(self) -> bool:
        """Check if bwrap is available."""
        import shutil
        return shutil.which("bwrap") is not None

    @pytest.mark.skipif(
        not os.path.exists("/usr/bin/bwrap"),
        reason="bwrap not installed"
    )
    def test_ps_only_shows_sandbox_processes(self, tmp_path: Path) -> None:
        """ps aux should only show sandbox processes, not host."""
        import subprocess
        
        # Create a test script
        script = tmp_path / "test.sh"
        script.write_text("#!/bin/bash\nps aux | wc -l")
        script.chmod(0o755)
        
        # Run without sandbox
        result_unsandboxed = subprocess.run(
            ["bash", "-c", "ps aux | wc -l"],
            capture_output=True,
            text=True,
        )
        unsandboxed_count = int(result_unsandboxed.stdout.strip())
        
        # Run with sandbox
        result_sandboxed = subprocess.run(
            [
                "bwrap",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--proc", "/proc",
                "--dev", "/dev",
                "--unshare-pid",
                "--",
                "bash", "-c", "ps aux | wc -l",
            ],
            capture_output=True,
            text=True,
        )
        sandboxed_count = int(result_sandboxed.stdout.strip())
        
        # Sandboxed should see far fewer processes
        assert sandboxed_count < unsandboxed_count
        assert sandboxed_count <= 5  # Just bwrap, bash, ps, etc.

    @pytest.mark.skipif(
        not os.path.exists("/usr/bin/bwrap"),
        reason="bwrap not installed"
    )
    def test_etc_passwd_not_accessible(self, tmp_path: Path) -> None:
        """/etc/passwd should not be accessible in sandbox."""
        import subprocess
        
        result = subprocess.run(
            [
                "bwrap",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--proc", "/proc",
                "--dev", "/dev",
                "--",
                "cat", "/etc/passwd",
            ],
            capture_output=True,
            text=True,
        )
        
        # Should fail because /etc is not mounted
        assert result.returncode != 0
        assert "No such file" in result.stderr or result.stdout == ""

    @pytest.mark.skipif(
        not os.path.exists("/usr/bin/bwrap"),
        reason="bwrap not installed"
    )
    def test_workspace_writable(self, tmp_path: Path) -> None:
        """Workspace should be writable in sandbox."""
        import subprocess
        
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        
        result = subprocess.run(
            [
                "bwrap",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--bind", str(workspace), "/workspace",
                "--proc", "/proc",
                "--dev", "/dev",
                "--chdir", "/workspace",
                "--",
                "bash", "-c", "echo test > test.txt && cat test.txt",
            ],
            capture_output=True,
            text=True,
        )
        
        assert result.returncode == 0
        assert "test" in result.stdout
        assert (workspace / "test.txt").exists()

    @pytest.mark.skipif(
        not os.path.exists("/usr/bin/bwrap"),
        reason="bwrap not installed"
    )
    def test_subprocess_inherits_restrictions(self, tmp_path: Path) -> None:
        """Subprocesses should inherit sandbox restrictions."""
        import subprocess
        
        # Create a nested script that tries to access /etc
        nested_script = tmp_path / "nested.sh"
        nested_script.write_text(
            "#!/bin/bash\n"
            "cat /etc/passwd 2>&1 || echo 'BLOCKED'\n"
        )
        nested_script.chmod(0o755)
        
        result = subprocess.run(
            [
                "bwrap",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", str(nested_script), "/nested.sh",
                "--proc", "/proc",
                "--dev", "/dev",
                "--unshare-pid",
                "--",
                "bash", "-c", "bash /nested.sh",
            ],
            capture_output=True,
            text=True,
        )
        
        # The nested script should also be blocked
        assert "BLOCKED" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
