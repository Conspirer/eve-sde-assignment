"""A small JSON cache on top of Redis.

Every function "fails open": if Redis is missing or down, reads return None
(a cache miss) and writes do nothing. The cache can only make the API faster,
never break it.
"""
import json
import logging
from typing import Any

import redis

from app import redis_client
from app.config import settings

logger = logging.getLogger(__name__)


def cache_get(key: str) -> Any | None:
    if redis_client.client is None:
        return None
    try:
        raw = redis_client.client.get(key)
    except redis.RedisError as exc:
        logger.warning("cache get failed key=%s error=%s", key, exc)
        return None
    return json.loads(raw) if raw is not None else None


def cache_set(key: str, value: Any, ttl: int | None = None) -> None:
    if redis_client.client is None:
        return
    try:
        # The TTL (expiry) is a safety net: even if we forget to clear a key,
        # it can't be stale for longer than this.
        redis_client.client.set(key, json.dumps(value), ex=ttl or settings.cache_ttl_seconds)
    except redis.RedisError as exc:
        logger.warning("cache set failed key=%s error=%s", key, exc)


def cache_delete_prefix(prefix: str) -> None:
    """Delete every key starting with `prefix`, e.g. all cached centre pages."""
    if redis_client.client is None:
        return
    try:
        # SCAN walks the keys in small batches. KEYS would block Redis on a big database.
        keys = list(redis_client.client.scan_iter(match=f"{prefix}*"))
        if keys:
            redis_client.client.delete(*keys)
    except redis.RedisError as exc:
        logger.warning("cache invalidation failed prefix=%s error=%s", prefix, exc)
