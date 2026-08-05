"""Always-on Railway/API scheduler for reminders and follow-up jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import timezone

from sqlalchemy import select

from app.db import create_session_factory
from app.models.tables import Reminder, ScheduledJob, utcnow

logger = logging.getLogger("auren.scheduler")


def _as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def process_due_work(session_factory) -> None:
    async with session_factory() as session:
        now = utcnow()
        reminders = (
            await session.scalars(
                select(Reminder).where(
                    Reminder.status == "pending",
                    Reminder.due_at.is_not(None),
                )
            )
        ).all()
        fired = 0
        for reminder in reminders:
            due = _as_utc(reminder.due_at)
            if due is not None and due <= now:
                reminder.status = "due"
                fired += 1

        jobs = (
            await session.scalars(
                select(ScheduledJob).where(
                    ScheduledJob.status == "scheduled",
                    ScheduledJob.run_at <= now,
                ).limit(20)
            )
        ).all()
        for job in jobs:
            job.status = "completed"
            job.completed_at = now
            job.attempts += 1
            # Payload is retained for the UI / next voice session to pick up.
            logger.info(
                "Completed scheduled job %s type=%s user=%s",
                job.id,
                job.job_type,
                job.user_id,
            )

        if fired or jobs:
            await session.commit()
            logger.info("Scheduler fired reminders=%s jobs=%s", fired, len(jobs))


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
                await process_due_work(session_factory)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Scheduler stopped")
        raise
