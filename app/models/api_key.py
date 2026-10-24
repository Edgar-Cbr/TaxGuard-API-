"""ApiKey SQLAlchemy 2.0 ORM model."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CHAR, Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.usage_log import UsageLog
    from app.models.user import User


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """API Key credentials model storing secure SHA-256 hashes and metadata."""

    __tablename__ = "api_keys"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="Default Key",
    )
    key_prefix: Mapped[str] = mapped_column(
        String(14),
        nullable=False,
    )
    key_hash: Mapped[str] = mapped_column(
        CHAR(64),
        unique=True,
        index=True,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="api_keys",
    )
    usage_logs: Mapped[list["UsageLog"]] = relationship(
        "UsageLog",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("name", "Default Key")
        kwargs.setdefault("is_active", True)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<ApiKey id={self.id} prefix={self.key_prefix} active={self.is_active}>"
