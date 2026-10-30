"""Authentication router: User registration, login, logout, and profile."""

from typing import Annotated

from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    get_current_user,
    get_db,
    get_redis,
    rate_limit_auth,
    security_bearer,
)
from app.core.exceptions import AppException, AuthenticationException
from app.core.security import (
    create_access_token,
    get_password_hash,
    revoke_access_token,
    verify_password,
)
from app.models.user import Subscription, User, UserTier
from app.schemas.auth import (
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

router = APIRouter()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    dependencies=[Depends(rate_limit_auth)],
)
async def register_user(
    payload: UserRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Register a new user with an initial default FREE subscription tier."""
    normalized_email = payload.email.strip().lower()

    # Check if user already exists
    result = await db.execute(select(User).where(User.email == normalized_email))
    existing_user = result.scalar_one_or_none()
    if existing_user is not None:
        raise AppException(
            message="A user with this email address already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="EMAIL_ALREADY_EXISTS",
        )

    # Hash password and create User entity
    hashed_pwd = get_password_hash(payload.password)
    user = User(
        email=normalized_email,
        hashed_password=hashed_pwd,
        tier=UserTier.FREE,
        is_active=True,
    )
    db.add(user)
    await db.flush()  # populate user.id

    # Create default FREE subscription (1,000 monthly quota, 5 req/sec rate limit)
    subscription = Subscription(
        user_id=user.id,
        monthly_quota=1000,
        rate_limit_per_sec=5,
    )
    db.add(subscription)
    await db.commit()

    # Re-fetch user with subscription loaded
    result = await db.execute(
        select(User).options(selectinload(User.subscription)).where(User.id == user.id)
    )
    user_with_sub = result.scalar_one()
    return user_with_sub


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate user and issue JWT",
    dependencies=[Depends(rate_limit_auth)],
)
async def login_user(
    payload: UserLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Authenticate email and password, issuing an access JWT."""
    normalized_email = payload.email.strip().lower()
    result = await db.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.hashed_password):
        raise AuthenticationException(
            message="Incorrect email or password.",
            error_code="INVALID_CREDENTIALS",
        )

    if not user.is_active:
        raise AuthenticationException(
            message="User account is deactivated.",
            error_code="ACCOUNT_INACTIVE",
        )

    access_token = create_access_token(subject=user.id)
    return TokenResponse(access_token=access_token, token_type="bearer")


@router.post(
    "/logout",
    status_code=status.HTTP_200_OK,
    summary="Revoke current JWT access token",
)
async def logout_user(
    current_user: Annotated[User, Depends(get_current_user)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Security(security_bearer)
    ],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> dict[str, str]:
    """Blacklist the active JWT access token until its natural expiration (SEC-08)."""
    if credentials and credentials.credentials:
        await revoke_access_token(credentials.credentials, redis_client)
    return {"message": "Successfully logged out."}


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current authenticated user profile",
)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Return the profile and subscription details of the currently authenticated user."""
    return current_user
