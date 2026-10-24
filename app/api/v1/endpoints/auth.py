"""Authentication router: User registration, login, and profile."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.core.exceptions import AppException, AuthenticationException
from app.core.security import create_access_token, get_password_hash, verify_password
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
)
async def register_user(
    payload: UserRegisterRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Register a new user with an initial default FREE subscription tier."""
    # Check if user already exists
    result = await db.execute(select(User).where(User.email == payload.email))
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
        email=payload.email,
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
)
async def login_user(
    payload: UserLoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenResponse:
    """Authenticate email and password, issuing an access JWT."""
    result = await db.execute(select(User).where(User.email == payload.email))
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
