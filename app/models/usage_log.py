"""UsageLog SQLAlchemy 2.0 ORM model for asynchronous audit and analytics."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class UsageLog(TimestampMixin, Base):
    """Audit log tracking API key consumption, endpoints, status codes, and latency."""

    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    api_key_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    status_code: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    latency_ms: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )

    # Relationships
    api_key: Mapped["ApiKey"] = relationship(
        "ApiKey",
        back_populates="usage_logs",
    )

    def __repr__(self) -> str:
        return f"<UsageLog id={self.id} endpoint={self.endpoint} status={self.status_code} latency={self.latency_ms}ms>"
