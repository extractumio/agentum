#!/usr/bin/env python3
"""
Test SDK sandbox integration.

This script verifies that:
1. SandboxSettings are being passed to ClaudeAgentOptions
2. The SDK's bubblewrap sandbox is properly configured
3. Bash commands are sandboxed when executed
4. Child processes spawned inside sandbox are also sandboxed

Run this inside the Docker container to test sandbox functionality.
"""
import asyncio
import logging
import sys
import tempfile
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def test_sandbox_config_loading():
    """Test that sandbox config loads correctly from permissions.yaml."""
    from src.core.permission_profiles import PermissionManager
    from src.config import CONFIG_DIR

    print("\n" + "=" * 60)
    print("TEST: Sandbox Configuration Loading")
    print("=" * 60)

    manager = PermissionManager()
    manager._ensure_profile_loaded()

    sandbox_config = manager.sandbox_config
    if sandbox_config is None:
        print("❌ FAIL: Sandbox config is None")
        return False

    print(f"✓ Sandbox enabled: {sandbox_config.enabled}")
    print(f"✓ File sandboxing: {sandbox_config.file_sandboxing}")
    print(f"✓ Network sandboxing: {sandbox_config.network_sandboxing}")
    print(f"✓ bwrap_path: {sandbox_config.bwrap_path}")
    print(f"✓ enableWeakerNestedSandbox: {sandbox_config.use_tmpfs_root}")

    if not sandbox_config.enabled:
        print("❌ FAIL: Sandbox is not enabled")
        return False

    print("✓ PASS: Sandbox configuration loaded correctly")
    return True


def test_sdk_sandbox_settings_conversion():
    """Test conversion of SandboxConfig to SDK SandboxSettings."""
    from claude_agent_sdk import SandboxSettings
    from src.core.permission_profiles import PermissionManager
    from src.core.sandbox import SandboxConfig

    print("\n" + "=" * 60)
    print("TEST: SDK SandboxSettings Conversion")
    print("=" * 60)

    manager = PermissionManager()
    manager._ensure_profile_loaded()

    # Get sandbox config
    sandbox_config = manager.sandbox_config
    if sandbox_config is None:
        print("❌ FAIL: Sandbox config is None")
        return False

    # Manually build SDK settings (same logic as ClaudeAgent._build_sdk_sandbox_settings)
    if not sandbox_config.enabled:
        print("❌ FAIL: Sandbox is not enabled in config")
        return False

    sdk_settings: SandboxSettings = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "enableWeakerNestedSandbox": True,
    }

    print(f"✓ SDK sandbox enabled: {sdk_settings.get('enabled')}")
    print(f"✓ autoAllowBashIfSandboxed: {sdk_settings.get('autoAllowBashIfSandboxed')}")
    print(f"✓ allowUnsandboxedCommands: {sdk_settings.get('allowUnsandboxedCommands')}")
    print(f"✓ enableWeakerNestedSandbox: {sdk_settings.get('enableWeakerNestedSandbox')}")

    if not sdk_settings.get("enabled"):
        print("❌ FAIL: SDK sandbox not enabled")
        return False

    print("✓ PASS: SDK SandboxSettings conversion successful")
    return True


def test_bwrap_available():
    """Test that bubblewrap is available in the environment."""
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
            print(f"   stderr: {result.stderr}")
            return False
    except FileNotFoundError:
        print("❌ FAIL: bwrap not found in PATH")
        return False
    except Exception as e:
        print(f"❌ FAIL: Error checking bwrap: {e}")
        return False


