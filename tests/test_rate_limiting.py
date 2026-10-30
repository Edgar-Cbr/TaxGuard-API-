from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.core.security import hash_api_key
from tests.conftest import MockRedis


@pytest.fixture
async def registered_user_and_key(async_client: AsyncClient) -> tuple[dict, str]:
    """Helper fixture that registers a user and generates a valid API key."""
    email = f"ratelimit_user_{datetime.now().timestamp()}@fintech.io"
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "Password123!"},
    )
    token = login_res.json()["access_token"]

    key_res = await async_client.post(
        "/api/v1/keys",
        json={"name": "Rate Limit Test Key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    raw_api_key = key_res.json()["api_key"]
    return key_res.json(), raw_api_key


@pytest.mark.asyncio
async def test_missing_api_key_header_returns_401(async_client: AsyncClient):
    """Test requests without X-API-Key are rejected with 401 Unauthorized."""
    response = await async_client.get("/api/v1/keys/verify")
    assert response.status_code == 401
    data = response.json()
    assert data["status_code"] == 401
    assert "error_code" in data


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(async_client: AsyncClient):
    """Test requests with unknown or forged X-API-Key are rejected with 401."""
    fake_key = "tg_live_00000000000000000000000000000000"
    response = await async_client.get(
        "/api/v1/keys/verify",
        headers={"X-API-Key": fake_key},
    )
    assert response.status_code == 401
    assert response.json()["error_code"] == "INVALID_API_KEY"


@pytest.mark.asyncio
async def test_valid_api_key_injects_headers_and_caches_in_redis(
    async_client: AsyncClient,
    mock_redis: MockRedis,
    registered_user_and_key: tuple[dict, str],
):
    """Test successful request injects X-RateLimit and X-Quota headers and caches metadata."""
    key_data, raw_key = registered_user_and_key
    headers = {"X-API-Key": raw_key}

    response = await async_client.get("/api/v1/keys/verify", headers=headers)
    assert response.status_code == 200
    res_data = response.json()
    assert res_data["valid"] is True
    assert res_data["rate_limit_per_sec"] == 5
    assert res_data["monthly_quota"] == 1000

    # Verify injected response headers
    assert response.headers.get("x-ratelimit-limit") == "5"
    assert response.headers.get("x-ratelimit-remaining") == "4"
    assert response.headers.get("x-quota-remaining") == "999"

    # Verify metadata was cached in Redis
    key_hash = hash_api_key(raw_key)
    cached_meta = await mock_redis.get(f"apikey:meta:{key_hash}")
    assert cached_meta is not None
    assert "rate_limit_per_sec" in cached_meta


@pytest.mark.asyncio
async def test_rate_limit_threshold_triggers_429_without_leaking_monthly_quota(
    async_client: AsyncClient,
    mock_redis: MockRedis,
    registered_user_and_key: tuple[dict, str],
):
    """Test making more requests than rate_limit_per_sec triggers 429 and does NOT consume monthly quota."""
    key_data, raw_key = registered_user_and_key
    headers = {"X-API-Key": raw_key}

    # Free plan allows 5 requests per second (frozen within exact same second)
    user_id = None
    fixed_now = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
    with patch("app.core.rate_limit.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        for i in range(5):
            res = await async_client.get("/api/v1/keys/verify", headers=headers)
            assert res.status_code == 200, f"Request {i + 1} should succeed"
            user_id = res.json()["user_id"]

        # 6th and 7th requests within the same epoch second must be blocked with 429
        for _ in range(2):
            blocked_res = await async_client.get("/api/v1/keys/verify", headers=headers)
            assert blocked_res.status_code == 429
            data = blocked_res.json()
            assert data["error_code"] == "RATE_LIMIT_EXCEEDED"
            assert "retry_after_seconds" in data["details"]
            assert blocked_res.headers.get("retry-after") == "1"

    # Verify monthly quota counter in Redis is still 5 (not 7!)
    year_month = fixed_now.strftime("%Y-%m")
    quota_key = f"quota:{user_id}:{year_month}"
    assert int(await mock_redis.get(quota_key) or 0) == 5


@pytest.mark.asyncio
async def test_monthly_quota_exceeded_triggers_429(
    async_client: AsyncClient,
    mock_redis: MockRedis,
    registered_user_and_key: tuple[dict, str],
):
    """Test exceeding monthly quota triggers 429 with QUOTA_EXCEEDED error code."""
    key_data, raw_key = registered_user_and_key
    headers = {"X-API-Key": raw_key}

    # First request loads metadata and verifies key
    init_res = await async_client.get("/api/v1/keys/verify", headers=headers)
    assert init_res.status_code == 200
    user_id = init_res.json()["user_id"]

    # Artificially saturate monthly quota counter in Redis to 1000
    year_month = datetime.now(UTC).strftime("%Y-%m")
    quota_key = f"quota:{user_id}:{year_month}"
    await mock_redis.set(quota_key, "1000")

    # Next request must be blocked by quota exhaustion
    quota_blocked = await async_client.get("/api/v1/keys/verify", headers=headers)
    assert quota_blocked.status_code == 429
    data = quota_blocked.json()
    assert data["error_code"] == "QUOTA_EXCEEDED"


@pytest.mark.asyncio
async def test_revoked_key_rejected_with_401(
    async_client: AsyncClient,
    registered_user_and_key: tuple[dict, str],
):
    """Test revoking an API key causes immediate rejection with 401 on subsequent requests."""
    key_data, raw_key = registered_user_and_key
    headers = {"X-API-Key": raw_key}

    # Verify key works initially
    valid_res = await async_client.get("/api/v1/keys/verify", headers=headers)
    assert valid_res.status_code == 200

    # Register a dedicated user and key to test revocation
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "revoke_test@fintech.io", "password": "Password123!"},
    )
    tok = (
        await async_client.post(
            "/api/v1/auth/login",
            json={"email": "revoke_test@fintech.io", "password": "Password123!"},
        )
    ).json()["access_token"]
    auth_h = {"Authorization": f"Bearer {tok}"}

    k_res = await async_client.post("/api/v1/keys", json={"name": "K1"}, headers=auth_h)
    k_id = k_res.json()["id"]
    k_raw = k_res.json()["api_key"]

    # Works before revoking
    assert (
        await async_client.get("/api/v1/keys/verify", headers={"X-API-Key": k_raw})
    ).status_code == 200

    # Revoke
    await async_client.delete(f"/api/v1/keys/{k_id}", headers=auth_h)

    # Rejection after revoking
    revoked_res = await async_client.get(
        "/api/v1/keys/verify", headers={"X-API-Key": k_raw}
    )
    assert revoked_res.status_code == 401
