"""Pytest fixtures for unit and integration testing."""

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Force testing environment
os.environ["ENVIRONMENT"] = "testing"

import app.models  # noqa: F401
from app.api.deps import get_db, get_redis
from app.db.base import Base
from app.main import app

# In-memory SQLite async engine with StaticPool
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


class MockRedis:
    """In-memory Redis mock for test isolation."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, *args, **kwargs) -> bool:
        self._store[key] = str(value)
        return True

    async def setex(self, key: str, time: int, value: str) -> bool:
        self._store[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        count = 0
        for k in keys:
            if k in self._store:
                del self._store[k]
                count += 1
        return count

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        pass


@pytest.fixture(scope="session")
def mock_redis() -> MockRedis:
    """Session-scoped mock Redis client."""
    return MockRedis()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a clean isolated transactional database session for each test."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def async_client(
    db_session: AsyncSession,
    mock_redis: MockRedis,
) -> AsyncGenerator[AsyncClient, None]:
    """AsyncClient bound to FastAPI application with DB and Redis overrides."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    async def override_get_redis() -> MockRedis:
        return mock_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()
