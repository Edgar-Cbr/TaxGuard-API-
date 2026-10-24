import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import (
    AppException,
    RateLimitExceededException,
    format_error_response,
)
from app.core.redis import check_redis_health, close_redis
from app.db.session import check_db_health, engine

logging.basicConfig(
    level=logging.INFO if not settings.DEBUG else logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("taxguard.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager to handle startup and shutdown events."""
    logger.info(
        f"Starting {settings.PROJECT_NAME} v{settings.VERSION} [{settings.ENVIRONMENT}]"
    )

    # Verify Database Connection
    db_health = await check_db_health()
    if db_health.get("available"):
        logger.info(
            f"Database connected successfully (latency: {db_health.get('latency_ms')} ms)"
        )
    else:
        logger.warning(
            f"Database connection could not be established on startup: {db_health.get('error')}"
        )

    # Verify Redis Connection
    redis_health = await check_redis_health()
    if redis_health.get("available"):
        logger.info(
            f"Redis connected successfully (latency: {redis_health.get('latency_ms')} ms)"
        )
    else:
        logger.warning(
            f"Redis connection could not be established on startup: {redis_health.get('error')}"
        )

    yield

    logger.info(f"Shutting down {settings.PROJECT_NAME}...")
    await close_redis()
    await engine.dispose()
    logger.info("Database and Redis connections safely closed.")


def create_application() -> FastAPI:
    """FastAPI Application Factory."""
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="High-performance B2B Micro-SaaS API for fiscal ID, postal address & corporate email validation.",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        lifespan=lifespan,
    )

    # CORS Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request ID and Request Logging Middleware
    @application.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Global Exception Handlers (RFC 7807 compliant)
    @application.exception_handler(AppException)
    async def app_exception_handler(
        request: Request, exc: AppException
    ) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitExceededException) and exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)

        return format_error_response(
            request=request,
            status_code=exc.status_code,
            error_code=exc.error_code,
            message=exc.message,
            details=exc.details,
            headers=headers or None,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return format_error_response(
            request=request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="VALIDATION_ERROR",
            message="Input validation failed",
            details=exc.errors(),
        )

    @application.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return format_error_response(
            request=request,
            status_code=exc.status_code,
            error_code=f"HTTP_{exc.status_code}",
            message=str(exc.detail),
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            f"Unhandled error processing request {request.url.path}: {exc}"
        )
        return format_error_response(
            request=request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal error occurred. Please contact support.",
        )

    # Register API v1 routes
    application.include_router(api_router, prefix=settings.API_V1_STR)

    # Root / Info Endpoint
    @application.get("/", tags=["system"])
    async def root():
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
            "documentation": "/docs",
            "health": "/health",
        }

    # Healthcheck Endpoint
    @application.get("/health", tags=["system"])
    @application.get(f"{settings.API_V1_STR}/health", tags=["system"])
    async def health_check():
        db_status = await check_db_health()
        redis_status = await check_redis_health()

        is_healthy = bool(db_status.get("available") and redis_status.get("available"))
        overall_status = "healthy" if is_healthy else "degraded"

        payload = {
            "status": overall_status,
            "version": settings.VERSION,
            "environment": settings.ENVIRONMENT,
            "services": {
                "database": db_status,
                "redis": redis_status,
            },
        }

        # Return 200 in development/testing or degraded, but indicate accurate service status
        return JSONResponse(
            status_code=status.HTTP_200_OK
            if is_healthy
            else status.HTTP_503_SERVICE_UNAVAILABLE,
            content=payload,
        )

    return application


app = create_application()
