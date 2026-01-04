"""
Tests for authentication endpoints and JWT handling.

Comprehensive coverage of:
- Token generation and validation
- Response structure validation
- Authentication protection on endpoints
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
    def test_token_response_structure(self, client: TestClient) -> None:
        """Token response has complete TokenResponse structure."""
        response = client.post("/api/v1/auth/token")

        assert response.status_code == 200
        data = response.json()
        
        # Validate all TokenResponse fields
        assert "access_token" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 0
        
        assert "token_type" in data
        assert data["token_type"] == "bearer"
        
        assert "user_id" in data
        assert isinstance(data["user_id"], str)
        assert len(data["user_id"]) > 0
        
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

    @pytest.mark.unit
    def test_token_is_valid_jwt(self, client: TestClient) -> None:
        """Token returned is a valid JWT format."""
        response = client.post("/api/v1/auth/token")
        token = response.json()["access_token"]

        # JWT has 3 parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3
        
        # Each part should be non-empty
        for part in parts:
            assert len(part) > 0

    @pytest.mark.unit
    def test_each_request_creates_new_user(self, client: TestClient) -> None:
        """Each token request creates a new anonymous user."""
        response1 = client.post("/api/v1/auth/token")
        response2 = client.post("/api/v1/auth/token")

        user_id1 = response1.json()["user_id"]
        user_id2 = response2.json()["user_id"]

        assert user_id1 != user_id2

    @pytest.mark.unit
    def test_token_returns_json(self, client: TestClient) -> None:
        """Token endpoint returns JSON content type."""
        response = client.post("/api/v1/auth/token")
        
        assert response.status_code == 200
        assert "application/json" in response.headers.get("content-type", "")


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

    @pytest.mark.unit
    def test_validate_empty_token(self, auth_service: AuthService) -> None:
        """Empty token returns None."""
        result = auth_service.validate_token("")
        assert result is None

    @pytest.mark.unit
    def test_validate_malformed_token(self, auth_service: AuthService) -> None:
        """Malformed token (missing parts) returns None."""
        result = auth_service.validate_token("not.a.valid.token.at.all")
        assert result is None


class TestAuthProtection:
    """Tests for authentication protection on endpoints."""

    @pytest.mark.unit
    def test_sessions_requires_auth(self, client: TestClient) -> None:
        """Session endpoints require authentication."""
        response = client.get("/api/v1/sessions")

        assert response.status_code == 401  # Unauthorized without token
        data = response.json()
        assert "detail" in data

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

    @pytest.mark.unit
    def test_missing_bearer_prefix_rejected(self, client: TestClient) -> None:
        """Token without Bearer prefix is rejected."""
        # Get a valid token first
        token_response = client.post("/api/v1/auth/token")
        token = token_response.json()["access_token"]
        
        # Try without Bearer prefix
        headers = {"Authorization": token}
        response = client.get("/api/v1/sessions", headers=headers)

        assert response.status_code == 401

    @pytest.mark.unit
    def test_truncated_token_rejected(self, auth_service: AuthService) -> None:
        """Truncated token (missing signature) returns None on validation."""
        # Generate a valid token then truncate it
        token, _ = auth_service.generate_token("test-user")
        parts = token.split(".")
        # Remove the signature part
        truncated = ".".join(parts[:2])
        
        result = auth_service.validate_token(truncated)
        assert result is None

    @pytest.mark.unit
    def test_health_does_not_require_auth(self, client: TestClient) -> None:
        """Health endpoint is accessible without auth."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
