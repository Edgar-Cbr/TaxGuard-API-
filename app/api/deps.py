"""Common FastAPI dependencies for dependency injection."""

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthenticationException
from app.core.redis import get_redis_client
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User

__all__ = ["get_current_user", "get_db", "get_redis"]

security_bearer = HTTPBearer(auto_error=False)


async def get_redis() -> Redis:
    """Dependency for obtaining an async Redis client."""
    return get_redis_client()


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Validate Bearer JWT and retrieve authenticated User from database."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationException(
            message="Missing or malformed Authorization header",
            error_code="UNAUTHORIZED",
        )

    payload = decode_access_token(credentials.credentials)
    user_id_raw: str | None = payload.get("sub")
    if not user_id_raw:
        raise AuthenticationException(
            message="Token payload is missing subject claim",
            error_code="INVALID_TOKEN",
        )

    try:
        user_uuid = uuid.UUID(user_id_raw)
    except (ValueError, TypeError) as exc:
        raise AuthenticationException(
            message="Invalid user identifier in token",
            error_code="INVALID_TOKEN",
        ) from exc

    result = await db.execute(
        select(User)
        .options(selectinload(User.subscription))
        .where(User.id == user_uuid)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise AuthenticationException(
            message="User not found",
            error_code="USER_NOT_FOUND",
        )

    if not user.is_active:
        raise AuthenticationException(
            message="User account is deactivated",
            error_code="ACCOUNT_INACTIVE",
        )

    return user
