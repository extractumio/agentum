"""
Session service for Agentum API.

Handles session CRUD operations and synchronization between
the database and file-based SessionManager.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import SESSIONS_DIR
from ..core.sessions import SessionManager, generate_session_id
from ..db.models import Session

logger = logging.getLogger(__name__)


class SessionService:
    """
    Service for session management.

    Provides methods for creating, querying, and updating sessions.
    Synchronizes between SQLite database and file-based session storage.
    """

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        """
        Initialize the session service.

        Args:
            sessions_dir: Directory for file-based session storage.
        """
        self._sessions_dir = sessions_dir or SESSIONS_DIR
        self._session_manager = SessionManager(self._sessions_dir)

    async def create_session(
        self,
        db: AsyncSession,
        user_id: str,
        task: str,
        working_dir: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Session:
        """
        Create a new session.

        Creates both a database record and the file-based session folder.

        Args:
            db: Database session.
            user_id: Owner user ID.
            task: Task description.
            working_dir: Working directory for the agent.
            model: Claude model to use.

        Returns:
            The created Session database object.
        """
        session_id = generate_session_id()

        # Create file-based session (creates folder structure)
        self._session_manager.create_session(
            working_dir=working_dir or str(self._sessions_dir),
            session_id=session_id
        )

        # Create database record
        db_session = Session(
            id=session_id,
            user_id=user_id,
            status="pending",
            task=task,
            model=model,
            working_dir=working_dir,
        )
        db.add(db_session)
        await db.commit()
        await db.refresh(db_session)

        logger.info(f"Created session: {session_id} for user: {user_id}")
        return db_session

    async def get_session(
        self,
        db: AsyncSession,
        session_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Session]:
        """
        Get a session by ID.

        Args:
            db: Database session.
            session_id: The session ID.
            user_id: Optional user ID to verify ownership.

        Returns:
            Session if found and authorized, None otherwise.
        """
        query = select(Session).where(Session.id == session_id)

        if user_id:
            query = query.where(Session.user_id == user_id)

        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        db: AsyncSession,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Session], int]:
        """
        List sessions for a user.

        Args:
            db: Database session.
            user_id: The user ID.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.

        Returns:
            Tuple of (sessions list, total count).
        """
        # Count query
        from sqlalchemy import func
        count_query = select(func.count()).select_from(Session).where(
            Session.user_id == user_id
        )
        count_result = await db.execute(count_query)
        total = count_result.scalar_one()

        # List query
        query = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(query)
        sessions = list(result.scalars().all())

        return sessions, total

    async def update_session(
        self,
        db: AsyncSession,
        session: Session,
        status: Optional[str] = None,
        num_turns: Optional[int] = None,
        duration_ms: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        cancel_requested: Optional[bool] = None,
        completed_at: Optional[datetime] = None,
    ) -> Session:
        """
        Update a session.

        Args:
            db: Database session.
            session: The session to update.
            status: New status.
            num_turns: Number of turns.
            duration_ms: Duration in milliseconds.
            total_cost_usd: Total cost in USD.
            cancel_requested: Whether cancellation was requested.
            completed_at: Completion timestamp.

        Returns:
            The updated session.
        """
        if status is not None:
            session.status = status
        if num_turns is not None:
            session.num_turns = num_turns
        if duration_ms is not None:
            session.duration_ms = duration_ms
        if total_cost_usd is not None:
            session.total_cost_usd = total_cost_usd
        if cancel_requested is not None:
            session.cancel_requested = cancel_requested
        if completed_at is not None:
            session.completed_at = completed_at

        session.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(session)

        return session

    async def request_cancellation(
        self,
        db: AsyncSession,
        session: Session,
    ) -> Session:
        """
        Request cancellation for a running session.

        Args:
            db: Database session.
            session: The session to cancel.

        Returns:
            The updated session.
        """
        return await self.update_session(
            db,
            session,
            cancel_requested=True,
        )

    def get_session_output(self, session_id: str) -> dict:
        """
        Get the output.yaml content for a session.

        Args:
            session_id: The session ID.

        Returns:
            Parsed output.yaml as a dictionary.
        """
        return self._session_manager.parse_output(session_id)

    def get_session_info(self, session_id: str) -> dict:
        """
        Get the file-based session info (session_info.json).

        This contains token usage, cumulative stats, and other metadata
        not stored in the database.

        Args:
            session_id: The session ID.

        Returns:
            Session info as a dictionary, or empty dict if not found.
        """
        try:
            session_info = self._session_manager.load_session(session_id)
            return session_info.model_dump()
        except Exception as e:
            logger.warning(f"Failed to load session info for {session_id}: {e}")
            return {}

    def is_cancellation_requested(self, session_id: str) -> bool:
        """
        Check if cancellation was requested for a session.

        This is used by the agent runner to check for cancellation
        without requiring a database connection.

        Args:
            session_id: The session ID.

        Returns:
            True if cancellation was requested.
        """
        # For now, we just return False. The actual check will be done
        # via the database in the agent runner.
        return False


# Global session service instance
session_service = SessionService()
