"""User-owned generated artifacts

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "artifacts" in tables:
        return
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("filename", sa.String(length=360), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_user_id", "artifacts", ["user_id"])
    op.create_index(
        "ix_artifacts_user_created", "artifacts", ["user_id", "created_at"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "artifacts" in set(sa.inspect(bind).get_table_names()):
        op.drop_table("artifacts")
