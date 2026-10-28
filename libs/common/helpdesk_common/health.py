"""Эндпоинты /health/live и /health/ready.

live  — процесс жив (используется healthcheck в docker compose);
ready — сервис готов обслуживать запросы: все зависимости (БД, Redis, брокер)
        отвечают. Если хоть одна не отвечает — 503, и Nginx/оркестратор
        не должны слать сюда трафик.
"""

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

Check = Callable[[], Awaitable[object]]


def make_health_router(checks: dict[str, Check] | None = None, timeout: float = 2.0) -> APIRouter:
    """checks — словарь «имя → асинхронная функция», которая бросает исключение,
    если зависимость недоступна. Например: {"postgres": ping_db, "redis": redis.ping}."""
    checks = checks or {}
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready")
    async def ready() -> JSONResponse:
        results: dict[str, str] = {}
        healthy = True
        for name, check in checks.items():
            try:
                await asyncio.wait_for(check(), timeout=timeout)
                results[name] = "ok"
            except Exception as exc:  # noqa: BLE001 — любая ошибка = зависимость недоступна
                healthy = False
                results[name] = f"fail: {type(exc).__name__}"
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "fail", "checks": results},
        )

    return router