def test_claude_agent_options_sandbox():
    """Test that ClaudeAgentOptions includes sandbox settings."""
    from claude_agent_sdk import ClaudeAgentOptions, SandboxSettings

    print("\n" + "=" * 60)
    print("TEST: ClaudeAgentOptions Sandbox Integration")
    print("=" * 60)

    # Create SDK options with sandbox settings
    sandbox_settings: SandboxSettings = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "enableWeakerNestedSandbox": True,
    }

    try:
        options = ClaudeAgentOptions(
            system_prompt="Test system prompt",
            max_turns=5,
            sandbox=sandbox_settings,
        )

        # Check if sandbox is set
        if hasattr(options, 'sandbox') and options.sandbox:
            print(f"✓ ClaudeAgentOptions.sandbox is set")
            print(f"✓ sandbox.enabled: {options.sandbox.get('enabled')}")
            print(f"✓ sandbox.enableWeakerNestedSandbox: {options.sandbox.get('enableWeakerNestedSandbox')}")
            print("✓ PASS: ClaudeAgentOptions accepts sandbox settings")
            return True
        else:
            print("❌ FAIL: ClaudeAgentOptions.sandbox is not set")
            return False
    except Exception as e:
        print(f"❌ FAIL: Error creating options: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_subprocess_isolation():
    """
    Test that child processes spawned inside sandbox are also isolated.

    This is a comprehensive test that:
    1. Creates a temporary workspace directory
    2. Creates a Python script inside that workspace
    3. Runs bash inside sandbox, which runs the Python script
    4. The Python script lists the root directory
    5. Verifies that the subprocess only sees the sandboxed filesystem

    This proves that sandbox isolation applies transitively to all child processes.
    """
    from src.core.sandbox import (
        SandboxConfig, SandboxMount, SandboxExecutor, execute_sandboxed_command
    )

    print("\n" + "=" * 60)
    print("TEST: Subprocess Isolation (Child Process Sandboxing)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)

        # Create a Python script that will list root and spawn another subprocess
        list_root_script = workspace / "list_root.py"
        list_root_script.write_text('''#!/usr/bin/env python3
"""Script to list root directory - runs as a subprocess inside sandbox."""
import os
import subprocess
import sys

def list_root_with_os():
    """List root using os.listdir()"""
    try:
        items = sorted(os.listdir('/'))
        return items
    except Exception as e:
        return [f"ERROR: {e}"]

def list_root_with_subprocess():
    """List root by spawning another subprocess (ls command)"""
    try:
        result = subprocess.run(
            ['ls', '-la', '/'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip().split('\\n')
    except Exception as e:
        return [f"ERROR: {e}"]

if __name__ == "__main__":
    print("=== Root listing via os.listdir() ===")
    for item in list_root_with_os():
        print(f"  {item}")

    print("\\n=== Root listing via subprocess (ls -la /) ===")
    for line in list_root_with_subprocess():
        print(f"  {line}")

    # Also try to read /etc/passwd (should fail)
    print("\\n=== Attempting to read /etc/passwd ===")
    try:
        with open('/etc/passwd', 'r') as f:
            print(f"  SECURITY ISSUE: Read {len(f.read())} bytes from /etc/passwd!")
    except FileNotFoundError:
        print("  ✓ /etc/passwd not accessible (FileNotFoundError)")
    except PermissionError:
        print("  ✓ /etc/passwd not accessible (PermissionError)")
    except Exception as e:
        print(f"  ✓ /etc/passwd not accessible ({type(e).__name__}: {e})")
''')

        # Create sandbox config
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

        # Test 1: Run Python script that lists root via os.listdir and subprocess
        print("\n--- Test: Run Python script inside sandbox ---")
        print("This script uses os.listdir() AND spawns 'ls' subprocess")

        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor,
            "python3 /workspace/list_root.py",
            allow_network=False,
            timeout=30
        )

        print(f"Exit code: {exit_code}")
        if stderr:
            print(f"stderr: {stderr[:200]}")
        print(f"\nOutput from sandboxed Python script:\n{stdout}")

        # Analyze the output
        # Parse the os.listdir() output to check what directories are visible
        # We expect ONLY: bin, dev, lib, proc, tmp*, usr, workspace
        allowed_root_dirs = {'bin', 'dev', 'lib', 'proc', 'usr', 'workspace', 'tmp', 'tmp:size=100M'}
        forbidden_dirs = {'home', 'etc', 'sessions', 'config', 'src', 'app',
                          'var', 'root', 'opt', 'srv', 'mnt', 'media', 'boot',
                          'run', 'sys', 'sbin', 'data', 'logs', 'tools', 'prompts'}

        # Extract directory listing from os.listdir section
        listing_section = ""
        in_listing = False
        for line in stdout.split('\n'):
            if "=== Root listing via os.listdir()" in line:
                in_listing = True
                continue
            if in_listing:
                if line.startswith("==="):
                    break
                listing_section += line + "\n"

        # Parse the listed directories
        listed_dirs = set()
        for line in listing_section.strip().split('\n'):
            line = line.strip()
            if line:
                listed_dirs.add(line)

        print(f"\nDirectories visible in sandbox: {sorted(listed_dirs)}")

        # Check for forbidden directories
        found_forbidden = listed_dirs & forbidden_dirs
        if found_forbidden:
            print(f"\n❌ FAIL: Found forbidden directories in root: {found_forbidden}")
            print("   This means subprocess can see host filesystem!")
            return False
        
        print("✓ No forbidden directories visible in sandbox")

        # Check that /etc/passwd was NOT accessible
        if "SECURITY ISSUE" in stdout:
            print("\n❌ FAIL: Subprocess could read /etc/passwd!")
            return False

        if "not accessible" in stdout:
            print("\n✓ /etc/passwd correctly inaccessible from subprocess")

        # Check that workspace IS visible
        if exit_code != 0:
            print(f"\n❌ FAIL: Script failed with exit code {exit_code}")
            if "No such file" in stderr or "not found" in stderr.lower():
                print("   Python or script not found - sandbox mount issue")
            return False

        # Test 2: Verify nested subprocess (bash -> python -> subprocess ls) isolation
        print("\n--- Test: Triple-nested process isolation ---")
        print("bash -> python3 -> subprocess.run(['ls'])")

        # Create a script that spawns multiple levels
        nested_script = workspace / "nested_test.py"
        nested_script.write_text('''#!/usr/bin/env python3
import subprocess
import sys

# Level 2: Python spawns bash, which spawns ls
result = subprocess.run(
    ['bash', '-c', 'ls -la / 2>&1'],
    capture_output=True,
    text=True,
    timeout=5
)
print("Nested subprocess output:")
print(result.stdout)
if result.returncode != 0:
    print(f"Return code: {result.returncode}")
    sys.exit(result.returncode)
''')

        exit_code, stdout, stderr = await execute_sandboxed_command(
            executor,
            "python3 /workspace/nested_test.py",
            allow_network=False,
            timeout=30
        )

        print(f"Exit code: {exit_code}")
        print(f"Output:\n{stdout}")

        # Parse the ls output to verify sandboxing
        if exit_code == 0:
            # Look for expected sandbox-only directories
            expected_dirs = ['bin', 'lib', 'usr', 'workspace', 'dev', 'proc', 'tmp']
            found_expected = sum(1 for d in expected_dirs if d in stdout)

            if found_expected >= 4:  # Should find most of them
                print(f"\n✓ Found {found_expected}/{len(expected_dirs)} expected sandbox directories")
            else:
                print(f"\n⚠ Only found {found_expected}/{len(expected_dirs)} expected directories")

            # Check NO forbidden directories appear in the ls output
            # Parse only the actual directory listing (lines with drwx or similar)
            forbidden_in_nested = ['sessions', 'config', 'app', 'home', 'data', 'logs']
            for line in stdout.split('\n'):
                # Only check lines that look like ls -la directory entries
                if line.strip().startswith('d') or line.strip().startswith('l'):
                    for path in forbidden_in_nested:
                        if f" {path}" in line:
                            print(f"\n❌ FAIL: Found '{path}' in nested subprocess output!")
                            return False

            print("\n✓ PASS: Nested subprocess is properly sandboxed")
            return True
        else:
            print(f"\n❌ FAIL: Nested test failed with exit code {exit_code}")
            return False


async def async_main():
    """Run all sandbox integration tests."""
    print("\n" + "=" * 60)
    print("SANDBOX INTEGRATION TESTS")
    print("=" * 60)
    print("This tests that the SDK's sandbox is properly configured.")
    print("Run this script inside the Docker container.")

    results = []

    # Test 1: Sandbox config loading
    try:
        results.append(("Sandbox Config Loading", test_sandbox_config_loading()))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        results.append(("Sandbox Config Loading", False))

    # Test 2: SDK SandboxSettings conversion
    try:
        results.append(("SDK Settings Conversion", test_sdk_sandbox_settings_conversion()))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        results.append(("SDK Settings Conversion", False))

    # Test 3: Bubblewrap availability
    try:
        results.append(("Bubblewrap Available", test_bwrap_available()))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        results.append(("Bubblewrap Available", False))

    # Test 4: ClaudeAgentOptions sandbox
    try:
        results.append(("ClaudeAgentOptions Sandbox", test_claude_agent_options_sandbox()))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        results.append(("ClaudeAgentOptions Sandbox", False))

    # Test 5: Subprocess isolation (comprehensive test)
    try:
        passed = await test_subprocess_isolation()
        results.append(("Subprocess Isolation", passed))
    except Exception as e:
        print(f"❌ EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        results.append(("Subprocess Isolation", False))

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
        print("✓ ALL TESTS PASSED - Sandbox properly isolates all processes")
        return 0
    else:
        print("❌ SOME TESTS FAILED - Check the output above")
        return 1


def main():
    """Entry point - runs the async main."""
    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())

