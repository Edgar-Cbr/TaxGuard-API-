"""Common FastAPI dependencies for dependency injection."""

import uuid
from typing import Annotated

from fastapi import Depends, Request, Response, Security
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import AuthenticationException
from app.core.rate_limit import (
    ApiKeyMetadata,
    check_auth_rate_limit,
    check_rate_limit_and_quota,
    get_api_key_metadata,
)
from app.core.redis import get_redis_client
from app.core.security import decode_access_token, hash_api_key, is_token_revoked
from app.db.session import get_db
from app.models.user import User

__all__ = [
    "api_key_header_scheme",
    "get_client_ip",
    "get_current_user",
    "get_db",
    "get_redis",
    "rate_limit_auth",
    "security_bearer",
    "verify_api_key_and_quota",
]

security_bearer = HTTPBearer(
    scheme_name="HTTPBearer",
    description="JWT Access Token obtained from `/api/v1/auth/login`",
    auto_error=False,
)

api_key_header_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="APIKeyHeader",
    description="Production API Key formatted as `tg_live_<32_hex_chars>`",
    auto_error=False,
)


def get_client_ip(request: Request) -> str:
    """Extract client IP address safely, respecting X-Forwarded-For if available."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


async def get_redis() -> Redis:
    """Dependency for obtaining an async Redis client."""
    return get_redis_client()


async def rate_limit_auth(
    request: Request,
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> None:
    """Enforce IP-based rate limiting on sensitive authentication endpoints (SEC-05)."""
    client_ip = get_client_ip(request)
    endpoint = request.url.path.strip("/").split("/")[-1] or "auth"
    await check_auth_rate_limit(
        client_ip=client_ip,
        action=endpoint,
        redis_client=redis_client,
        max_requests=10,
        window_seconds=60,
    )


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(security_bearer)
    ],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[Redis, Depends(get_redis)],
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

    # Check token revocation blacklist in Redis (SEC-08)
    jti = payload.get("jti")
    if jti and await is_token_revoked(jti, redis_client):
        raise AuthenticationException(
            message="Token has been revoked",
            error_code="TOKEN_REVOKED",
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


async def verify_api_key_and_quota(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[Redis, Depends(get_redis)],
    x_api_key: Annotated[str | None, Security(api_key_header_scheme)] = None,
) -> ApiKeyMetadata:
    """Validate API Key, check sliding-window rate limit, and track monthly quota."""
    # 1. Extraer header X-API-Key. Si no existe, retornar 401 Unauthorized
    if not x_api_key or not x_api_key.strip():
        raise AuthenticationException(
            message="Missing or empty X-API-Key header.",
            error_code="MISSING_API_KEY",
        )

    # 2. Calcular SHA-256 y buscar metadatos en Redis / PostgreSQL
    key_hash = hash_api_key(x_api_key)
    metadata = await get_api_key_metadata(
        key_hash=key_hash,
        db=db,
        redis_client=redis_client,
    )

    # 3. Verificar Rate Limit por segundo y cuota mensual en Redis
    limit_result = await check_rate_limit_and_quota(
        metadata=metadata,
        redis_client=redis_client,
    )

    # 4. Inyectar encabezados en el objeto Response
    response.headers["X-RateLimit-Limit"] = str(limit_result.limit)
    response.headers["X-RateLimit-Remaining"] = str(limit_result.remaining)
    response.headers["X-Quota-Remaining"] = str(limit_result.quota_remaining)

    # Adjuntar metadatos al estado de la petición para auditoría y logs downstream
    request.state.api_key_metadata = metadata

    return metadata
