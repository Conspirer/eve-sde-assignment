"""The shared Redis connection, or None when REDIS_URL isn't set.

Code must read `redis_client.client` at call time (not `from ... import client`)
so tests can swap in a fake Redis.
"""
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import settings


def make_client(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        # Fail fast. If Redis is slow or down, give up at once and carry on
        # without it. redis-py retries by default, which would add seconds
        # to every API request while Redis is down.
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
        retry=Retry(NoBackoff(), retries=0),
    )


client: redis.Redis | None = make_client(settings.redis_url) if settings.redis_url else None
