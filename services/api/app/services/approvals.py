"""Two-phase confirmation for consequential tools."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
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
                PendingAction.status.in_(("pending", "executing", "executed")),
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
    await session.flush()
    session.add(
        ActionAudit(
            user_id=user_id,
            tool=tool,
            arguments=arguments,
            event_type="proposed",
            actor="agent",
            pending_action_id=action.id,
            summary=preview,
        )
    )
    await session.commit()
    await session.refresh(action)
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


async def claim_pending(
    session: AsyncSession,
    user_id: str,
    *,
    action_id: str | None = None,
    actor: str = "user",
) -> PendingAction | None:
    """Atomically claim one pending action for execution.

    The compare-and-set update is the trust boundary: concurrent confirmations
    may read the same candidate, but only one can transition it to executing.
    """
    action = await get_pending(session, user_id, action_id=action_id)
    if action is None:
        return None

    result = await session.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action.id,
            PendingAction.user_id == user_id,
            PendingAction.status == "pending",
        )
        .values(status="executing", resolved_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        await session.rollback()
        return None

    session.add(
        ActionAudit(
            user_id=action.user_id,
            tool=action.tool,
            arguments=action.arguments,
            event_type="confirmed",
            actor=actor,
            pending_action_id=action.id,
            summary="User confirmed",
        )
    )
    await session.commit()
    claimed = await session.get(PendingAction, action.id)
    if claimed is not None:
        await session.refresh(claimed)
    return claimed


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


async def supersede_pending(
    session: AsyncSession,
    *,
    user_id: str,
    tool: str,
    argument_key: str,
    argument_value: object,
) -> int:
    """Invalidate older approvals when a mutable resource is revised."""
    rows = (
        await session.scalars(
            select(PendingAction).where(
                PendingAction.user_id == user_id,
                PendingAction.tool == tool,
                PendingAction.status == "pending",
            )
        )
    ).all()
    changed = 0
    for row in rows:
        if (row.arguments or {}).get(argument_key) != argument_value:
            continue
        row.status = "superseded"
        row.resolved_at = utcnow()
        session.add(
            ActionAudit(
                user_id=user_id,
                tool=tool,
                arguments=row.arguments,
                event_type="superseded",
                actor="system",
                pending_action_id=row.id,
                summary="Superseded by a newer draft version",
            )
        )
        changed += 1
    if changed:
        await session.commit()
    return changed


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
