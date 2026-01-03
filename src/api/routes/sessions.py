"""
Session management endpoints for Agentum API.

Provides endpoints for:
- POST /sessions/run - Unified endpoint to create session and start task
- POST /sessions - Create session without starting
- GET /sessions - List sessions
- GET /sessions/{id} - Get session details
- POST /sessions/{id}/task - Start task on existing session
- POST /sessions/{id}/cancel - Cancel running task
- GET /sessions/{id}/result - Get task result
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...services.agent_runner import agent_runner, TaskParams
from ...services.session_service import session_service
from ..deps import get_current_user_id
from ..models import (
    AgentConfigOverrides,
    CancelResponse,
    CreateSessionRequest,
    ResultMetrics,
    ResultResponse,
    RunTaskRequest,
    SessionListResponse,
    SessionResponse,
    StartTaskRequest,
    TaskStartedResponse,
    TokenUsageResponse,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def session_to_response(session) -> SessionResponse:
    """Convert a database Session to SessionResponse."""
    return SessionResponse(
        id=session.id,
        status=session.status,
        task=session.task,
        model=session.model,
        working_dir=session.working_dir,
        created_at=session.created_at,
        updated_at=session.updated_at,
        completed_at=session.completed_at,
        num_turns=session.num_turns,
        duration_ms=session.duration_ms,
        total_cost_usd=session.total_cost_usd,
        cancel_requested=session.cancel_requested,
    )


def build_task_params(
    session_id: str,
    task: str,
    working_dir: str | None,
    additional_dirs: list[str],
    resume_session_id: str | None,
    fork_session: bool,
    config: AgentConfigOverrides,
) -> TaskParams:
    """
    Build TaskParams from request data.

    Converts API request fields to TaskParams dataclass.
    """
    return TaskParams(
        task=task,
        session_id=session_id,
        resume_session_id=resume_session_id,
        fork_session=fork_session,
        working_dir=working_dir,
        additional_dirs=additional_dirs,
        model=config.model,
        max_turns=config.max_turns,
        timeout_seconds=config.timeout_seconds,
        enable_skills=config.enable_skills,
        enable_file_checkpointing=config.enable_file_checkpointing,
        permission_mode=config.permission_mode,
        role=config.role,
        max_buffer_size=config.max_buffer_size,
        output_format=config.output_format,
        include_partial_messages=config.include_partial_messages,
        profile=config.profile,
    )


# =============================================================================
# POST /sessions/run - Unified endpoint (recommended)
# =============================================================================

@router.post("/run", response_model=TaskStartedResponse, status_code=status.HTTP_201_CREATED)
async def run_task(
    request: RunTaskRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TaskStartedResponse:
    """
    Create a new session and start task execution immediately.

    This is the primary endpoint for running agent tasks. It:
    1. Creates a new session (or resumes an existing one if resume_session_id is provided)
    2. Starts task execution in the background
    3. Returns immediately with session ID

    Use GET /sessions/{id} to check status and GET /sessions/{id}/result for output.

    Matches CLI capabilities:
        python agent.py --task "..." --model "..." --max-turns 50
        python agent.py --resume SESSION_ID --task "Continue..."
    """
    # Create session in database
    session = await session_service.create_session(
        db=db,
        user_id=user_id,
        task=request.task,
        working_dir=request.working_dir,
        model=request.config.model,
    )

    # Build task parameters
    params = build_task_params(
        session_id=session.id,
        task=request.task,
        working_dir=request.working_dir,
        additional_dirs=request.additional_dirs,
        resume_session_id=request.resume_session_id,
        fork_session=request.fork_session,
        config=request.config,
    )

    # Start the agent in background
    await agent_runner.start_task(params)

    # Update session to running status
    await session_service.update_session(db=db, session=session, status="running")

    return TaskStartedResponse(
        session_id=session.id,
        status="running",
        message="Task execution started",
        resumed_from=request.resume_session_id,
    )


# =============================================================================
# POST /sessions - Create session without starting
# =============================================================================

@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    request: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    Create a new session without starting execution.

    Use POST /sessions/{id}/task to start the task later.
    For most use cases, prefer POST /sessions/run which creates and starts in one call.
    """
    session = await session_service.create_session(
        db=db,
        user_id=user_id,
        task=request.task,
        working_dir=request.working_dir,
        model=request.model,
    )

    return session_to_response(session)


# =============================================================================
# GET /sessions - List sessions
# =============================================================================

@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """
    List sessions for the current user.

    Returns a paginated list of sessions ordered by creation date (newest first).
    """
    sessions, total = await session_service.list_sessions(
        db=db,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )

    return SessionListResponse(
        sessions=[session_to_response(s) for s in sessions],
        total=total,
    )


