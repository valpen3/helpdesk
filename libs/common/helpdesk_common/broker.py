"""Rotate RabbitMQ nodes on disconnect, retaining robust channels and consumers."""

import asyncio
import os
from itertools import cycle

import aio_pika
from aio_pika.connection import make_url
from aio_pika.robust_connection import RobustConnection


class ClusterConnection(RobustConnection):
    def __init__(self, url, **kwargs):
        nodes = os.getenv("MQ_HOSTS", url.host).split(",")
        self._nodes = cycle(node.strip() for node in nodes if node.strip())
        first = next(self._nodes, None)
        if first is None:
            raise ValueError("MQ_HOSTS must contain at least one hostname")
        super().__init__(url.with_host(first), **kwargs)

    async def _on_connection_close(self, closing: asyncio.Future) -> None:
        # UnderlayConnection also invokes this callback after a failed connect.
        # Advance before the robust recovery loop wakes up, including when the
        # next node is down too. Explicit close must never trigger recovery.
        if not self._close_called and not self.is_closed:
            self.url = self.url.with_host(next(self._nodes))
        await super()._on_connection_close(closing)


def cluster_connection_options() -> dict:
    """Keep single-node mode for local unit tests and external deployments."""
    if not os.getenv("MQ_HOSTS"):
        return {}
    return {
        "connection_class": ClusterConnection,
        "fail_fast": "0",
        "reconnect_interval": "1",
        "heartbeat": 10,
    }


async def connect_broker(**kwargs):
    options = cluster_connection_options()
    if not options:
        return await aio_pika.connect_robust(**kwargs)
    options.pop("connection_class")
    timeout = kwargs.pop("timeout", 5)
    connection = ClusterConnection(make_url(**{**kwargs, **options}), **{**kwargs, **options})
    try:
        async with asyncio.timeout(timeout):
            await connection.connect(timeout=timeout)
        return connection
    except BaseException:
        # Cancel the robust retry task too when startup is cancelled/times out.
        await connection.close()
        raise
