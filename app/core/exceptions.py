from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class AppException(Exception):
    """Base exception class for TaxGuard API."""

    def __init__(
        self,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        error_code: str = "BAD_REQUEST",
        details: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details


class RateLimitExceededException(AppException):
    """Exception raised when API rate limit or monthly quota is exceeded."""

    def __init__(
        self,
        message: str = "Rate limit or quota exceeded",
        retry_after: int | None = None,
        error_code: str = "RATE_LIMIT_EXCEEDED",
    ):
        super().__init__(
            message=message,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code=error_code,
            details={"retry_after_seconds": retry_after} if retry_after else None,
        )
        self.retry_after = retry_after


class AuthenticationException(AppException):
    """Exception raised for authentication / API Key failures."""

    def __init__(
        self,
        message: str = "Invalid or missing authentication credentials",
        error_code: str = "UNAUTHORIZED",
    ):
        super().__init__(
            message=message,
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=error_code,
        )


class EntityNotFoundException(AppException):
    """Exception raised when a requested resource is not found."""

    def __init__(
        self,
        message: str = "Resource not found",
        error_code: str = "NOT_FOUND",
    ):
        super().__init__(
            message=message,
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=error_code,
        )


def format_error_response(
    request: Request,
    status_code: int,
    error_code: str,
    message: str,
    details: Any = None,
) -> JSONResponse:
    """Format standardized RFC 7807-inspired JSON response."""
    request_id = getattr(request.state, "request_id", None) or request.headers.get(
        "X-Request-ID"
    )
    content = {
        "status_code": status_code,
        "error_code": error_code,
        "message": message,
        "path": request.url.path,
        "request_id": request_id,
    }
    if details is not None:
        content["details"] = details
    return JSONResponse(status_code=status_code, content=content)
