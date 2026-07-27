"""Webhook duplicate protection shared by all message providers."""

import logging
from typing import Final

import redis.asyncio as redis

from config import settings


logger = logging.getLogger(__name__)
_local_events: set[str] = set()
_redis_client: redis.Redis | None = None
KEY_PREFIX: Final = "webhook-event"


def _get_redis() -> redis.Redis | None:
    global _redis_client
    if _redis_client is None and settings.redis_url:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _key(provider: str, event_id: str) -> str:
    return f"{KEY_PREFIX}:{provider}:{event_id}"


async def claim_event(provider: str, event_id: str | None) -> bool:
    """Atomically claim an inbound event; return False when it was already seen."""
    if not event_id:
        return True

    key = _key(provider, event_id)
    client = _get_redis()
    if client is not None:
        try:
            return bool(await client.set(key, "1", nx=True, ex=settings.idempotency_ttl_seconds))
        except Exception as exc:
            if settings.require_redis:
                raise RuntimeError("Redis idempotency store is unavailable") from exc
            logger.warning("Redis idempotency claim failed; using local development fallback", exc_info=True)

    if key in _local_events:
        return False
    _local_events.add(key)
    return True


async def release_event(provider: str, event_id: str | None) -> None:
    """Allow a provider retry if processing failed before a response was sent."""
    if not event_id:
        return
    key = _key(provider, event_id)
    _local_events.discard(key)
    client = _get_redis()
    if client is not None:
        try:
            await client.delete(key)
        except Exception:
            logger.warning("Redis idempotency release failed", exc_info=True)
