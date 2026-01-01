#!/usr/bin/env python3
"""
Agentum - Entry Point

A self-sufficient AI agent with tool use capabilities.

Usage:
    # Run with task from CLI
    python agent.py --task "Your task description" --dir /path/to/working/dir

    # Run with task from file
    python agent.py --task-file ./tasks/my-task.md --dir /path/to/working/dir

    # Resume a previous session
    python agent.py --resume SESSION_ID --task "Continue the task"

    # List sessions
    python agent.py --list-sessions

    # Use custom permissions config
    python agent.py --task "Task" --permissions-config ./my-permissions.json

    # Show available tools and permissions
    python agent.py --show-tools
"""
import argparse
import asyncio
import json
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Add parent directory to path for imports when running directly
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    sys.path.insert(0, str(Path(__file__).parent.parent))

# Import paths from central config
from config import (
    AGENT_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    CONFIG_DIR,
    ENV_FILE,
)

# Load environment variables from .env file (if not already loaded by wrapper)
if ENV_FILE.exists() and not os.environ.get("ANTHROPIC_API_KEY"):
    load_dotenv(ENV_FILE)

# These imports don't depend on external SDKs
from permissions import (
    create_default_settings,
    load_permissions_from_config,
)
from permission_config import (
    AVAILABLE_TOOLS,
    PermissionMode,
    create_default_permissions_file,
)
from permission_profiles import (
    ProfiledPermissionManager,
    ProfileType,
    ProfileNotFoundError,
    validate_profile_files,
)
from schemas import AgentConfig, TaskStatus
from sessions import SessionManager

# Configure basic logging (will be reconfigured by setup_logging)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent")


class APIKeyError(Exception):
    """Raised when the API key is missing or invalid."""
    pass


