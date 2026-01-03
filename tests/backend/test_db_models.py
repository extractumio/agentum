"""
Tests for database models.
"""
import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import User, Session


class TestUserModel:
    """Tests for the User model."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_user(self, test_session: AsyncSession) -> None:
        """Can create a user in the database."""
        user = User(id="test-user-id", type="anonymous")
        test_session.add(user)
        await test_session.commit()

        result = await test_session.execute(
            select(User).where(User.id == "test-user-id")
        )
        db_user = result.scalar_one()

        assert db_user.id == "test-user-id"
        assert db_user.type == "anonymous"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_default_type(self, test_session: AsyncSession) -> None:
        """User type defaults to 'anonymous'."""
        user = User(id="default-type-user")
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        assert user.type == "anonymous"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_user_created_at(self, test_session: AsyncSession) -> None:
        """User has a created_at timestamp."""
        user = User(id="timestamp-user")
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        assert user.created_at is not None
        assert isinstance(user.created_at, datetime)


class TestSessionModel:
    """Tests for the Session model."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_session(self, test_session: AsyncSession) -> None:
        """Can create a session in the database."""
        # First create a user
        user = User(id="session-owner")
        test_session.add(user)
        await test_session.commit()

        # Create session
        session = Session(
            id="20260103_120000_abcd1234",
            user_id="session-owner",
            task="Test task",
            status="pending"
        )
        test_session.add(session)
        await test_session.commit()

        result = await test_session.execute(
            select(Session).where(Session.id == "20260103_120000_abcd1234")
        )
        db_session = result.scalar_one()

        assert db_session.id == "20260103_120000_abcd1234"
        assert db_session.task == "Test task"
        assert db_session.status == "pending"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_defaults(self, test_session: AsyncSession) -> None:
        """Session has correct default values."""
        user = User(id="defaults-owner")
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_defaults",
            user_id="defaults-owner",
            task="Defaults test"
        )
        test_session.add(session)
        await test_session.commit()
        await test_session.refresh(session)

        assert session.status == "pending"
        assert session.num_turns == 0
        assert session.cancel_requested is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_user_relationship(
        self,
        test_session: AsyncSession
    ) -> None:
        """Session has relationship to user."""
        user = User(id="rel-owner")
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_relation",
            user_id="rel-owner",
            task="Relationship test"
        )
        test_session.add(session)
        await test_session.commit()

        # Refresh to load relationship
        await test_session.refresh(session)
        await test_session.refresh(user)

        assert session.user_id == user.id

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_session_update(self, test_session: AsyncSession) -> None:
        """Can update session fields."""
        user = User(id="update-owner")
        test_session.add(user)
        await test_session.commit()

        session = Session(
            id="20260103_120000_update",
            user_id="update-owner",
            task="Update test"
        )
        test_session.add(session)
        await test_session.commit()

        # Update the session
        session.status = "completed"
        session.num_turns = 5
        session.total_cost_usd = 0.0123
        await test_session.commit()
        await test_session.refresh(session)

        assert session.status == "completed"
        assert session.num_turns == 5
        assert session.total_cost_usd == pytest.approx(0.0123)

