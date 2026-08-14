"""Proactive delivery: inbox, quiet hours, and background jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "user_settings" not in tables:
        op.create_table(
            "user_settings",
            sa.Column("user_id", sa.String(length=128), primary_key=True),
            sa.Column("timezone_name", sa.String(length=64), nullable=False),
            sa.Column("quiet_hours_start", sa.String(length=5), nullable=True),
            sa.Column("quiet_hours_end", sa.String(length=5), nullable=True),
            sa.Column("max_interruptions_per_day", sa.Integer(), nullable=False),
            sa.Column("spoken_delivery_enabled", sa.Boolean(), nullable=False),
            sa.Column("pod_wake_enabled", sa.Boolean(), nullable=False),
            sa.Column("autonomous_memory_enabled", sa.Boolean(), nullable=False),
            sa.Column("semantic_memory_enabled", sa.Boolean(), nullable=False),
            sa.Column("procedural_memory_enabled", sa.Boolean(), nullable=False),
            sa.Column("prospective_memory_enabled", sa.Boolean(), nullable=False),
            sa.Column("morning_brief_enabled", sa.Boolean(), nullable=False),
            sa.Column("morning_brief_hour", sa.Integer(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "notifications" not in tables:
        op.create_table(
            "notifications",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("speak_text", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("source_type", sa.String(length=32), nullable=True),
            sa.Column("source_id", sa.String(length=64), nullable=True),
            sa.Column("speak_queued", sa.Boolean(), nullable=False),
            sa.Column("interruption", sa.Boolean(), nullable=False),
            sa.Column("pod_wake_attempted", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("spoken_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
        op.create_index("ix_notifications_status", "notifications", ["status"])
        op.create_index(
            "ix_notifications_user_status_created",
            "notifications",
            ["user_id", "status", "created_at"],
        )
        op.create_index(
            "ix_notifications_user_source",
            "notifications",
            ["user_id", "source_type", "source_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "notifications" in tables:
        op.drop_table("notifications")
    if "user_settings" in tables:
        op.drop_table("user_settings")
