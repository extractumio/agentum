"""
Skills management for Agentum.

Handles loading, parsing, and executing skills from the skills/ folder.
Skills are markdown files with YAML frontmatter that provide instructions
and optional scripts for the agent to execute.

Skill file format:
    ---
    name: skill-name
    description: Brief description of what the skill does.
    ---
    
    # Skill Title
    
    ## Instructions
    ...
"""
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Import paths from central config
from ..config import SKILLS_DIR
from .exceptions import SkillError

logger = logging.getLogger(__name__)

# Constants for text truncation limits
DESCRIPTION_PREVIEW_LENGTH = 50
BODY_PREVIEW_LENGTH = 200


def _parse_skill_frontmatter(content: str) -> tuple[str, str, str]:
    """
    Parse YAML frontmatter from a skill markdown file.
    
    Expected format:
        ---
        name: skill-name
        description: Brief description of what the skill does.
        ---
        
        # Skill Title
        ...
    
    Args:
        content: Full markdown content of the skill file.
    
    Returns:
        Tuple of (name, description, body).
        - name: Skill name from frontmatter
        - description: Brief description from frontmatter
        - body: The markdown content after frontmatter
    """
    name = ""
    description = ""
    body = content
    
    # Check for YAML frontmatter (starts with ---)
    if content.startswith("---"):
        # Find the closing ---
        end_match = re.search(r"\n---\s*\n", content[3:])
        if end_match:
            frontmatter_text = content[3:end_match.start() + 3]
            body = content[end_match.end() + 3:].strip()
            
            try:
                frontmatter = yaml.safe_load(frontmatter_text)
                if isinstance(frontmatter, dict):
                    name = frontmatter.get("name", "")
                    description = frontmatter.get("description", "")
            except yaml.YAMLError as e:
                logger.warning(f"Failed to parse skill frontmatter: {e}")
    
    return name, description, body


@dataclass
class Skill:
    """
    Represents a loaded skill.
    
    Skills have YAML frontmatter with name and description,
    followed by the full instructions body.
    
    Attributes:
        name: The skill name (from frontmatter or folder name).
        description_file: Path to the skill's markdown file.
        script_file: Optional path to the skill's script file.
        description: Brief description (from frontmatter).
        body: The markdown content after frontmatter (full instructions).
        content: The full raw markdown content including frontmatter.
    """
    name: str
    description_file: Path
    script_file: Optional[Path] = None
    description: str = ""
    body: str = ""
    content: str = ""
    metadata: dict = field(default_factory=dict)


