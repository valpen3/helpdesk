from fastapi import FastAPI
from fastapi.testclient import TestClient

from helpdesk_common.health import make_health_router
from helpdesk_common.logging import RequestIdMiddleware, setup_logging


async def ok_check():
    return True


async def broken_check():
    raise ConnectionError("redis is down")


def make_app(checks):
    setup_logging("test-service")
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    app.include_router(make_health_router(checks))
    return app


def test_live():
    client = TestClient(make_app({}))
    assert client.get("/health/live").json() == {"status": "ok"}


def test_ready_ok():
    client = TestClient(make_app({"postgres": ok_check}))
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"] == {"postgres": "ok"}


def test_ready_fails_when_dependency_down():
    client = TestClient(make_app({"postgres": ok_check, "redis": broken_check}))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] == "fail: ConnectionError"


def test_request_id_is_propagated():
    client = TestClient(make_app({}))
    response = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
