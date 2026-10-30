"""Security Hardening Verification Tests (SEC-01 through SEC-08).

Ensures strict compliance with OWASP API Security Top 10 (2023) and OWASP ASVS v4.0.3.
"""

import time

import pytest
import yaml
from httpx import AsyncClient

from app.core.security import (
    generate_stripe_signature,
    verify_stripe_signature,
)
from app.services.email_validator import validate_corporate_email
from tests.conftest import MockRedis


# SEC-01: Docker host network binding
def test_sec_01_docker_compose_ports_bound_to_localhost():
    """Verify docker-compose.yml binds database and redis ports exclusively to 127.0.0.1."""
    with open("docker-compose.yml", encoding="utf-8") as f:
        compose_data = yaml.safe_load(f)

    db_ports = compose_data["services"]["db"]["ports"]
    redis_ports = compose_data["services"]["redis"]["ports"]

    assert any(p.startswith("127.0.0.1:5432") for p in db_ports), (
        "Postgres port must be bound to 127.0.0.1"
    )
    assert any(p.startswith("127.0.0.1:6379") for p in redis_ports), (
        "Redis port must be bound to 127.0.0.1"
    )


# SEC-02: CORS Credential Reflection Prevention
@pytest.mark.asyncio
async def test_sec_02_cors_wildcard_disables_allow_credentials(
    async_client: AsyncClient,
):
    """Verify wildcard CORS does not reflect allow-credentials: true (OWASP ASVS 14.4.6)."""
    response = await async_client.get(
        "/",
        headers={"Origin": "https://malicious-attacker.com"},
    )
    assert response.status_code == 200
    # When CORS_ORIGINS is "*", allow_credentials must not be true
    assert response.headers.get("access-control-allow-credentials") is None


# SEC-03: Batch validation quota consumption
@pytest.mark.asyncio
async def test_sec_03_batch_quota_consumption_and_rollback(
    async_client: AsyncClient,
    mock_redis: MockRedis,
):
    """Verify batch validations decrement 1 quota point per item and reject when quota is exceeded."""
    # 1. Register user
    reg = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "batch-sec@enterprise.com", "password": "SecurePassword123!"},
    )
    assert reg.status_code == 201

    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "batch-sec@enterprise.com", "password": "SecurePassword123!"},
    )
    token = login.json()["access_token"]

    # Create API key
    key_res = await async_client.post(
        "/api/v1/keys",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Batch Security Key"},
    )
    api_key = key_res.json()["api_key"]
    user_id = reg.json()["id"]

    # 2. Call batch with 3 RFCs, 2 Postal codes = 5 items total
    batch_payload = {
        "tax_ids": ["BBA830831LJ2", "GODE561231GR8", "MOCG8001017TA"],
        "postal_codes": ["06000", "64000"],
        "emails": [],
    }

    res = await async_client.post(
        "/api/v1/validate/batch",
        headers={"X-API-Key": api_key},
        json=batch_payload,
    )
    assert res.status_code == 200
    year_month = time.strftime("%Y-%m")
    quota_key = f"quota:{user_id}:{year_month}"
    consumed = int(await mock_redis.get(quota_key) or 0)
    assert consumed == 5, f"Expected 5 quota units consumed for 5 items, got {consumed}"

    # 3. Exceed remaining quota (simulate near-exhaustion)
    await mock_redis.set(quota_key, "998")  # Free plan quota is 1000

    # Sending 5 items (998 + 5 = 1003 > 1000) should be blocked with 429
    blocked = await async_client.post(
        "/api/v1/validate/batch",
        headers={"X-API-Key": api_key},
        json=batch_payload,
    )
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "QUOTA_EXCEEDED"


