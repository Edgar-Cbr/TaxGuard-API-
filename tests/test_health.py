from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.config import settings


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    """Test root endpoint returns application metadata."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == settings.PROJECT_NAME
    assert data["version"] == settings.VERSION
    assert "health" in data
    assert "documentation" in data


@pytest.mark.asyncio
async def test_health_endpoint_structure(async_client: AsyncClient):
    """Test health check returns expected status keys and services dictionary."""
    response = await async_client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert "version" in data
    assert "services" in data
    assert "database" in data["services"]
    assert "redis" in data["services"]


@pytest.mark.asyncio
async def test_health_endpoint_when_all_healthy(async_client: AsyncClient):
    """Test health check returns 200 and healthy when DB and Redis are available."""
    mock_db = {"status": "healthy", "available": True, "latency_ms": 1.2}
    mock_redis = {"status": "healthy", "available": True, "latency_ms": 0.5}

    with (
        patch("app.main.check_db_health", new_callable=AsyncMock, return_value=mock_db),
        patch(
            "app.main.check_redis_health",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ),
    ):
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["services"]["database"]["available"] is True
        assert data["services"]["redis"]["available"] is True


@pytest.mark.asyncio
async def test_health_endpoint_when_service_unhealthy(async_client: AsyncClient):
    """Test health check returns 503 when a backend dependency is unavailable."""
    mock_db = {"status": "unhealthy", "available": False, "error": "Connection refused"}
    mock_redis = {"status": "healthy", "available": True, "latency_ms": 0.5}

    with (
        patch("app.main.check_db_health", new_callable=AsyncMock, return_value=mock_db),
        patch(
            "app.main.check_redis_health",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ),
    ):
        response = await async_client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["services"]["database"]["available"] is False


@pytest.mark.asyncio
async def test_request_id_middleware(async_client: AsyncClient):
    """Test that X-Request-ID header is propagated or automatically generated."""
    # Test auto-generation
    res1 = await async_client.get("/")
    assert "x-request-id" in res1.headers
    assert len(res1.headers["x-request-id"]) > 0

    # Test propagation of client-provided request ID
    custom_id = "test-req-12345"
    res2 = await async_client.get("/", headers={"X-Request-ID": custom_id})
    assert res2.headers.get("x-request-id") == custom_id
