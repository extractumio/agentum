"""
Test dynamically loaded dangerous command patterns.

Loads all patterns from YAML files and validates them against
their test-exploit-string to ensure proper blocking.
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.dangerous_patterns_loader import load_dangerous_patterns
from src.core.permissions import PermissionManager, create_permission_callback


class TestDangerousPatterns:
    """Test all dangerous patterns from YAML configuration."""
    
    @pytest.fixture(scope="class")
    def patterns_dir(self) -> Path:
        """Get the patterns directory."""
        return PROJECT_ROOT / "config" / "security" / "dangerous_patterns"
    
    @pytest.fixture(scope="class")
    def all_pattern_data(self, patterns_dir: Path) -> list[tuple[str, str, str]]:
        """
        Load all patterns with their test strings.
        Returns list of (file_name, pattern, test_exploit_string) tuples.
        """
        pattern_data = []
        yaml_files = sorted(patterns_dir.glob("*.yaml"))
        
        for yaml_file in yaml_files:
            with open(yaml_file, "r") as f:
                data = yaml.safe_load(f)
            
            if data and "patterns" in data:
                for item in data["patterns"]:
                    if isinstance(item, dict):
                        pattern = item.get("pattern")
                        test_string = item.get("test-exploit-string")
                        if pattern and test_string:
                            pattern_data.append((yaml_file.name, pattern, test_string))
        
        return pattern_data
    
    @pytest.fixture(scope="class")
    def loaded_patterns(self) -> list[str]:
        """Load all patterns."""
        return load_dangerous_patterns()
    
    @pytest.mark.unit
    def test_all_patterns_block_exploits(
        self,
        all_pattern_data: list[tuple[str, str, str]]
    ) -> None:
        """Test that each pattern correctly blocks its test-exploit-string."""
        failures = []
        
        for file_name, pattern, exploit_string in all_pattern_data:
            try:
                if not re.search(pattern, exploit_string, flags=re.IGNORECASE):
                    failures.append((file_name, pattern, exploit_string))
            except re.error as e:
                failures.append((file_name, pattern, f"REGEX ERROR: {e}"))
        
        if failures:
            msg = "\n🚨 PATTERN FAILURES:\n\n"
            for fname, pat, expl in failures[:10]:
                msg += f"  ❌ {fname}\n"
                msg += f"     Pattern: {pat[:60]}...\n"
                msg += f"     Test:    {expl[:60]}...\n\n"
            if len(failures) > 10:
                msg += f"  ... and {len(failures) - 10} more\n"
            
            pytest.fail(
                f"{msg}\n"
                f"Total: {len(failures)}/{len(all_pattern_data)} patterns failed!\n"
            )
        
        print(f"\n✓ All {len(all_pattern_data)} patterns correctly block their exploits")
    
    @pytest.mark.unit
    def test_patterns_loaded_count(self, loaded_patterns: list[str]) -> None:
        """Verify patterns are loaded."""
        assert len(loaded_patterns) >= 100, f"Expected 100+ patterns, got {len(loaded_patterns)}"
        print(f"\n✓ Loaded {len(loaded_patterns)} patterns")
    
    @pytest.mark.unit
    def test_all_yaml_files_valid(self, patterns_dir: Path) -> None:
        """Verify all YAML files are valid."""
        yaml_files = sorted(patterns_dir.glob("*.yaml"))
        invalid = []
        
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r") as f:
                    data = yaml.safe_load(f)
                
                if not data or "patterns" not in data:
                    invalid.append((yaml_file.name, "Missing 'patterns' key"))
                    continue
                
                for i, item in enumerate(data["patterns"]):
                    if not isinstance(item, dict):
                        invalid.append((yaml_file.name, f"Pattern {i} not a dict"))
                        continue
                    if "pattern" not in item:
                        invalid.append((yaml_file.name, f"Pattern {i} missing 'pattern'"))
                    if "test-exploit-string" not in item:
                        invalid.append((yaml_file.name, f"Pattern {i} missing 'test-exploit-string'"))
                
            except Exception as e:
                invalid.append((yaml_file.name, str(e)))
        
        if invalid:
            msg = "\n".join(f"  - {f}: {e}" for f, e in invalid)
            pytest.fail(f"Invalid YAML files:\n{msg}")
        
        print(f"\n✓ All {len(yaml_files)} YAML files are valid")


class TestPermissionCallback:
    """Test dangerous pattern blocking in permission callback."""
    
    @pytest.fixture
    def mock_permission_manager(self) -> MagicMock:
        """Create a mock permission manager that allows everything."""
        manager = MagicMock(spec=PermissionManager)
        manager.is_allowed.return_value = True
        return manager
    
    @pytest.fixture
    def mock_context(self) -> MagicMock:
        """Create a mock context."""
        return MagicMock()
    
    @pytest_asyncio.fixture
    async def permission_callback(self, mock_permission_manager):
        """Create a permission callback."""
        return create_permission_callback(
            permission_manager=mock_permission_manager,
            sandbox_executor=None,
        )
    
    @pytest.fixture(scope="class")
    def exploit_strings(self) -> list[str]:
        """Get all exploit strings from YAML files."""
        patterns_dir = PROJECT_ROOT / "config" / "security" / "dangerous_patterns"
        yaml_files = sorted(patterns_dir.glob("*.yaml"))
        exploits = []
        
        for yaml_file in yaml_files:
            with open(yaml_file, "r") as f:
                data = yaml.safe_load(f)
            
            if data and "patterns" in data:
                for item in data["patterns"]:
                    if isinstance(item, dict):
                        test_string = item.get("test-exploit-string")
                        if test_string:
                            exploits.append(test_string)
        
        return exploits[:20]  # Test first 20 for speed
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_exploits_denied_by_callback(
        self,
        permission_callback,
        mock_context,
        exploit_strings: list[str]
    ) -> None:
        """Test that exploit strings are denied by permission callback."""
        failures = []
        
        for exploit in exploit_strings:
            result = await permission_callback(
                "Bash",
                {"command": exploit},
                mock_context
            )
            
            if result.behavior != "deny":
                failures.append(exploit)
        
        if failures:
            msg = "\n".join(f"  - {e[:60]}..." for e in failures[:5])
            pytest.fail(
                f"Exploits not denied by callback:\n{msg}\n"
                f"Total: {len(failures)}/{len(exploit_strings)} not blocked"
            )
        
        print(f"\n✓ All {len(exploit_strings)} test exploits denied by callback")
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_safe_commands_allowed(
        self,
        permission_callback,
        mock_context
    ) -> None:
        """Test that safe commands are allowed."""
        safe_commands = [
            "ls -la",
            "cat file.txt",
            "python script.py",
            "git status",
            "npm test",
        ]
        
        for command in safe_commands:
            result = await permission_callback(
                "Bash",
                {"command": command},
                mock_context
            )
            
            assert result.behavior == "allow", f"Safe command blocked: {command}"
        
        print(f"\n✓ All {len(safe_commands)} safe commands allowed")
    
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_sandbox_bypass_blocked(
        self,
        permission_callback,
        mock_context
    ) -> None:
        """Test that sandbox bypass attempts are blocked."""
        result = await permission_callback(
            "Bash",
            {"command": "ls", "dangerouslyDisableSandbox": True},
            mock_context
        )
        
        assert result.behavior == "deny"
        assert result.interrupt is True
        print("\n✓ Sandbox bypass attempts blocked")
