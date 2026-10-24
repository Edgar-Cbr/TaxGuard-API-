"""Pydantic schemas for API key lifecycle management."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    """Payload to request the creation of a new API key."""

    name: str = Field(
        default="Default Key",
        max_length=100,
        description="Friendly identifier for the API key",
    )


class ApiKeyCreatedResponse(BaseModel):
    """Response returned upon key creation, revealing plaintext key only once."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    api_key: str = Field(
        description="Plaintext API key. Store this securely as it will not be shown again."
    )
    created_at: datetime


class ApiKeyResponse(BaseModel):
    """Safe representation of an API key without revealing secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiKeyRevokeResponse(BaseModel):
    """Confirmation message of API key revocation."""

    message: str = "API Key successfully revoked."
    revoked_key_id: uuid.UUID


class UserUsageStatsResponse(BaseModel):
    """Aggregated usage metrics and status breakdown for authenticated user."""

    total_calls: int = Field(description="Total requests recorded across all user keys")
    avg_latency_ms: float = Field(description="Average latency in milliseconds")
    status_codes: dict[str, int] = Field(
        default_factory=dict,
        description="Breakdown of responses grouped by HTTP status code (e.g. 200, 422, 429)",
    )
    monthly_quota: int = Field(
        description="Total monthly requests allowed under active tier"
    )
    quota_remaining: int = Field(description="Remaining monthly requests")
    tier: str = Field(description="Active user tier (FREE, PRO, ENTERPRISE)")
