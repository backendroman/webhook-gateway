"""lease_expired outcome, attempt_window_start

budget = attempt_count - attempt_window_start, replay moves the window

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "outcome IN ('success', 'retryable', 'permanent', 'timeout', 'network_error', 'blocked')"
_NEW = (
    "outcome IN ('success', 'retryable', 'permanent', 'timeout', 'network_error', "
    "'blocked', 'lease_expired')"
)


def upgrade() -> None:
    op.drop_constraint("ck_attempts_outcome", "attempts", type_="check")
    op.create_check_constraint("ck_attempts_outcome", "attempts", _NEW)

    op.add_column(
        "deliveries",
        sa.Column("attempt_window_start", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_deliveries_attempt_window_valid",
        "deliveries",
        "attempt_window_start >= 0 AND attempt_window_start <= attempt_count",
    )


def downgrade() -> None:
    op.drop_constraint("ck_deliveries_attempt_window_valid", "deliveries", type_="check")
    op.drop_column("deliveries", "attempt_window_start")
    op.execute("DELETE FROM attempts WHERE outcome = 'lease_expired'")
    op.drop_constraint("ck_attempts_outcome", "attempts", type_="check")
    op.create_check_constraint("ck_attempts_outcome", "attempts", _OLD)
