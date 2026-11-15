"""Long-lived real clients: do not restart them during the failure experiment."""

import asyncio
import json
import os
import time
from uuid import uuid4

import aio_pika

from helpdesk_common.broker import connect_broker
from helpdesk_common.redis_client import make_redis_client


async def main():
    redis = make_redis_client(
        username=os.environ["REDIS_USER"],
        password=os.environ["REDIS_PASSWORD"],
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    connection = await connect_broker(
        host=os.environ["MQ_HOST"],
        login=os.environ["MQ_USER"],
        password=os.environ["MQ_PASSWORD"],
        timeout=15,
    )
    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    exchange = await channel.get_exchange("ha.probe", ensure=False)
    queue = await channel.get_queue("ha.probe.q", ensure=False)
    marker = uuid4().hex
    await redis.set("ticket:ha:marker", marker, ex=3600)
    if await redis.wait(2, 10000) != 2:
        raise RuntimeError("Both Redis replicas must acknowledge the baseline marker")
    try:
        while True:
            sample = {"timestamp": time.time(), "redis": False, "rabbitmq": False}
            try:
                async with asyncio.timeout(2):
                    assert await redis.get("ticket:ha:marker") == marker, "replicated marker lost"
                    value = uuid4().hex
                    await redis.set("ticket:ha:live", value, ex=60)
                    assert await redis.get("ticket:ha:live") == value
                    primary = await redis.connection_pool.sentinel_manager.discover_master(
                        os.getenv("REDIS_MASTER_NAME", "helpdesk")
                    )
                    sample.update(redis=True, redis_primary=primary[0])
            except Exception as exc:
                sample["redis_error"] = type(exc).__name__ + ": " + str(exc)
            try:
                async with asyncio.timeout(2):
                    body = uuid4().hex.encode()
                    await exchange.publish(
                        aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
                        routing_key="probe",
                        mandatory=True,
                        timeout=1,
                    )
                    # Drain any prior in-flight messages before checking this confirm.
                    while True:
                        message = await queue.get(timeout=1, fail=False)
                        if message is None:
                            raise RuntimeError("Confirmed message is missing")
                        await message.ack()
                        if message.body == body:
                            break
                    sample.update(rabbitmq=True, rabbitmq_node=connection.url.host)
            except Exception as exc:
                sample["rabbitmq_error"] = type(exc).__name__
            print(json.dumps(sample), flush=True)
            await asyncio.sleep(0.2)
    finally:
        await redis.aclose()
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
