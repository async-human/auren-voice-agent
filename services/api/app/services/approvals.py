"""Two-phase confirmation for consequential tools."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import ActionAudit, PendingAction, utcnow

PENDING_TTL = timedelta(minutes=15)


async def propose_action(
    session: AsyncSession,
    *,
    user_id: str,
    tool: str,
    arguments: dict[str, Any],
    preview: str,
    idempotency_key: str | None = None,
    workflow_run_id: str | None = None,
) -> PendingAction:
    if idempotency_key:
        existing = await session.scalar(
            select(PendingAction).where(
                PendingAction.user_id == user_id,
                PendingAction.idempotency_key == idempotency_key,
                PendingAction.status.in_(("pending", "executed")),
            )
        )
        if existing is not None:
            return existing

    action = PendingAction(
        user_id=user_id,
        tool=tool,
        arguments=arguments,
        preview=preview,
        confirm_token=secrets.token_urlsafe(24),
        idempotency_key=idempotency_key,
        workflow_run_id=workflow_run_id,
        status="pending",
        expires_at=utcnow() + PENDING_TTL,
    )
    session.add(action)
    session.add(
        ActionAudit(
            user_id=user_id,
            tool=tool,
            arguments=arguments,
            event_type="proposed",
            actor="agent",
            pending_action_id=None,
            summary=preview,
        )
    )
    await session.commit()
    await session.refresh(action)
    # Back-fill pending id on the audit row we just wrote.
    audit = await session.scalar(
        select(ActionAudit)
        .where(ActionAudit.user_id == user_id, ActionAudit.event_type == "proposed")
        .order_by(ActionAudit.created_at.desc())
    )
    if audit is not None:
        audit.pending_action_id = action.id
        await session.commit()
    return action


def _as_utc(value):
    from datetime import timezone

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def get_pending(
    session: AsyncSession, user_id: str, *, action_id: str | None = None
) -> PendingAction | None:
    query = select(PendingAction).where(
        PendingAction.user_id == user_id,
        PendingAction.status == "pending",
    )
    if action_id:
        query = query.where(PendingAction.id == action_id)
    else:
        query = query.order_by(PendingAction.created_at.desc())
    action = await session.scalar(query.limit(1))
    if action is None:
        return None
    if _as_utc(action.expires_at) <= utcnow():
        action.status = "expired"
        await session.commit()
        return None
    return action


async def list_pending(session: AsyncSession, user_id: str, limit: int = 10) -> list[PendingAction]:
    rows = (
        await session.scalars(
            select(PendingAction)
            .where(PendingAction.user_id == user_id, PendingAction.status == "pending")
            .order_by(PendingAction.created_at.desc())
            .limit(limit)
        )
    ).all()
    alive: list[PendingAction] = []
    now = utcnow()
    for row in rows:
        if _as_utc(row.expires_at) <= now:
            row.status = "expired"
        else:
            alive.append(row)
    await session.commit()
    return alive


async def resolve_action(
    session: AsyncSession,
    action: PendingAction,
    *,
    status: str,
    actor: str,
    result_summary: str | None = None,
) -> PendingAction:
    action.status = status
    action.resolved_at = utcnow()
    action.result_summary = result_summary
    session.add(
        ActionAudit(
            user_id=action.user_id,
            tool=action.tool,
            arguments=action.arguments,
            event_type=status,
            actor=actor,
            pending_action_id=action.id,
            summary=result_summary or action.preview,
        )
    )
    await session.commit()
    await session.refresh(action)
    return action


async def record_audit(
    session: AsyncSession,
    *,
    user_id: str,
    tool: str,
    event_type: str,
    actor: str = "system",
    arguments: dict[str, Any] | None = None,
    pending_action_id: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        ActionAudit(
            user_id=user_id,
            tool=tool,
            arguments=arguments,
            event_type=event_type,
            actor=actor,
            pending_action_id=pending_action_id,
            summary=summary,
            details=details,
        )
    )
    await session.commit()
