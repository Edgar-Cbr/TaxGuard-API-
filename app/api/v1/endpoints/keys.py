"""API Key management endpoints: creation, listing, and revocation."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_db,
    get_redis,
    verify_api_key_and_quota,
)
from app.core.exceptions import EntityNotFoundException
from app.core.rate_limit import ApiKeyMetadata
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.api_key import (
    ApiKeyCreatedResponse,
    ApiKeyCreateRequest,
    ApiKeyResponse,
    ApiKeyRevokeResponse,
)

router = APIRouter()


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a new API Key",
)
async def create_new_api_key(
    payload: ApiKeyCreateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ApiKeyCreatedResponse:
    """Generate a new API key. The plaintext key is returned ONLY once in this response."""
    raw_key, key_prefix, key_hash = generate_api_key()

    new_key = ApiKey(
        user_id=current_user.id,
        name=payload.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        is_active=True,
    )
    db.add(new_key)
    await db.commit()
    await db.refresh(new_key)

    return ApiKeyCreatedResponse(
        id=new_key.id,
        name=new_key.name,
        key_prefix=new_key.key_prefix,
        api_key=raw_key,
        created_at=new_key.created_at,
    )


@router.get(
    "",
    response_model=list[ApiKeyResponse],
    summary="List all API Keys for current user",
)
async def list_user_api_keys(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ApiKey]:
    """List all API keys belonging to the authenticated user without exposing secrets."""
    result = await db.execute(
        select(ApiKey)
        .where(ApiKey.user_id == current_user.id)
        .order_by(ApiKey.created_at.desc())
    )
    keys = list(result.scalars().all())
    return keys


@router.delete(
    "/{key_id}",
    response_model=ApiKeyRevokeResponse,
    summary="Revoke an API Key and invalidate its Redis cache",
)
async def revoke_api_key(
    key_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> ApiKeyRevokeResponse:
    """Revoke an active API key immediately and evict it from the Redis auth cache."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.id == key_id,
            ApiKey.user_id == current_user.id,
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise EntityNotFoundException(
            message="API Key not found or does not belong to the user.",
            error_code="KEY_NOT_FOUND",
        )

    # Update database record
    api_key.is_active = False
    api_key.revoked_at = datetime.now(UTC)
    await db.commit()

    # Evict cache key from Redis
    cache_key = f"apikey:meta:{api_key.key_hash}"
    try:
        await redis_client.delete(cache_key)
    except Exception:
        # Cache eviction error should not abort revocation transaction
        pass

    return ApiKeyRevokeResponse(
        message="API Key successfully revoked.",
        revoked_key_id=api_key.id,
    )


@router.get(
    "/verify",
    summary="Verify X-API-Key status, active rate limits and remaining quota",
)
async def verify_key(
    metadata: Annotated[ApiKeyMetadata, Depends(verify_api_key_and_quota)],
) -> dict:
    """Introspection endpoint to test and verify X-API-Key validity and quota."""
    return {
        "valid": True,
        "api_key_id": metadata.api_key_id,
        "user_id": metadata.user_id,
        "tier": metadata.tier,
        "monthly_quota": metadata.monthly_quota,
        "rate_limit_per_sec": metadata.rate_limit_per_sec,
    }
