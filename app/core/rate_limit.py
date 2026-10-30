"""Sliding window rate limiting and monthly quota tracking via Redis."""

import json
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthenticationException, RateLimitExceededException
from app.models.api_key import ApiKey
from app.models.user import User, UserTier


class ApiKeyMetadata(BaseModel):
    """Cached metadata model for fast O(1) API Key authorization and limits."""

    model_config = ConfigDict(from_attributes=True)

    api_key_id: uuid.UUID
    user_id: uuid.UUID
    tier: UserTier
    monthly_quota: int
    rate_limit_per_sec: int
    is_active: bool


class RateLimitResult(BaseModel):
    """Rate limit consumption calculation for HTTP response headers."""

    limit: int
    remaining: int
    quota_remaining: int


async def get_api_key_metadata(
    key_hash: str,
    db: AsyncSession,
    redis_client: Redis,
) -> ApiKeyMetadata:
    """Retrieve API Key metadata from Redis cache (TTL 300s) or fall back to PostgreSQL."""
    cache_key = f"apikey:meta:{key_hash}"

    # 1. Check Redis Cache
    try:
        cached_raw = await redis_client.get(cache_key)
        if cached_raw:
            data = json.loads(cached_raw)
            metadata = ApiKeyMetadata(**data)
            if not metadata.is_active:
                raise AuthenticationException(
                    message="API Key is inactive or revoked.",
                    error_code="REVOKED_API_KEY",
                )
            return metadata
    except AuthenticationException:
        raise
    except Exception:
        # Cache reading failure falls back gracefully to database
        pass

    # 2. Query PostgreSQL with eager loading of User and Subscription
    stmt = (
        select(ApiKey)
        .options(selectinload(ApiKey.user).selectinload(User.subscription))
        .where(ApiKey.key_hash == key_hash)
    )
    result = await db.execute(stmt)
    api_key = result.scalar_one_or_none()

    if api_key is None or not api_key.is_active or api_key.revoked_at is not None:
        raise AuthenticationException(
            message="Invalid or revoked API Key.",
            error_code="INVALID_API_KEY",
        )

    user = api_key.user
    if user is None or not user.is_active:
        raise AuthenticationException(
            message="User account associated with this API Key is inactive.",
            error_code="ACCOUNT_INACTIVE",
        )

    sub = user.subscription
    monthly_quota = sub.monthly_quota if sub else 1000
    rate_limit_per_sec = sub.rate_limit_per_sec if sub else 5

    metadata = ApiKeyMetadata(
        api_key_id=api_key.id,
        user_id=user.id,
        tier=user.tier,
        monthly_quota=monthly_quota,
        rate_limit_per_sec=rate_limit_per_sec,
        is_active=api_key.is_active,
    )

    # 3. Store in Redis with TTL 300 seconds (5 minutes)
    try:
        await redis_client.setex(cache_key, 300, metadata.model_dump_json())
    except Exception:
        pass

    return metadata


