"""B2B Data and Fiscal Validation endpoints protected by API Key and Rate Limiting."""

from typing import Annotated

from fastapi import APIRouter, Depends
from redis.asyncio import Redis

from app.api.deps import get_redis, verify_api_key_and_quota
from app.core.rate_limit import ApiKeyMetadata
from app.schemas.validator import (
    BatchValidationRequest,
    BatchValidationResponse,
    CorporateEmailValidationRequest,
    CorporateEmailValidationResponse,
    PostalCodeValidationRequest,
    PostalCodeValidationResponse,
    TaxIdValidationRequest,
    TaxIdValidationResponse,
)
from app.services.address_validator import validate_postal_code
from app.services.email_validator import validate_corporate_email
from app.services.tax_validator import validate_rfc

router = APIRouter()


@router.post(
    "/tax-id",
    response_model=TaxIdValidationResponse,
    summary="Validate Tax ID / RFC format, calendar date, and Modulo 11 checksum",
)
async def validate_tax_id_endpoint(
    payload: TaxIdValidationRequest,
    _auth: Annotated[ApiKeyMetadata, Depends(verify_api_key_and_quota)],
) -> TaxIdValidationResponse:
    """Validate a Mexican RFC (Persona Física o Moral) or international TIN."""
    result = validate_rfc(payload.tax_id)
    return TaxIdValidationResponse(
        valid=result.valid,
        entity_type=result.entity_type,
        normalized_id=result.normalized_id,
        registration_date=result.registration_date,
        checksum_valid=result.checksum_valid,
        errors=result.errors,
    )


@router.post(
    "/postal-code",
    response_model=PostalCodeValidationResponse,
    summary="Validate postal code against official catalog with sub-10ms Redis caching",
)
async def validate_postal_code_endpoint(
    payload: PostalCodeValidationRequest,
    _auth: Annotated[ApiKeyMetadata, Depends(verify_api_key_and_quota)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> PostalCodeValidationResponse:
    """Validate a 5-digit postal code and retrieve city, state, municipality and settlements."""
    result = await validate_postal_code(
        postal_code=payload.postal_code,
        country_code=payload.country_code,
        redis_client=redis_client,
    )
    return PostalCodeValidationResponse(
        valid=result.valid,
        country_code=result.country_code,
        postal_code=result.postal_code,
        state=result.state,
        state_code=result.state_code,
        municipality=result.municipality,
        city=result.city,
        settlements=result.settlements,
        cached=result.cached,
        errors=result.errors,
    )


@router.post(
    "/corporate-email",
    response_model=CorporateEmailValidationResponse,
    summary="Verify email syntax, filter disposable/free providers, and check MX records",
)
async def validate_corporate_email_endpoint(
    payload: CorporateEmailValidationRequest,
    _auth: Annotated[ApiKeyMetadata, Depends(verify_api_key_and_quota)],
) -> CorporateEmailValidationResponse:
    """Verify corporate domain status, syntax, disposable domains and live DNS MX records."""
    result = await validate_corporate_email(
        email=payload.email,
        check_dns=payload.check_dns,
    )
    return CorporateEmailValidationResponse(
        valid=result.valid,
        email=result.email,
        domain=result.domain,
        syntax_valid=result.syntax_valid,
        is_corporate=result.is_corporate,
        is_free_provider=result.is_free_provider,
        is_disposable=result.is_disposable,
        has_mx_records=result.has_mx_records,
        mx_records=result.mx_records,
        errors=result.errors,
    )


@router.post(
    "/batch",
    response_model=BatchValidationResponse,
    summary="Perform bulk validation across Tax IDs, postal codes, and corporate emails",
)
async def validate_batch_endpoint(
    payload: BatchValidationRequest,
    _auth: Annotated[ApiKeyMetadata, Depends(verify_api_key_and_quota)],
    redis_client: Annotated[Redis, Depends(get_redis)],
) -> BatchValidationResponse:
    """Batch validation endpoint for enterprise onboarding pipelines."""
    # 1. Validate Tax IDs
    tax_results = [
        TaxIdValidationResponse(**validate_rfc(tid).to_dict())
        for tid in payload.tax_ids
    ]

    # 2. Validate Postal Codes
    postal_results = [
        PostalCodeValidationResponse(
            **(
                await validate_postal_code(zip_code, redis_client=redis_client)
            ).to_dict()
        )
        for zip_code in payload.postal_codes
    ]

    # 3. Validate Corporate Emails
    email_results = [
        CorporateEmailValidationResponse(
            **(await validate_corporate_email(em, check_dns=True)).to_dict()
        )
        for em in payload.emails
    ]

    return BatchValidationResponse(
        tax_ids=tax_results,
        postal_codes=postal_results,
        emails=email_results,
    )
