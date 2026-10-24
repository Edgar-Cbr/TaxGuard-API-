"""Common FastAPI dependencies for dependency injection."""

from redis.asyncio import Redis

from app.core.redis import get_redis_client
from app.db.session import get_db

__all__ = ["get_db", "get_redis"]


async def get_redis() -> Redis:
    """Dependency for obtaining an async Redis client."""
    return get_redis_client()