def validate_api_key() -> str:
    """
    Validate that ANTHROPIC_API_KEY is set and appears valid.
    
    Returns:
        The validated API key.
    
    Raises:
        APIKeyError: If the key is missing or invalid.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    
    if not api_key:
        # Check if .env file exists
        env_exists = ENV_FILE.exists()
        env_hint = (
            f"\n   Your .env file exists at: {ENV_FILE}"
            if env_exists else
            f"\n   Create a .env file at: {ENV_FILE}"
        )
        
        raise APIKeyError(
            f"ANTHROPIC_API_KEY environment variable is not set.\n"
            f"\n"
            f"   To fix this, either:\n"
            f"   1. Add ANTHROPIC_API_KEY=sk-ant-... to your .env file{env_hint}\n"
            f"   2. Export it in your shell: export ANTHROPIC_API_KEY=sk-ant-...\n"
            f"   3. Pass it when running: ANTHROPIC_API_KEY=sk-ant-... python agent.py ..."
        )
    
    # Validate key format (Anthropic keys start with sk-ant-)
    if not api_key.startswith("sk-ant-"):
        raise APIKeyError(
            f"ANTHROPIC_API_KEY appears invalid.\n"
            f"\n"
            f"   Expected format: sk-ant-api03-...\n"
            f"   Got: {api_key[:15]}...\n"
            f"\n"
            f"   Please check your API key at: https://console.anthropic.com/settings/keys"
        )
    
    # Check minimum length (Anthropic keys are typically 100+ chars)
    if len(api_key) < 50:
        raise APIKeyError(
            f"ANTHROPIC_API_KEY appears truncated (only {len(api_key)} characters).\n"
            f"\n"
            f"   Anthropic API keys are typically 100+ characters.\n"
            f"   Please check that you copied the full key."
        )
    
    return api_key


def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[Path] = None,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5
) -> None:
    """
    Configure file-based logging with rotation for the agent.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
        log_file: Path to log file. Defaults to AGENT/logs/agent.jsonl.
        max_bytes: Maximum size of log file before rotation (default: 10 MB).
        backup_count: Number of backup files to keep (default: 5).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Default to AGENT/logs/agent.jsonl if no file specified
    if log_file is None:
        log_file = LOGS_DIR / "agent.jsonl"

    # Ensure log directory exists
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Create rotating file handler
    rotating_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    rotating_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    # Clear existing handlers and configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(rotating_handler)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Agentum - Execute tasks with AI agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --task "List all Python files" --dir ./my-project
  %(prog)s --task-file ./tasks/my-task.md --dir ./my-project
  %(prog)s --resume 20241229_123456_abc123 --task "Continue"
  %(prog)s --list-sessions
  %(prog)s --permissions-config ./custom-permissions.json --task "Task"
  %(prog)s --show-tools
  %(prog)s --init-permissions
        """
    )

    # Task specification
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument(
        "--task", "-t",
        type=str,
        help="Task description to execute"
    )
    task_group.add_argument(
        "--task-file", "-f",
        type=str,
        help="Path to file containing task description"
    )

    # Working directory
    parser.add_argument(
        "--dir", "-d",
        type=str,
        help="Working directory for the agent"
    )
    parser.add_argument(
        "--add-dir", "-a",
        action="append",
        default=[],
        help="Additional directories the agent can access (can be repeated)"
    )

    # Session management
    parser.add_argument(
        "--resume", "-r",
        type=str,
        metavar="SESSION_ID",
        help="Resume a previous session by ID"
    )
    parser.add_argument(
        "--fork-session",
        action="store_true",
        help="Fork to new session when resuming instead of continuing original"
    )
    parser.add_argument(
        "--list-sessions", "-l",
        action="store_true",
        help="List all sessions and exit"
    )

    # Agent configuration
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929"),
        help="Claude model to use (default: claude-sonnet-4-5-20250929)"
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=100,
        help="Maximum number of turns (default: 100)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout in seconds (default: 1800 = 30 minutes)"
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Disable custom skills (enabled by default)"
    )

    # Permission configuration
    parser.add_argument(
        "--permissions-config", "-p",
        type=str,
        metavar="PATH",
        help="Path to legacy permissions.json configuration file"
    )
    parser.add_argument(
        "--user-profile",
        type=str,
        metavar="PATH",
        help="""
Path to user permission profile file (YAML or JSON).
Overrides default AGENT/config/permissions.user.yaml.
Use this to customize restrictions during task execution."""
    )
    parser.add_argument(
        "--system-profile",
        type=str,
        metavar="PATH",
        help="""
Path to system permission profile file (YAML or JSON).
Overrides default AGENT/config/permissions.system.yaml.
Used for agent initialization/finalization operations."""
    )
    parser.add_argument(
        "--permission-mode",
        type=str,
        choices=["default", "acceptEdits", "plan", "bypassPermissions"],
        default=None,
        help="Permission mode to use (overrides config file)"
    )
    parser.add_argument(
        "--show-tools",
        action="store_true",
        help="Show all available tools and their descriptions"
    )
    parser.add_argument(
        "--show-permissions",
        action="store_true",
        help="Show current permission configuration"
    )
    parser.add_argument(
        "--init-permissions",
        action="store_true",
        help="Create default permissions.json in AGENT/config/"
    )
    parser.add_argument(
        "--check-profiles",
        action="store_true",
        help="""
Validate that permission profile files exist in AGENT/config/:
  - permissions.system.yaml (system operations)
  - permissions.user.yaml (task execution)