async def check_rate_limit_and_quota(
    metadata: ApiKeyMetadata,
    redis_client: Redis,
) -> RateLimitResult:
    """Enforce atomic rate limit per second and cumulative monthly quota via Redis pipeline."""
    now = datetime.now(UTC)
    epoch_second = int(now.timestamp())
    year_month = now.strftime("%Y-%m")

    rate_key = f"ratelimit:{metadata.api_key_id}:{epoch_second}"
    quota_key = f"quota:{metadata.user_id}:{year_month}"

    # 1. Check per-second rate limit first so rejected 429 requests do not consume monthly quota
    rate_pipe = redis_client.pipeline(transaction=True)
    rate_pipe.incr(rate_key)
    rate_pipe.expire(rate_key, 2)  # 2 second TTL ensures clean sliding transition
    rate_results = await rate_pipe.execute()
    rate_count = int(rate_results[0])

    if rate_count > metadata.rate_limit_per_sec:
        raise RateLimitExceededException(
            message=f"Rate limit of {metadata.rate_limit_per_sec} requests per second exceeded.",
            retry_after=1,
            error_code="RATE_LIMIT_EXCEEDED",
        )

    # 2. Check and increment monthly quota only for non-rate-limited requests
    quota_pipe = redis_client.pipeline(transaction=True)
    quota_pipe.incr(quota_key)
    quota_pipe.expire(quota_key, 35 * 86400)  # 35 days TTL covers month rollover
    quota_results = await quota_pipe.execute()
    quota_count = int(quota_results[0])

    # Enforce Monthly Quota
    if quota_count > metadata.monthly_quota:
        raise RateLimitExceededException(
            message=f"Monthly quota limit of {metadata.monthly_quota} requests consumed for {year_month}.",
            retry_after=None,
            error_code="QUOTA_EXCEEDED",
        )

    remaining_rate = max(0, metadata.rate_limit_per_sec - rate_count)
    remaining_quota = max(0, metadata.monthly_quota - quota_count)

    return RateLimitResult(
        limit=metadata.rate_limit_per_sec,
        remaining=remaining_rate,
        quota_remaining=remaining_quota,
    )


async def check_and_consume_batch_quota(
    user_id: uuid.UUID,
    additional_count: int,
    monthly_quota: int,
    redis_client: Redis,
) -> int:
    """Atomically consume additional monthly quota for multi-item batch validations (SEC-03).

    Args:
        user_id: Authenticated user UUID.
        additional_count: Number of additional validations beyond the initial 1 request already counted.
        monthly_quota: Subscription monthly quota ceiling.
        redis_client: Active Redis client.

    Returns:
        Remaining quota after consuming the entire batch.

    Raises:
        RateLimitExceededException: If batch volume exceeds remaining monthly quota.
    """
    now = datetime.now(UTC)
    year_month = now.strftime("%Y-%m")
    quota_key = f"quota:{user_id}:{year_month}"

    if additional_count <= 0:
        current = await redis_client.get(quota_key)
        consumed = int(current) if current else 0
        return max(0, monthly_quota - consumed)

    quota_pipe = redis_client.pipeline(transaction=True)
    quota_pipe.incrby(quota_key, additional_count)
    quota_pipe.expire(quota_key, 35 * 86400)
    results = await quota_pipe.execute()
    new_quota_count = int(results[0])

    if new_quota_count > monthly_quota:
        # Roll back over-consumption so rejected batch does not deduct quota
        try:
            await redis_client.incrby(quota_key, -additional_count)
        except Exception:
            pass
        raise RateLimitExceededException(
            message=f"Batch of {additional_count + 1} validations exceeds monthly quota of {monthly_quota}.",
            retry_after=None,
            error_code="QUOTA_EXCEEDED",
        )

    return max(0, monthly_quota - new_quota_count)


async def check_auth_rate_limit(
    client_ip: str,
    action: str,
    redis_client: Redis,
    max_requests: int = 10,
    window_seconds: int = 60,
) -> None:
    """Rate limit sensitive authentication actions by client IP (SEC-05: OWASP ASVS 2.4.1).

    Args:
        client_ip: Source IP address of the client request.
        action: Subsystem action identifier (e.g., 'login', 'register').
        redis_client: Active Redis connection.
        max_requests: Maximum allowed attempts in window (default 10).
        window_seconds: Sliding window interval in seconds (default 60).

    Raises:
        RateLimitExceededException: When attempt frequency exceeds threshold.
    """
    safe_ip = client_ip.strip() if client_ip else "unknown"
    rate_key = f"ratelimit:auth:{action}:{safe_ip}"

    pipe = redis_client.pipeline(transaction=True)
    pipe.incr(rate_key)
    pipe.expire(rate_key, window_seconds)
    results = await pipe.execute()
    attempts = int(results[0])

    if attempts > max_requests:
        raise RateLimitExceededException(
            message=f"Too many {action} attempts. Please try again after {window_seconds} seconds.",
            retry_after=window_seconds,
            error_code="RATE_LIMIT_EXCEEDED",
        )
