"""Stripe billing webhook and plan management endpoints."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_redis
from app.core.exceptions import (
    AppException,
    AuthenticationException,
    EntityNotFoundException,
)
from app.core.security import verify_stripe_signature
from app.models.user import Subscription, User, UserTier

router = APIRouter()

stripe_signature_scheme = APIKeyHeader(
    name="Stripe-Signature",
    scheme_name="StripeWebhookSignature",
    description="HMAC-SHA256 Stripe webhook signature (`t=<timestamp>,v1=<hmac>` or hex digest)",
    auto_error=False,
)


class StripeWebhookResponse(BaseModel):
    """Response model for Stripe webhook event processing."""

    status: str = Field(description="Processing outcome: 'success' or 'ignored'")
    event: str | None = Field(default=None, description="Processed Stripe event type")
    message: str | None = Field(
        default=None, description="Informational status message"
    )
    user_id: str | None = Field(default=None, description="Upgraded user UUID")
    tier: str | None = Field(default=None, description="Updated subscription tier")
    monthly_quota: int | None = Field(
        default=None, description="New monthly request quota"
    )
    rate_limit_per_sec: int | None = Field(
        default=None, description="New per-second rate limit"
    )
    invalidated_keys_count: int | None = Field(
        default=None, description="Number of Redis cached API keys invalidated"
    )


@router.post(
    "/webhook",
    response_model=StripeWebhookResponse,
    status_code=status.HTTP_200_OK,
    summary="Process signed Stripe checkout.session.completed webhook for plan upgrades",
)
async def stripe_webhook(
    request: Request,
    payload: dict[str, Any],
    db: Annotated[AsyncSession, Depends(get_db)],
    redis_client: Annotated[Redis, Depends(get_redis)],
    stripe_signature: Annotated[str | None, Security(stripe_signature_scheme)] = None,
) -> StripeWebhookResponse:
    """Process incoming Stripe webhook events.

    Verifies the cryptographic HMAC-SHA256 'Stripe-Signature' header, and on
    'checkout.session.completed', upgrades the user to PRO tier
    (50,000 monthly quota and 50 req/sec rate limit) and invalidates
    cached API key metadata in Redis.
    """
    raw_body = await request.body()
    if not verify_stripe_signature(raw_body, stripe_signature):
        raise AuthenticationException(
            message="Missing or invalid Stripe-Signature header.",
            error_code="INVALID_WEBHOOK_SIGNATURE",
        )

    event_type = payload.get("type")

    if event_type != "checkout.session.completed":
        return StripeWebhookResponse(
            status="ignored",
            event=str(event_type) if event_type else None,
            message=f"Event type '{event_type}' not processed.",
        )

    raw_data = payload.get("data")
    data_dict = raw_data if isinstance(raw_data, dict) else {}
    raw_obj = data_dict.get("object")
    data_object = raw_obj if isinstance(raw_obj, dict) else {}

    client_ref_id = data_object.get("client_reference_id")
    raw_email = data_object.get("customer_email")
    customer_email = (
        str(raw_email).strip().lower()
        if isinstance(raw_email, str) and raw_email.strip()
        else None
    )
    stripe_customer_id = data_object.get("customer")

    if not client_ref_id and not customer_email:
        raise AppException(
            message="Webhook payload must provide client_reference_id or customer_email.",
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="INVALID_WEBHOOK_PAYLOAD",
        )

    # Find target user
    stmt = select(User).options(
        selectinload(User.subscription),
        selectinload(User.api_keys),
    )
    if client_ref_id:
        try:
            user_uuid = uuid.UUID(str(client_ref_id))
            stmt = stmt.where(User.id == user_uuid)
        except ValueError:
            stmt = stmt.where(User.email == customer_email)
    else:
        stmt = stmt.where(User.email == customer_email)

    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None:
        raise EntityNotFoundException(
            message=f"User associated with checkout session not found ({client_ref_id or customer_email}).",
            error_code="USER_NOT_FOUND",
        )

    # 1. Upgrade User tier to PRO
    user.tier = UserTier.PRO

    # 2. Update Subscription limits: 50,000 requests/month, 50 requests/sec
    if user.subscription is None:
        user.subscription = Subscription(
            user_id=user.id,
            monthly_quota=50000,
            rate_limit_per_sec=50,
            stripe_customer_id=stripe_customer_id,
        )
        db.add(user.subscription)
    else:
        user.subscription.monthly_quota = 50000
        user.subscription.rate_limit_per_sec = 50
        if stripe_customer_id:
            user.subscription.stripe_customer_id = stripe_customer_id

    await db.commit()

    # 3. Invalidate Redis metadata cache for all active API keys of this user
    invalidated_count = 0
    for key in user.api_keys:
        cache_key = f"apikey:meta:{key.key_hash}"
        try:
            await redis_client.delete(cache_key)
            invalidated_count += 1
        except Exception:
            pass

    return StripeWebhookResponse(
        status="success",
        event="checkout.session.completed",
        user_id=str(user.id),
        tier=UserTier.PRO.value,
        monthly_quota=50000,
        rate_limit_per_sec=50,
        invalidated_keys_count=invalidated_count,
    )
