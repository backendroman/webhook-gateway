"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TS = sa.DateTime(timezone=True)
UUID = pg.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "endpoints",
        sa.Column("id", UUID, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("event_types", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default="active", nullable=False),
        sa.Column("paused_until", TS, nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("char_length(url) <= 2048", name="ck_endpoints_url_length"),
        sa.CheckConstraint(
            "cardinality(event_types) > 0", name="ck_endpoints_event_types_not_empty"
        ),
        sa.CheckConstraint("status IN ('active', 'paused')", name="ck_endpoints_status"),
        sa.CheckConstraint(
            "status = 'paused' OR paused_until IS NULL",
            name="ck_endpoints_paused_until_only_paused",
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0", name="ck_endpoints_consecutive_failures_non_negative"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_endpoints"),
    )
    op.create_index(
        "ix_endpoints_event_types", "endpoints", ["event_types"], postgresql_using="gin"
    )

    op.create_table(
        "events",
        sa.Column("id", UUID, nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint("char_length(type) BETWEEN 1 AND 200", name="ck_events_type_length"),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.LargeBinary(), nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("response_body", pg.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.CheckConstraint(
            "char_length(key) BETWEEN 1 AND 255", name="ck_idempotency_keys_key_length"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_idempotency_keys_event_id_events",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("key", name="pk_idempotency_keys"),
    )

    _create_deliveries()
    _create_attempts()


def _create_deliveries() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("event_id", UUID, nullable=False),
        sa.Column("endpoint_id", UUID, nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", TS, nullable=False),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("lease_token", UUID, nullable=True),
        sa.Column("last_status_code", sa.SmallInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'in_flight', 'succeeded', 'failed', 'dead')",
            name="ck_deliveries_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_deliveries_attempt_count_non_negative"),
        sa.CheckConstraint(
            "(status = 'in_flight') = (locked_until IS NOT NULL AND lease_token IS NOT NULL)",
            name="ck_deliveries_lease_only_in_flight",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["endpoints.id"],
            name="fk_deliveries_endpoint_id_endpoints",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_deliveries_event_id_events", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deliveries"),
        sa.UniqueConstraint("event_id", "endpoint_id", name="uq_deliveries_event_id_endpoint_id"),
    )
    # worker poll
    op.create_index(
        "ix_deliveries_pending_due",
        "deliveries",
        ["status", "next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # expired leases
    op.create_index(
        "ix_deliveries_lease_expiry",
        "deliveries",
        ["locked_until"],
        postgresql_where=sa.text("status = 'in_flight'"),
    )
    # pagination
    op.create_index(
        "ix_deliveries_endpoint_page",
        "deliveries",
        ["endpoint_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_deliveries_status_page",
        "deliveries",
        ["status", sa.text("created_at DESC"), sa.text("id DESC")],
    )
    op.create_index(
        "ix_deliveries_page", "deliveries", [sa.text("created_at DESC"), sa.text("id DESC")]
    )


def _create_attempts() -> None:
    op.create_table(
        "attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("delivery_id", UUID, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", TS, nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_attempts_attempt_number_positive"),
        sa.CheckConstraint(
            "outcome IN ('success', 'retryable', 'permanent', 'timeout', 'network_error', 'blocked')",
            name="ck_attempts_outcome",
        ),
        sa.CheckConstraint("duration_ms >= 0", name="ck_attempts_duration_non_negative"),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["deliveries.id"],
            name="fk_attempts_delivery_id_deliveries",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_attempts"),
        sa.UniqueConstraint(
            "delivery_id", "attempt_number", name="uq_attempts_delivery_id_attempt_number"
        ),
    )


def downgrade() -> None:
    op.drop_table("attempts")
    op.drop_table("deliveries")
    op.drop_table("idempotency_keys")
    op.drop_table("events")
    op.drop_table("endpoints")
