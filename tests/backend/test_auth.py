"""
Tests for authentication endpoints and JWT handling.
"""
import pytest
from fastapi.testclient import TestClient

from src.services.auth_service import AuthService


class TestAuthEndpoint:
    """Tests for POST /api/v1/auth/token."""

    @pytest.mark.unit
    def test_get_anonymous_token(self, client: TestClient) -> None:
        """Can get an anonymous JWT token."""
        response = client.post("/api/v1/auth/token")

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "user_id" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] > 0

    @pytest.mark.unit
    def test_token_is_valid_jwt(self, client: TestClient) -> None:
        """Token returned is a valid JWT format."""
        response = client.post("/api/v1/auth/token")
        token = response.json()["access_token"]

        # JWT has 3 parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3

    @pytest.mark.unit
    def test_each_request_creates_new_user(self, client: TestClient) -> None:
        """Each token request creates a new anonymous user."""
        response1 = client.post("/api/v1/auth/token")
        response2 = client.post("/api/v1/auth/token")

        user_id1 = response1.json()["user_id"]
        user_id2 = response2.json()["user_id"]

        assert user_id1 != user_id2


class TestAuthService:
    """Unit tests for AuthService."""

    @pytest.mark.unit
    def test_generate_token(self, auth_service: AuthService) -> None:
        """Can generate a JWT token."""
        token, expires_in = auth_service.generate_token("test-user-id")

        assert token is not None
        assert len(token) > 0
        assert expires_in > 0

    @pytest.mark.unit
    def test_validate_token_success(self, auth_service: AuthService) -> None:
        """Valid token returns the user ID."""
        user_id = "test-user-123"
        token, _ = auth_service.generate_token(user_id)

        result = auth_service.validate_token(token)

        assert result == user_id

    @pytest.mark.unit
    def test_validate_token_invalid(self, auth_service: AuthService) -> None:
        """Invalid token returns None."""
        result = auth_service.validate_token("invalid-token")

        assert result is None

    @pytest.mark.unit
    def test_validate_token_tampered(self, auth_service: AuthService) -> None:
        """Tampered token returns None."""
        token, _ = auth_service.generate_token("test-user")
        # Tamper with the token
        tampered = token[:-5] + "xxxxx"

        result = auth_service.validate_token(tampered)

        assert result is None


class TestAuthProtection:
    """Tests for authentication protection on endpoints."""

    @pytest.mark.unit
    def test_sessions_requires_auth(self, client: TestClient) -> None:
        """Session endpoints require authentication."""
        response = client.get("/api/v1/sessions")

        assert response.status_code == 401  # Unauthorized without token

    @pytest.mark.unit
    def test_sessions_with_valid_token(
        self,
        client: TestClient,
        auth_headers: dict
    ) -> None:
        """Session endpoints work with valid token."""
        response = client.get("/api/v1/sessions", headers=auth_headers)

        assert response.status_code == 200

    @pytest.mark.unit
    def test_invalid_token_rejected(self, client: TestClient) -> None:
        """Invalid token is rejected."""
        headers = {"Authorization": "Bearer invalid-token"}
        response = client.get("/api/v1/sessions", headers=headers)

        assert response.status_code == 401

