"""ticket-service: API тикетов, hello-world и проверки готовности."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from app.api.tickets import router as tickets_router
from app.cache import TicketCache, make_redis
from app.config import settings
from app.db import engine, ping_db
from app.messaging.outbox import run_outbox_worker
from app.messaging.sla_consumer import open_sla_consumer
from helpdesk_common import RequestIdMiddleware, get_logger, make_health_router, setup_logging
from helpdesk_common.metrics import instrument_app

setup_logging(settings.service_name, settings.log_level)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = make_redis(settings) if settings.cache_enabled else None
    app.state.ticket_cache = TicketCache(redis, timeout=settings.cache_timeout)
    outbox_task = asyncio.create_task(run_outbox_worker(), name="ticket-outbox")
    try:
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(
                open_sla_consumer(
                    host=settings.mq_host,
                    port=settings.mq_port,
                    login=settings.mq_user,
                    password=settings.mq_password.get_secret_value(),
                    virtualhost=settings.mq_vhost,
                    queue_name=settings.mq_sla_queue,
                    cache=app.state.ticket_cache,
                    timeout=settings.mq_timeout,
                )
            )
            yield
    finally:
        outbox_task.cancel()
        try:
            await outbox_task
        except asyncio.CancelledError:
            pass
        if redis is not None:
            await redis.aclose()
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


instrument_app(app)
