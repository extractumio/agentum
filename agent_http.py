#!/usr/bin/env python3
"""
HTTP Entry point for Agentum (via API).

This script runs the agent via HTTP API endpoints, providing the same
CLI interface as agent_cli.py but executing tasks through the REST API.

Requires the API server to be running (see src/api/main.py).

Usage:
    # Run with task (same as agent_cli.py)
    python agent_http.py --task "Your task description" --dir /path/to/working/dir

    # Run with task from file
    python agent_http.py --task-file ./tasks/my-task.md --dir /path/to/working/dir

    # Resume a previous session
    python agent_http.py --resume SESSION_ID --task "Continue the task"
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

# Add project root to sys.path for imports
_project_root = Path(__file__).parent
sys.path.insert(0, str(_project_root))

from src.core.cli_common import (  # noqa: E402
    add_config_override_arguments,
    add_directory_arguments,
    add_http_arguments,
    add_logging_arguments,
    add_output_arguments,
    add_permission_arguments,
    add_role_argument,
    add_session_arguments,
    add_task_arguments,
    create_common_parser,
)
from src.core.constants import (  # noqa: E402
    AnsiColors,
    StatusIcons,
    TASK_PREVIEW_LENGTH,
)
from src.core.logging_config import setup_http_logging  # noqa: E402
from src.core.output import (  # noqa: E402
    format_result,
    print_result_box,
    print_sessions_table,
    print_status,
)
from src.core.tasks import load_task  # noqa: E402

# Module logger
logger = logging.getLogger("agent_http")


# =============================================================================
# Configuration Loading
# =============================================================================

def load_api_config() -> dict[str, Any]:
    """
    Load API configuration from config/api.yaml.

    Returns:
        API configuration dict with host and port.

    Raises:
        SystemExit: If config file is missing or invalid.
    """
    config_path = Path(__file__).parent / "config" / "api.yaml"

    if not config_path.exists():
        print(f"{AnsiColors.ERROR}✗ API configuration not found: {config_path}{AnsiColors.RESET}")
        print("Create config/api.yaml or start the API server first.")
        sys.exit(1)

    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        print(f"{AnsiColors.ERROR}✗ Failed to parse api.yaml: {e}{AnsiColors.RESET}")
        sys.exit(1)

    api_config = config.get("api", {})
    if not api_config.get("host") or not api_config.get("port"):
        print(f"{AnsiColors.ERROR}✗ API config missing 'host' or 'port'{AnsiColors.RESET}")
        sys.exit(1)

    return api_config


def get_base_url(api_config: dict[str, Any]) -> str:
    """Build the API base URL from config."""
    host = api_config["host"]
    port = api_config["port"]

    # Use localhost for 0.0.0.0
    if host == "0.0.0.0":
        host = "127.0.0.1"

    return f"http://{host}:{port}/api/v1"


# =============================================================================
# HTTP Client
# =============================================================================

class APIError(Exception):
    """API request error."""
    pass


class APIClient:
    """Simple HTTP client for Agentum API."""

    def __init__(self, base_url: str, token: Optional[str] = None) -> None:
        """
        Initialize the API client.

        Args:
            base_url: Base URL for API requests.
            token: Optional authentication token.
        """
        self.base_url = base_url
        self.token = token

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
        timeout: int = 30
    ) -> dict:
        """Make an HTTP request to the API."""
        url = f"{self.base_url}{endpoint}"

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body = json.dumps(data).encode("utf-8") if data else None
        request = Request(url, data=body, headers=headers, method=method)

        logger.info(f"HTTP {method} {endpoint}")
        if data and logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Request body: {json.dumps(data, indent=2)}")

        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                logger.info(f"HTTP {method} {endpoint} -> {response.status}")
                return result
        except HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            try:
                error_data = json.loads(error_body)
                detail = error_data.get("detail", error_body)
            except json.JSONDecodeError:
                detail = error_body
            logger.error(f"HTTP {method} {endpoint} -> {e.code}: {detail}")
            raise APIError(f"HTTP {e.code}: {detail}") from e
        except URLError as e:
            logger.error(f"HTTP {method} {endpoint} -> Connection failed: {e.reason}")
            raise APIError(f"Connection failed: {e.reason}") from e
        except TimeoutError:
            logger.error(f"HTTP {method} {endpoint} -> Timeout")
            raise APIError("Request timed out") from None

    def get(self, endpoint: str, timeout: int = 30) -> dict:
        """Make a GET request."""
        return self._request("GET", endpoint, timeout=timeout)

    def post(self, endpoint: str, data: dict, timeout: int = 30) -> dict:
        """Make a POST request."""
        return self._request("POST", endpoint, data=data, timeout=timeout)

    def get_token(self, user_id: str = "cli-user") -> str:
        """Get an authentication token."""
        logger.info(f"Authenticating as: {user_id}")
        response = self.post("/auth/token", {"user_id": user_id})
        logger.info("Authentication successful")
        return response["access_token"]

    def run_task(self, request_data: dict) -> dict:
        """Start a task via POST /sessions/run."""
        task = request_data.get("task", "")[:50]
        logger.info(f"Starting task: {task}...")
        return self.post("/sessions/run", request_data)

    def get_session(self, session_id: str) -> dict:
        """Get session details via GET /sessions/{id}."""
        return self.get(f"/sessions/{session_id}")

    def get_result(self, session_id: str) -> dict:
        """Get task result via GET /sessions/{id}/result."""
        logger.info(f"Fetching result for session: {session_id}")
        return self.get(f"/sessions/{session_id}/result")

    def list_sessions(self, limit: int = 50) -> dict:
        """List sessions via GET /sessions."""
        logger.info(f"Listing sessions (limit={limit})")
        return self.get(f"/sessions?limit={limit}")


# =============================================================================
# CLI Argument Parsing
# =============================================================================

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments (mirrors agent_cli.py)."""
    parser = create_common_parser(
        description="Agentum - Execute tasks via HTTP API",
        epilog="""
Examples:
  %(prog)s --task "List all Python files" --dir ./my-project
  %(prog)s --task-file ./tasks/my-task.md --dir ./my-project
  %(prog)s --resume 20241229_123456_abc123 --task "Continue"
  %(prog)s --list-sessions
        """
    )

    # Add argument groups using shared functions
    add_task_arguments(parser)
    add_directory_arguments(parser)
    add_session_arguments(parser)
    add_config_override_arguments(parser)
    add_permission_arguments(parser)
    add_role_argument(parser)
    add_logging_arguments(parser)
    add_output_arguments(parser)
    add_http_arguments(parser)  # HTTP-specific: --poll-interval, --no-wait, --api-url

    return parser.parse_args()


