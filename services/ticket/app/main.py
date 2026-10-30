"""ticket-service, этап 1: заглушка hello-world.

На следующих этапах здесь появятся подключения к PostgreSQL, Redis и RabbitMQ
(в lifespan) и роутеры с эндпоинтами тикетов.
"""

from fastapi import FastAPI

from app.config import settings
from helpdesk_common import RequestIdMiddleware, get_logger, make_health_router, setup_logging

setup_logging(settings.service_name, settings.log_level)
log = get_logger(__name__)

app = FastAPI(
    title="ticket-service",
    version="0.1.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
)
app.add_middleware(RequestIdMiddleware)
app.include_router(make_health_router())


@app.get("/hello")
async def hello() -> dict[str, str]:
    log.info("hello_called")
    return {"message": "hello from ticket-service"}
