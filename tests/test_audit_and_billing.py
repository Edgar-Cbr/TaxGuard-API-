import json
import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_stripe_signature, hash_api_key
from app.models.api_key import ApiKey
from app.models.usage_log import UsageLog
from app.models.user import User, UserTier
from app.services.usage_tracker import record_usage_log
from tests.conftest import MockRedis, TestingSessionLocal


def _signed_webhook_post_args(payload: dict) -> tuple[bytes, dict[str, str]]:
    """Serialize payload and generate valid Stripe-Signature header."""
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = generate_stripe_signature(raw_bytes)
    headers = {
        "Content-Type": "application/json",
        "Stripe-Signature": sig,
    }
    return raw_bytes, headers


# ==============================================================================
# 1. Background Usage Tracker Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_record_usage_log_background_task(
    db_session: AsyncSession,
):
    """Test background task records usage_log and updates ApiKey.last_used_at."""
    # Create test user and API key
    user = User(
        email="usage_bg_test@fintech.io",
        hashed_password="hashed_pwd_dummy",
    )
    db_session.add(user)
    await db_session.flush()

    raw_key = "tg_live_0123456789abcdef0123456789abcdef"
    key = ApiKey(
        user_id=user.id,
        name="Background Test Key",
        key_prefix="tg_live_01",
        key_hash=hash_api_key(raw_key),
    )
    db_session.add(key)
    await db_session.commit()
    assert key.last_used_at is None

    # Execute background tracking function
    await record_usage_log(
        api_key_id=key.id,
        endpoint="/api/v1/validate/tax-id",
        status_code=200,
        latency_ms=15.42,
        session_factory=TestingSessionLocal,
    )

    # Verify UsageLog was written
    log_res = await db_session.execute(
        select(UsageLog).where(UsageLog.api_key_id == key.id)
    )
    log = log_res.scalar_one()
    assert log.endpoint == "/api/v1/validate/tax-id"
    assert log.status_code == 200
    assert log.latency_ms == 15.42
    assert log.created_at is not None

    # Verify ApiKey.last_used_at was updated
    key_res = await db_session.execute(select(ApiKey).where(ApiKey.id == key.id))
    updated_key = key_res.scalar_one()
    assert updated_key.last_used_at is not None


# ==============================================================================
# 2. Aggregated Usage Analytics Endpoint Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_get_keys_usage_analytics_endpoint(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """Test GET /api/v1/keys/usage aggregates calls, latency, and status codes."""
    # Register and login
    email = f"analytics_{datetime.now().timestamp()}@fintech.io"
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    token = login_res.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # Create key
    key_res = await async_client.post(
        "/api/v1/keys",
        json={"name": "Analytics Key"},
        headers=auth_header,
    )
    key_id = uuid.UUID(key_res.json()["id"])

    # Insert simulated usage logs: two 200s (10ms, 20ms) and one 422 (6ms)
    now = datetime.now(UTC)
    db_session.add_all(
        [
            UsageLog(
                api_key_id=key_id,
                endpoint="/api/v1/validate/tax-id",
                status_code=200,
                latency_ms=10.0,
                created_at=now,
            ),
            UsageLog(
                api_key_id=key_id,
                endpoint="/api/v1/validate/tax-id",
                status_code=200,
                latency_ms=20.0,
                created_at=now,
            ),
            UsageLog(
                api_key_id=key_id,
                endpoint="/api/v1/validate/postal-code",
                status_code=422,
                latency_ms=6.0,
                created_at=now,
            ),
        ]
    )
    await db_session.commit()

    # Query usage stats
    response = await async_client.get("/api/v1/keys/usage", headers=auth_header)
    assert response.status_code == 200
    data = response.json()

    assert data["total_calls"] == 3
    # Average of 10, 20, 6 is 12.0
    assert data["avg_latency_ms"] == 12.0
    assert data["status_codes"].get("200") == 2
    assert data["status_codes"].get("422") == 1
    assert data["monthly_quota"] == 1000
    assert data["quota_remaining"] == 997
    assert data["tier"] == "FREE"


# ==============================================================================
# 3. Stripe Webhook Billing & Plan Upgrades
# ==============================================================================


