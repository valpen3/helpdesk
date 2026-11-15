from redis.asyncio import Redis


class DeliveryRegistry:
    """Suppress completed duplicate emails and serialize retries per recipient."""

    def __init__(self, redis: Redis, *, done_ttl: int, lock_ttl: int):
        self.redis = redis
        self.done_ttl = done_ttl
        self.lock_ttl = lock_ttl

    @staticmethod
    def _keys(event_id: str, recipient: str) -> tuple[str, str]:
        recipient_key = recipient.casefold()
        return (
            f"notif:done:{event_id}:{recipient_key}",
            f"notif:lock:{event_id}:{recipient_key}",
        )

    async def claim(self, event_id: str, recipient: str) -> bool | None:
        done_key, lock_key = self._keys(event_id, recipient)
        if await self.redis.exists(done_key):
            return None
        claimed = await self.redis.set(lock_key, "1", nx=True, ex=self.lock_ttl)
        return True if claimed else False

    async def mark_sent(self, event_id: str, recipient: str) -> None:
        done_key, lock_key = self._keys(event_id, recipient)
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.set(done_key, "1", ex=self.done_ttl)
            pipeline.delete(lock_key)
            await pipeline.execute()

    async def release(self, event_id: str, recipient: str) -> None:
        _, lock_key = self._keys(event_id, recipient)
        await self.redis.delete(lock_key)


__all__ = ["DeliveryRegistry"]
