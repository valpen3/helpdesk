"""Структурированные JSON-логи и сквозной X-Request-ID.

Каждая строка лога — один JSON-объект с полями service, level, timestamp, event
и, внутри HTTP-запроса, request_id. По request_id один запрос прослеживается
через логи gateway и всех сервисов.
"""

import logging
import sys
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


def setup_logging(service: str, level: str = "INFO") -> None:
    """Настроить structlog. Вызывается один раз при старте сервиса."""

    def add_service(_logger, _method, event_dict):
        event_dict.setdefault("service", service)
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_service,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Берёт X-Request-ID из запроса (его ставит Nginx) или создаёт новый,
    кладёт в контекст логов, возвращает в ответе и пишет строку access-лога."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        log = get_logger("access")
        started = time.perf_counter()
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                response = await call_next(request)
            except Exception:
                log.exception("request_failed", method=request.method, path=request.url.path)
                raise
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            # health-проверки дёргаются каждые несколько секунд, не засоряем ими лог
            if not request.url.path.startswith("/health"):
                log.info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_ms=duration_ms,
                )
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
