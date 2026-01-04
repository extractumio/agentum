"""
Pytest configuration and fixtures for backend tests.

Provides fixtures for:
- In-memory test database
- FastAPI test client with mock dependencies
- Temporary sessions directory with automatic cleanup
- Mock services (agent runner)
"""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Add project root to path before importing project modules
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db.database import Base, get_db  # noqa: E402
from src.api.main import create_app  # noqa: E402
from src.services.auth_service import AuthService  # noqa: E402
from src.services.agent_runner import AgentRunner  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "unit: marks tests as unit tests (fast, no external dependencies)"
    )
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (may use real database)"
    )


# In-memory SQLite for testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def temp_sessions_dir() -> Generator[Path, None, None]:
    """
    Create a temporary directory for test sessions.
    
    This directory is automatically cleaned up after each test,
    preventing session folder artifacts from accumulating.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="agentum_test_sessions_"))
    yield temp_dir
    # Cleanup after test
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest_asyncio.fixture
async def test_engine():
    """Create a test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session


@pytest_asyncio.fixture
async def test_db_override(test_engine):
    """
    Fixture that provides a dependency override for get_db.

    Returns a function that can be used with app.dependency_overrides.
    """
    async_session = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with async_session() as session:
            yield session

    return override_get_db


@pytest.fixture
def mock_agent_runner() -> MagicMock:
    """Create a mock agent runner that doesn't actually run agents."""
    runner = MagicMock(spec=AgentRunner)
    runner.is_running.return_value = False
    runner.start_task = AsyncMock()
    runner.cancel_task = AsyncMock(return_value=True)
    runner.get_result.return_value = None
    return runner


@pytest.fixture
def auth_service() -> AuthService:
    """Create an auth service instance for testing."""
    service = AuthService()
    # Set a fixed JWT secret for testing
    service._jwt_secret = "test-secret-key-for-jwt-signing"
    return service


@pytest.fixture
def test_session_service(temp_sessions_dir):
    """
    Create a session service that uses temp directory.
    
    This prevents test sessions from being created in the real sessions folder.
    """
    from src.services.session_service import SessionService
    return SessionService(sessions_dir=temp_sessions_dir)


@pytest.fixture
def test_app(test_db_override, mock_agent_runner, temp_sessions_dir):
    """
    Create a FastAPI app configured for testing.

    Uses in-memory database, mock agent runner, and temp sessions directory.
    """
    from src.services.session_service import SessionService
    
    # Create session service with temp directory BEFORE patching
    temp_session_service = SessionService(sessions_dir=temp_sessions_dir)
    
    # Patch at multiple levels to ensure temp directory is used
    with patch("src.api.main.load_api_config") as mock_config:
        mock_config.return_value = {
            "api": {
                "host": "0.0.0.0",
                "port": 40080,
                "cors_origins": ["http://localhost:50080"],
            }
        }

        # Patch session_service at the routes level (this is where it's imported)
        with patch("src.api.routes.sessions.session_service", temp_session_service):
            app = create_app()

            # Override database dependency
            app.dependency_overrides[get_db] = test_db_override

            # Patch agent runner for session routes
            with patch("src.api.routes.sessions.agent_runner", mock_agent_runner):
                yield app

            app.dependency_overrides.clear()


@pytest.fixture
def client(test_app) -> Generator[TestClient, None, None]:
    """Create a synchronous test client."""
    with TestClient(test_app) as c:
        yield c


@pytest_asyncio.fixture
async def async_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
def auth_headers(client) -> dict:
    """Get authentication headers with a valid token."""
    response = client.post("/api/v1/auth/token")
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def async_auth_headers(async_client) -> dict:
    """Get authentication headers for async client."""
    response = await async_client.post("/api/v1/auth/token")
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def created_session(client, auth_headers) -> dict:
    """Create a session and return its data for use in tests."""
    response = client.post(
        "/api/v1/sessions",
        headers=auth_headers,
        json={"task": "Fixture test task", "working_dir": "/tmp"}
    )
    assert response.status_code == 201
    return response.json()


# Cleanup fixture to run after all tests in the module
@pytest.fixture(scope="session", autouse=True)
def cleanup_test_artifacts():
    """
    Clean up any leftover test artifacts after all tests complete.
    
    This is a safety net in case individual cleanups fail.
    """
    yield
    # After all tests, clean up any temp directories that might remain
    temp_base = Path(tempfile.gettempdir())
    for item in temp_base.iterdir():
        if item.is_dir() and item.name.startswith("agentum_test_sessions_"):
            try:
                shutil.rmtree(item, ignore_errors=True)
            except Exception:
                pass
