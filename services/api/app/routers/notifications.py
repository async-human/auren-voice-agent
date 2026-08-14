from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.models.schemas import (
    NotificationListResponse,
    SpeechAckRequest,
)
from app.models.tables import User
from app.security.auth import require_user
from app.security.service_auth import require_service_token
from app.services import notifications as notification_service

worker_router = APIRouter(
    prefix="/v1/notifications",
    tags=["notifications"],
    dependencies=[Depends(require_service_token)],
)

user_router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


@user_router.get("", response_model=NotificationListResponse)
async def list_my_notifications(
    status_filter: str | None = Query(default=None, alias="status"),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> NotificationListResponse:
    if status_filter and status_filter not in {"unread", "read", "dismissed"}:
        raise HTTPException(status_code=400, detail="Invalid status filter")
    rows, unread = await notification_service.list_notifications(
        session, user.id, status=status_filter
    )
    return NotificationListResponse(
        notifications=[notification_service.to_item(row) for row in rows],
        unread_count=unread,
    )


@user_router.post("/read-all")
async def read_all(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    updated = await notification_service.mark_all_read(session, user.id)
    return {"ok": True, "updated": updated}


@user_router.post("/{notification_id}/read")
async def read_one(
    notification_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await notification_service.mark_read(session, user.id, notification_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {"ok": True, "id": row.id, "status": row.status}


@user_router.post("/{notification_id}/dismiss")
async def dismiss_one(
    notification_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await notification_service.dismiss(session, user.id, notification_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return {"ok": True, "id": row.id, "status": row.status}


@worker_router.post("/speech-ack")
async def speech_ack(
    payload: SpeechAckRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    updated = await notification_service.ack_speech(session, payload.user_id, payload.ids)
    return {"ok": True, "updated": updated}
