"""User and Subscription SQLAlchemy 2.0 ORM models."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey


class UserTier(StrEnum):
    """Subscription tiers available for users."""

    FREE = "FREE"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User account entity."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    tier: Mapped[UserTier] = mapped_column(
        SQLEnum(UserTier, name="user_tier_enum", native_enum=True),
        nullable=False,
        default=UserTier.FREE,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    # Relationships
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        "ApiKey",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", uuid.uuid4())
        kwargs.setdefault("tier", UserTier.FREE)
        kwargs.setdefault("is_active", True)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} tier={self.tier}>"


class Subscription(UUIDPrimaryKeyMixin, Base):
    """User subscription and API consumption plan."""

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    monthly_quota: Mapped[int] = mapped_column(
        Integer,
        default=1000,
        nullable=False,
    )
    rate_limit_per_sec: Mapped[int] = mapped_column(
        Integer,
        default=5,
        nullable=False,
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    user: Mapped["User"] = relationship(
        "User",
        back_populates="subscription",
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("monthly_quota", 1000)
        kwargs.setdefault("rate_limit_per_sec", 5)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return f"<Subscription id={self.id} user_id={self.user_id} quota={self.monthly_quota}>"

