"""Durable one-time OAuth state

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "oauth_connections" in tables:
        columns = {
            column["name"] for column in sa.inspect(bind).get_columns("oauth_connections")
        }
        if "timezone_name" not in columns:
            with op.batch_alter_table("oauth_connections") as batch:
                batch.add_column(sa.Column("timezone_name", sa.String(length=64), nullable=True))
    if "oauth_states" not in tables:
        op.create_table(
            "oauth_states",
            sa.Column("state_hash", sa.String(length=64), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_oauth_states_user_id", "oauth_states", ["user_id"])
        op.create_index("ix_oauth_states_expires", "oauth_states", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "oauth_states" in tables:
        op.drop_table("oauth_states")
    if "oauth_connections" in tables:
        columns = {
            column["name"] for column in sa.inspect(bind).get_columns("oauth_connections")
        }
        if "timezone_name" in columns:
            with op.batch_alter_table("oauth_connections") as batch:
                batch.drop_column("timezone_name")
