"""Bounded-cardinality HTTP metrics, shared by all FastAPI services."""

import time

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "handler", "status"])
DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["method", "handler"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(("/metrics", "/health/")):
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status = 500

        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            # Only templates (e.g. /tickets/{id}); never IDs, tokens or query strings.
            handler = getattr(scope.get("route"), "path", "unmatched")
            REQUESTS.labels(scope["method"], handler, str(status)).inc()
            DURATION.labels(scope["method"], handler).observe(time.perf_counter() - started)


def instrument_app(app: FastAPI, *, expose=True):
    app.add_middleware(MetricsMiddleware)
    if expose:

        @app.get("/metrics", include_in_schema=False)
        async def metrics():
            return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