Supports .yaml, .yml, and .json formats."""
    )

    # Output
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file for results (default: stdout)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    # Logging
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )

    # Setup
    parser.add_argument(
        "--init",
        action="store_true",
        help="Initialize .claude settings in working directory"
    )

    return parser.parse_args()


def list_sessions() -> None:
    """List all sessions and their status."""
    session_manager = SessionManager(SESSIONS_DIR)
    sessions = session_manager.list_sessions()

    if not sessions:
        print("No sessions found.")
        return

    print(f"\n{'Session ID':<30} {'Status':<10} {'Created':<20} {'Working Dir'}")
    print("-" * 100)

    for session in sessions:
        created = session.created_at.strftime("%Y-%m-%d %H:%M:%S")
        working_dir = session.working_dir
        if len(working_dir) > 40:
            working_dir = "..." + working_dir[-37:]
        print(f"{session.session_id:<30} {session.status:<10} {created:<20} {working_dir}")

    print()


def init_settings(working_dir: Path) -> None:
    """Initialize .claude settings in working directory."""
    create_default_settings(working_dir)
    print(f"Created .claude/settings.local.json in {working_dir}")


def init_permissions() -> None:
    """Create default permissions.json in AGENT/config/."""
    config_path = create_default_permissions_file()
    print(f"Created default permissions configuration at: {config_path}")
    print("\nYou can edit this file to customize:")
    print("  - Allowed/denied tool patterns")


def check_profiles() -> None:
    """Validate that permission profile files exist in AGENT/config/."""
    try:
        system_path, user_path = validate_profile_files()
        print("Permission profiles found:")
        print(f"  ✓ System profile: {system_path}")
        print(f"  ✓ User profile:   {user_path}")
        print("\nProfile usage:")
        print("  - System profile: Used for agent initialization/finalization")
        print("  - User profile: Used during task execution (sandboxed)")
        print("\nCustomize with CLI options:")
        print("  --user-profile ./custom-user-profile.json")
        print("  --system-profile ./custom-system-profile.json")
    except ProfileNotFoundError as e:
        print(f"✗ {e}")
        print("\nTo create profiles, copy templates from the repository or create manually.")
        raise SystemExit(1)
    print("  - Enabled/disabled tools")
    print("  - Permission mode")
    print("  - Hooks for dynamic permission management")


def show_tools() -> None:
    """Display all available tools with descriptions."""
    print("\n" + "=" * 70)
    print("CLAUDE CODE AVAILABLE TOOLS")
    print("=" * 70)

    # Group tools by category
    categories: dict[str, list] = {}
    for tool in AVAILABLE_TOOLS.values():
        cat = tool.category.value
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool)

    for category, tools in sorted(categories.items()):
        print(f"\n[{category.upper()}]")
        print("-" * 50)
        for tool in tools:
            safe_marker = " (safe)" if tool.is_safe else ""
            print(f"\n  {tool.name}{safe_marker}")
            # Clean up description - remove leading/trailing whitespace from each line
            desc_lines = tool.description.strip().split("\n")
            for line in desc_lines:
                print(f"    {line.strip()}")
            if tool.example_patterns:
                print(f"    Examples: {', '.join(tool.example_patterns[:3])}")

    print("\n" + "=" * 70)
    print(f"Total: {len(AVAILABLE_TOOLS)} tools")
    print("=" * 70 + "\n")


def show_permissions(permissions_config: Optional[str] = None) -> None:
    """Display current permission configuration."""
    config_path = Path(permissions_config) if permissions_config else None
    manager = load_permissions_from_config(config_path=config_path)

    config = manager.config

    print("\n" + "=" * 70)
    print("PERMISSION CONFIGURATION")
    print("=" * 70)

    print(f"\nMode: {config.defaultMode.value}")

    print("\n[ENABLED TOOLS]")
    for tool in manager.enabled_tools:
        print(f"  - {tool}")

    if config.tools.disabled:
        print("\n[DISABLED TOOLS]")
        for tool in config.tools.disabled:
            print(f"  - {tool}")

    print("\n[ALLOW RULES]")
    for rule in config.permissions.allow:
        print(f"  ✓ {rule}")

    print("\n[DENY RULES]")
    for rule in config.permissions.deny:
        print(f"  ✗ {rule}")

    if config.permissions.ask:
        print("\n[ASK RULES] (require confirmation)")
        for rule in config.permissions.ask:
            print(f"  ? {rule}")

    # Show hooks if configured
    hooks_configured = (
        config.hooks.PreToolUse or
        config.hooks.PostToolUse or
        config.hooks.PermissionRequest
    )
    if hooks_configured:
        print("\n[HOOKS]")
        if config.hooks.PreToolUse:
            print(f"  PreToolUse: {len(config.hooks.PreToolUse)} hooks")
        if config.hooks.PostToolUse:
            print(f"  PostToolUse: {len(config.hooks.PostToolUse)} hooks")
        if config.hooks.PermissionRequest:
            print(f"  PermissionRequest: {len(config.hooks.PermissionRequest)} hooks")

    print("\n" + "=" * 70 + "\n")


async def execute_task(args: argparse.Namespace) -> int:
    """
    Execute the agent task.

    Args:
        args: Parsed command-line arguments.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    # Import here to avoid loading SDK for info commands
    from agent_core import ClaudeAgent
    from exceptions import AgentError, TaskError
    from tasks import load_task

    # Determine working directory
    working_dir = Path(args.dir).resolve() if args.dir else Path.cwd()

    # Load task
    try:
        task = load_task(
            task=args.task,
            file_path=args.task_file,
            working_dir=working_dir
        )
    except TaskError as e:
        logger.error(f"Task error: {e}")
        return 1

    logger.info(f"Task: {task[:100]}{'...' if len(task) > 100 else ''}")
    logger.info(f"Working directory: {working_dir}")

    # Load permission profiles (required)
    system_profile_path = (
        Path(args.system_profile) if args.system_profile else None
    )
    user_profile_path = (
        Path(args.user_profile) if args.user_profile else None
    )

    profiled_permission_manager = ProfiledPermissionManager(
        system_profile_path=system_profile_path,
        user_profile_path=user_profile_path
    )

    if args.system_profile:
        logger.info(f"Using system profile: {args.system_profile}")
    if args.user_profile:
        logger.info(f"Using user profile: {args.user_profile}")

    # Start with system profile for initialization phase
    profiled_permission_manager.activate_system_profile()

    # Get allowed tools from user profile (will be used during task execution)
    # Access user_profile directly without activating it
    user_tools = profiled_permission_manager.user_profile.tools
    allowed_tools = list(set(user_tools.enabled) - set(user_tools.disabled))

    logger.info(f"Allowed tools: {', '.join(allowed_tools)}")

    # Build configuration
    config = AgentConfig(
        model=args.model,
        max_turns=args.max_turns,
        timeout_seconds=args.timeout,
        enable_skills=not args.no_skills,
        working_dir=str(working_dir),
        additional_dirs=args.add_dir,
        allowed_tools=allowed_tools,
        permissions_config=args.permissions_config,
    )

    # Create and run agent with permission enforcement
    agent = ClaudeAgent(
        config=config,
        sessions_dir=SESSIONS_DIR,
        logs_dir=LOGS_DIR,
        profiled_permission_manager=profiled_permission_manager,
    )

    try:
        # Execute task and exit
        result = await agent.run_with_timeout(
            task=task,
            resume_session_id=args.resume,
            fork_session=args.fork_session,
        )

        # Output results
        if args.json:
            output = result.model_dump_json(indent=2)
        else:
            output = format_result(result)

        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Results written to: {args.output}")

        # Log metrics
        if result.metrics:
            logger.info(
                f"Metrics: turns={result.metrics.num_turns}, "
                f"duration={result.metrics.duration_ms}ms, "
                f"cost=${result.metrics.total_cost_usd or 0:.4f}"
            )

        if result.session_info:
            logger.info(f"Session ID: {result.session_info.session_id}")

        return 0 if result.status == TaskStatus.COMPLETE else 1

    except AgentError as e:
        logger.error(f"Agent error: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


def format_result(result) -> str:
    """
    Format agent result for human-readable output.

    Args:
        result: AgentResult object.

    Returns:
        Formatted string.
    """
    lines = [f"Status: {result.status}"]

    if result.error:
        lines.append(f"Error: {result.error}")

    if result.comments:
        lines.append(f"Comments: {result.comments}")

    if result.output:
        lines.append(f"Output: {result.output}")

    if result.result_files:
        lines.append("Files:")
        for filepath in result.result_files:
            lines.append(f"  - {filepath}")

    if result.session_info:
        lines.append(f"Session: {result.session_info.session_id}")

    return "\n".join(lines)


def wrap_text(text: str, width: int = 70) -> list[str]:
    """Wrap text to specified width, preserving words."""
    if len(text) <= width:
        return [text]
    
    words = text.split()
    lines: list[str] = []
    current_line: list[str] = []
    current_len = 0
    
    for word in words:
        word_len = len(word)
        if current_len + word_len + (1 if current_line else 0) <= width:
            current_line.append(word)
            current_len += word_len + (1 if len(current_line) > 1 else 0)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_len = word_len
    
    if current_line:
        lines.append(" ".join(current_line))
    
    return lines


def get_terminal_width() -> int:
    """Get terminal width with sensible default."""
    try:
        import shutil
        width = shutil.get_terminal_size().columns
        return max(50, min(width, 120))
    except Exception:
        return 80


def print_result_box(result) -> None:
    """
    Print a compact result summary to stdout (statistics only).
    
    Args:
        result: AgentResult object with status, output, error, and session_info.
    """
    # Colors
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    
    # Single-line box characters
    TL, TR, BL, BR = "┌", "┐", "└", "┘"
    H, V = "─", "│"
    LT, RT = "├", "┤"
    
    terminal_width = get_terminal_width()
    width = min(terminal_width - 4, 55)
    inner_width = width - 2
    
    status_upper = str(result.status).upper()
    is_complete = status_upper in ("COMPLETE", "TASKSTATUS.COMPLETE")
    is_partial = status_upper in ("PARTIAL", "TASKSTATUS.PARTIAL")
    
    if is_complete:
        status_color = GREEN
        status_icon = "✓"
        status_text = "COMPLETE"
    elif is_partial:
        status_color = YELLOW
        status_icon = "!"
        status_text = "PARTIAL"
    else:
        status_color = RED
        status_icon = "✗"
        status_text = "FAILED"
    
    print()
    
    # Top border
    print(f"{status_color}{TL}{H * inner_width}{TR}{RESET}")
    
    # Status line (centered)
    status_content = f" {status_icon} {status_text} "
    status_padding = (inner_width - len(status_content)) // 2
    print(
        f"{status_color}{V}{RESET}"
        f"{' ' * status_padding}"
        f"{status_color}{BOLD}{status_content}{RESET}"
        f"{' ' * (inner_width - status_padding - len(status_content))}"
        f"{status_color}{V}{RESET}"
    )
    
    # Separator
    print(f"{status_color}{LT}{H * inner_width}{RT}{RESET}")
    
    # Session line (if available)
    if result.session_info:
        session_line = f" Session: {result.session_info.session_id}"
        padding = inner_width - len(session_line)
        print(
            f"{status_color}{V}{RESET}"
            f"{GRAY}{session_line}{RESET}"
            f"{' ' * max(0, padding)}"
            f"{status_color}{V}{RESET}"
        )
    
    # Metrics (if available)
    if result.metrics:
        duration_ms = result.metrics.duration_ms
        if duration_ms < 1000:
            duration_str = f"{duration_ms}ms"
        elif duration_ms < 60000:
            duration_str = f"{duration_ms / 1000:.1f}s"
        else:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) / 1000
            duration_str = f"{minutes}m {seconds:.1f}s"
        
        turns = result.metrics.num_turns
        cost = result.metrics.total_cost_usd or 0
        
        metrics_line = f" Duration: {duration_str} | Turns: {turns} | ${cost:.4f}"
        padding = inner_width - len(metrics_line)
        print(
            f"{status_color}{V}{RESET}"
            f"{WHITE}{metrics_line}{RESET}"
            f"{' ' * max(0, padding)}"
            f"{status_color}{V}{RESET}"
        )
    
    # Error summary (if any)
    if result.error:
        error_preview = result.error[:40] + "..." if len(result.error) > 40 else result.error
        error_line = f" Error: {error_preview}"
        padding = inner_width - len(error_line)
        print(
            f"{status_color}{V}{RESET}"
            f"{RED}{error_line}{RESET}"
            f"{' ' * max(0, padding)}"
            f"{status_color}{V}{RESET}"
        )
    
    # Comments (if any)
    if result.comments:
        comments_preview = result.comments[:40] + "..." if len(result.comments) > 40 else result.comments
        comments_line = f" Comments: {comments_preview}"
        padding = inner_width - len(comments_line)
        print(
            f"{status_color}{V}{RESET}"
            f"{GRAY}{comments_line}{RESET}"
            f"{' ' * max(0, padding)}"
            f"{status_color}{V}{RESET}"
        )
    
    # Result files (if any)
    if result.result_files:
        files_count = len(result.result_files)
        files_line = f" Files: {files_count} generated"
        padding = inner_width - len(files_line)
        print(
            f"{status_color}{V}{RESET}"
            f"{WHITE}{files_line}{RESET}"
            f"{' ' * max(0, padding)}"
            f"{status_color}{V}{RESET}"
        )
        # Show first 2 files
        for filepath in result.result_files[:2]:
            file_line = f"   - {filepath}"
            if len(file_line) > inner_width:
                file_line = file_line[:inner_width - 3] + "..."
            padding = inner_width - len(file_line)
            print(
                f"{status_color}{V}{RESET}"
                f"{DIM}{file_line}{RESET}"
                f"{' ' * max(0, padding)}"
                f"{status_color}{V}{RESET}"
            )
        if files_count > 2:
            more_line = f"   ... +{files_count - 2} more"
            padding = inner_width - len(more_line)
            print(
                f"{status_color}{V}{RESET}"
                f"{DIM}{more_line}{RESET}"
                f"{' ' * max(0, padding)}"
                f"{status_color}{V}{RESET}"
            )
    
    # Bottom border
    print(f"{status_color}{BL}{H * inner_width}{BR}{RESET}")
    print()


