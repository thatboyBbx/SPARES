"""
Phase 0 smoke test: the FastAPI app must boot and respond on /health.

This is deliberately the first test in the repo — everything else in
later phases assumes this passes.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check_returns_ok() -> None:
    """The /health endpoint should report status 'ok' with a 200 response."""
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert "environment" in body
