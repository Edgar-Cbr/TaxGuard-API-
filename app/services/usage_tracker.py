"""Asynchronous audit and consumption tracking service for background tasks."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update

from app.db.session import async_session_maker
from app.models.api_key import ApiKey
from app.models.usage_log import UsageLog

logger = logging.getLogger("taxguard.usage_tracker")

session_factory_override: Any = None


async def record_usage_log(
    api_key_id: uuid.UUID,
    endpoint: str,
    status_code: int,
    latency_ms: float,
    session_factory: Any = None,
) -> None:
    """Insert a usage log record and update ApiKey.last_used_at in the background.

    This function opens its own isolated AsyncSession so it can safely execute
    inside FastAPI BackgroundTasks after the HTTP response has already closed.
    """
    now = datetime.now(UTC)
    factory = session_factory or session_factory_override or async_session_maker
    async with factory() as session:
        try:
            # 1. Insert UsageLog record
            log_entry = UsageLog(
                api_key_id=api_key_id,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=round(latency_ms, 2),
                created_at=now,
            )
            session.add(log_entry)

            # 2. Update ApiKey.last_used_at timestamp
            stmt = (
                update(ApiKey).where(ApiKey.id == api_key_id).values(last_used_at=now)
            )
            await session.execute(stmt)

            await session.commit()
            logger.debug(
                f"Recorded usage log for key={api_key_id} endpoint={endpoint} latency={latency_ms:.2f}ms"
            )
        except Exception as exc:
            await session.rollback()
            logger.error(
                f"Failed to record usage log for key={api_key_id}: {exc}",
                exc_info=True,
            )
