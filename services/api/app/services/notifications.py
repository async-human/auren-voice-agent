"""Inbox delivery: always visible, spoken/pod-wake only inside stated boundaries."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.schemas import NotificationItem
from app.models.tables import Notification, utcnow
from app.services import runpod as runpod_service
from app.services import user_settings as settings_service

import httpx


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def to_item(row: Notification) -> NotificationItem:
    return NotificationItem(
        id=row.id,
        kind=row.kind,
        title=row.title,
        body=row.body,
        status=row.status,
        source_type=row.source_type,
        source_id=row.source_id,
        created_at=_iso(row.created_at),
        read_at=_iso(row.read_at),
        spoken_at=_iso(row.spoken_at),
    )


async def _existing_unread(
    session: AsyncSession,
    user_id: str,
    source_type: str | None,
    source_id: str | None,
    kind: str,
) -> Notification | None:
    if not source_type or not source_id:
        return None
    return await session.scalar(
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.kind == kind,
            Notification.source_type == source_type,
            Notification.source_id == source_id,
            Notification.status == "unread",
        )
        .limit(1)
    )


async def interruption_count_today(
    session: AsyncSession, user_id: str, row
) -> int:
    started = settings_service.local_day_start(utcnow(), row)
    count = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.interruption.is_(True),
            Notification.created_at >= started,
        )
    )
    return int(count or 0)


async def deliver(
    session: AsyncSession,
    *,
    user_id: str,
    kind: str,
    title: str,
    body: str,
    source_type: str | None = None,
    source_id: str | None = None,
    api_settings: Settings | None = None,
    http: httpx.AsyncClient | None = None,
) -> Notification:
    existing = await _existing_unread(session, user_id, source_type, source_id, kind)
    if existing is not None:
        return existing

    default_tz = api_settings.default_timezone if api_settings else "UTC"
    policy = await settings_service.get_or_create(
        session, user_id, default_timezone=default_tz
    )
    now = utcnow()
    speak_text = f"{title}. {body}".strip()
    if len(speak_text) > 400:
        speak_text = f"{speak_text[:397]}..."

    quiet = settings_service.in_quiet_hours(now, policy)
    used = await interruption_count_today(session, user_id, policy)
    under_cap = used < policy.max_interruptions_per_day
    may_speak = bool(policy.spoken_delivery_enabled and not quiet and under_cap)
    may_wake = bool(
        may_speak
        and policy.pod_wake_enabled
        and api_settings is not None
        and api_settings.pod_wake_configured
    )

    notification = Notification(
        user_id=user_id,
        kind=kind,
        title=title[:300],
        body=body,
        speak_text=speak_text if may_speak else None,
        status="unread",
        source_type=source_type,
        source_id=source_id,
        speak_queued=may_speak,
        interruption=may_speak,
        pod_wake_attempted=False,
    )
    session.add(notification)
    await session.flush()

    if may_wake:
        notification.pod_wake_attempted = await runpod_service.start_pod(
            api_settings, http
        )
    return notification


async def list_notifications(
    session: AsyncSession,
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 40,
) -> tuple[list[Notification], int]:
    query = select(Notification).where(Notification.user_id == user_id)
    if status:
        query = query.where(Notification.status == status)
    query = query.order_by(Notification.created_at.desc()).limit(limit)
    rows = list((await session.scalars(query)).all())
    unread = await session.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.status == "unread",
        )
    )
    return rows, int(unread or 0)


async def mark_read(
    session: AsyncSession, user_id: str, notification_id: str
) -> Notification | None:
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        return None
    if row.status == "unread":
        row.status = "read"
        row.read_at = utcnow()
        await session.commit()
    return row


async def mark_all_read(session: AsyncSession, user_id: str) -> int:
    now = utcnow()
    result = await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.status == "unread")
        .values(status="read", read_at=now)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return int(result.rowcount or 0)


async def dismiss(
    session: AsyncSession, user_id: str, notification_id: str
) -> Notification | None:
    row = await session.get(Notification, notification_id)
    if row is None or row.user_id != user_id:
        return None
    row.status = "dismissed"
    if row.read_at is None:
        row.read_at = utcnow()
    await session.commit()
    return row


async def pending_speech(
    session: AsyncSession, user_id: str, *, limit: int = 8
) -> list[Notification]:
    rows = (
        await session.scalars(
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.speak_queued.is_(True),
                Notification.spoken_at.is_(None),
            )
            .order_by(Notification.created_at.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def ack_speech(session: AsyncSession, user_id: str, ids: list[str]) -> int:
    if not ids:
        return 0
    now = utcnow()
    result = await session.execute(
        update(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.id.in_(ids),
            Notification.spoken_at.is_(None),
        )
        .values(spoken_at=now, speak_queued=False)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return int(result.rowcount or 0)
