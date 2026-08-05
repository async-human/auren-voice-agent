"""Workflow execution: OAuth, approvals, audit, jobs

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "oauth_connections" not in tables:
        op.create_table(
            "oauth_connections",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("account_email", sa.String(length=320), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=False),
            sa.Column("access_token_encrypted", sa.Text(), nullable=False),
            sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_oauth_connections_user_provider",
            "oauth_connections",
            ["user_id", "provider"],
            unique=True,
        )

    if "pending_actions" not in tables:
        op.create_table(
            "pending_actions",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("tool", sa.String(length=64), nullable=False),
            sa.Column("arguments", sa.JSON(), nullable=False),
            sa.Column("preview", sa.Text(), nullable=False),
            sa.Column("confirm_token", sa.String(length=64), nullable=False),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("workflow_run_id", sa.String(length=32), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_pending_actions_user_status", "pending_actions", ["user_id", "status"]
        )
        op.create_index(
            "ix_pending_actions_token", "pending_actions", ["confirm_token"], unique=True
        )

    if "action_audits" not in tables:
        op.create_table(
            "action_audits",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("tool", sa.String(length=64), nullable=False),
            sa.Column("arguments", sa.JSON(), nullable=True),
            sa.Column("event_type", sa.String(length=32), nullable=False),
            sa.Column("actor", sa.String(length=16), nullable=False),
            sa.Column("pending_action_id", sa.String(length=32), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_action_audits_user_created", "action_audits", ["user_id", "created_at"]
        )

    if "workflow_runs" not in tables:
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("goal", sa.Text(), nullable=False),
            sa.Column("plan", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("context", sa.JSON(), nullable=True),
            sa.Column("result_summary", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_workflow_runs_user_status", "workflow_runs", ["user_id", "status"]
        )

    if "scheduled_jobs" not in tables:
        op.create_table(
            "scheduled_jobs",
            sa.Column("id", sa.String(length=32), primary_key=True),
            sa.Column("user_id", sa.String(length=128), nullable=False),
            sa.Column("job_type", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("workflow_run_id", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_scheduled_jobs_due", "scheduled_jobs", ["status", "run_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for name in (
        "scheduled_jobs",
        "workflow_runs",
        "action_audits",
        "pending_actions",
        "oauth_connections",
    ):
        if name in tables:
            op.drop_table(name)
