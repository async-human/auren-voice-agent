"""Ephemeral active-tab page context for voice explanation

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "page_contexts" in tables:
        return

    op.create_table(
        "page_contexts",
        sa.Column("user_id", sa.String(length=128), primary_key=True),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="extension"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_page_contexts_expires", "page_contexts", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "page_contexts" not in tables:
        return
    op.drop_index("ix_page_contexts_expires", table_name="page_contexts")
    op.drop_table("page_contexts")
