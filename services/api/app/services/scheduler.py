"""Always-on Railway/API scheduler for reminders and follow-up jobs."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta, timezone

from sqlalchemy import select, update

from app.db import create_session_factory
from app.models.tables import Reminder, ScheduledJob, WorkflowRun, utcnow

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
        reminder_result = await session.execute(
            update(Reminder)
            .where(
                Reminder.status == "pending",
                Reminder.due_at.is_not(None),
                Reminder.due_at <= now,
            )
            .values(status="due")
            .execution_options(synchronize_session=False)
        )
        fired = reminder_result.rowcount or 0

        jobs = (
            await session.scalars(
                select(ScheduledJob)
                .where(
                    ScheduledJob.status == "scheduled",
                    ScheduledJob.run_at <= now,
                )
                .order_by(ScheduledJob.run_at)
                .limit(20)
            )
        ).all()
        delivered = 0
        for job in jobs:
            message = str((job.payload or {}).get("message") or "").strip()
            if not message:
                job.attempts += 1
                job.last_error = "Scheduled job payload has no message."
                if job.attempts >= 3:
                    job.status = "failed"
                else:
                    job.run_at = now + timedelta(minutes=job.attempts)
                continue

            claim = await session.execute(
                update(ScheduledJob)
                .where(
                    ScheduledJob.id == job.id,
                    ScheduledJob.status == "scheduled",
                )
                .values(
                    status="completed",
                    completed_at=now,
                    attempts=ScheduledJob.attempts + 1,
                    last_error=None,
                )
                .execution_options(synchronize_session=False)
            )
            if claim.rowcount != 1:
                continue

            session.add(
                Reminder(
                    user_id=job.user_id,
                    title=message,
                    notes=f"Delivered from scheduled {job.job_type} job {job.id}.",
                    due_at=now,
                    timezone_name="UTC",
                    status="due",
                )
            )
            if job.workflow_run_id:
                workflow = await session.get(WorkflowRun, job.workflow_run_id)
                if workflow is not None and workflow.user_id == job.user_id:
                    context = dict(workflow.context or {})
                    delivered_jobs = list(context.get("delivered_job_ids") or [])
                    if job.id not in delivered_jobs:
                        delivered_jobs.append(job.id)
                    context["delivered_job_ids"] = delivered_jobs[-50:]
                    workflow.context = context
                    if workflow.status not in {"completed", "failed", "cancelled"}:
                        workflow.status = "awaiting_input"

            delivered += 1
            logger.info(
                "Delivered scheduled job %s type=%s user=%s",
                job.id,
                job.job_type,
                job.user_id,
            )

        if fired or jobs:
            await session.commit()
            logger.info(
                "Scheduler fired reminders=%s delivered_jobs=%s",
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
                await process_due_work(session_factory)
            except Exception:  # noqa: BLE001
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Scheduler stopped")
        raise
