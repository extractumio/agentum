"""
Dynamic loader for dangerous command patterns.

Loads security patterns from YAML configuration files in the
config/security/dangerous_patterns directory. This allows for
easy customization and updates to security policies without
code changes.

SECURITY: This loader will FAIL if patterns cannot be loaded.
No fallback patterns are used to ensure explicit configuration.
"""
import logging
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


class PatternsLoadError(Exception):
    """Raised when dangerous patterns cannot be loaded."""
    pass


class DangerousPatternsLoader:
    """
    Loads and caches dangerous command patterns from YAML configuration.
    
    This class provides a singleton-like interface for loading security
    patterns from the config/security/dangerous_patterns directory.
    """
    
    _instance: Optional["DangerousPatternsLoader"] = None
    _cached_patterns: Optional[list[str]] = None
    
    def __init__(self, config_dir: Optional[Path] = None) -> None:
        """
        Initialize the patterns loader.
        
        Args:
            config_dir: Base config directory. Defaults to Project/config.
        """
        if config_dir is None:
            # Default to Project/config directory
            self.config_dir = Path(__file__).parent.parent.parent / "config"
        else:
            self.config_dir = Path(config_dir)
        
        self.patterns_dir = self.config_dir / "security" / "dangerous_patterns"
    
    @classmethod
    def get_instance(cls, config_dir: Optional[Path] = None) -> "DangerousPatternsLoader":
        """
        Get singleton instance of the loader.
        
        Args:
            config_dir: Optional config directory for first initialization.
            
        Returns:
            Singleton loader instance.
        """
        if cls._instance is None:
            cls._instance = cls(config_dir=config_dir)
        return cls._instance
    
    def load_patterns(self, force_reload: bool = False) -> list[str]:
        """
        Load dangerous command patterns from YAML files.
        
        Args:
            force_reload: If True, bypass cache and reload from files.
            
        Returns:
            List of regex patterns for dangerous commands.
            
        Raises:
            PatternsLoadError: If patterns cannot be loaded (no fallback).
        """
        # Return cached patterns if available
        if not force_reload and self._cached_patterns is not None:
            return self._cached_patterns
        
        patterns: list[str] = []
        errors: list[str] = []
        
        # Check if patterns directory exists
        if not self.patterns_dir.exists():
            raise PatternsLoadError(
                f"SECURITY ERROR: Patterns directory not found: {self.patterns_dir}. "
                "Cannot start without security patterns. "
                "Ensure config/security/dangerous_patterns directory exists."
            )
        
        # Load all YAML files in order
        yaml_files = sorted(self.patterns_dir.glob("*.yaml"))
        
        if not yaml_files:
            raise PatternsLoadError(
                f"SECURITY ERROR: No pattern files found in {self.patterns_dir}. "
                "Cannot start without security patterns. "
                "Add YAML pattern files to config/security/dangerous_patterns."
            )
        
        logger.info(f"Loading dangerous patterns from {len(yaml_files)} files")
        
        for yaml_file in yaml_files:
            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                
                if not data or "patterns" not in data:
                    errors.append(f"{yaml_file.name}: Invalid format (missing 'patterns' key)")
                    continue
                
                # Extract patterns from the file
                file_patterns = data["patterns"]
                for item in file_patterns:
                    if isinstance(item, dict) and "pattern" in item:
                        patterns.append(item["pattern"])
                    elif isinstance(item, str):
                        # Support simple string format too
                        patterns.append(item)
                
                logger.debug(
                    f"Loaded {len(file_patterns)} patterns from {yaml_file.name}"
                )
                
            except Exception as e:
                errors.append(f"{yaml_file.name}: {e}")
                continue
        
        # Report any errors encountered
        if errors:
            logger.warning(f"Errors loading pattern files: {errors}")
        
        if not patterns:
            error_details = "; ".join(errors) if errors else "unknown reason"
            raise PatternsLoadError(
                f"SECURITY ERROR: No patterns loaded from config files. "
                f"Cannot start without security patterns. Errors: {error_details}"
            )
        
        logger.info(f"Successfully loaded {len(patterns)} dangerous patterns")
        
        # Cache the patterns
        self._cached_patterns = patterns
        return patterns
    
    def get_patterns_by_file(self, filename: str) -> list[str]:
        """
        Load patterns from a specific file.
        
        Args:
            filename: Name of the YAML file (e.g., "01-destructive-filesystem.yaml")
            
        Returns:
            List of patterns from that file.
        """
        yaml_file = self.patterns_dir / filename
        
        if not yaml_file.exists():
            logger.warning(f"Pattern file not found: {filename}")
            return []
        
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            
            if not data or "patterns" not in data:
                logger.warning(f"Invalid format in {filename}")
                return []
            
            patterns: list[str] = []
            for item in data["patterns"]:
                if isinstance(item, dict) and "pattern" in item:
                    patterns.append(item["pattern"])
                elif isinstance(item, str):
                    patterns.append(item)
            
            return patterns
            
        except Exception as e:
            logger.error(f"Failed to load {filename}: {e}")
            return []
    
    def reload(self) -> list[str]:
        """
        Force reload patterns from disk.
        
        Returns:
            Newly loaded patterns.
        """
        return self.load_patterns(force_reload=True)
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cached patterns (useful for testing)."""
        if cls._instance:
            cls._instance._cached_patterns = None


def load_dangerous_patterns(config_dir: Optional[Path] = None) -> list[str]:
    """
    Convenience function to load dangerous patterns.
    
    Args:
        config_dir: Optional config directory path.
        
    Returns:
        List of regex patterns for dangerous commands.
        
    Raises:
        PatternsLoadError: If patterns cannot be loaded.
    """
    loader = DangerousPatternsLoader.get_instance(config_dir=config_dir)
    return loader.load_patterns()


def get_patterns_loader(config_dir: Optional[Path] = None) -> DangerousPatternsLoader:
    """
    Get the patterns loader instance.
    
    Args:
        config_dir: Optional config directory path.
        
    Returns:
        DangerousPatternsLoader instance.
    """
    return DangerousPatternsLoader.get_instance(config_dir=config_dir)
