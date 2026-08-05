from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.config import Settings
from app.dependencies import get_http_client, get_session, get_settings
from app.models.tables import User
from app.security.auth import require_user
from app.services import approvals
from app.tools import registry
from app.tools.base import ToolContext, ToolError

router = APIRouter(prefix="/v1/actions", tags=["actions"])


@router.get("/pending")
async def pending_actions(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await approvals.list_pending(session, user.id)
    return {
        "actions": [
            {
                "id": row.id,
                "tool": row.tool,
                "preview": row.preview,
                "arguments": row.arguments,
                "expires_at": row.expires_at.isoformat() if row.expires_at else None,
            }
            for row in rows
        ]
    }


@router.post("/{action_id}/confirm")
async def confirm_action(
    action_id: str,
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
    session: AsyncSession = Depends(get_session),
) -> dict:
    action = await approvals.get_pending(session, user.id, action_id=action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Pending action not found")
    await approvals.resolve_action(
        session, action, status="confirmed", actor="user", result_summary="UI confirm"
    )
    context = ToolContext(user_id=user.id, settings=settings, http=http, session=session)
    try:
        result = await registry.execute_handler(context, action.tool, action.arguments)
    except ToolError as error:
        await approvals.resolve_action(
            session, action, status="failed", actor="system", result_summary=str(error)
        )
        raise HTTPException(status_code=400, detail=str(error)) from error
    action.status = "executed"
    action.result_summary = result.summary
    await session.commit()
    await approvals.record_audit(
        session,
        user_id=user.id,
        tool=action.tool,
        event_type="executed",
        actor="user",
        pending_action_id=action.id,
        summary=result.summary,
        details=result.data,
    )
    return {"ok": True, "summary": result.summary, "data": result.data}


@router.post("/{action_id}/reject")
async def reject_action(
    action_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    action = await approvals.get_pending(session, user.id, action_id=action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="Pending action not found")
    await approvals.resolve_action(
        session, action, status="rejected", actor="user", result_summary="UI reject"
    )
    return {"ok": True, "rejected": True}
