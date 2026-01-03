"""
Tests for the health check endpoint.
"""
import pytest
from fastapi.testclient import TestClient


class TestHealthEndpoint:
    """Tests for GET /api/v1/health."""

    @pytest.mark.unit
    def test_health_returns_ok(self, client: TestClient) -> None:
        """Health endpoint returns status ok."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.unit
    def test_health_returns_version(self, client: TestClient) -> None:
        """Health endpoint returns API version."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert data["version"] == "1.0.0"

    @pytest.mark.unit
    def test_health_returns_timestamp(self, client: TestClient) -> None:
        """Health endpoint returns a timestamp."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert "timestamp" in data
        # Timestamp should be a valid ISO format string
        assert "T" in data["timestamp"]

    @pytest.mark.unit
    def test_health_no_auth_required(self, client: TestClient) -> None:
        """Health endpoint doesn't require authentication."""
        # No Authorization header
        response = client.get("/api/v1/health")
        assert response.status_code == 200