def main() -> int:
    """
    Main entry point.

    Returns:
        Exit code.
    """
    args = parse_args()

    # Setup logging
    setup_logging(args.log_level)

    # Handle special commands
    if args.list_sessions:
        list_sessions()
        return 0

    if args.show_tools:
        show_tools()
        return 0

    if args.show_permissions:
        show_permissions(args.permissions_config)
        return 0

    if args.init_permissions:
        init_permissions()
        return 0

    if args.check_profiles:
        check_profiles()
        return 0

    if args.init:
        working_dir = Path(args.dir).resolve() if args.dir else Path.cwd()
        init_settings(working_dir)
        return 0

    # Import TaskError for validation
    from exceptions import TaskError

    # Validate API key before doing anything else
    try:
        api_key = validate_api_key()
        # Print first 20 chars of key for monitoring
        key_preview = api_key[:20] + "..." if len(api_key) > 20 else api_key
        print(f"\033[92m✓\033[0m API Key loaded: \033[90m{key_preview}\033[0m")
    except APIKeyError as e:
        print(f"\n\033[91m✗ API Key Error\033[0m\n", file=sys.stderr)
        print(f"{e}\n", file=sys.stderr)
        return 1

    # Validate required arguments
    if not args.task and not args.task_file:
        logger.error(
            "No task specified. Use --task or --task-file to provide a task."
        )
        return 1

    # Run the task
    try:
        return asyncio.run(execute_task(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
