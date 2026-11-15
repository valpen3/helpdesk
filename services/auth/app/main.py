"""FastAPI entrypoint and dependency lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.users import router as users_router
from app.bootstrap import seed_admin
from app.cache import make_redis, ping_redis
from app.config import settings
from app.db import engine, ping_db
from app.security import validate_key_pair
from helpdesk_common import RequestIdMiddleware, make_health_router, setup_logging

setup_logging(settings.service_name, settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = make_redis()
    app.state.redis = redis
    try:
        validate_key_pair()
        await seed_admin()
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


app = FastAPI(
    title="auth-service",
    version="0.1.0",
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(RequestIdMiddleware)
app.include_router(
    make_health_router({"postgres": ping_db, "redis": lambda: ping_redis(app.state.redis)})
)
app.include_router(auth_router)
app.include_router(users_router)