# =============================================================================
# GET /sessions/{id} - Get session details
# =============================================================================

@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """
    Get session details.

    Returns the current state of a session including status and metrics.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return session_to_response(session)


# =============================================================================
# POST /sessions/{id}/task - Start task on existing session
# =============================================================================

@router.post("/{session_id}/task", response_model=TaskStartedResponse)
async def start_task(
    session_id: str,
    request: StartTaskRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> TaskStartedResponse:
    """
    Start or continue task execution on an existing session.

    Starts the agent in the background. Use GET /sessions/{id} to check status
    and GET /sessions/{id}/events (SSE) to stream real-time events.

    If no task is provided, uses the session's stored task.
    Supports resuming from a different session via resume_session_id.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if agent_runner.is_running(session_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task is already running for this session",
        )

    # Use request.task if provided, otherwise fall back to session.task
    task_to_run = request.task or session.task
    if not task_to_run:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No task specified. Provide task in request or create session with task.",
        )

    # Determine resume session:
    # - If request.resume_session_id is set, resume from that session
    # - Otherwise, if this session was already run before (has turns), resume it
    resume_from = request.resume_session_id
    if not resume_from and session.num_turns > 0:
        # This session has history, resume from itself
        resume_from = session_id

    # Determine working directory: request override > session stored value
    effective_working_dir = request.working_dir or session.working_dir

    # Build task parameters
    params = build_task_params(
        session_id=session_id,
        task=task_to_run,
        working_dir=effective_working_dir,
        additional_dirs=request.additional_dirs,
        resume_session_id=resume_from,
        fork_session=request.fork_session,
        config=request.config,
    )

    # Start the agent in background
    await agent_runner.start_task(params)

    # Update session to running status
    session = await session_service.update_session(
        db=db,
        session=session,
        status="running",
    )

    return TaskStartedResponse(
        session_id=session_id,
        status="running",
        message="Task execution started",
        resumed_from=resume_from if resume_from != session_id else None,
    )


# =============================================================================
# POST /sessions/{id}/cancel - Cancel running task
# =============================================================================

@router.post("/{session_id}/cancel", response_model=CancelResponse)
async def cancel_task(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> CancelResponse:
    """
    Cancel a running task.

    Requests cancellation of the running agent. The agent will stop
    at the next opportunity (typically after the current tool completes).
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if not agent_runner.is_running(session_id):
        # Already stopped, just update status
        if session.status == "running":
            session = await session_service.update_session(
                db=db,
                session=session,
                status="cancelled",
                completed_at=datetime.now(timezone.utc),
            )

        return CancelResponse(
            session_id=session_id,
            status=session.status,
            message="Task is not running",
        )

    # Cancel the running task
    cancelled = await agent_runner.cancel_task(session_id)

    if cancelled:
        session = await session_service.update_session(
            db=db,
            session=session,
            status="cancelled",
            cancel_requested=True,
            completed_at=datetime.now(timezone.utc),
        )

    return CancelResponse(
        session_id=session_id,
        status="cancelled" if cancelled else session.status,
        message="Cancellation requested" if cancelled else "Failed to cancel",
    )


# =============================================================================
# GET /sessions/{id}/result - Get task result
# =============================================================================

@router.get("/{session_id}/result", response_model=ResultResponse)
async def get_result(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> ResultResponse:
    """
    Get task result.

    Returns the output.yaml content and execution metrics for a completed session.
    Includes token usage from the file-based session info.
    """
    session = await session_service.get_session(
        db=db,
        session_id=session_id,
        user_id=user_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Get output from file-based storage
    output = session_service.get_session_output(session_id)

    # Get session info for token usage data
    session_info = session_service.get_session_info(session_id)
    cumulative_usage = session_info.get("cumulative_usage")

    # Build token usage from cumulative stats
    usage = None
    if cumulative_usage:
        usage = TokenUsageResponse(
            input_tokens=cumulative_usage.get("input_tokens", 0),
            output_tokens=cumulative_usage.get("output_tokens", 0),
            cache_creation_input_tokens=cumulative_usage.get(
                "cache_creation_input_tokens", 0
            ),
            cache_read_input_tokens=cumulative_usage.get(
                "cache_read_input_tokens", 0
            ),
        )

    # Build metrics from session data + file-based info
    metrics = ResultMetrics(
        duration_ms=session.duration_ms,
        num_turns=session.num_turns or 0,
        total_cost_usd=session.total_cost_usd,
        model=session.model or session_info.get("model"),
        usage=usage,
    )

    return ResultResponse(
        session_id=session_id,
        status=output.get("status", "FAILED"),
        error=output.get("error", ""),
        comments=output.get("comments", ""),
        output=output.get("output", ""),
        result_files=output.get("result_files", []),
        metrics=metrics,
    )
