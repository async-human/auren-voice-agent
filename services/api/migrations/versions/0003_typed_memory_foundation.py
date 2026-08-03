"""Typed memory lifecycle, evidence, and audit foundation

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Local databases may have been bootstrapped by metadata.create_all()
    # before Alembic was introduced. Add only what is absent so the migration
    # can safely adopt both legacy and newly-created development schemas.
    session_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("conversation_sessions")
    }
    with op.batch_alter_table("conversation_sessions") as batch:
        if "topics" not in session_columns:
            batch.add_column(sa.Column("topics", sa.JSON(), nullable=True))
        if "outcomes" not in session_columns:
            batch.add_column(sa.Column("outcomes", sa.JSON(), nullable=True))
        if "open_threads" not in session_columns:
            batch.add_column(sa.Column("open_threads", sa.JSON(), nullable=True))
        if "importance" not in session_columns:
            batch.add_column(
                sa.Column(
                    "importance",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("0.5"),
                )
            )

    memory_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("memories")
    }
    memory_indexes = {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes("memories")
    }
    with op.batch_alter_table("memories") as batch:
        if "memory_type" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "memory_type",
                    sa.String(length=16),
                    nullable=False,
                    server_default="semantic",
                )
            )
        if "status" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "status",
                    sa.String(length=16),
                    nullable=False,
                    server_default="active",
                )
            )
        if "structured_value" not in memory_columns:
            batch.add_column(sa.Column("structured_value", sa.JSON(), nullable=True))
        if "confidence" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "confidence",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("1.0"),
                )
            )
        if "importance" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "importance",
                    sa.Float(),
                    nullable=False,
                    server_default=sa.text("0.5"),
                )
            )
        if "sensitivity" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "sensitivity",
                    sa.String(length=16),
                    nullable=False,
                    server_default="normal",
                )
            )
        if "source" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "source",
                    sa.String(length=16),
                    nullable=False,
                    server_default="explicit",
                )
            )
        if "valid_from" not in memory_columns:
            batch.add_column(
                sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
            )
        if "valid_until" not in memory_columns:
            batch.add_column(
                sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True)
            )
        if "last_confirmed_at" not in memory_columns:
            batch.add_column(
                sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "superseded_by_id" not in memory_columns:
            batch.add_column(
                sa.Column("superseded_by_id", sa.String(length=32), nullable=True)
            )
        if "updated_at" not in memory_columns:
            batch.add_column(
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    nullable=False,
                    server_default=sa.func.now(),
                )
            )
        if "ix_memories_user_type_status" not in memory_indexes:
            batch.create_index(
                "ix_memories_user_type_status",
                ["user_id", "memory_type", "status"],
                unique=False,
            )

    # Phase 1 session-distilled memories carry source_session_id. Preserve that
    # provenance while treating direct remember-tool writes as explicit.
    op.execute(
        sa.text(
            "UPDATE memories SET source = 'autonomous' "
            "WHERE source_session_id IS NOT NULL"
        )
    )

    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "memory_evidence" not in existing:
        op.create_table(
            "memory_evidence",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("memory_id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("session_id", sa.String(length=32), nullable=True),
            sa.Column("turn_id", sa.String(length=32), nullable=True),
            sa.Column(
                "relation",
                sa.String(length=16),
                nullable=False,
                server_default="supports",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_memory_evidence_memory_id", "memory_evidence", ["memory_id"])
        op.create_index("ix_memory_evidence_user_id", "memory_evidence", ["user_id"])
        op.create_index(
            "ix_memory_evidence_memory_created",
            "memory_evidence",
            ["memory_id", "created_at"],
        )
        op.create_index(
            "ix_memory_evidence_user_session",
            "memory_evidence",
            ["user_id", "session_id"],
        )

    if "memory_events" not in existing:
        op.create_table(
            "memory_events",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("memory_id", sa.String(length=32), nullable=False),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("event_type", sa.String(length=24), nullable=False),
            sa.Column(
                "actor",
                sa.String(length=16),
                nullable=False,
                server_default="system",
            ),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_memory_events_memory_id", "memory_events", ["memory_id"])
        op.create_index("ix_memory_events_user_id", "memory_events", ["user_id"])
        op.create_index(
            "ix_memory_events_memory_created",
            "memory_events",
            ["memory_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_memory_events_memory_created", table_name="memory_events")
    op.drop_index("ix_memory_events_user_id", table_name="memory_events")
    op.drop_index("ix_memory_events_memory_id", table_name="memory_events")
    op.drop_table("memory_events")

    op.drop_index("ix_memory_evidence_user_session", table_name="memory_evidence")
    op.drop_index("ix_memory_evidence_memory_created", table_name="memory_evidence")
    op.drop_index("ix_memory_evidence_user_id", table_name="memory_evidence")
    op.drop_index("ix_memory_evidence_memory_id", table_name="memory_evidence")
    op.drop_table("memory_evidence")

    with op.batch_alter_table("memories") as batch:
        batch.drop_index("ix_memories_user_type_status")
        batch.drop_column("updated_at")
        batch.drop_column("superseded_by_id")
        batch.drop_column("last_confirmed_at")
        batch.drop_column("valid_until")
        batch.drop_column("valid_from")
        batch.drop_column("source")
        batch.drop_column("sensitivity")
        batch.drop_column("importance")
        batch.drop_column("confidence")
        batch.drop_column("structured_value")
        batch.drop_column("status")
        batch.drop_column("memory_type")

    with op.batch_alter_table("conversation_sessions") as batch:
        batch.drop_column("importance")
        batch.drop_column("open_threads")
        batch.drop_column("outcomes")
        batch.drop_column("topics")