class SkillManager:
    """
    Manages skills loading and execution.
    
    Skills are organized as folders in the skills/ directory:
    - skills/
      - skill_name.md/
        - skill_name.md  (description/instructions)
        - skill_name.py  (optional script)
    """
    
    SUPPORTED_SCRIPT_EXTENSIONS = [".py", ".sh", ".bash"]
    
    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        """
        Initialize the skill manager.
        
        Args:
            skills_dir: Directory containing skills. Defaults to AGENT/skills.
        """
        self._skills_dir = skills_dir or SKILLS_DIR
        self._loaded_skills: dict[str, Skill] = {}
    
    @property
    def skills_dir(self) -> Path:
        """Get the skills directory."""
        return self._skills_dir
    
    def list_skills(self) -> list[str]:
        """
        List all available skill names.
        
        Returns:
            List of skill names.
        """
        if not self._skills_dir.exists():
            return []
        
        skills = []
        for item in self._skills_dir.iterdir():
            if item.is_dir():
                # Extract skill name (remove .md suffix if present)
                skill_name = item.name
                if skill_name.endswith(".md"):
                    skill_name = skill_name[:-3]
                skills.append(skill_name)
        
        return sorted(skills)
    
    def _find_skill_dir(self, skill_name: str) -> Optional[Path]:
        """
        Find the skill directory for a given skill name.
        
        Args:
            skill_name: Name of the skill to find.
        
        Returns:
            Path to the skill directory, or None if not found.
        """
        # Try exact match first (e.g., "test.md" folder)
        skill_dir = self._skills_dir / f"{skill_name}.md"
        if skill_dir.is_dir():
            return skill_dir
        
        # Try without .md suffix
        skill_dir = self._skills_dir / skill_name
        if skill_dir.is_dir():
            return skill_dir
        
        return None
    
    def load_skill(self, skill_name: str) -> Skill:
        """
        Load a skill by name.
        
        Args:
            skill_name: Name of the skill to load.
        
        Returns:
            Loaded Skill object.
        
        Raises:
            SkillError: If the skill cannot be found or loaded.
        """
        # Check cache
        if skill_name in self._loaded_skills:
            return self._loaded_skills[skill_name]
        
        skill_dir = self._find_skill_dir(skill_name)
        if skill_dir is None:
            available = self.list_skills()
            raise SkillError(
                f"Skill '{skill_name}' not found. "
                f"Available skills: {available}"
            )
        
        # Find the markdown description file
        description_file = None
        for md_file in skill_dir.glob("*.md"):
            description_file = md_file
            break
        
        if description_file is None:
            raise SkillError(
                f"No markdown file found in skill directory: {skill_dir}"
            )
        
        # Find optional script file anywhere in skill directory (recursive)
        script_file = None
        
        # First, look for exact name match recursively
        for ext in self.SUPPORTED_SCRIPT_EXTENSIONS:
            matches = list(skill_dir.rglob(f"{skill_name}{ext}"))
            if matches:
                script_file = matches[0]
                break
        
        # If no exact match, find any script file recursively
        if script_file is None:
            for ext in self.SUPPORTED_SCRIPT_EXTENSIONS:
                matches = list(skill_dir.rglob(f"*{ext}"))
                if matches:
                    script_file = matches[0]
                    break
        
        # Read the markdown content
        try:
            content = description_file.read_text(encoding="utf-8")
        except IOError as e:
            raise SkillError(f"Failed to read skill file: {e}")
        
        # Parse frontmatter for name and description
        parsed_name, description, body = _parse_skill_frontmatter(content)
        
        # Use frontmatter name if available, otherwise folder name
        final_name = parsed_name if parsed_name else skill_name
        
        skill = Skill(
            name=final_name,
            description_file=description_file,
            script_file=script_file,
            description=description,
            body=body,
            content=content,
        )
        
        self._loaded_skills[skill_name] = skill
        logger.info(f"Loaded skill: {final_name} - {description[:DESCRIPTION_PREVIEW_LENGTH]}...")
        
        return skill
    
    def load_all_skills(self) -> dict[str, Skill]:
        """
        Load all available skills.
        
        Returns:
            Dictionary of skill name to Skill object.
        """
        for skill_name in self.list_skills():
            try:
                self.load_skill(skill_name)
            except SkillError as e:
                logger.warning(f"Failed to load skill '{skill_name}': {e}")
        
        return self._loaded_skills.copy()
    
    def run_skill_script(
        self,
        skill_name: str,
        args: Optional[list[str]] = None,
        cwd: Optional[Path] = None,
        timeout: int = 300
    ) -> tuple[int, str, str]:
        """
        Run a skill's associated script.
        
        Args:
            skill_name: Name of the skill.
            args: Optional arguments to pass to the script.
            cwd: Working directory for the script.
            timeout: Timeout in seconds (default 5 minutes).
        
        Returns:
            Tuple of (return_code, stdout, stderr).
        
        Raises:
            SkillError: If the skill has no script or execution fails.
        """
        skill = self.load_skill(skill_name)
        
        if skill.script_file is None:
            raise SkillError(f"Skill '{skill_name}' has no associated script")
        
        script_path = skill.script_file
        cmd: list[str] = []
        
        if script_path.suffix == ".py":
            cmd = [sys.executable, str(script_path)]
        elif script_path.suffix in [".sh", ".bash"]:
            cmd = ["bash", str(script_path)]
        else:
            cmd = [str(script_path)]
        
        if args:
            cmd.extend(args)
        
        logger.info(f"Running skill script: {' '.join(cmd)}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd or script_path.parent,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            raise SkillError(
                f"Skill script timed out after {timeout} seconds"
            )
        except Exception as e:
            raise SkillError(f"Failed to run skill script: {e}")
    
    def get_skill_prompt_section(self, skill_name: str) -> str:
        """
        Get the prompt section for a skill.
        
        This returns a formatted string that can be injected into
        the system or user prompt.
        
        Args:
            skill_name: Name of the skill.
        
        Returns:
            Formatted skill prompt section.
        """
        skill = self.load_skill(skill_name)
        
        sections = [
            f"## Skill: {skill.name}",
            "",
            skill.content,
        ]
        
        if skill.script_file:
            sections.extend([
                "",
                f"**Script**: `{skill.script_file}`",
                "",
                "To execute this skill's script, run:",
                "```bash",
                f"python {skill.script_file}",
                "```",
            ])
        
        return "\n".join(sections)
    
    def get_all_skills_prompt(self) -> str:
        """
        Get prompt section for all available skills.
        
        Returns:
            Formatted string with all skill information.
        """
        skills = self.load_all_skills()
        
        if not skills:
            return ""
        
        sections = [
            "# Available Skills",
            "",
            "The following skills are available for use:",
            "",
        ]
        
        for skill_name, skill in sorted(skills.items()):
            sections.append(f"## {skill_name}")
            sections.append("")
            sections.append(skill.description if skill.description else skill.body[:BODY_PREVIEW_LENGTH])
            if skill.script_file:
                sections.append("")
                sections.append(f"**Script**: `{skill.script_file}`")
            sections.append("")
        
        return "\n".join(sections)

    def get_all_skills_prompt_for_workspace(self) -> str:
        """
        Get prompt section for all available skills with workspace-relative paths.
        
        Only includes skill headers (name, description) to minimize prompt size.
        The agent should read the full skill file when it needs detailed instructions.
        
        Skills are copied to the workspace when invoked, so all paths are
        relative to the workspace root (./skills/<skill_name>/).
        
        Returns:
            Formatted string with skill headers using workspace paths.
        """
        skills = self.load_all_skills()
        
        if not skills:
            return ""
        
        sections = [
            "# Available Skills",
            "",
            "The following skills are available in your workspace.",
            "To use a skill, read its full instructions from `./skills/<skill_name>/<name>.md`",
            "",
        ]
        
        for skill_name, skill in sorted(skills.items()):
            sections.append(f"## {skill.name}")
            if skill.description:
                sections.append(f"*{skill.description}*")
            
            if skill.script_file:
                # Compute relative path within skill directory
                skill_dir = self._find_skill_dir(skill_name)
                if skill_dir:
                    try:
                        script_relative = skill.script_file.relative_to(skill_dir)
                        workspace_script_path = f"./skills/{skill_name}/{script_relative}"
                    except ValueError:
                        workspace_script_path = f"./skills/{skill_name}/{skill.script_file.name}"
                else:
                    workspace_script_path = f"./skills/{skill_name}/{skill.script_file.name}"
                
                workspace_readme_path = f"./skills/{skill_name}/{skill.description_file.name}"
                sections.append("")
                sections.append(f"- **Instructions**: `{workspace_readme_path}`")
                sections.append(f"- **Script**: `{workspace_script_path}`")
            sections.append("")
        
        return "\n".join(sections)

    def get_skill_full_content(self, skill_name: str) -> str:
        """
        Get the full body content of a skill (without frontmatter).
        
        Use this when the agent needs the complete instructions
        to execute a skill.
        
        Args:
            skill_name: Name of the skill.
        
        Returns:
            The full markdown body (instructions) of the skill.
        """
        skill = self.load_skill(skill_name)
        return skill.body

    def get_skill_source_dir(self, skill_name: str) -> Path:
        """
        Get the source directory path for a skill.
        
        Args:
            skill_name: Name of the skill.
        
        Returns:
            Path to the skill's source directory.
        
        Raises:
            SkillError: If the skill is not found.
        """
        skill_dir = self._find_skill_dir(skill_name)
        if skill_dir is None:
            available = self.list_skills()
            raise SkillError(
                f"Skill '{skill_name}' not found. "
                f"Available skills: {available}"
            )
        return skill_dir

    def get_workspace_script_path(self, skill_name: str) -> Optional[str]:
        """
        Get the workspace-relative script path for a skill.
        
        Args:
            skill_name: Name of the skill.
        
        Returns:
            Workspace-relative script path (e.g., "./skills/meow/scripts/meow.py"),
            or None if skill has no script.
        """
        skill = self.load_skill(skill_name)
        if skill.script_file is None:
            return None
        
        # Compute relative path within skill directory
        skill_dir = self._find_skill_dir(skill_name)
        if skill_dir:
            try:
                script_relative = skill.script_file.relative_to(skill_dir)
                return f"./skills/{skill_name}/{script_relative}"
            except ValueError:
                pass
        return f"./skills/{skill_name}/{skill.script_file.name}"


