"""Baseline schema with verified users

Reminders and notes created before authentication existed are keyed to
throwaway `anonymous-<nonce>` ids that can never be attributed to anyone, so
this migration discards them rather than leaving unreachable rows behind.

Revision ID: 0001
Revises:
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("external_id", sa.String(length=255), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("display_name", sa.String(length=200), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_users_external_id", "users", ["external_id"], unique=True)

    if "reminders" not in existing:
        op.create_table(
            "reminders",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=500), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("timezone_name", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
        op.create_index("ix_reminders_status", "reminders", ["status"])
        op.create_index("ix_reminders_user_due", "reminders", ["user_id", "due_at"])

    if "notes" not in existing:
        op.create_table(
            "notes",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_notes_user_id", "notes", ["user_id"])
        op.create_index("ix_notes_user_created", "notes", ["user_id", "created_at"])

    op.execute(sa.text("DELETE FROM reminders WHERE user_id LIKE 'anonymous-%'"))
    op.execute(sa.text("DELETE FROM notes WHERE user_id LIKE 'anonymous-%'"))


def downgrade() -> None:
    op.drop_index("ix_users_external_id", table_name="users")
    op.drop_table("users")
