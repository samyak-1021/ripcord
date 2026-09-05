"""Tests for the health-check endpoint."""

from fastapi.testclient import TestClient

from ripcord.main import create_app

client = TestClient(create_app())


def test_health_returns_ok() -> None:
    """/health should return 200 with a well-formed body."""
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "ripcord"
    assert "version" in body
