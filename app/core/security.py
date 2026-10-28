"""Security utilities: Bcrypt password hashing, JWT creation/decoding, API Key & Webhook cryptography."""

import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import AuthenticationException


def get_password_hash(password: str) -> str:
    """Hash plaintext password with bcrypt (safely bounded to 72 UTF-8 bytes)."""
    salt = bcrypt.gensalt()
    pwd_bytes = password.encode("utf-8")[:72]
    return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plaintext password against stored bcrypt hash."""
    try:
        pwd_bytes = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(
            pwd_bytes,
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False


def create_access_token(
    subject: str | Any,
    expires_delta: timedelta | None = None,
) -> str:
    """Generate signed JWT access token for authentication."""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "iat": datetime.now(UTC),
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
        return payload
    except JWTError as exc:
        raise AuthenticationException(
            message="Could not validate credentials",
            error_code="INVALID_TOKEN",
        ) from exc


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new secure API key.

    Returns:
        tuple[raw_key, key_prefix, key_hash]:
            raw_key: Plaintext key formatted as 'tg_live_<32_hex_chars>'
            key_prefix: First 10 characters (e.g. 'tg_live_ab')
            key_hash: SHA-256 hexadecimal hash (64 characters)
    """
    token_hex = secrets.token_hex(16)  # 16 bytes = 32 hexadecimal characters
    raw_key = f"tg_live_{token_hex}"
    key_prefix = raw_key[:10]
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def hash_api_key(raw_key: str) -> str:
    """Calculate SHA-256 digest of an API key for safe database storage and lookup."""
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


def generate_stripe_signature(
    payload_bytes: bytes,
    secret: str | None = None,
    timestamp: int | None = None,
) -> str:
    """Generate an HMAC-SHA256 Stripe-Signature header (`t=<ts>,v1=<hex>`)."""
    webhook_secret = secret or settings.STRIPE_WEBHOOK_SECRET
    ts = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{ts}.".encode() + payload_bytes
    digest = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={digest}"


def verify_stripe_signature(
    payload_bytes: bytes,
    sig_header: str | None,
    secret: str | None = None,
) -> bool:
    """Verify HMAC-SHA256 Stripe-Signature header (`t=<ts>,v1=<sig>` or raw hex digest)."""
    if not sig_header or not sig_header.strip():
        return False

    webhook_secret = secret or settings.STRIPE_WEBHOOK_SECRET
    header = sig_header.strip()

    # Support standard Stripe format: t=<timestamp>,v1=<signature>
    if "v1=" in header and "t=" in header:
        parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
        ts_str = parts.get("t", "")
        v1_sig = parts.get("v1", "")
        if not ts_str or not v1_sig:
            return False
        signed_payload = f"{ts_str}.".encode() + payload_bytes
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, v1_sig)

    # Also support direct HMAC-SHA256 hex digest over raw payload bytes
    expected_raw = hmac.new(
        webhook_secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_raw, header)
