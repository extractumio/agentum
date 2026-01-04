"""
Agent runner service for Agentum API.

Manages background agent execution tasks with cancellation support.
Uses the unified task_runner for execution (shared with CLI).
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import AGENT_DIR
from ..core.schemas import TaskExecutionParams
from ..core.task_runner import execute_agent_task
from ..core.tracer import BackendConsoleTracer, EventingTracer
from ..db.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


@dataclass
class TaskParams:
    """
    Parameters for agent task execution.

    Matches CLI arguments and agent.yaml configuration options.
    All fields are optional - if not provided, values from agent.yaml are used.
    """
    # Task
    task: str

    # Session
    session_id: str
    resume_session_id: Optional[str] = None
    fork_session: bool = False

    # Working directory (CLI: --dir, --add-dir)
    working_dir: Optional[str] = None
    additional_dirs: Optional[list[str]] = None

    # Agent config overrides (CLI: --model, --max-turns, --timeout, etc.)
    model: Optional[str] = None
    max_turns: Optional[int] = None
    timeout_seconds: Optional[int] = None
    enable_skills: Optional[bool] = None
    enable_file_checkpointing: Optional[bool] = None
    permission_mode: Optional[str] = None
    role: Optional[str] = None
    max_buffer_size: Optional[int] = None
    output_format: Optional[str] = None
    include_partial_messages: Optional[bool] = None

    # Permission profile (CLI: --profile)
    profile: Optional[str] = None

    def __post_init__(self):
        if self.additional_dirs is None:
            self.additional_dirs = []


class AgentRunner:
    """
    Manages background agent execution.

    Provides methods to start, cancel, and track agent tasks.
    Each task runs in a background asyncio Task.
    """

    def __init__(self) -> None:
        """Initialize the agent runner."""
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, bool] = {}
        self._event_queues: dict[str, asyncio.Queue] = {}
        self._results: dict[str, dict[str, Any]] = {}

    async def _update_session_status(
        self,
        session_id: str,
        status: str,
        model: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
    ) -> None:
        """
        Update session status in database using a fresh session.

        This method creates its own database session to avoid issues
        with closed sessions from request handlers.
        """
        from ..db.models import Session

        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Session).where(Session.id == session_id)
            )
            session = result.scalar_one_or_none()

            if session:
                session.status = status
                session.updated_at = datetime.now(timezone.utc)

                if model is not None:
                    session.model = model
                if num_turns is not None:
                    session.num_turns = num_turns
                if duration_ms is not None:
                    session.duration_ms = duration_ms
                if total_cost_usd is not None:
                    session.total_cost_usd = total_cost_usd
                if status in ("completed", "failed", "cancelled"):
                    session.completed_at = datetime.now(timezone.utc)

                await db.commit()
                logger.debug(f"Updated session {session_id} status to {status}")

    async def start_task(self, params: TaskParams) -> None:
        """
        Start agent execution in background.

        Args:
            params: TaskParams with all execution parameters.

        Raises:
            RuntimeError: If task is already running for this session.
        """
        session_id = params.session_id

        if session_id in self._running_tasks:
            raise RuntimeError(f"Task already running for session: {session_id}")

        # Initialize cancel flag and event queue
        self._cancel_flags[session_id] = False
        self._event_queues.setdefault(session_id, asyncio.Queue())

        # Start the background task
        task_coro = self._run_agent(params)
        self._running_tasks[session_id] = asyncio.create_task(task_coro)

        logger.info(f"Started background task for session: {session_id}")

    async def _run_agent(self, params: TaskParams) -> None:
        """
        Run the agent in background using the unified task runner.

        Uses execute_agent_task() for consistent behavior with CLI.
        """
        session_id = params.session_id

        try:
            # Determine working directory
            if params.working_dir:
                working_dir = Path(params.working_dir).resolve()
            else:
                # Use AGENT_DIR as sensible default for API (not server's cwd)
                working_dir = AGENT_DIR

            logger.info(f"Task: {params.task[:100]}{'...' if len(params.task) > 100 else ''}")

            # Build TaskExecutionParams from TaskParams
            event_queue = self._event_queues.get(session_id)
            base_tracer = BackendConsoleTracer(session_id=session_id)
            tracer = EventingTracer(base_tracer, event_queue=event_queue)

            exec_params = TaskExecutionParams(
                task=params.task,
                working_dir=working_dir,
                session_id=session_id,
                resume_session_id=params.resume_session_id,
                fork_session=params.fork_session,
                # Config overrides
                model=params.model,
                max_turns=params.max_turns,
                timeout_seconds=params.timeout_seconds,
                permission_mode=params.permission_mode,
                role=params.role,
                profile_path=Path(params.profile) if params.profile else None,
                additional_dirs=params.additional_dirs or [],
                enable_skills=params.enable_skills,
                enable_file_checkpointing=params.enable_file_checkpointing,
                # Backend uses BackendConsoleTracer for linear logging
                tracer=tracer,
            )

            # Execute using unified task runner
            result = await execute_agent_task(exec_params)

            # Store result
            self._results[session_id] = {
                "status": result.status.value,
                "output": result.output,
                "error": result.error,
                "comments": result.comments,
                "result_files": result.result_files,
                "metrics": result.metrics.model_dump() if result.metrics else None,
            }

            # Update database with final status and metrics
            metrics = result.metrics
            await self._update_session_status(
                session_id=session_id,
                status="completed",
                model=metrics.model if metrics else params.model,
                num_turns=metrics.num_turns if metrics else None,
                duration_ms=metrics.duration_ms if metrics else None,
                total_cost_usd=metrics.total_cost_usd if metrics else None,
            )

            logger.info(f"Agent completed for session: {session_id}")

        except asyncio.CancelledError:
            logger.info(f"Agent cancelled for session: {session_id}")
            self._results[session_id] = {
                "status": "cancelled",
                "error": "Task was cancelled",
            }
            if "tracer" in locals():
                tracer.emit_event(
                    "cancelled",
                    {
                        "session_id": session_id,
                        "message": "Task was cancelled",
                    },
                )
            await self._update_session_status(session_id, "cancelled")
            raise

        except Exception as e:
            logger.exception(f"Agent failed for session: {session_id}")
            self._results[session_id] = {
                "status": "failed",
                "error": str(e),
            }
            if "tracer" in locals():
                tracer.emit_event(
                    "error",
                    {
                        "message": str(e),
                        "error_type": "server_error",
                    },
                )
            await self._update_session_status(session_id, "failed")

        finally:
            # Cleanup
            self._running_tasks.pop(session_id, None)
            self._cancel_flags.pop(session_id, None)
            # Keep event queue until client disconnects

    async def cancel_task(self, session_id: str) -> bool:
        """
        Cancel a running task.

        Args:
            session_id: The session ID.

        Returns:
            True if cancelled, False if not running.
        """
        if session_id not in self._running_tasks:
            return False

        # Set cancel flag (for graceful cancellation)
        self._cancel_flags[session_id] = True

        # Cancel the asyncio task
        task = self._running_tasks[session_id]
        task.cancel()

        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        logger.info(f"Cancelled task for session: {session_id}")
        return True

    def is_running(self, session_id: str) -> bool:
        """
        Check if a task is currently running.

        Args:
            session_id: The session ID.

        Returns:
            True if running.
        """
        return session_id in self._running_tasks

    def is_cancellation_requested(self, session_id: str) -> bool:
        """
        Check if cancellation was requested.

        Args:
            session_id: The session ID.

        Returns:
            True if cancellation was requested.
        """
        return self._cancel_flags.get(session_id, False)

    def get_event_queue(self, session_id: str) -> Optional[asyncio.Queue]:
        """
        Get the SSE event queue for a session.

        Args:
            session_id: The session ID.

        Returns:
            The event queue, or None if not found.
        """
        return self._event_queues.get(session_id)

    def get_or_create_event_queue(self, session_id: str) -> asyncio.Queue:
        """
        Get or create the SSE event queue for a session.

        Args:
            session_id: The session ID.

        Returns:
            The event queue.
        """
        if session_id not in self._event_queues:
            self._event_queues[session_id] = asyncio.Queue()
        return self._event_queues[session_id]

    def clear_event_queue(self, session_id: str) -> None:
        """
        Remove the event queue for a session.

        Args:
            session_id: The session ID.
        """
        self._event_queues.pop(session_id, None)

    def get_result(self, session_id: str) -> Optional[dict]:
        """
        Get the result of a completed task.

        Args:
            session_id: The session ID.

        Returns:
            Result dictionary, or None if not found.
        """
        return self._results.get(session_id)

    def cleanup_session(self, session_id: str) -> None:
        """
        Cleanup resources for a session.

        Args:
            session_id: The session ID.
        """
        self._event_queues.pop(session_id, None)
        self._results.pop(session_id, None)


# Global agent runner instance
agent_runner = AgentRunner()
