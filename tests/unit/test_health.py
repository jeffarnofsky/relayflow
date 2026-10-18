from fastapi.testclient import TestClient

from relayflow.main import app

client = TestClient(app)


def test_health_live() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "status": "alive",
        "service": "relayflow",
    }
