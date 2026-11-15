"""Ошибочные запросы отклоняются до выполнения операций с БД."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
headers = {"X-User-Id": str(uuid4()), "X-User-Email": "client@example.com"}


@pytest.mark.parametrize(
    "payload",
    [
        {"title": " ", "description": "Описание"},
        {"title": "x" * 201, "description": "Описание"},
        {"title": "Тема", "description": " "},
        {"title": "Тема", "description": "Описание", "priority": "urgent"},
        {"title": "Тема", "description": "Описание", "status": "closed"},
    ],
)
def test_invalid_create(payload):
    assert client.post("/api/v1/tickets", json=payload, headers=headers).status_code == 422


@pytest.mark.parametrize(
    "bad_headers",
    [
        {},
        {"X-User-Id": "not-a-uuid", "X-User-Email": "client@example.com"},
        {"X-User-Id": str(uuid4()), "X-User-Email": "not-an-email"},
    ],
)
def test_temporary_author_headers_required(bad_headers):
    response = client.post(
        "/api/v1/tickets",
        json={"title": "Тема", "description": "Описание"},
        headers=bad_headers,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/tickets/0",
        "/api/v1/tickets/9223372036854775808",
        "/api/v1/tickets?page=0",
        "/api/v1/tickets?size=101",
        "/api/v1/tickets?status=unknown",
        "/api/v1/tickets?author_id=bad",
    ],
)
def test_invalid_path_and_query(path):
    assert client.get(path).status_code == 422


@pytest.mark.parametrize("version", [0, -1, True, "1", 2147483647])
def test_invalid_version(version):
    response = client.post(
        "/api/v1/tickets/1/status", json={"status": "closed", "version": version}
    )
    assert response.status_code == 422
