"""End-to-End integration test covering the complete TaxGuard B2B Micro-SaaS lifecycle.

Flow:
1. User registration & JWT authentication
2. API Key creation with secure hashing
3. B2B Fiscal ID, Postal Code, and Corporate Email validations with X-API-Key
4. Rate limiting and quota header assertions (X-RateLimit-*, X-Quota-Remaining)
5. Usage statistics retrieval
6. Stripe checkout session upgrade to PRO tier and Redis cache eviction
7. Upgraded rate limits (50 req/s) verification
8. API Key revocation and subsequent request rejection (401)
"""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.user import User, UserTier
from app.services import usage_tracker
from tests.conftest import MockRedis, TestingSessionLocal


@pytest.mark.asyncio
async def test_full_b2b_saas_lifecycle_e2e(
    async_client: AsyncClient,
    db_session: AsyncSession,
    mock_redis: MockRedis,
):
    """Execute full end-to-end user onboarding, key rotation, validation, and upgrade lifecycle."""
    usage_tracker.session_factory_override = TestingSessionLocal

    try:
        # --------------------------------------------------------------------------
        # Step 1: User Registration & Login
        # --------------------------------------------------------------------------
        user_email = "cto@neobank-fintech.io"
        user_password = "SecurePassword2026!"

        reg_res = await async_client.post(
            "/api/v1/auth/register",
            json={"email": user_email, "password": user_password},
        )
        assert reg_res.status_code == 201
        user_data = reg_res.json()
        assert user_data["email"] == user_email
        assert user_data["tier"] == "FREE"
        user_id = user_data["id"]

        login_res = await async_client.post(
            "/api/v1/auth/login",
            json={"email": user_email, "password": user_password},
        )
        assert login_res.status_code == 200
        access_token = login_res.json()["access_token"]
        jwt_headers = {"Authorization": f"Bearer {access_token}"}

        # Verify /me endpoint
        me_res = await async_client.get("/api/v1/auth/me", headers=jwt_headers)
        assert me_res.status_code == 200
        assert me_res.json()["id"] == user_id

        # --------------------------------------------------------------------------
        # Step 2: Generate API Key
        # --------------------------------------------------------------------------
        key_res = await async_client.post(
            "/api/v1/keys",
            json={"name": "Production Invoicing Gateway"},
            headers=jwt_headers,
        )
        assert key_res.status_code == 201
        key_payload = key_res.json()
        raw_api_key = key_payload["api_key"]
        key_id = key_payload["id"]
        assert raw_api_key.startswith("tg_live_")
        assert key_payload["key_prefix"].startswith("tg_live_")

        api_key_headers = {"X-API-Key": raw_api_key}

        # --------------------------------------------------------------------------
        # Step 3: Validate Tax ID (Mexican RFC) under FREE tier (limit: 5 req/s, 1000 quota)
        # --------------------------------------------------------------------------
        tax_res = await async_client.post(
            "/api/v1/validate/tax-id",
            json={"tax_id": "BBA830831LJ2"},
            headers=api_key_headers,
        )
        assert tax_res.status_code == 200
        assert tax_res.json()["valid"] is True
        assert tax_res.json()["entity_type"] == "LEGAL_ENTITY"
        assert tax_res.json()["checksum_valid"] is True

        # Validate rate limiting headers
        assert tax_res.headers.get("X-RateLimit-Limit") == "5"
        assert int(tax_res.headers.get("X-RateLimit-Remaining")) == 4
        assert int(tax_res.headers.get("X-Quota-Remaining")) == 999

        # --------------------------------------------------------------------------
        # Step 4: Validate Postal Code
        # --------------------------------------------------------------------------
        zip_res = await async_client.post(
            "/api/v1/validate/postal-code",
            json={"postal_code": "06000", "country_code": "MX"},
            headers=api_key_headers,
        )
        assert zip_res.status_code == 200
        assert zip_res.json()["valid"] is True
        assert zip_res.json()["state"] == "Ciudad de México"
        assert zip_res.json()["municipality"] == "Cuauhtémoc"
        assert int(zip_res.headers.get("X-Quota-Remaining")) == 998

        # --------------------------------------------------------------------------
        # Step 5: Validate Corporate Email (with mocked DNS)
        # --------------------------------------------------------------------------
        with patch(
            "app.services.email_validator._resolve_mx_sync",
            return_value=["mail.fintech-corp.com"],
        ):
            email_res = await async_client.post(
                "/api/v1/validate/corporate-email",
                json={"email": "compliance@fintech-corp.com", "check_dns": True},
                headers=api_key_headers,
            )
        assert email_res.status_code == 200
        assert email_res.json()["valid"] is True
        assert email_res.json()["is_corporate"] is True
        assert email_res.json()["is_disposable"] is False
        assert email_res.json()["has_mx_records"] is True
        assert int(email_res.headers.get("X-Quota-Remaining")) == 997

        # --------------------------------------------------------------------------
        # Step 6: Query Aggregated Usage Stats
        # --------------------------------------------------------------------------
        stats_res = await async_client.get("/api/v1/keys/usage", headers=jwt_headers)
        assert stats_res.status_code == 200
        stats_data = stats_res.json()
        assert stats_data["tier"] == "FREE"
        assert stats_data["monthly_quota"] == 1000
        assert stats_data["quota_remaining"] == 997

        # --------------------------------------------------------------------------
        # Step 7: Simulate Stripe Webhook Upgrade to PRO
        # --------------------------------------------------------------------------
        stripe_payload = {
            "id": "evt_live_simulated_9988",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_live_session_pro_plan",
                    "customer": "cus_stripe_enterprise_8899",
                    "client_reference_id": user_id,
                    "customer_email": user_email,
                }
            },
        }

        upgrade_res = await async_client.post(
            "/api/v1/billing/webhook",
            json=stripe_payload,
        )
        assert upgrade_res.status_code == 200
        upgrade_data = upgrade_res.json()
        assert upgrade_data["status"] == "success"
        assert upgrade_data["tier"] == "PRO"
        assert upgrade_data["monthly_quota"] == 50000
        assert upgrade_data["rate_limit_per_sec"] == 50

        # Verify user tier in DB
        user_db = (
            await db_session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        ).scalar_one()
        assert user_db.tier == UserTier.PRO

        # --------------------------------------------------------------------------
        # Step 8: Validate upgraded limits in subsequent API call
        # --------------------------------------------------------------------------
        upgraded_tax_res = await async_client.post(
            "/api/v1/validate/tax-id",
            json={"tax_id": "XAXX010101000"},
            headers=api_key_headers,
        )
        assert upgraded_tax_res.status_code == 200
        assert upgraded_tax_res.headers.get("X-RateLimit-Limit") == "50"
        # Remaining quota reflects PRO quota (50000 - 4 consumed = 49996)
        assert int(upgraded_tax_res.headers.get("X-Quota-Remaining")) == 49996

        # --------------------------------------------------------------------------
        # Step 9: Revoke API Key
        # --------------------------------------------------------------------------
        del_res = await async_client.delete(
            f"/api/v1/keys/{key_id}",
            headers=jwt_headers,
        )
        assert del_res.status_code == 200
        assert del_res.json()["revoked_key_id"] == key_id

        # Verify key is deactivated in DB
        key_db = (
            await db_session.execute(
                select(ApiKey).where(ApiKey.id == uuid.UUID(key_id))
            )
        ).scalar_one()
        assert key_db.is_active is False
        assert key_db.revoked_at is not None

        # --------------------------------------------------------------------------
        # Step 10: Attempt to call validation with revoked key -> 401 Unauthorized
        # --------------------------------------------------------------------------
        revoked_call_res = await async_client.post(
            "/api/v1/validate/tax-id",
            json={"tax_id": "BBA830831LJ2"},
            headers=api_key_headers,
        )
        assert revoked_call_res.status_code == 401
        assert revoked_call_res.json()["error_code"] == "INVALID_API_KEY"
    finally:
        usage_tracker.session_factory_override = None
