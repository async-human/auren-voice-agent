from __future__ import annotations

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.config import Settings
from app.db import create_engine, create_schema, create_session_factory
from app.models.tables import Notification, ScheduledJob, utcnow
from app.services.scheduler import process_due_work


async def test_settings_and_inbox_require_sign_in(client: AsyncClient) -> None:
    settings = await client.get("/v1/settings")
    inbox = await client.get("/v1/notifications")
    assert settings.status_code == 401
    assert inbox.status_code == 401


async def test_settings_round_trip_and_empty_inbox(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    empty = await client.get("/v1/notifications", headers=auth_headers)
    assert empty.status_code == 200
    assert empty.json()["notifications"] == []
    assert empty.json()["unread_count"] == 0

    updated = await client.put(
        "/v1/settings",
        headers=auth_headers,
        json={
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "morning_brief_enabled": True,
            "morning_brief_hour": 8,
            "spoken_delivery_enabled": True,
            "pod_wake_enabled": False,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["quiet_hours_start"] == "22:00"
    assert body["quiet_hours_end"] == "07:00"
    assert body["morning_brief_enabled"] is True
    assert body["morning_brief_hour"] == 8
    assert body["pod_wake_available"] is False

    loaded = await client.get("/v1/settings", headers=auth_headers)
    assert loaded.json()["quiet_hours_start"] == "22:00"


async def test_invalid_quiet_hours_are_rejected(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.put(
        "/v1/settings",
        headers=auth_headers,
        json={"quiet_hours_start": "25:00"},
    )
    assert response.status_code == 400


async def test_email_reply_check_notifies_without_sending(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    http = AsyncClient()

    async with session_factory() as session:
        session.add(
            ScheduledJob(
                user_id="mail-user",
                job_type="email_reply_check",
                payload={
                    "message": "If Priya has not replied, nudge me",
                    "query": "from:priya subject:invoice",
                },
                run_at=utcnow() - timedelta(minutes=1),
                status="scheduled",
            )
        )
        await session.commit()

    await process_due_work(session_factory, settings=settings, http=http)

    async with session_factory() as session:
        job = (
            await session.scalars(
                select(ScheduledJob).where(ScheduledJob.user_id == "mail-user")
            )
        ).one()
        note = (
            await session.scalars(
                select(Notification).where(Notification.user_id == "mail-user")
            )
        ).one()

    await http.aclose()
    await engine.dispose()

    assert job.status == "completed"
    assert note.kind == "email_reply_check"
    assert "gmail" in note.body.lower() or "google" in note.body.lower()
    assert "send" not in note.body.lower() or "did not" in note.body.lower()
