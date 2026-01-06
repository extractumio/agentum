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
from ..services import event_service
from ..services.event_stream import EventHub, EventSinkQueue

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
        self._results: dict[str, dict[str, Any]] = {}
        self._event_hub = EventHub()

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
                if status in ("completed", "complete", "partial", "failed", "cancelled"):
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

        # Start the background task
        task_coro = self._run_agent(params)
        self._running_tasks[session_id] = asyncio.create_task(task_coro)

        logger.info(f"Started background task for session: {session_id}")

    async def _run_agent(self, params: TaskParams) -> None:
        """
        Run the agent in background using the unified task runner.

        Uses execute_agent_task() for consistent behavior with CLI.

        Error Handling Strategy:
        - Creates tracer early to ensure error events can be sent to frontend
        - Catches exceptions at all levels and emits proper error events
        - Always emits a completion/error event so frontend knows session ended
        - Updates database status on completion or failure
        """
        session_id = params.session_id
        tracer: Optional[EventingTracer] = None

        event_queue = EventSinkQueue(self._event_hub, session_id)
        last_sequence = await event_service.get_last_sequence(session_id)

        def emit_error_event(message: str, error_type: str = "server_error") -> None:
            """Helper to emit error event even if tracer creation failed."""
            error_event = {
                "type": "error",
                "data": {
                    "message": message,
                    "error_type": error_type,
                    "session_id": session_id,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sequence": last_sequence + 1,
                "session_id": session_id,
            }
            if tracer is not None:
                tracer.emit_event("error", error_event["data"])
                return

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return

            loop.create_task(event_service.record_event(error_event))
            loop.create_task(self._event_hub.publish(session_id, error_event))

        try:
            # Create tracer early for error reporting
            base_tracer = BackendConsoleTracer(session_id=session_id)
            def record_event(event: dict[str, Any]) -> None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(event_service.record_event(event))

            tracer = EventingTracer(
                base_tracer,
                event_queue=event_queue,
                event_sink=record_event,
                session_id=session_id,
                initial_sequence=last_sequence,
            )

            # Determine working directory
            if params.working_dir:
                working_dir = Path(params.working_dir).resolve()
            else:
                working_dir = AGENT_DIR

            logger.info(f"Task: {params.task[:100]}{'...' if len(params.task) > 100 else ''}")

            exec_params = TaskExecutionParams(
                task=params.task,
                working_dir=working_dir,
                session_id=session_id,
                resume_session_id=params.resume_session_id,
                fork_session=params.fork_session,
                model=params.model,
                max_turns=params.max_turns,
                timeout_seconds=params.timeout_seconds,
                permission_mode=params.permission_mode,
                role=params.role,
                profile_path=Path(params.profile) if params.profile else None,
                additional_dirs=params.additional_dirs or [],
                enable_skills=params.enable_skills,
                enable_file_checkpointing=params.enable_file_checkpointing,
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
            final_status = result.status.value.lower()
            if final_status == "error":
                final_status = "failed"
            await self._update_session_status(
                session_id=session_id,
                status=final_status,
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
            emit_error_event("Task was cancelled", "cancelled")
            await self._update_session_status(session_id, "cancelled")
            raise

        except Exception as e:
            error_message = str(e)
            logger.exception(f"Agent failed for session: {session_id}")

            # Provide user-friendly error message
            if "Can't find source path" in error_message:
                user_message = (
                    f"Internal sandbox configuration error: {error_message}. "
                    "Check backend logs for details."
                )
            elif "bwrap" in error_message.lower():
                user_message = (
                    f"Sandbox execution error: {error_message}. "
                    "The sandboxed command failed to execute."
                )
            else:
                user_message = f"Internal error: {error_message}"

            self._results[session_id] = {
                "status": "failed",
                "error": user_message,
            }
            emit_error_event(user_message, "execution_error")
            await self._update_session_status(session_id, "failed")

        finally:
            # Cleanup
            self._running_tasks.pop(session_id, None)
            self._cancel_flags.pop(session_id, None)
            # Subscribers handle their own cleanup

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
        """Deprecated: event queues are managed per-subscriber."""
        return None

    async def subscribe(self, session_id: str) -> asyncio.Queue:
        """Subscribe to events for a session."""
        return await self._event_hub.subscribe(session_id)

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        """Unsubscribe from events for a session."""
        await self._event_hub.unsubscribe(session_id, queue)

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
        self._results.pop(session_id, None)


# Global agent runner instance
agent_runner = AgentRunner()