# =============================================================================
# Helpers
# =============================================================================

def build_request_data(args: argparse.Namespace, task: str) -> dict:
    """
    Build RunTaskRequest data from CLI arguments.

    Args:
        args: Parsed CLI arguments.
        task: The task content.

    Returns:
        Request data dict for POST /sessions/run.
    """
    # Resolve working directory to absolute path
    working_dir = None
    if args.dir:
        working_dir = str(Path(args.dir).resolve())

    # Build config overrides
    config: dict[str, Any] = {}

    if args.model:
        config["model"] = args.model
    if args.max_turns is not None:
        config["max_turns"] = args.max_turns
    if args.timeout is not None:
        config["timeout_seconds"] = args.timeout
    if args.no_skills:
        config["enable_skills"] = False
    if args.profile:
        config["profile"] = args.profile
    if args.permission_mode:
        config["permission_mode"] = args.permission_mode
    if args.role:
        config["role"] = args.role

    request_data = {
        "task": task,
        "working_dir": working_dir,
        "additional_dirs": args.add_dir,
        "fork_session": args.fork_session,
        "config": config,
    }

    if args.resume:
        request_data["resume_session_id"] = args.resume

    return request_data


def poll_for_completion(
    client: APIClient,
    session_id: str,
    poll_interval: float = 2.0
) -> dict:
    """Poll session status until completion."""
    spinner_chars = StatusIcons.SPINNER
    spinner_idx = 0
    start_time = time.time()

    while True:
        try:
            session = client.get_session(session_id)
        except APIError as e:
            print(f"\n{AnsiColors.ERROR}✗ Failed to get session status: {e}{AnsiColors.RESET}")
            sys.exit(1)

        status = session.get("status", "")

        if status in ("completed", "failed", "cancelled"):
            # Clear the spinner line
            print("\r" + " " * 60 + "\r", end="")
            return session

        # Display spinner with elapsed time
        elapsed = int(time.time() - start_time)
        spinner = spinner_chars[spinner_idx % len(spinner_chars)]
        spinner_idx += 1

        turns = session.get("num_turns", 0)
        print(
            f"\r{AnsiColors.INFO}{spinner}{AnsiColors.RESET} Running... "
            f"({elapsed}s, turns: {turns})",
            end="",
            flush=True
        )

        time.sleep(poll_interval)


def print_api_error(message: str) -> None:
    """Print an API error with server startup instructions."""
    print(f"\n{AnsiColors.ERROR}✗ {message}{AnsiColors.RESET}")
    print("\nIs the API server running?")
    print(f"  Start with: cd {Path(__file__).parent}")
    print("             python -m uvicorn src.api.main:app --port 40080")


