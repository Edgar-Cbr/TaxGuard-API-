import logging
import time

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger("taxguard.redis")

_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    """Obtain or initialize the global async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3.0,
            socket_timeout=3.0,
        )
    return _redis_client


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection pool closed.")


async def check_redis_health() -> dict[str, str | float | bool]:
    """Verify Redis connection via PING and measure latency in ms."""
    start_time = time.perf_counter()
    try:
        client = get_redis_client()
        pong = await client.ping()
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        if pong:
            return {
                "status": "healthy",
                "available": True,
                "latency_ms": latency_ms,
            }
        return {
            "status": "degraded",
            "available": False,
            "latency_ms": latency_ms,
            "error": "Unexpected PING response",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning(f"Redis health check failed: {exc}")
        return {
            "status": "unhealthy",
            "available": False,
            "latency_ms": latency_ms,
            "error": str(exc),
        }
