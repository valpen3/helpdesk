"""ticket-service: API тикетов, hello-world и проверки готовности."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.tickets import router as tickets_router
from app.config import settings
from app.db import engine, ping_db
from helpdesk_common import RequestIdMiddleware, get_logger, make_health_router, setup_logging

setup_logging(settings.service_name, settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await engine.dispose()


app = FastAPI(
    title="ticket-service",
    version="0.1.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.include_router(make_health_router({"postgres": ping_db}))
app.include_router(tickets_router)


@app.get("/hello")
async def hello() -> dict[str, str]:
    log.info("hello_called")
    return {"message": "hello from ticket-service"}