# =============================================================================
# Main Execution
# =============================================================================

def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging to agent_http.log
    setup_http_logging(args.log_level)

    logger.info("=" * 50)
    logger.info("AGENT HTTP CLIENT")
    logger.info("=" * 50)

    # Load API config
    if args.api_url:
        base_url = args.api_url.rstrip("/")
        logger.info(f"Using API URL from --api-url: {base_url}")
    else:
        api_config = load_api_config()
        base_url = get_base_url(api_config)
        logger.info(f"Using API URL from config: {base_url}")

    # Create client without token first
    client = APIClient(base_url)

    # Authenticate with retry (server may still be starting)
    max_retries = 10
    retry_delay = 1.0
    
    for attempt in range(max_retries):
        try:
            token = client.get_token()
            client.token = token
            break
        except APIError as e:
            if attempt < max_retries - 1:
                print(
                    f"\r{AnsiColors.DIM}Waiting for API server... "
                    f"(attempt {attempt + 1}/{max_retries}){AnsiColors.RESET}",
                    end="",
                    flush=True
                )
                time.sleep(retry_delay)
            else:
                print()  # Clear the waiting line
                print_api_error(f"Failed to authenticate: {e}")
                return 1
    
    # Clear waiting message if any
    print("\r" + " " * 60 + "\r", end="")

    # Handle --list-sessions
    if args.list_sessions:
        try:
            response = client.list_sessions()
            sessions = response.get("sessions", [])
            print_sessions_table(sessions)
        except APIError as e:
            print(f"{AnsiColors.ERROR}✗ Failed to list sessions: {e}{AnsiColors.RESET}")
            return 1
        return 0

    # Validate required arguments for task execution
    if not args.task and not args.task_file:
        print(f"{AnsiColors.ERROR}✗ No task specified. Use --task or --task-file{AnsiColors.RESET}")
        return 1

    # Load task using shared function from core module
    working_dir = Path(args.dir).resolve() if args.dir else Path.cwd()
    try:
        task = load_task(args.task, args.task_file, working_dir)
    except Exception as e:
        logger.error(f"Failed to load task: {e}")
        print(f"{AnsiColors.ERROR}✗ {e}{AnsiColors.RESET}")
        return 1

    task_preview = task[:TASK_PREVIEW_LENGTH] + ("..." if len(task) > TASK_PREVIEW_LENGTH else "")
    logger.info(f"Task: {task_preview}")
    logger.info(f"Working directory: {working_dir}")

    print_status(f"✓ API endpoint: {base_url}", "dim")
    print_status(f"✓ Task: {task_preview}", "dim")
    if args.dir:
        print_status(f"✓ Working directory: {working_dir}", "dim")
    print_status("✓ Authenticated with API", "dim")

    # Build request
    request_data = build_request_data(args, task)

    # Start task
    try:
        response = client.run_task(request_data)
    except APIError as e:
        print(f"\n{AnsiColors.ERROR}✗ Failed to start task: {e}{AnsiColors.RESET}")
        return 1

    session_id = response.get("session_id", "")
    logger.info(f"Task started: session_id={session_id}")
    print_status(f"✓ Task started: {session_id}", "success")

    if response.get("resumed_from"):
        logger.info(f"Resumed from: {response['resumed_from']}")
        print_status(f"  Resumed from: {response['resumed_from']}", "dim")

    # If --no-wait, exit immediately
    if args.no_wait:
        print_status(f"\nSession ID: {session_id}", "info")
        print_status("Use --resume to continue or check status later.", "dim")
        return 0

    # Poll for completion
    print()
    poll_for_completion(client, session_id, args.poll_interval)

    # Get result
    try:
        result = client.get_result(session_id)
    except APIError as e:
        print(f"{AnsiColors.ERROR}✗ Failed to get result: {e}{AnsiColors.RESET}")
        return 1

    # Display result using shared function from core module
    if args.json:
        output = json.dumps(result, indent=2)
        print(output)
    else:
        print_result_box(result, session_id=session_id)

    # Write output file if specified
    if args.output:
        if args.json:
            output_text = json.dumps(result, indent=2)
        else:
            output_text = format_result(result, session_id=session_id)
        Path(args.output).write_text(output_text)
        print_status(f"Results written to: {args.output}", "dim")

    # Return exit code based on status
    status = result.get("status", "FAILED").upper()
    logger.info(f"Task completed: status={status}, session_id={session_id}")
    logger.info("=" * 50)
    return 0 if status == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
