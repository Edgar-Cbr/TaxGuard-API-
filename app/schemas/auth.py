"""Pydantic schemas for authentication and user management."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.user import UserTier


class UserRegisterRequest(BaseModel):
    """Payload for user registration."""

    email: EmailStr
    password: str = Field(
        min_length=8, description="User password (minimum 8 characters)"
    )


class UserLoginRequest(BaseModel):
    """Payload for user login."""

    email: EmailStr
    password: str


class SubscriptionResponse(BaseModel):
    """Subscription details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    monthly_quota: int
    rate_limit_per_sec: int
    current_period_end: datetime | None = None


class UserResponse(BaseModel):
    """User account response DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    tier: UserTier
    is_active: bool
    created_at: datetime
    subscription: SubscriptionResponse | None = None


class TokenResponse(BaseModel):
    """OAuth2 / JWT Token response."""

    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT Token decoded payload."""

    sub: str | None = None
    exp: datetime | None = None
