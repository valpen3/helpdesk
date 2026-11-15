import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from helpdesk_common import RequestIdMiddleware, get_logger, make_health_router, setup_logging
from helpdesk_sla.api import router as sla_router
from helpdesk_sla.config import settings
from helpdesk_sla.consumer import SlaConsumer
from helpdesk_sla.db import engine, ping_db, session_factory
from helpdesk_sla.publisher import open_publisher
from helpdesk_sla.scheduler import run_scheduler

setup_logging("sla-service", settings.log_level)
log = get_logger(__name__)
consumer = SlaConsumer(settings)
consumer_task: asyncio.Task | None = None
scheduler_task: asyncio.Task | None = None


async def check_consumer() -> None:
    await consumer.check_ready()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global consumer_task, scheduler_task
    async with AsyncExitStack() as stack:
        publisher = await stack.enter_async_context(open_publisher(settings))
        consumer_task = asyncio.create_task(consumer.run())
        scheduler_task = asyncio.create_task(
            run_scheduler(session_factory, publisher, settings.sla_check_interval)
        )
        try:
            yield
        finally:
            for task in (consumer_task, scheduler_task):
                if task is not None:
                    task.cancel()
            for task in (consumer_task, scheduler_task):
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task
            consumer_task = None
            scheduler_task = None
            await engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestIdMiddleware)
app.include_router(sla_router)
app.include_router(
    make_health_router(
        {"postgres": ping_db, "rabbitmq": check_consumer}, timeout=settings.mq_timeout
    )
)


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
