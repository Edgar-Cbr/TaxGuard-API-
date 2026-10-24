"""SQLAlchemy 2.0 DeclarativeBase and metadata registry."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy 2.0 ORM models."""

    pass


class TimestampMixin:
    """Reusable mixin for model creation timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("created_at", datetime.now(UTC))
        super().__init__(**kwargs)


class UUIDPrimaryKeyMixin:
    """Reusable mixin for UUID v4 primary keys."""

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", uuid.uuid4())
        super().__init__(**kwargs)