# SEC-04: Replay attack prevention on Stripe webhook
def test_sec_04_stripe_signature_replay_attack_window():
    """Verify Stripe webhook signature rejects requests outside 300s window."""
    payload = b'{"type": "invoice.payment_succeeded"}'
    current_ts = int(time.time())

    # Valid fresh signature
    fresh_sig = generate_stripe_signature(payload, timestamp=current_ts)
    assert verify_stripe_signature(payload, fresh_sig, tolerance=300) is True

    # Replayed signature from 301 seconds ago (expired)
    expired_sig = generate_stripe_signature(payload, timestamp=current_ts - 301)
    assert verify_stripe_signature(payload, expired_sig, tolerance=300) is False

    # Signature from future beyond tolerance (clock drift attack)
    future_sig = generate_stripe_signature(payload, timestamp=current_ts + 301)
    assert verify_stripe_signature(payload, future_sig, tolerance=300) is False


# SEC-05: Rate limiting on authentication endpoints
@pytest.mark.asyncio
async def test_sec_05_auth_rate_limiting(
    async_client: AsyncClient,
    mock_redis: MockRedis,
):
    """Verify /login and /register enforce 10 requests per minute per IP."""
    # First 10 login attempts pass (getting 401 due to invalid credentials)
    for _ in range(10):
        res = await async_client.post(
            "/api/v1/auth/login",
            json={"email": "nonexistent@test.com", "password": "WrongPassword123!"},
        )
        assert res.status_code == 401

    # 11th request must be blocked with 429 Too Many Requests
    blocked = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@test.com", "password": "WrongPassword123!"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert blocked.headers.get("retry-after") == "60"


# SEC-06: Internal hostname & IP literal rejection (SSRF)
@pytest.mark.asyncio
async def test_sec_06_ssrf_internal_host_rejection():
    """Verify localhost, private IPs, and reserved hosts are rejected without DNS lookups."""
    # Emails that pass RFC email syntax but target internal or reserved hosts
    internal_emails = [
        "admin@localhost.localdomain",
        "service@test.internal",
        "dev@sandbox.lan",
    ]

    for em in internal_emails:
        res = await validate_corporate_email(em, check_dns=True)
        assert res.valid is False
        assert res.is_corporate is False
        assert any("reserved, local, or IP literal" in err for err in res.errors)

    # Emails with invalid syntax or special-use TLDs (like bare localhost or .local) must also fail cleanly
    for invalid_em in ["root@localhost", "worker@intranet.local"]:
        invalid_email_res = await validate_corporate_email(invalid_em, check_dns=True)
        assert invalid_email_res.valid is False
        assert invalid_email_res.syntax_valid is False


# SEC-07: Defensive HTTP Security Headers
@pytest.mark.asyncio
async def test_sec_07_defensive_http_security_headers(
    async_client: AsyncClient,
):
    """Verify HTTP responses contain robust defensive security headers (OWASP ASVS 14.4)."""
    res = await async_client.get("/")
    assert res.status_code == 200

    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    assert res.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
    assert "max-age=31536000" in res.headers.get("strict-transport-security", "")


# SEC-08: Token Revocation and Logout
@pytest.mark.asyncio
async def test_sec_08_token_revocation_and_logout(
    async_client: AsyncClient,
    mock_redis: MockRedis,
):
    """Verify token revocation via POST /logout invalidates subsequent authentication."""
    # 1. Register and login
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": "logout-test@corp.com", "password": "SecurePassword123!"},
    )
    login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "logout-test@corp.com", "password": "SecurePassword123!"},
    )
    token = login.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    # 2. Verify /me works with active token
    me_res = await async_client.get("/api/v1/auth/me", headers=auth_headers)
    assert me_res.status_code == 200

    # 3. Call /logout
    logout_res = await async_client.post("/api/v1/auth/logout", headers=auth_headers)
    assert logout_res.status_code == 200
    assert "Successfully logged out" in logout_res.json()["message"]

    # 4. Subsequent call to /me must fail with 401 TOKEN_REVOKED
    revoked_me = await async_client.get("/api/v1/auth/me", headers=auth_headers)
    assert revoked_me.status_code == 401
    assert revoked_me.json()["error_code"] == "TOKEN_REVOKED"
