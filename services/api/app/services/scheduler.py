"""Always-on API scheduler for reminders and follow-up jobs."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.tables import Reminder, utcnow
from app.services import jobs as job_service
from app.services import notifications as notification_service

logger = logging.getLogger("auren.scheduler")


async def fire_due_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    api_settings,
    http,
) -> int:
    async with session_factory() as session:
        now = utcnow()
        pending = (
            await session.scalars(
                select(Reminder).where(
                    Reminder.status == "pending",
                    Reminder.due_at.is_not(None),
                    Reminder.due_at <= now,
                )
            )
        ).all()
        fired = 0
        for reminder in pending:
            claimed = await session.execute(
                update(Reminder)
                .where(Reminder.id == reminder.id, Reminder.status == "pending")
                .values(status="due")
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                continue
            await notification_service.deliver(
                session,
                user_id=reminder.user_id,
                kind="reminder",
                title=reminder.title,
                body=reminder.notes or "This reminder is due.",
                source_type="reminder",
                source_id=reminder.id,
                api_settings=api_settings,
                http=http,
            )
            fired += 1
        if fired:
            await session.commit()
            logger.info("Scheduler fired reminders=%s", fired)
        return fired


async def process_due_work(session_factory, settings=None, http=None) -> None:
    fired = await fire_due_reminders(
        session_factory, api_settings=settings, http=http
    )
    delivered = await job_service.run_due_jobs(
        session_factory, api_settings=settings, http=http
    )
    if fired or delivered:
        logger.info(
            "Scheduler tick reminders=%s jobs=%s",
            fired,
            delivered,
        )


async def scheduler_loop(app) -> None:
    settings = app.state.settings
    if not settings.scheduler_enabled:
        return
    session_factory = app.state.session_factory
    interval = settings.scheduler_poll_seconds
    logger.info("Scheduler started (poll=%ss)", interval)
    try:
        while True:
            try:
                await process_due_work(
                    session_factory,
                    settings=settings,
                    http=getattr(app.state, "http_client", None),
                )
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Scheduler stopped")
        raise