class SkillType:
    """Enumeration of skill types for hybrid integration."""
    INSTRUCTION_ONLY = "instruction_only"  # Markdown instructions, no script
    SCRIPT_BASED = "script_based"  # Has associated script for MCP tool


class SkillIntegration:
    """
    Represents how a skill should be integrated with the agent.
    
    Provides type detection and appropriate integration path:
    - INSTRUCTION_ONLY: Inject into system prompt
    - SCRIPT_BASED: Create MCP tool via @tool decorator
    """
    
    def __init__(self, skill: Skill):
        """
        Initialize skill integration.
        
        Args:
            skill: The skill to analyze.
        """
        self.skill = skill
        self.skill_type = self._detect_type()
    
    def _detect_type(self) -> str:
        """
        Detect the skill type based on its contents.
        
        Returns:
            SkillType constant indicating how to integrate.
        """
        if self.skill.script_file and self.skill.script_file.exists():
            return SkillType.SCRIPT_BASED
        return SkillType.INSTRUCTION_ONLY
    
    @property
    def is_script_based(self) -> bool:
        """Check if skill has an associated script."""
        return self.skill_type == SkillType.SCRIPT_BASED
    
    @property
    def is_instruction_only(self) -> bool:
        """Check if skill is instruction-only (no script)."""
        return self.skill_type == SkillType.INSTRUCTION_ONLY
    
    def get_prompt_content(self) -> str:
        """
        Get content for prompt injection (instruction-only skills).
        
        Returns:
            Skill body content for system prompt.
        """
        return self.skill.body
    
    def get_mcp_tool_name(self) -> str:
        """
        Get the MCP tool name for this skill (script-based skills).
        
        Returns:
            MCP tool name in format "skill_<name>".
        """
        safe_name = self.skill.name.replace("-", "_").replace(".", "_").lower()
        return f"skill_{safe_name}"


