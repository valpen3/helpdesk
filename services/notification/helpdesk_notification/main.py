import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from helpdesk_common import RequestIdMiddleware, make_health_router, setup_logging
from helpdesk_notification.config import Settings
from helpdesk_notification.consumer import Consumer
from helpdesk_notification.mailer import Mailer

settings = Settings()
setup_logging("notification-service", settings.log_level)
mailer = Mailer(settings)
consumer = Consumer(settings, mailer)
worker: asyncio.Task | None = None


async def check_worker():
    if worker is None or worker.done():
        raise RuntimeError("Consumer task is not running")
    await consumer.check_ready()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker
    worker = asyncio.create_task(consumer.run())
    try:
        yield
    finally:
        worker.cancel()
        with suppress(asyncio.CancelledError):
            await worker
        worker = None


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestIdMiddleware)
app.include_router(
    make_health_router(
        {"consumer": check_worker, "smtp": mailer.ping}, timeout=settings.io_timeout + 1
    )
)
