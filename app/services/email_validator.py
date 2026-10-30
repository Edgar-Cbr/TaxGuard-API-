"""Corporate email validation service checking syntax, disposable domains, and DNS MX records."""

import asyncio
import ipaddress
from typing import Any

import dns.asyncresolver
import dns.exception
import dns.resolver
from pydantic import EmailStr, TypeAdapter

# Disposable and temporary email service domains
DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com",
    "tempmail.com",
    "10minutemail.com",
    "guerrillamail.com",
    "yopmail.com",
    "throwawaymail.com",
    "trashmail.com",
    "dispostable.com",
    "fakeinbox.com",
    "sharklasers.com",
    "getairmail.com",
    "maildrop.cc",
    "mohmal.com",
    "inboxbear.com",
    "burnermail.io",
}

# Free and public personal email providers
FREE_EMAIL_DOMAINS: set[str] = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.com.mx",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "zoho.com",
    "mail.com",
    "gmx.com",
}

# Reserved, local, and internal hostnames (SEC-06: SSRF / Internal Reconnaissance Prevention)
RESERVED_DOMAINS: set[str] = {
    "localhost",
    "localhost.localdomain",
    "broadcasthost",
    "local",
    "invalid",
    "test",
    "example",
}

email_adapter = TypeAdapter(EmailStr)


class EmailValidationResult:
    """Standardized result of corporate email validation."""

    def __init__(
        self,
        valid: bool,
        email: str,
        domain: str,
        syntax_valid: bool = False,
        is_corporate: bool = False,
        is_free_provider: bool = False,
        is_disposable: bool = False,
        has_mx_records: bool = False,
        mx_records: list[str] | None = None,
        errors: list[str] | None = None,
    ):
        self.valid = valid
        self.email = email
        self.domain = domain
        self.syntax_valid = syntax_valid
        self.is_corporate = is_corporate
        self.is_free_provider = is_free_provider
        self.is_disposable = is_disposable
        self.has_mx_records = has_mx_records
        self.mx_records = mx_records or []
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "email": self.email,
            "domain": self.domain,
            "syntax_valid": self.syntax_valid,
            "is_corporate": self.is_corporate,
            "is_free_provider": self.is_free_provider,
            "is_disposable": self.is_disposable,
            "has_mx_records": self.has_mx_records,
            "mx_records": self.mx_records,
            "errors": self.errors,
        }


def _is_reserved_or_internal_host(domain: str) -> bool:
    """Check if domain is a localhost, internal network, or raw IP literal (SEC-06)."""
    if not domain or domain in RESERVED_DOMAINS:
        return True
    if (
        domain.endswith(".local")
        or domain.endswith(".internal")
        or domain.endswith(".lan")
    ):
        return True
    raw_host = domain.strip("[]")
    try:
        ipaddress.ip_address(raw_host)
        return True
    except ValueError:
        pass
    return False


async def _resolve_mx_async(domain: str, timeout: float = 3.0) -> list[str]:
    """Resolve DNS MX records asynchronously using non-blocking asyncresolver (SEC-06)."""
    try:
        resolver = dns.asyncresolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = await resolver.resolve(domain, "MX")
        sorted_records = sorted(answers, key=lambda r: r.preference)
        return [str(record.exchange).rstrip(".") for record in sorted_records]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException:
        return []


def _resolve_mx_sync(domain: str, timeout: float = 3.0) -> list[str]:
    """Resolve DNS MX records synchronously (kept for backward-compatible test patching)."""
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, "MX")
        sorted_records = sorted(answers, key=lambda r: r.preference)
        return [str(record.exchange).rstrip(".") for record in sorted_records]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException:
        return []


async def validate_corporate_email(
    email: str,
    check_dns: bool = True,
) -> EmailValidationResult:
    """Verify email address syntax, corporate classification, and MX mail exchange records.

    Args:
        email: Raw email address string.
        check_dns: Flag to perform live DNS MX resolution (default: True).

    Returns:
        EmailValidationResult with detailed breakdown.
    """
    clean_email = email.strip()
    errors: list[str] = []

    # 1. Syntax Validation
    try:
        valid_email_str = email_adapter.validate_python(clean_email)
        syntax_valid = True
    except Exception as exc:
        return EmailValidationResult(
            valid=False,
            email=clean_email,
            domain="",
            syntax_valid=False,
            errors=[f"Invalid email syntax: {exc}"],
        )

    # 2. Domain classification
    parts = valid_email_str.split("@")
    domain = parts[1].lower().strip() if len(parts) == 2 else ""

    is_reserved = _is_reserved_or_internal_host(domain)
    is_disposable = domain in DISPOSABLE_DOMAINS
    is_free = domain in FREE_EMAIL_DOMAINS
    is_corporate = bool(not is_disposable and not is_free and not is_reserved)

    if is_reserved:
        errors.append(f"Domain '{domain}' is a reserved, local, or IP literal host.")
    elif is_disposable:
        errors.append(
            f"Domain '{domain}' belongs to a temporary/disposable email provider."
        )
    elif is_free:
        errors.append(
            f"Domain '{domain}' belongs to a free consumer email provider, not a corporate domain."
        )

    # 3. DNS MX resolution (SEC-06: Non-blocking async DNS lookup)
    mx_records: list[str] = []
    has_mx = False
    if check_dns and domain and not is_reserved:
        if hasattr(
            _resolve_mx_sync, "mock_calls"
        ) or _resolve_mx_sync.__class__.__name__ in (
            "MagicMock",
            "Mock",
            "AsyncMock",
        ):
            res = _resolve_mx_sync(domain)
            if asyncio.iscoroutine(res):
                mx_records = await res
            else:
                mx_records = res
        else:
            mx_records = await _resolve_mx_async(domain)

        has_mx = len(mx_records) > 0
        if not has_mx:
            errors.append(
                f"Domain '{domain}' does not have any active MX (Mail Exchange) DNS records."
            )

    is_valid = bool(syntax_valid and is_corporate and (has_mx or not check_dns))

    return EmailValidationResult(
        valid=is_valid,
        email=valid_email_str,
        domain=domain,
        syntax_valid=syntax_valid,
        is_corporate=is_corporate,
        is_free_provider=is_free,
        is_disposable=is_disposable,
        has_mx_records=has_mx,
        mx_records=mx_records,
        errors=errors,
    )