def categorize_skills(
    skills_dir: Optional[Path] = None
) -> tuple[list[Skill], list[Skill]]:
    """
    Categorize skills by type for hybrid integration.
    
    Args:
        skills_dir: Directory containing skills.
    
    Returns:
        Tuple of (instruction_only_skills, script_based_skills).
    """
    manager = SkillManager(skills_dir)
    all_skills = manager.load_all_skills()
    
    instruction_only: list[Skill] = []
    script_based: list[Skill] = []
    
    for skill in all_skills.values():
        integration = SkillIntegration(skill)
        if integration.is_script_based:
            script_based.append(skill)
        else:
            instruction_only.append(skill)
    
    return instruction_only, script_based


def get_instruction_skills_prompt(skills_dir: Optional[Path] = None) -> str:
    """
    Get prompt content for instruction-only skills.
    
    These skills are injected into the system prompt as they
    don't have scripts that can be exposed as MCP tools.
    
    Args:
        skills_dir: Directory containing skills.
    
    Returns:
        Formatted prompt content for instruction-only skills.
    """
    instruction_skills, _ = categorize_skills(skills_dir)
    
    if not instruction_skills:
        return ""
    
    sections = [
        "# Instruction Skills",
        "",
        "The following skills provide instructions for specific tasks:",
        "",
    ]
    
    for skill in sorted(instruction_skills, key=lambda s: s.name):
        integration = SkillIntegration(skill)
        sections.append(f"## {skill.name}")
        if skill.description:
            sections.append(f"*{skill.description}*")
        sections.append("")
        sections.append(integration.get_prompt_content())
        sections.append("")
    
    return "\n".join(sections)


def load_skill(skill_name: str, skills_dir: Optional[Path] = None) -> Skill:
    """
    Convenience function to load a skill.
    
    Args:
        skill_name: Name of the skill to load.
        skills_dir: Optional skills directory.
    
    Returns:
        Loaded Skill object.
    """
    manager = SkillManager(skills_dir)
    return manager.load_skill(skill_name)


def run_skill(
    skill_name: str,
    args: Optional[list[str]] = None,
    skills_dir: Optional[Path] = None,
    cwd: Optional[Path] = None
) -> tuple[int, str, str]:
    """
    Convenience function to run a skill's script.
    
    Args:
        skill_name: Name of the skill.
        args: Optional arguments for the script.
        skills_dir: Optional skills directory.
        cwd: Working directory for execution.
    
    Returns:
        Tuple of (return_code, stdout, stderr).
    """
    manager = SkillManager(skills_dir)
    return manager.run_skill_script(skill_name, args, cwd)
