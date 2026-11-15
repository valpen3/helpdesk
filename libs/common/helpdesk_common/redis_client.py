"""Redis clients that discover the writable primary through Sentinel."""

import os

from redis.asyncio import Redis
from redis.asyncio.sentinel import Sentinel
from redis.backoff import NoBackoff
from redis.retry import Retry


def make_redis_client(*, host="redis", port=6379, **kwargs) -> Redis:
    nodes = os.getenv("REDIS_SENTINELS", "")
    options = {"decode_responses": True, "retry": Retry(NoBackoff(), 0), **kwargs}
    if not nodes:
        return Redis(host=host, port=port, **options)
    addresses = []
    for node in nodes.split(","):
        hostname, separator, node_port = node.strip().partition(":")
        if not hostname:
            raise ValueError("REDIS_SENTINELS contains an empty host")
        addresses.append((hostname, int(node_port) if separator else 26379))
    sentinel = Sentinel(
        addresses,
        sentinel_kwargs={
            "password": os.environ["REDIS_SENTINEL_PASSWORD"],
            "socket_timeout": options.get("socket_timeout", 1),
            "socket_connect_timeout": options.get("socket_connect_timeout", 1),
        },
        **options,
    )
    return sentinel.master_for(os.getenv("REDIS_MASTER_NAME", "helpdesk"))
