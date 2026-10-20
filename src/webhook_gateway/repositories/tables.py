# migrations are hand-written, test_migrations checks they match this file

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING)

TS = DateTime(timezone=True)

endpoints = Table(
    "endpoints",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("url", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),
    Column("event_types", ARRAY(Text), nullable=False),
    Column("secret", Text, nullable=False),
    Column("status", Text, nullable=False, server_default="active"),
    Column("paused_until", TS),
    Column("consecutive_failures", Integer, nullable=False, server_default="0"),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
    CheckConstraint("char_length(url) <= 2048", name="url_length"),
    CheckConstraint("cardinality(event_types) > 0", name="event_types_not_empty"),
    CheckConstraint("status IN ('active', 'paused')", name="status"),
    CheckConstraint("status = 'paused' OR paused_until IS NULL", name="paused_until_only_paused"),
    CheckConstraint("consecutive_failures >= 0", name="consecutive_failures_non_negative"),
    Index("ix_endpoints_event_types", "event_types", postgresql_using="gin"),
)

events = Table(
    "events",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("type", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", TS, nullable=False),
    CheckConstraint("char_length(type) BETWEEN 1 AND 200", name="type_length"),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column("key", Text, primary_key=True),
    Column("request_hash", LargeBinary, nullable=False),
    Column(
        "event_id",
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("response_status", SmallInteger, nullable=False),
    Column("response_body", JSONB, nullable=False),
    Column("created_at", TS, nullable=False),
    CheckConstraint("char_length(key) BETWEEN 1 AND 255", name="key_length"),
)

deliveries = Table(
    "deliveries",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "event_id",
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "endpoint_id",
        UUID(as_uuid=True),
        ForeignKey("endpoints.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("status", Text, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    # attempt_count at the last replay
    Column("attempt_window_start", Integer, nullable=False, server_default="0"),
    Column("next_attempt_at", TS, nullable=False),
    Column("locked_until", TS),
    Column("lease_token", UUID(as_uuid=True)),
    Column("last_status_code", SmallInteger),
    Column("last_error", Text),
    Column("created_at", TS, nullable=False),
    Column("updated_at", TS, nullable=False),
    UniqueConstraint("event_id", "endpoint_id"),
    CheckConstraint(
        "status IN ('pending', 'in_flight', 'succeeded', 'failed', 'dead')", name="status"
    ),
    CheckConstraint("attempt_count >= 0", name="attempt_count_non_negative"),
    CheckConstraint(
        "attempt_window_start >= 0 AND attempt_window_start <= attempt_count",
        name="attempt_window_valid",
    ),
    CheckConstraint(
        "(status = 'in_flight') = (locked_until IS NOT NULL AND lease_token IS NOT NULL)",
        name="lease_only_in_flight",
    ),
    # worker poll
    Index(
        "ix_deliveries_pending_due",
        "status",
        "next_attempt_at",
        postgresql_where=text("status = 'pending'"),
    ),
    # expired leases
    Index(
        "ix_deliveries_lease_expiry",
        "locked_until",
        postgresql_where=text("status = 'in_flight'"),
    ),
    # pagination
    Index(
        "ix_deliveries_endpoint_page",
        "endpoint_id",
        text("created_at DESC"),
        text("id DESC"),
    ),
    Index("ix_deliveries_status_page", "status", text("created_at DESC"), text("id DESC")),
    Index("ix_deliveries_page", text("created_at DESC"), text("id DESC")),
)

attempts = Table(
    "attempts",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column(
        "delivery_id",
        UUID(as_uuid=True),
        ForeignKey("deliveries.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("attempt_number", Integer, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("status_code", SmallInteger),
    Column("response_body", Text),
    Column("error", Text),
    Column("duration_ms", Integer, nullable=False),
    Column("started_at", TS, nullable=False),
    UniqueConstraint("delivery_id", "attempt_number"),
    CheckConstraint("attempt_number >= 1", name="attempt_number_positive"),
    CheckConstraint(
        "outcome IN ('success', 'retryable', 'permanent', 'timeout', 'network_error', "
        "'blocked', 'lease_expired')",
        name="outcome",
    ),
    CheckConstraint("duration_ms >= 0", name="duration_non_negative"),
)
