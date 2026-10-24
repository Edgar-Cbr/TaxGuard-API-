"""Pytest fixtures for unit and integration testing."""

import os
from collections.abc import AsyncGenerator
from typing import Any

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


class MockPipeline:
    """Mock Redis Pipeline for atomic batch operations."""

    def __init__(self, store: dict[str, str]):
        self._store = store
        self._commands: list[tuple[str, Any]] = []

    def incr(self, key: str, amount: int = 1):
        self._commands.append(("incr", key, amount))
        return self

    def expire(self, key: str, seconds: int):
        self._commands.append(("expire", key, seconds))
        return self

    async def execute(self) -> list[Any]:
        results = []
        for cmd in self._commands:
            action = cmd[0]
            if action == "incr":
                k, amt = cmd[1], cmd[2]
                current = int(self._store.get(k, 0)) + amt
                self._store[k] = str(current)
                results.append(current)
            elif action == "expire":
                results.append(True)
        self._commands.clear()
        return results


class MockRedis:
    """In-memory Redis mock for test isolation."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def pipeline(self, transaction: bool = True) -> MockPipeline:
        return MockPipeline(self._store)

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def set(self, key: str, value: str, *args, **kwargs) -> bool:
        self._store[key] = str(value)
        return True

    async def setex(self, key: str, time: int, value: str) -> bool:
        self._store[key] = str(value)
        return True

    async def incr(self, key: str, amount: int = 1) -> int:
        current = int(self._store.get(key, 0)) + amount
        self._store[key] = str(current)
        return current

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

    def clear(self) -> None:
        self._store.clear()


@pytest.fixture
def mock_redis() -> MockRedis:
    """Per-test mock Redis client."""
    client = MockRedis()
    client.clear()
    return client


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
