"""Pydantic schemas for B2B validation requests and responses."""

from pydantic import BaseModel, ConfigDict, Field


class TaxIdValidationRequest(BaseModel):
    """Payload to validate a Tax Identification Number (RFC / TIN)."""

    tax_id: str = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Mexican RFC or international TIN",
        examples=["BBA830831LJ2", "GODE561231GR0"],
    )


class TaxIdValidationResponse(BaseModel):
    """Response returned from Tax ID validation."""

    model_config = ConfigDict(from_attributes=True)

    valid: bool
    entity_type: str = Field(
        description="Type of tax entity: 'INDIVIDUAL', 'LEGAL_ENTITY', 'GENERIC', or 'UNKNOWN'"
    )
    normalized_id: str
    registration_date: str | None = None
    checksum_valid: bool
    errors: list[str] = Field(default_factory=list)


class PostalCodeValidationRequest(BaseModel):
    """Payload to validate a postal code."""

    postal_code: str = Field(
        ...,
        min_length=1,
        max_length=10,
        description="5-digit Mexican postal code",
        examples=["06000", "44100", "64000"],
    )
    country_code: str = Field(
        default="MX",
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code",
        examples=["MX"],
    )


class PostalCodeValidationResponse(BaseModel):
    """Response returned from postal code lookup."""

    model_config = ConfigDict(from_attributes=True)

    valid: bool
    country_code: str
    postal_code: str
    state: str | None = None
    state_code: str | None = None
    municipality: str | None = None
    city: str | None = None
    settlements: list[str] = Field(default_factory=list)
    cached: bool = Field(description="True if served directly from Redis cache")
    errors: list[str] = Field(default_factory=list)


class CorporateEmailValidationRequest(BaseModel):
    """Payload to validate a corporate business email address."""

    email: str = Field(
        ...,
        description="Email address to verify",
        examples=["contact@enterprise.com", "user@gmail.com"],
    )
    check_dns: bool = Field(
        default=True,
        description="Whether to perform live DNS MX lookup",
    )


class CorporateEmailValidationResponse(BaseModel):
    """Response returned from corporate email validation."""

    model_config = ConfigDict(from_attributes=True)

    valid: bool
    email: str
    domain: str
    syntax_valid: bool
    is_corporate: bool
    is_free_provider: bool
    is_disposable: bool
    has_mx_records: bool
    mx_records: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BatchValidationRequest(BaseModel):
    """Payload for batch multi-entity validation."""

    tax_ids: list[str] = Field(default_factory=list, max_length=50)
    postal_codes: list[str] = Field(default_factory=list, max_length=50)
    emails: list[str] = Field(default_factory=list, max_length=50)


class BatchValidationResponse(BaseModel):
    """Response with bulk validation results."""

    tax_ids: list[TaxIdValidationResponse] = Field(default_factory=list)
    postal_codes: list[PostalCodeValidationResponse] = Field(default_factory=list)
    emails: list[CorporateEmailValidationResponse] = Field(default_factory=list)