@pytest.mark.asyncio
async def test_stripe_webhook_upgrades_user_to_pro_and_cleans_cache(
    async_client: AsyncClient,
    mock_redis: MockRedis,
    db_session: AsyncSession,
):
    """Test signed Stripe webhook updates tier to PRO (50k quota, 50 req/s) and evicts Redis cache."""
    # 1. Register user and create API key
    email = "upgrade_target@fintech.io"
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    token = login_res.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    key_res = await async_client.post(
        "/api/v1/keys",
        json={"name": "Pre-Upgrade Key"},
        headers=auth_header,
    )
    raw_key = key_res.json()["api_key"]
    key_hash = hash_api_key(raw_key)

    # 2. Prime Redis cache for this key
    cache_key = f"apikey:meta:{key_hash}"
    await mock_redis.set(cache_key, '{"tier": "FREE", "monthly_quota": 1000}')
    assert await mock_redis.get(cache_key) is not None

    # Find user ID
    user_res = await async_client.get("/api/v1/auth/me", headers=auth_header)
    user_id = user_res.json()["id"]

    # 3. Simulate signed Stripe webhook payload
    webhook_payload = {
        "id": "evt_test_123456",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_987654321",
                "customer": "cus_stripe_enterprise_001",
                "client_reference_id": user_id,
                "customer_email": email,
            }
        },
    }

    raw_bytes, headers = _signed_webhook_post_args(webhook_payload)
    webhook_res = await async_client.post(
        "/api/v1/billing/webhook",
        content=raw_bytes,
        headers=headers,
    )
    assert webhook_res.status_code == 200
    res_data = webhook_res.json()
    assert res_data["status"] == "success"
    assert res_data["tier"] == "PRO"
    assert res_data["monthly_quota"] == 50000
    assert res_data["rate_limit_per_sec"] == 50

    # 4. Verify Redis cache was evicted
    assert await mock_redis.get(cache_key) is None

    # 5. Verify user is upgraded in DB
    user_in_db = (
        await db_session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    ).scalar_one()
    assert user_in_db.tier == UserTier.PRO
    assert user_in_db.subscription.monthly_quota == 50000
    assert user_in_db.subscription.rate_limit_per_sec == 50
    assert user_in_db.subscription.stripe_customer_id == "cus_stripe_enterprise_001"


@pytest.mark.asyncio
async def test_stripe_webhook_rejects_missing_or_invalid_signature(
    async_client: AsyncClient,
):
    """Test webhook rejects unsigned or forged requests with 401 INVALID_WEBHOOK_SIGNATURE."""
    payload = {
        "type": "checkout.session.completed",
        "data": {"object": {"customer_email": "attacker@fintech.io"}},
    }
    res_missing = await async_client.post("/api/v1/billing/webhook", json=payload)
    assert res_missing.status_code == 401
    assert res_missing.json()["error_code"] == "INVALID_WEBHOOK_SIGNATURE"

    res_forged = await async_client.post(
        "/api/v1/billing/webhook",
        json=payload,
        headers={"Stripe-Signature": "t=12345,v1=deadbeef"},
    )
    assert res_forged.status_code == 401
    assert res_forged.json()["error_code"] == "INVALID_WEBHOOK_SIGNATURE"


@pytest.mark.asyncio
async def test_stripe_webhook_ignored_events(async_client: AsyncClient):
    """Test webhook ignores non-checkout events gracefully."""
    raw_bytes, headers = _signed_webhook_post_args(
        {"type": "payment_intent.created", "data": {}}
    )
    response = await async_client.post(
        "/api/v1/billing/webhook",
        content=raw_bytes,
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_stripe_webhook_invalid_payload(async_client: AsyncClient):
    """Test webhook returns 400 when missing client reference and email or when data is null."""
    raw_bytes, headers = _signed_webhook_post_args(
        {"type": "checkout.session.completed", "data": {"object": {}}}
    )
    response = await async_client.post(
        "/api/v1/billing/webhook",
        content=raw_bytes,
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_WEBHOOK_PAYLOAD"

    raw_null, headers_null = _signed_webhook_post_args(
        {"type": "checkout.session.completed", "data": None}
    )
    res_null = await async_client.post(
        "/api/v1/billing/webhook",
        content=raw_null,
        headers=headers_null,
    )
    assert res_null.status_code == 400
    assert res_null.json()["error_code"] == "INVALID_WEBHOOK_PAYLOAD"
