"""
Tests for the session service.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User, Session
from src.services.session_service import SessionService


@pytest_asyncio.fixture
async def session_service_with_user(
    test_session: AsyncSession
) -> tuple[SessionService, str]:
    """Create a session service and a test user."""
    # Create a test user
    user = User(id="service-test-user")
    test_session.add(user)
    await test_session.commit()

    service = SessionService()
    return service, "service-test-user"


class TestSessionServiceCreate:
    """Tests for session creation."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can create a session through the service."""
        service, user_id = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Service test task",
            working_dir="/tmp"
        )

        assert session.id is not None
        assert session.task == "Service test task"
        assert session.status == "pending"
        assert session.user_id == user_id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session_with_model(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can specify model when creating session."""
        service, user_id = session_service_with_user

        session = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Model test",
            model="claude-haiku-4-5-20251001"
        )

        assert session.model == "claude-haiku-4-5-20251001"


class TestSessionServiceQuery:
    """Tests for session queries."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can get a session by ID."""
        service, user_id = session_service_with_user

        # Create a session
        created = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Get test"
        )

        # Get it back
        session = await service.get_session(
            db=test_session,
            session_id=created.id,
            user_id=user_id
        )

        assert session is not None
        assert session.id == created.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_not_found(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Returns None for non-existent session."""
        service, user_id = session_service_with_user

        session = await service.get_session(
            db=test_session,
            session_id="nonexistent",
            user_id=user_id
        )

        assert session is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_session_wrong_user(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Returns None when user doesn't match."""
        service, user_id = session_service_with_user

        # Create a session
        created = await service.create_session(
            db=test_session,
            user_id=user_id,
            task="Wrong user test"
        )

        # Try to get with different user
        session = await service.get_session(
            db=test_session,
            session_id=created.id,
            user_id="different-user"
        )

        assert session is None

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_sessions(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can list sessions for a user."""
        service, user_id = session_service_with_user

        # Create multiple sessions
        await service.create_session(
            db=test_session, user_id=user_id, task="Task 1"
        )
        await service.create_session(
            db=test_session, user_id=user_id, task="Task 2"
        )

        sessions, total = await service.list_sessions(
            db=test_session,
            user_id=user_id
        )

        assert total == 2
        assert len(sessions) == 2

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_list_sessions_pagination(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """List supports pagination."""
        service, user_id = session_service_with_user

        # Create 5 sessions
        for i in range(5):
            await service.create_session(
                db=test_session, user_id=user_id, task=f"Task {i}"
            )

        # Get first 2
        sessions, total = await service.list_sessions(
            db=test_session,
            user_id=user_id,
            limit=2,
            offset=0
        )

        assert total == 5
        assert len(sessions) == 2


class TestSessionServiceUpdate:
    """Tests for session updates."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_status(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can update session status."""
        service, user_id = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Update test"
        )

        updated = await service.update_session(
            db=test_session,
            session=session,
            status="running"
        )

        assert updated.status == "running"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_metrics(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can update session metrics."""
        service, user_id = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Metrics test"
        )

        updated = await service.update_session(
            db=test_session,
            session=session,
            num_turns=10,
            duration_ms=5000,
            total_cost_usd=0.05
        )

        assert updated.num_turns == 10
        assert updated.duration_ms == 5000
        assert updated.total_cost_usd == pytest.approx(0.05)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_request_cancellation(
        self,
        test_session: AsyncSession,
        session_service_with_user: tuple[SessionService, str]
    ) -> None:
        """Can request cancellation."""
        service, user_id = session_service_with_user

        session = await service.create_session(
            db=test_session, user_id=user_id, task="Cancel test"
        )

        updated = await service.request_cancellation(
            db=test_session,
            session=session
        )

        assert updated.cancel_requested is True

