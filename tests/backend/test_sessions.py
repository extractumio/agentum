"""
Tests for session management endpoints.
"""
import pytest
from fastapi.testclient import TestClient


class TestSessionRun:
    """Tests for POST /api/v1/sessions/run (unified endpoint)."""

    @pytest.mark.unit
    def test_run_task_creates_and_starts(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Run endpoint creates session and starts task."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={"task": "Test task"}
        )

        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert data["status"] == "running"
        assert data["message"] == "Task execution started"

    @pytest.mark.unit
    def test_run_task_with_config_overrides(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can pass config overrides to run endpoint."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={
                "task": "Test task",
                "working_dir": "/tmp",
                "config": {
                    "model": "claude-sonnet-4-5-20250929",
                    "max_turns": 50,
                    "enable_skills": False
                }
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_run_task_with_resume(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can specify session to resume."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={
                "task": "Continue the task",
                "resume_session_id": "20260101_120000_abc12345",
                "fork_session": True
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["resumed_from"] == "20260101_120000_abc12345"

    @pytest.mark.unit
    def test_run_task_requires_task(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Task field is required."""
        response = client.post(
            "/api/v1/sessions/run",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 422  # Validation error


class TestSessionCreate:
    """Tests for POST /api/v1/sessions."""

    @pytest.mark.unit
    def test_create_session(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can create a new session."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task", "working_dir": "/tmp"}
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["status"] == "pending"
        assert data["task"] == "Test task"

    @pytest.mark.unit
    def test_create_session_generates_id(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Created session has a valid ID format."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task"}
        )

        session_id = response.json()["id"]
        # Session ID format: YYYYMMDD_HHMMSS_uuid8
        parts = session_id.split("_")
        assert len(parts) == 3
        assert len(parts[0]) == 8  # Date
        assert len(parts[1]) == 6  # Time
        assert len(parts[2]) == 8  # UUID fragment

    @pytest.mark.unit
    def test_create_session_with_model(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can specify model when creating session."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={
                "task": "Test task",
                "model": "claude-sonnet-4-5-20250929"
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "claude-sonnet-4-5-20250929"

    @pytest.mark.unit
    def test_create_session_requires_task(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Task field is required."""
        response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 422  # Validation error


class TestSessionList:
    """Tests for GET /api/v1/sessions."""

    @pytest.mark.unit
    def test_list_sessions_empty(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """List returns empty when no sessions exist."""
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["sessions"] == []
        assert data["total"] == 0

    @pytest.mark.unit
    def test_list_sessions_after_create(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Created sessions appear in list."""
        # Create a session
        client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Task 1"}
        )

        # List sessions
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data["sessions"]) == 1
        assert data["total"] == 1

    @pytest.mark.unit
    def test_sessions_isolated_by_user(self, client: TestClient) -> None:
        """Users can only see their own sessions."""
        # Create session with user 1
        response1 = client.post("/api/v1/auth/token")
        headers1 = {"Authorization": f"Bearer {response1.json()['access_token']}"}
        client.post(
            "/api/v1/sessions",
            headers=headers1,
            json={"task": "User 1 task"}
        )

        # Create session with user 2
        response2 = client.post("/api/v1/auth/token")
        headers2 = {"Authorization": f"Bearer {response2.json()['access_token']}"}
        client.post(
            "/api/v1/sessions",
            headers=headers2,
            json={"task": "User 2 task"}
        )

        # User 1 should only see their session
        list1 = client.get("/api/v1/sessions", headers=headers1)
        assert list1.json()["total"] == 1
        assert list1.json()["sessions"][0]["task"] == "User 1 task"

        # User 2 should only see their session
        list2 = client.get("/api/v1/sessions", headers=headers2)
        assert list2.json()["total"] == 1
        assert list2.json()["sessions"][0]["task"] == "User 2 task"


class TestSessionGet:
    """Tests for GET /api/v1/sessions/{id}."""

    @pytest.mark.unit
    def test_get_session(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can get a specific session by ID."""
        # Create a session
        create_response = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task"}
        )
        session_id = create_response.json()["id"]

        # Get the session
        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["task"] == "Test task"

    @pytest.mark.unit
    def test_get_session_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session."""
        response = client.get(
            "/api/v1/sessions/nonexistent-session-id",
            headers=auth_headers
        )

        assert response.status_code == 404

    @pytest.mark.unit
    def test_get_session_wrong_user(self, client: TestClient) -> None:
        """Cannot get another user's session."""
        # Create session with user 1
        response1 = client.post("/api/v1/auth/token")
        headers1 = {"Authorization": f"Bearer {response1.json()['access_token']}"}
        create = client.post(
            "/api/v1/sessions",
            headers=headers1,
            json={"task": "Private task"}
        )
        session_id = create.json()["id"]

        # Try to get with user 2
        response2 = client.post("/api/v1/auth/token")
        headers2 = {"Authorization": f"Bearer {response2.json()['access_token']}"}
        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers=headers2
        )

        assert response.status_code == 404


class TestSessionTask:
    """Tests for POST /api/v1/sessions/{id}/task."""

    @pytest.mark.unit
    def test_start_task(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can start a task for a session."""
        # Create a session
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Initial task"}
        )
        session_id = create.json()["id"]

        # Start the task
        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={"task": "Execute: echo hello"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert data["session_id"] == session_id

    @pytest.mark.unit
    def test_start_task_with_empty_body(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can start task with empty body, uses session's task."""
        # Create a session with task
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Session task"}
        )
        session_id = create.json()["id"]

        # Start with empty body - should use session's task
        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_start_task_with_config_overrides(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can pass config overrides when starting task."""
        # Create a session
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task"}
        )
        session_id = create.json()["id"]

        # Start with config overrides
        response = client.post(
            f"/api/v1/sessions/{session_id}/task",
            headers=auth_headers,
            json={
                "config": {
                    "model": "claude-haiku-4-5-20251001",
                    "max_turns": 10
                }
            }
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"

    @pytest.mark.unit
    def test_start_task_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session."""
        response = client.post(
            "/api/v1/sessions/nonexistent/task",
            headers=auth_headers,
            json={"task": "Test"}
        )

        assert response.status_code == 404


class TestSessionCancel:
    """Tests for POST /api/v1/sessions/{id}/cancel."""

    @pytest.mark.unit
    def test_cancel_session(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can request cancellation of a session."""
        # Create a session
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Long task"}
        )
        session_id = create.json()["id"]

        # Cancel it
        response = client.post(
            f"/api/v1/sessions/{session_id}/cancel",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id

    @pytest.mark.unit
    def test_cancel_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session."""
        response = client.post(
            "/api/v1/sessions/nonexistent/cancel",
            headers=auth_headers
        )

        assert response.status_code == 404


class TestSessionResult:
    """Tests for GET /api/v1/sessions/{id}/result."""

    @pytest.mark.unit
    def test_get_result(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Can get result for a session."""
        # Create a session
        create = client.post(
            "/api/v1/sessions",
            headers=auth_headers,
            json={"task": "Test task"}
        )
        session_id = create.json()["id"]

        # Get result
        response = client.get(
            f"/api/v1/sessions/{session_id}/result",
            headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert "status" in data

    @pytest.mark.unit
    def test_get_result_not_found(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Returns 404 for non-existent session."""
        response = client.get(
            "/api/v1/sessions/nonexistent/result",
            headers=auth_headers
        )

        assert response.status_code == 404

