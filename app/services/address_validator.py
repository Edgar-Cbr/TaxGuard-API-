"""Postal address and postal code verification service with 24-hour Redis caching."""

import json
from pathlib import Path
from typing import Any
from redis.asyncio import Redis

POSTAL_CODES_FILE = Path(__file__).parent.parent / "data" / "postal_codes.json"
_LOCAL_CATALOG: dict[str, dict[str, Any]] | None = None


def _get_catalog() -> dict[str, dict[str, Any]]:
    """Lazy load the postal code catalog from JSON file."""
    global _LOCAL_CATALOG
    if _LOCAL_CATALOG is None:
        if POSTAL_CODES_FILE.exists():
            with open(POSTAL_CODES_FILE, encoding="utf-8") as f:
                _LOCAL_CATALOG = json.load(f)
        else:
            _LOCAL_CATALOG = {}
    return _LOCAL_CATALOG


class AddressValidationResult:
    """Standardized result of a postal code check."""

    def __init__(
        self,
        valid: bool,
        country_code: str,
        postal_code: str,
        state: str | None = None,
        state_code: str | None = None,
        municipality: str | None = None,
        city: str | None = None,
        settlements: list[str] | None = None,
        cached: bool = False,
        errors: list[str] | None = None,
    ):
        self.valid = valid
        self.country_code = country_code
        self.postal_code = postal_code
        self.state = state
        self.state_code = state_code
        self.municipality = municipality
        self.city = city
        self.settlements = settlements or []
        self.cached = cached
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "country_code": self.country_code,
            "postal_code": self.postal_code,
            "state": self.state,
            "state_code": self.state_code,
            "municipality": self.municipality,
            "city": self.city,
            "settlements": self.settlements,
            "cached": self.cached,
            "errors": self.errors,
        }


async def validate_postal_code(
    postal_code: str,
    country_code: str = "MX",
    redis_client: Redis | None = None,
) -> AddressValidationResult:
    """Validate postal code, returning administrative settlement details with sub-10ms Redis caching.

    Args:
        postal_code: 5-digit postal code.
        country_code: Two-letter ISO country code (default: 'MX').
        redis_client: Optional async Redis client for caching.

    Returns:
        AddressValidationResult with geo-administrative details.
    """
    clean_country = country_code.strip().upper()
    clean_zip = postal_code.strip()

    # Mexican postal codes are 5 digits (can have leading zero)
    if clean_country == "MX" and clean_zip.isdigit() and len(clean_zip) < 5:
        clean_zip = clean_zip.zfill(5)

    if not clean_zip.isdigit() or len(clean_zip) != 5:
        return AddressValidationResult(
            valid=False,
            country_code=clean_country,
            postal_code=clean_zip,
            errors=[f"Invalid postal code format: expected 5 digits, received '{clean_zip}'."],
        )

    cache_key = f"catalog:zip:{clean_country}:{clean_zip}"

    # 1. Check Redis Cache (TTL 24 hours)
    if redis_client is not None:
        try:
            cached_data_str = await redis_client.get(cache_key)
            if cached_data_str:
                data = json.loads(cached_data_str)
                return AddressValidationResult(
                    valid=True,
                    country_code=clean_country,
                    postal_code=clean_zip,
                    state=data.get("state"),
                    state_code=data.get("state_code"),
                    municipality=data.get("municipality"),
                    city=data.get("city"),
                    settlements=data.get("settlements", []),
                    cached=True,
                    errors=[],
                )
        except Exception:
            pass

    # 2. Lookup in local catalog
    catalog = _get_catalog()
    entry = catalog.get(clean_zip)

    if entry is None or entry.get("country_code", "MX") != clean_country:
        return AddressValidationResult(
            valid=False,
            country_code=clean_country,
            postal_code=clean_zip,
            errors=[f"Postal code '{clean_zip}' not found in official {clean_country} catalog."],
        )

    # 3. Cache result in Redis for 24 hours (86400 seconds)
    if redis_client is not None:
        try:
            await redis_client.setex(cache_key, 86400, json.dumps(entry))
        except Exception:
            pass

    return AddressValidationResult(
        valid=True,
        country_code=clean_country,
        postal_code=clean_zip,
        state=entry.get("state"),
        state_code=entry.get("state_code"),
        municipality=entry.get("municipality"),
        city=entry.get("city"),
        settlements=entry.get("settlements", []),
        cached=False,
        errors=[],
    )
