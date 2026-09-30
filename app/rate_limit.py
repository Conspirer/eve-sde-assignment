"""Rate limiting: at most N requests per time window, per client IP.

Algorithm ("fixed window"): cut time into windows of `window_seconds`
(e.g. 13:00:00-13:00:59), count requests per IP in the current window, and
reject with 429 once the count is over the limit. The next window starts at 0.

Counts are stored in Redis, so every API server shares them. Without Redis
they're kept in this process's memory, which is fine for a single server.
"""
import logging
import threading
import time

import redis
from fastapi import HTTPException, Request, status

from app import redis_client

logger = logging.getLogger(__name__)

_memory_counts: dict[str, int] = {}
_memory_lock = threading.Lock()


def reset_memory_counts() -> None:
    """Used by tests to start each test with no counts."""
    with _memory_lock:
        _memory_counts.clear()


def _increment(key: str, window_seconds: int) -> int:
    """Add 1 to the counter for `key` and return the new value."""
    if redis_client.client is not None:
        try:
            # INCR and EXPIRE in one round trip. INCR is atomic, so two
            # requests at the same moment can't both read the old count.
            pipe = redis_client.client.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds)
            count, _ = pipe.execute()
            return count
        except redis.RedisError as exc:
            logger.warning("rate limit: Redis unavailable, using memory (%s)", exc)

    with _memory_lock:
        _memory_counts[key] = _memory_counts.get(key, 0) + 1
        return _memory_counts[key]


class RateLimiter:
    """A FastAPI dependency. Usage:

        login_limiter = RateLimiter("login", limit=5, window_seconds=60)

        @router.post("/login/", dependencies=[Depends(login_limiter)])
    """

    def __init__(self, name: str, limit: int, window_seconds: int):
        self.name = name
        self.limit = limit
        self.window_seconds = window_seconds

    def __call__(self, request: Request) -> None:
        # Note: behind a proxy or load balancer, the real IP is in the
        # X-Forwarded-For header, and the proxy must be configured to set it.
        client_ip = request.client.host if request.client else "unknown"
        now = int(time.time())
        window = now // self.window_seconds
        key = f"ratelimit:{self.name}:{client_ip}:{window}"

        if _increment(key, self.window_seconds) > self.limit:
            retry_after = self.window_seconds - (now % self.window_seconds)
            logger.warning("rate limit hit name=%s ip=%s", self.name, client_ip)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later",
                headers={"Retry-After": str(retry_after)},
            )
