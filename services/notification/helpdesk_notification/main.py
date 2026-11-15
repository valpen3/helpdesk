import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from helpdesk_common import RequestIdMiddleware, make_health_router, setup_logging
from helpdesk_common.metrics import instrument_app
from helpdesk_common.redis_client import make_redis_client
from helpdesk_notification.config import Settings
from helpdesk_notification.consumer import Consumer
from helpdesk_notification.idempotency import DeliveryRegistry
from helpdesk_notification.mailer import Mailer

settings = Settings()
setup_logging("notification-service", settings.log_level)
mailer = Mailer(settings)
redis = make_redis_client(
    host=settings.redis_host,
    port=settings.redis_port,
    username=settings.redis_user,
    password=settings.redis_password.get_secret_value(),
    decode_responses=True,
    socket_timeout=settings.io_timeout,
)
registry = DeliveryRegistry(
    redis, done_ttl=settings.idempotency_ttl, lock_ttl=settings.idempotency_lock_ttl
)
consumer = Consumer(settings, mailer, registry)
worker: asyncio.Task | None = None


async def check_worker():
    if worker is None or worker.done():
        raise RuntimeError("Consumer task is not running")
    await consumer.check_ready()


async def check_redis():
    if await redis.ping() is not True:
        raise ConnectionError("Redis idempotency store is not ready")


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
        await redis.aclose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestIdMiddleware)
app.include_router(
    make_health_router(
        {"consumer": check_worker, "smtp": mailer.ping, "redis": check_redis},
        timeout=settings.io_timeout + 1,
    )
)


instrument_app(app)
