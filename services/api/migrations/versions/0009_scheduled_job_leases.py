"""Lease scheduled jobs so multiple API replicas cannot execute them twice.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheduled_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scheduled_jobs")}
    if "locked_at" not in columns:
        op.add_column(
            "scheduled_jobs",
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheduled_jobs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("scheduled_jobs")}
    if "locked_at" in columns:
        op.drop_column("scheduled_jobs", "locked_at")
