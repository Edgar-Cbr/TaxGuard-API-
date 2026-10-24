import uuid

import pytest
from httpx import AsyncClient

from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    get_password_hash,
    hash_api_key,
    verify_password,
)
from tests.conftest import MockRedis

# ==============================================================================
# Security Unit Tests
# ==============================================================================


def test_generate_api_key_format_and_hashing():
    """Verify API key formatting, prefix, and SHA-256 integrity."""
    raw_key, key_prefix, key_hash = generate_api_key()

    # Rule 3: format tg_live_<32_hex_chars>
    assert raw_key.startswith("tg_live_")
    assert len(raw_key) == 8 + 32  # 40 characters total
    assert key_prefix == raw_key[:10]
    assert len(key_hash) == 64
    assert key_hash == hash_api_key(raw_key)


def test_password_hashing():
    """Verify Bcrypt hashing and verification."""
    password = "SuperSecretPassword123!"
    hashed = get_password_hash(password)
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False


def test_jwt_token_encoding_and_decoding():
    """Verify JWT access token creation and decoding."""
    user_id = str(uuid.uuid4())
    token = create_access_token(subject=user_id)
    payload = decode_access_token(token)
    assert payload["sub"] == user_id
    assert "exp" in payload


# ==============================================================================
# Auth & Keys Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_user_registration_flow(async_client: AsyncClient):
    """Test user registration automatically creates a FREE subscription."""
    payload = {
        "email": "developer@fintech.io",
        "password": "StrongPassword2026!",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "developer@fintech.io"
    assert data["tier"] == "FREE"
    assert data["is_active"] is True
    assert "subscription" in data
    assert data["subscription"]["monthly_quota"] == 1000
    assert data["subscription"]["rate_limit_per_sec"] == 5


@pytest.mark.asyncio
async def test_registration_duplicate_email_rejected(async_client: AsyncClient):
    """Test registering the same email twice returns 400 Bad Request."""
    payload = {
        "email": "duplicate@fintech.io",
        "password": "StrongPassword2026!",
    }
    res1 = await async_client.post("/api/v1/auth/register", json=payload)
    assert res1.status_code == 201

    res2 = await async_client.post("/api/v1/auth/register", json=payload)
    assert res2.status_code == 400
    assert res2.json()["error_code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_user_login_success_and_failure(async_client: AsyncClient):
    """Test login succeeds with correct credentials and fails with wrong password."""
    # Register first
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "login_test@fintech.io", "password": "Password123!"},
    )

    # Valid Login
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "login_test@fintech.io", "password": "Password123!"},
    )
    assert login_res.status_code == 200
    token_data = login_res.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"

    # Invalid Password Login
    bad_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "login_test@fintech.io", "password": "WrongPassword!"},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["error_code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_get_me_endpoint_security(async_client: AsyncClient):
    """Test /auth/me requires valid Bearer authentication."""
    # Unauthenticated request
    unauth_res = await async_client.get("/api/v1/auth/me")
    assert unauth_res.status_code == 401

    # Register and login
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "me_test@fintech.io", "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "me_test@fintech.io", "password": "Password123!"},
    )
    token = login_res.json()["access_token"]

    # Authenticated request
    headers = {"Authorization": f"Bearer {token}"}
    me_res = await async_client.get("/api/v1/auth/me", headers=headers)
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "me_test@fintech.io"


@pytest.mark.asyncio
async def test_api_key_lifecycle_create_list_revoke(
    async_client: AsyncClient,
    mock_redis: MockRedis,
):
    """Full lifecycle: create API key (reveals raw key once), list safely, and revoke."""
    # 1. Register & Login
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "keys_user@fintech.io", "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "keys_user@fintech.io", "password": "Password123!"},
    )
    token = login_res.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    # 2. Create API Key
    create_res = await async_client.post(
        "/api/v1/keys",
        json={"name": "Production Service Key"},
        headers=auth_header,
    )
    assert create_res.status_code == 201
    created_data = create_res.json()
    assert "api_key" in created_data
    raw_api_key = created_data["api_key"]
    key_id = created_data["id"]
    assert raw_api_key.startswith("tg_live_")
    assert created_data["key_prefix"] == raw_api_key[:10]

    # Pre-populate mock redis auth cache with this key's hash
    key_hash = hash_api_key(raw_api_key)
    cache_key = f"apikey:meta:{key_hash}"
    await mock_redis.set(cache_key, '{"user_id": "cached"}')
    assert await mock_redis.get(cache_key) is not None

    # 3. List API Keys
    list_res = await async_client.get("/api/v1/keys", headers=auth_header)
    assert list_res.status_code == 200
    keys_list = list_res.json()
    assert len(keys_list) >= 1
    found_key = next((k for k in keys_list if k["id"] == key_id), None)
    assert found_key is not None
    assert found_key["key_prefix"] == raw_api_key[:10]
    assert found_key["is_active"] is True
    # Verify plain key and hash are NOT leaked in list endpoint
    assert "api_key" not in found_key
    assert "key_hash" not in found_key

    # 4. Revoke API Key
    revoke_res = await async_client.delete(
        f"/api/v1/keys/{key_id}", headers=auth_header
    )
    assert revoke_res.status_code == 200
    assert revoke_res.json()["revoked_key_id"] == key_id

    # Verify Redis cache eviction
    assert await mock_redis.get(cache_key) is None

    # 5. List again to verify inactive status
    list_after_res = await async_client.get("/api/v1/keys", headers=auth_header)
    revoked_key_data = next(
        (k for k in list_after_res.json() if k["id"] == key_id), None
    )
    assert revoked_key_data["is_active"] is False
    assert revoked_key_data["revoked_at"] is not None


@pytest.mark.asyncio
async def test_revoke_nonexistent_api_key(async_client: AsyncClient):
    """Test revoking an unknown API Key returns 404 Not Found."""
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "nonexist@fintech.io", "password": "Password123!"},
    )
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexist@fintech.io", "password": "Password123!"},
    )
    token = login_res.json()["access_token"]
    auth_header = {"Authorization": f"Bearer {token}"}

    random_id = str(uuid.uuid4())
    res = await async_client.delete(f"/api/v1/keys/{random_id}", headers=auth_header)
    assert res.status_code == 404
    assert res.json()["error_code"] == "KEY_NOT_FOUND"
