import uuid
from datetime import UTC, datetime

from app.db.base import Base
from app.models import ApiKey, Subscription, UsageLog, User, UserTier


def test_base_metadata_contains_all_tables():
    """Verify that all four required tables are registered in Base.metadata."""
    table_names = set(Base.metadata.tables.keys())
    expected_tables = {"users", "subscriptions", "api_keys", "usage_logs"}
    assert expected_tables.issubset(table_names), (
        f"Missing tables: {expected_tables - table_names}"
    )


def test_user_model_initialization():
    """Verify User model attributes and defaults."""
    user = User(
        email="company@taxguard.io",
        hashed_password="secure_hashed_password_string",
    )
    assert user.email == "company@taxguard.io"
    assert user.tier == UserTier.FREE
    assert user.is_active is True
    assert isinstance(user.id, uuid.UUID)
    assert "company@taxguard.io" in repr(user)


def test_subscription_model_initialization():
    """Verify Subscription model defaults and foreign key representation."""
    user_id = uuid.uuid4()
    subscription = Subscription(
        user_id=user_id,
        stripe_customer_id="cus_test12345",
    )
    assert subscription.user_id == user_id
    assert subscription.monthly_quota == 1000
    assert subscription.rate_limit_per_sec == 5
    assert subscription.stripe_customer_id == "cus_test12345"
    assert "quota=1000" in repr(subscription)


def test_api_key_model_initialization():
    """Verify ApiKey model defaults, prefix, and hash length."""
    user_id = uuid.uuid4()
    mock_hash = "a" * 64
    api_key = ApiKey(
        user_id=user_id,
        name="Production ERP Key",
        key_prefix="tg_live_1234",
        key_hash=mock_hash,
    )
    assert api_key.name == "Production ERP Key"
    assert api_key.key_prefix == "tg_live_1234"
    assert api_key.key_hash == mock_hash
    assert api_key.is_active is True
    assert api_key.revoked_at is None
    assert api_key.last_used_at is None
    assert "tg_live_1234" in repr(api_key)


def test_usage_log_model_initialization():
    """Verify UsageLog model fields and latency tracking."""
    key_id = uuid.uuid4()
    now = datetime.now(UTC)
    log = UsageLog(
        id=1,
        api_key_id=key_id,
        endpoint="/api/v1/validate/tax-id",
        status_code=200,
        latency_ms=12.4,
        created_at=now,
    )
    assert log.id == 1
    assert log.api_key_id == key_id
    assert log.endpoint == "/api/v1/validate/tax-id"
    assert log.status_code == 200
    assert log.latency_ms == 12.4
    assert log.created_at == now
    assert "12.4ms" in repr(log)


def test_table_foreign_keys_and_indices():
    """Verify foreign key relations and critical indices exist in metadata."""
    # Subscriptions foreign key to users
    sub_table = Base.metadata.tables["subscriptions"]
    user_fk = list(sub_table.foreign_keys)[0]
    assert user_fk.target_fullname == "users.id"

    # ApiKeys foreign key to users and unique index on key_hash
    keys_table = Base.metadata.tables["api_keys"]
    key_fk = list(keys_table.foreign_keys)[0]
    assert key_fk.target_fullname == "users.id"
    key_hash_indexes = [idx for idx in keys_table.indexes if "key_hash" in idx.columns]
    assert any(idx.unique for idx in key_hash_indexes)

    # UsageLogs foreign key to api_keys
    logs_table = Base.metadata.tables["usage_logs"]
    log_fk = list(logs_table.foreign_keys)[0]
    assert log_fk.target_fullname == "api_keys.id"
