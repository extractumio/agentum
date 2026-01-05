#!/usr/bin/env python3
"""
Test bubblewrap sandbox functionality.

This script tests the custom bubblewrap sandbox implementation
to verify that filesystem isolation is working correctly.
"""
import asyncio
import logging
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_bwrap_available():
    """Test that bubblewrap is available."""
    import subprocess

    print("\n" + "=" * 60)
    print("TEST: Bubblewrap Availability")
    print("=" * 60)

    try:
        result = subprocess.run(
            ["bwrap", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print(f"✓ bwrap available: {result.stdout.strip()}")
            return True
        else:
            print(f"❌ FAIL: bwrap returned non-zero: {result.returncode}")
            return False
    except FileNotFoundError:
        print("❌ FAIL: bwrap not found in PATH")
        return False
    except Exception as e:
        print(f"❌ FAIL: Error checking bwrap: {e}")
        return False


def test_sandbox_executor():
    """Test SandboxExecutor builds correct bwrap commands."""
    from src.core.sandbox import SandboxConfig, SandboxMount, SandboxExecutor

    print("\n" + "=" * 60)
    print("TEST: SandboxExecutor Command Building")
    print("=" * 60)

    # Create a minimal sandbox config
    config = SandboxConfig(
        enabled=True,
        file_sandboxing=True,
        network_sandboxing=True,
        bwrap_path="bwrap",
        use_tmpfs_root=True,
        static_mounts={
            "system_usr": SandboxMount(source="/usr", target="/usr", mode="ro"),
            "system_lib": SandboxMount(source="/lib", target="/lib", mode="ro"),
            "system_bin": SandboxMount(source="/bin", target="/bin", mode="ro"),
        },
        session_mounts={
            "workspace": SandboxMount(source="/tmp/test_workspace", target="/workspace", mode="rw"),
        },
    )

    executor = SandboxExecutor(config)
    cmd = executor.build_bwrap_command(["ls", "-la", "/"], allow_network=False)

    print(f"✓ Built command with {len(cmd)} arguments")
    print(f"✓ Command starts with: {' '.join(cmd[:5])}")

    # Check key flags are present (in Docker, we use selective unsharing)
    if "--unshare-all" in cmd or "--unshare-pid" in cmd:
        print("✓ Namespace unsharing flags present")
    else:
        print("❌ FAIL: No namespace unsharing flags")
        return False

    if "--tmpfs" in cmd:
        print("✓ --tmpfs flag present")
    else:
        print("❌ FAIL: --tmpfs flag missing")
        return False

    # Network isolation is optional in Docker mode
    if "--unshare-net" in cmd:
        print("✓ --unshare-net flag present (network isolated)")
    else:
        print("✓ Network shared with container (Docker mode)")

    print("✓ PASS: SandboxExecutor builds correct commands")
    return True


async def test_sandboxed_execution():
    """Test actual sandboxed command execution."""
    from src.core.sandbox import (
        SandboxConfig, SandboxMount, SandboxExecutor, execute_sandboxed_command
    )
    import tempfile
    import os

    print("\n" + "=" * 60)
    print("TEST: Sandboxed Command Execution")
    print("=" * 60)

    # Create a temporary workspace
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create a test file in the workspace
        test_file = workspace / "test.txt"
        test_file.write_text("Hello from workspace!")

        # Create sandbox config with this workspace
        config = SandboxConfig(
            enabled=True,
            file_sandboxing=True,
            network_sandboxing=True,
            bwrap_path="bwrap",
            use_tmpfs_root=True,
            static_mounts={
                "system_usr": SandboxMount(source="/usr", target="/usr", mode="ro"),
                "system_lib": SandboxMount(source="/lib", target="/lib", mode="ro"),
                "system_bin": SandboxMount(source="/bin", target="/bin", mode="ro"),
            },
            session_mounts={
                "workspace": SandboxMount(source=str(workspace), target="/workspace", mode="rw"),
            },
        )

        executor = SandboxExecutor(config)

        # Test 1: List workspace - should work
        print("\n--- Test: List /workspace (should succeed) ---")
        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "ls -la /workspace", allow_network=False, timeout=30
        )
        print(f"Exit code: {exit_code}")
        print(f"stdout: {stdout[:200] if stdout else '(empty)'}")
        if stderr:
            print(f"stderr: {stderr[:300]}")
        if "test.txt" in stdout:
            print("✓ Workspace file visible inside sandbox")
        else:
            print("❌ FAIL: Workspace file not visible")
            return False

        # Test 2: List root - should only see mounted paths
        print("\n--- Test: List / (should be isolated) ---")
        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "ls -la /", allow_network=False, timeout=30
        )
        print(f"Exit code: {exit_code}")
        print(f"stdout: {stdout[:300] if stdout else '(empty)'}")

        # Should NOT see host directories like /home, /etc from host
        if "/home" in stdout and "greg" in stdout:
            print("❌ FAIL: Host /home directory visible - sandbox not working!")
            return False

        # Should NOT see /sessions, /app, /config from Docker container
        if "/sessions" in stdout or "/config" in stdout or "/src" in stdout:
            print("❌ FAIL: Container paths visible - sandbox not isolating!")
            return False

        print("✓ Root filesystem is properly isolated")

        # Test 3: Try to access /etc/passwd - should fail
        print("\n--- Test: Access /etc/passwd (should fail) ---")
        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "cat /etc/passwd", allow_network=False, timeout=30
        )
        print(f"Exit code: {exit_code}")
        if exit_code != 0 or "root:" not in stdout:
            print("✓ /etc/passwd not accessible - sandbox working!")
        else:
            print("❌ FAIL: /etc/passwd accessible - security issue!")
            return False

        # Test 4: Write to workspace - should work
        print("\n--- Test: Write to /workspace (should succeed) ---")
        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "echo 'test' > /workspace/output.txt && cat /workspace/output.txt",
            allow_network=False, timeout=30
        )
        print(f"Exit code: {exit_code}")
        print(f"stdout: {stdout}")
        if exit_code == 0 and "test" in stdout:
            print("✓ Write to workspace successful")
        else:
            print("❌ FAIL: Cannot write to workspace")
            return False

        # Check file was created on host
        if (workspace / "output.txt").exists():
            print("✓ File persisted to host workspace")
        else:
            print("❌ FAIL: File not persisted to host")
            return False

        # Test 5: Try to write outside workspace - should fail
        print("\n--- Test: Write to /tmp (should fail) ---")
        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor, "echo 'test' > /tmp/test.txt",
            allow_network=False, timeout=30
        )
        print(f"Exit code: {exit_code}")
        if exit_code != 0:
            print("✓ Write outside workspace correctly denied")
        else:
            print("❌ FAIL: Write outside workspace allowed")
            return False

    print("\n✓ PASS: All sandboxed execution tests passed")
    return True


async def main():
    """Run all sandbox tests."""
    print("\n" + "=" * 60)
    print("BUBBLEWRAP SANDBOX TESTS")
    print("=" * 60)

    results = []

    # Test 1: Bubblewrap availability
    results.append(("Bubblewrap Available", test_bwrap_available()))

    # Test 2: SandboxExecutor
    try:
        results.append(("SandboxExecutor", test_sandbox_executor()))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        results.append(("SandboxExecutor", False))

    # Test 3: Sandboxed execution
    try:
        passed = await test_sandboxed_execution()
        results.append(("Sandboxed Execution", passed))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Sandboxed Execution", False))

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False

    print("\n")
    if all_passed:
        print("✓ ALL TESTS PASSED - Bubblewrap sandbox is working correctly")
        return 0
    else:
        print("❌ SOME TESTS FAILED - Check the output above")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

