"""Tax Identification Number (RFC / TIN) validation service with Modulo 11 check."""

import re
from datetime import UTC, datetime
from typing import Any

# SAT Official character-to-value mapping (Anexo 20)
# 0-9 -> 0-9, A-N -> 10-23, & -> 24, O-Z -> 25-36, space -> 37, Ñ -> 38
CHAR_VALUES: dict[str, int] = {str(i): i for i in range(10)}
CHAR_VALUES.update({ch: idx for idx, ch in enumerate("ABCDEFGHIJKLMN", start=10)})
CHAR_VALUES["&"] = 24
CHAR_VALUES.update({ch: idx for idx, ch in enumerate("OPQRSTUVWXYZ", start=25)})
CHAR_VALUES[" "] = 37
CHAR_VALUES["Ñ"] = 38

# Regex patterns for Persona Física (13 chars) and Persona Moral (12 chars)
RFC_INDIVIDUAL_REGEX = re.compile(r"^[A-Z&Ñ]{4}[0-9]{6}[A-Z0-9]{3}$")
RFC_LEGAL_ENTITY_REGEX = re.compile(r"^[A-Z&Ñ]{3}[0-9]{6}[A-Z0-9]{3}$")

# Generic public and foreign RFCs recognized by SAT
GENERIC_RFCS = {"XAXX010101000", "XEXX010101000"}


class TaxValidationResult:
    """Standardized result of a Tax ID / RFC validation check."""

    def __init__(
        self,
        valid: bool,
        entity_type: str,
        normalized_id: str,
        registration_date: str | None = None,
        checksum_valid: bool = False,
        errors: list[str] | None = None,
    ):
        self.valid = valid
        self.entity_type = entity_type
        self.normalized_id = normalized_id
        self.registration_date = registration_date
        self.checksum_valid = checksum_valid
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "entity_type": self.entity_type,
            "normalized_id": self.normalized_id,
            "registration_date": self.registration_date,
            "checksum_valid": self.checksum_valid,
            "errors": self.errors,
        }


def calculate_sat_checksum(rfc: str) -> str:
    """Calculate the official SAT Modulo 11 verification digit.

    For Persona Moral (12 chars), a leading space is prepended to align
    the weights across positions 12 down to 2.
    """
    clean_rfc = rfc.upper().strip()
    if len(clean_rfc) == 12:
        clean_rfc = " " + clean_rfc

    total_sum = sum(
        CHAR_VALUES.get(char, 0) * (14 - (i + 1))
        for i, char in enumerate(clean_rfc[:12])
    )
    remainder = total_sum % 11
    if remainder == 0:
        return "0"
    check = 11 - remainder
    return "A" if check == 10 else str(check)


def validate_calendar_date(date_str: str) -> tuple[bool, str | None, str | None]:
    """Parse and validate YYMMDD 6-digit calendar date."""
    try:
        yy = int(date_str[0:2])
        mm = int(date_str[2:4])
        dd = int(date_str[4:6])

        now_utc = datetime.now(UTC)
        current_year = now_utc.year
        current_century = (current_year // 100) * 100
        cutoff_yy = current_year % 100

        # Heuristic: years higher than current 2-digit year belong to 1900s
        year = (current_century if yy <= cutoff_yy else current_century - 100) + yy

        parsed_date = datetime(year=year, month=mm, day=dd)
        formatted_date = parsed_date.strftime("%Y-%m-%d")

        if parsed_date.date() > now_utc.date():
            return (
                False,
                None,
                f"Invalid calendar date '{date_str}': registration date {formatted_date} cannot be in the future.",
            )

        return True, formatted_date, None
    except ValueError as exc:
        return False, None, f"Invalid calendar date '{date_str}': {exc}"


def validate_rfc(tax_id: str) -> TaxValidationResult:
    """Perform complete syntactical, calendar, and cryptographic Modulo 11 validation on an RFC.

    Args:
        tax_id: Raw string input for Mexican Tax ID (RFC).

    Returns:
        TaxValidationResult with detailed validation status and error breakdown.
    """
    errors: list[str] = []
    normalized = tax_id.strip().upper()

    if not normalized:
        return TaxValidationResult(
            valid=False,
            entity_type="UNKNOWN",
            normalized_id="",
            errors=["Tax ID cannot be empty."],
        )

    # Check for generic public RFCs
    if normalized in GENERIC_RFCS:
        return TaxValidationResult(
            valid=True,
            entity_type="GENERIC",
            normalized_id=normalized,
            registration_date="2001-01-01",
            checksum_valid=True,
            errors=[],
        )

    # 1. Determine entity type and validate format & length
    length = len(normalized)
    entity_type = "UNKNOWN"
    date_part = ""

    if length == 13:
        if not RFC_INDIVIDUAL_REGEX.match(normalized):
            errors.append(
                "Invalid syntax format for Persona Física (expected 4 letters + 6 digits + 3 alphanumeric characters)."
            )
        entity_type = "INDIVIDUAL"
        date_part = normalized[4:10]
    elif length == 12:
        if not RFC_LEGAL_ENTITY_REGEX.match(normalized):
            errors.append(
                "Invalid syntax format for Persona Moral (expected 3 letters + 6 digits + 3 alphanumeric characters)."
            )
        entity_type = "LEGAL_ENTITY"
        date_part = normalized[3:9]
    else:
        errors.append(
            f"Invalid Tax ID length ({length} characters). Must be 12 (Legal Entity) or 13 (Individual)."
        )
        return TaxValidationResult(
            valid=False,
            entity_type="UNKNOWN",
            normalized_id=normalized,
            errors=errors,
        )

    # 2. Validate calendar date (YYMMDD)
    date_valid = False
    reg_date = None
    if date_part:
        date_valid, reg_date, date_err = validate_calendar_date(date_part)
        if not date_valid:
            errors.append(date_err or "Invalid registration date.")

    # 3. Verify Modulo 11 verification digit
    expected_checksum = calculate_sat_checksum(normalized)
    provided_checksum = normalized[-1]
    checksum_valid = expected_checksum == provided_checksum

    if not checksum_valid:
        errors.append(
            f"Checksum mismatch: expected '{expected_checksum}', received '{provided_checksum}'."
        )

    is_valid = bool(len(errors) == 0 and checksum_valid and date_valid)

    return TaxValidationResult(
        valid=is_valid,
        entity_type=entity_type,
        normalized_id=normalized,
        registration_date=reg_date if date_valid else None,
        checksum_valid=checksum_valid,
        errors=errors,
    )
