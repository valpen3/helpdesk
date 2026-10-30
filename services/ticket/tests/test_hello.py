from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_hello():
    response = client.get("/hello")
    assert response.status_code == 200
    assert response.json() == {"message": "hello from ticket-service"}


def test_health_live():
    assert client.get("/health/live").json() == {"status": "ok"}
