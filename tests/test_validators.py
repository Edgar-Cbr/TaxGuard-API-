from datetime import datetime
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.services.address_validator import validate_postal_code
from app.services.email_validator import validate_corporate_email
from app.services.tax_validator import validate_rfc
from tests.conftest import MockRedis


@pytest.fixture
async def api_key_headers(async_client: AsyncClient) -> dict[str, str]:
    """Helper fixture to register user, generate key, and return X-API-Key header."""
    email = f"validator_test_{datetime.now().timestamp()}@fintech.io"
    await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "Password123!"},
    )
    tok = (
        await async_client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "Password123!"},
        )
    ).json()["access_token"]

    k_res = await async_client.post(
        "/api/v1/keys",
        json={"name": "Validator Suite Key"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    return {"X-API-Key": k_res.json()["api_key"]}


# ==============================================================================
# 1. Tax ID (RFC / TIN) Tests
# ==============================================================================


def test_rfc_persona_moral_valid():
    """Verify valid Persona Moral RFC (12 chars) with official Modulo 11 check."""
    result = validate_rfc("BBA830831LJ2")
    assert result.valid is True
    assert result.entity_type == "LEGAL_ENTITY"
    assert result.registration_date == "1983-08-31"
    assert result.checksum_valid is True
    assert len(result.errors) == 0


def test_rfc_persona_fisica_valid():
    """Verify valid Persona Física RFC (13 chars) with official Modulo 11 check."""
    result = validate_rfc("GODE561231GR0")
    assert result.valid is True
    assert result.entity_type == "INDIVIDUAL"
    assert result.registration_date == "1956-12-31"
    assert result.checksum_valid is True
    assert len(result.errors) == 0


def test_rfc_generic_public_valid():
    """Verify generic public SAT RFC."""
    result = validate_rfc("XAXX010101000")
    assert result.valid is True
    assert result.entity_type == "GENERIC"
    assert result.checksum_valid is True


def test_rfc_impossible_calendar_date():
    """Verify rejection of impossible calendar date (February 30th)."""
    # BBA83 02 30 (February 30th is impossible)
    result = validate_rfc("BBA830230LJ2")
    assert result.valid is False
    assert any("calendar date" in err.lower() for err in result.errors)


def test_rfc_checksum_mismatch():
    """Verify rejection when verification digit (homoclave checksum) is wrong."""
    # Last char should be 0, given 9
    result = validate_rfc("GODE561231GR9")
    assert result.valid is False
    assert result.checksum_valid is False
    assert any("checksum mismatch" in err.lower() for err in result.errors)


def test_rfc_invalid_length():
    """Verify rejection of non-conforming lengths."""
    result = validate_rfc("INVALID")
    assert result.valid is False
    assert any("length" in err.lower() for err in result.errors)


# ==============================================================================
# 2. Postal Code Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_postal_code_valid_and_caching(mock_redis: MockRedis):
    """Verify valid postal code lookup and subsequent 24-hour Redis caching."""
    # First lookup: served from catalog
    res1 = await validate_postal_code("06000", "MX", redis_client=mock_redis)
    assert res1.valid is True
    assert res1.state == "Ciudad de México"
    assert res1.municipality == "Cuauhtémoc"
    assert "Centro (Área 1)" in res1.settlements
    assert res1.cached is False

    # Second lookup: served from Redis cache
    res2 = await validate_postal_code("06000", "MX", redis_client=mock_redis)
    assert res2.valid is True
    assert res2.cached is True
    assert res2.municipality == "Cuauhtémoc"


@pytest.mark.asyncio
async def test_postal_code_invalid_format():
    """Verify rejection of invalid postal code format."""
    res = await validate_postal_code("ABCDE", "MX")
    assert res.valid is False
    assert any("expected 5 digits" in err for err in res.errors)


@pytest.mark.asyncio
async def test_postal_code_not_found():
    """Verify rejection of unknown postal code."""
    res = await validate_postal_code("99999", "MX")
    assert res.valid is False
    assert any("not found" in err.lower() for err in res.errors)


# ==============================================================================
# 3. Corporate Email Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_corporate_email_valid_with_mocked_dns():
    """Verify valid corporate email with active MX records."""
    with patch(
        "app.services.email_validator._resolve_mx_sync",
        return_value=["mail.fintechcorp.io", "backup.fintechcorp.io"],
    ):
        result = await validate_corporate_email("lead_architect@fintechcorp.io")
        assert result.valid is True
        assert result.is_corporate is True
        assert result.is_free_provider is False
        assert result.is_disposable is False
        assert result.has_mx_records is True
        assert "mail.fintechcorp.io" in result.mx_records


@pytest.mark.asyncio
async def test_corporate_email_free_provider_rejected():
    """Verify consumer email domains (Gmail/Yahoo/etc) are rejected as non-corporate."""
    result = await validate_corporate_email("developer@gmail.com", check_dns=False)
    assert result.valid is False
    assert result.is_corporate is False
    assert result.is_free_provider is True
    assert any("free consumer email" in err.lower() for err in result.errors)


@pytest.mark.asyncio
async def test_corporate_email_disposable_provider_rejected():
    """Verify temporary/disposable domains are rejected."""
    result = await validate_corporate_email("scammer@mailinator.com", check_dns=False)
    assert result.valid is False
    assert result.is_corporate is False
    assert result.is_disposable is True
    assert any("disposable" in err.lower() for err in result.errors)


@pytest.mark.asyncio
async def test_corporate_email_no_mx_records_rejected():
    """Verify corporate domain without MX records is rejected."""
    with patch(
        "app.services.email_validator._resolve_mx_sync",
        return_value=[],
    ):
        result = await validate_corporate_email("user@no-mail-records.com")
        assert result.valid is False
        assert result.has_mx_records is False
        assert any("mx" in err.lower() for err in result.errors)


# ==============================================================================
# 4. HTTP Endpoints & Batch Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_api_tax_id_endpoint(
    async_client: AsyncClient,
    api_key_headers: dict[str, str],
):
    """Test POST /api/v1/validate/tax-id endpoint."""
    res = await async_client.post(
        "/api/v1/validate/tax-id",
        json={"tax_id": "BBA830831LJ2"},
        headers=api_key_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["entity_type"] == "LEGAL_ENTITY"
    assert "x-ratelimit-remaining" in res.headers


@pytest.mark.asyncio
async def test_api_postal_code_endpoint(
    async_client: AsyncClient,
    api_key_headers: dict[str, str],
):
    """Test POST /api/v1/validate/postal-code endpoint."""
    res = await async_client.post(
        "/api/v1/validate/postal-code",
        json={"postal_code": "06000", "country_code": "MX"},
        headers=api_key_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["state"] == "Ciudad de México"


@pytest.mark.asyncio
async def test_api_corporate_email_endpoint(
    async_client: AsyncClient,
    api_key_headers: dict[str, str],
):
    """Test POST /api/v1/validate/corporate-email endpoint."""
    with patch(
        "app.services.email_validator._resolve_mx_sync",
        return_value=["mx1.fintech.io"],
    ):
        res = await async_client.post(
            "/api/v1/validate/corporate-email",
            json={"email": "cto@fintech.io", "check_dns": True},
            headers=api_key_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["valid"] is True
        assert data["is_corporate"] is True


@pytest.mark.asyncio
async def test_api_batch_endpoint(
    async_client: AsyncClient,
    api_key_headers: dict[str, str],
):
    """Test POST /api/v1/validate/batch endpoint."""
    with patch(
        "app.services.email_validator._resolve_mx_sync",
        return_value=["mx1.enterprise.com"],
    ):
        payload = {
            "tax_ids": ["BBA830831LJ2", "INVALID"],
            "postal_codes": ["06000", "99999"],
            "emails": ["cto@enterprise.com", "fake@mailinator.com"],
        }
        res = await async_client.post(
            "/api/v1/validate/batch",
            json=payload,
            headers=api_key_headers,
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data["tax_ids"]) == 2
        assert data["tax_ids"][0]["valid"] is True
        assert data["tax_ids"][1]["valid"] is False

        assert len(data["postal_codes"]) == 2
        assert data["postal_codes"][0]["valid"] is True
        assert data["postal_codes"][1]["valid"] is False

        assert len(data["emails"]) == 2
        assert data["emails"][0]["valid"] is True
        assert data["emails"][1]["valid"] is False
