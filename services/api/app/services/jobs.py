"""Typed background jobs that outlive a voice session.

Handlers run on the API process. They may read Gmail/Calendar and write inbox
items; they never send mail or change a calendar without a pending approval.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.models.tables import Reminder, ScheduledJob, WorkflowRun, utcnow
from app.services import google_oauth
from app.services import notifications as notification_service
from app.services import user_settings as settings_service
from app.tools.base import ToolContext, ToolError
from app.tools.calendar import ListEventsArgs, list_calendar_events
from app.tools.email_tools import SearchEmailsArgs, search_emails

logger = logging.getLogger("auren.jobs")

MAX_ATTEMPTS = 5
JOB_LEASE = timedelta(minutes=15)


def _payload_message(job: ScheduledJob) -> str:
    return str((job.payload or {}).get("message") or "").strip()


def _payload_query(job: ScheduledJob) -> str:
    payload = job.payload or {}
    return str(payload.get("query") or payload.get("gmail_query") or "").strip()


async def _notify(
    session: AsyncSession,
    job: ScheduledJob,
    *,
    kind: str,
    title: str,
    body: str,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> None:
    await notification_service.deliver(
        session,
        user_id=job.user_id,
        kind=kind,
        title=title,
        body=body,
        source_type="scheduled_job",
        source_id=job.id,
        api_settings=api_settings,
        http=http,
    )


async def _touch_workflow(session: AsyncSession, job: ScheduledJob) -> None:
    if not job.workflow_run_id:
        return
    workflow = await session.get(WorkflowRun, job.workflow_run_id)
    if workflow is None or workflow.user_id != job.user_id:
        return
    context = dict(workflow.context or {})
    delivered = list(context.get("delivered_job_ids") or [])
    if job.id not in delivered:
        delivered.append(job.id)
    context["delivered_job_ids"] = delivered[-50:]
    workflow.context = context
    if workflow.status not in {"completed", "failed", "cancelled"}:
        workflow.status = "awaiting_input"


async def handle_follow_up(
    session: AsyncSession,
    job: ScheduledJob,
    *,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> None:
    message = _payload_message(job) or "Follow-up"
    reminder = Reminder(
        user_id=job.user_id,
        title=message[:500],
        notes=f"Delivered from scheduled {job.job_type} job {job.id}.",
        due_at=utcnow(),
        timezone_name="UTC",
        status="due",
    )
    session.add(reminder)
    await session.flush()
    await _notify(
        session,
        job,
        kind="follow_up",
        title=message[:300],
        body="This follow-up is due. Open Auren when you want to continue.",
        api_settings=api_settings,
        http=http,
    )
    await _touch_workflow(session, job)


async def handle_email_reply_check(
    session: AsyncSession,
    job: ScheduledJob,
    *,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> None:
    message = _payload_message(job) or "Check for a reply"
    query = _payload_query(job)
    if not query:
        await _notify(
            session,
            job,
            kind="email_reply_check",
            title=message[:300],
            body="I could not check Gmail because this follow-up has no search query.",
            api_settings=api_settings,
            http=http,
        )
        await _touch_workflow(session, job)
        return
    if api_settings is None or http is None:
        raise RuntimeError("Email reply checks need the API HTTP client.")

    context = ToolContext(
        user_id=job.user_id, settings=api_settings, http=http, session=session
    )
    try:
        result = await search_emails(
            context,
            SearchEmailsArgs(query=query, max_results=5, include_body=False),
        )
    except (google_oauth.GoogleAuthError, ToolError) as error:
        await _notify(
            session,
            job,
            kind="email_reply_check",
            title=message[:300],
            body=f"I could not check Gmail: {error}",
            api_settings=api_settings,
            http=http,
        )
        await _touch_workflow(session, job)
        return

    messages = list((result.data or {}).get("messages") or [])
    if messages:
        first = messages[0] if isinstance(messages[0], dict) else {}
        subject = first.get("subject") or "a message"
        sender = first.get("from") or "someone"
        body = (
            f"There is a matching email: '{subject}' from {sender}. "
            "I did not reply or send anything."
        )
    else:
        body = (
            f"No matching email yet for '{query}'. {message} "
            "Say if you want a reminder or a draft — I will not send mail on my own."
        )
    await _notify(
        session,
        job,
        kind="email_reply_check",
        title=message[:300],
        body=body,
        api_settings=api_settings,
        http=http,
    )
    await _touch_workflow(session, job)


async def handle_morning_brief(
    session: AsyncSession,
    job: ScheduledJob,
    *,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> None:
    policy = await settings_service.get_or_create(
        session,
        job.user_id,
        default_timezone=api_settings.default_timezone if api_settings else "UTC",
    )
    parts: list[str] = []

    due = (
        await session.scalars(
            select(Reminder).where(
                Reminder.user_id == job.user_id,
                Reminder.status == "due",
            )
        )
    ).all()
    if due:
        titles = "; ".join(item.title for item in due[:5])
        parts.append(f"Due reminders: {titles}.")
    else:
        parts.append("No due reminders.")

    if api_settings is not None and http is not None:
        context = ToolContext(
            user_id=job.user_id, settings=api_settings, http=http, session=session
        )
        try:
            calendar = await list_calendar_events(
                context, ListEventsArgs(period="today", timezone=policy.timezone_name)
            )
            parts.append(calendar.summary)
        except (google_oauth.GoogleAuthError, ToolError) as error:
            parts.append(f"Calendar unavailable: {error}")
        try:
            inbox = await search_emails(
                context,
                SearchEmailsArgs(query="in:inbox", max_results=3, include_body=False),
            )
            parts.append(inbox.summary)
        except (google_oauth.GoogleAuthError, ToolError) as error:
            parts.append(f"Inbox unavailable: {error}")
    else:
        parts.append("Google tools were not available for this brief.")

    body = " ".join(parts)
    await _notify(
        session,
        job,
        kind="morning_brief",
        title="Your morning brief",
        body=body,
        api_settings=api_settings,
        http=http,
    )
    await _touch_workflow(session, job)

    if policy.morning_brief_enabled:
        session.add(
            ScheduledJob(
                user_id=job.user_id,
                job_type="morning_brief",
                payload={"message": "Morning brief"},
                run_at=settings_service.next_local_hour(
                    utcnow() + timedelta(minutes=1),
                    policy.timezone_name,
                    policy.morning_brief_hour,
                ),
                status="scheduled",
            )
        )


HANDLERS = {
    "follow_up_reminder": handle_follow_up,
    "custom": handle_follow_up,
    "email_reply_check": handle_email_reply_check,
    "morning_brief": handle_morning_brief,
}


async def execute_job(
    session: AsyncSession,
    job: ScheduledJob,
    *,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> None:
    handler = HANDLERS.get(job.job_type, handle_follow_up)
    await handler(session, job, api_settings=api_settings, http=http)


async def run_due_jobs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    api_settings: Settings | None,
    http: httpx.AsyncClient | None,
) -> int:
    now = utcnow()
    stale_before = now - JOB_LEASE
    async with session_factory() as session:
        jobs = (
            await session.scalars(
                select(ScheduledJob)
                .where(
                    or_(
                        and_(
                            ScheduledJob.status == "scheduled",
                            ScheduledJob.run_at <= now,
                        ),
                        and_(
                            ScheduledJob.status == "running",
                            ScheduledJob.locked_at.is_not(None),
                            ScheduledJob.locked_at <= stale_before,
                        ),
                    )
                )
                .order_by(ScheduledJob.run_at)
                .limit(20)
            )
        ).all()
        job_ids = [job.id for job in jobs]

    completed = 0
    for job_id in job_ids:
        async with session_factory() as session:
            claimed_at = utcnow()
            claim = await session.execute(
                update(ScheduledJob)
                .where(
                    ScheduledJob.id == job_id,
                    or_(
                        and_(
                            ScheduledJob.status == "scheduled",
                            ScheduledJob.run_at <= claimed_at,
                        ),
                        and_(
                            ScheduledJob.status == "running",
                            ScheduledJob.locked_at.is_not(None),
                            ScheduledJob.locked_at <= claimed_at - JOB_LEASE,
                        ),
                    ),
                )
                .values(
                    status="running",
                    attempts=ScheduledJob.attempts + 1,
                    last_error=None,
                    locked_at=claimed_at,
                )
                .execution_options(synchronize_session=False)
            )
            if claim.rowcount != 1:
                await session.rollback()
                continue
            await session.commit()

        async with session_factory() as session:
            job = await session.get(ScheduledJob, job_id)
            if job is None:
                continue
            try:
                await execute_job(
                    session, job, api_settings=api_settings, http=http
                )
                job.status = "completed"
                job.completed_at = utcnow()
                job.last_error = None
                job.locked_at = None
                completed += 1
            except Exception as error:  # noqa: BLE001 - jobs must not kill the scheduler
                logger.exception("Job %s (%s) failed", job.id, job.job_type)
                job.last_error = str(error)[:1000]
                if job.attempts >= MAX_ATTEMPTS:
                    job.status = "failed"
                else:
                    job.status = "scheduled"
                    job.run_at = utcnow() + timedelta(minutes=job.attempts)
                job.locked_at = None
            await session.commit()
            logger.info(
                "Job %s type=%s user=%s status=%s",
                job.id,
                job.job_type,
                job.user_id,
                job.status,
            )
    return completed
