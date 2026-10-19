from fastapi.testclient import TestClient

from relayflow.api.routes import health
from relayflow.main import app

client = TestClient(app)


def test_liveness_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_when_dependencies_are_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_database_ready", lambda: True)
    monkeypatch.setattr(health, "_redis_ready", lambda: True)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {
            "database": True,
            "redis": True,
        },
    }


def test_readiness_fails_when_database_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_database_ready", lambda: False)
    monkeypatch.setattr(health, "_redis_ready", lambda: True)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "not_ready"


def test_worker_check_when_worker_is_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_worker_count", lambda: 1)

    response = client.get("/health/worker")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "workers_available": 1,
    }


def test_worker_check_when_no_worker_is_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(health, "_worker_count", lambda: 0)

    response = client.get("/health/worker")

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "worker_unavailable"
