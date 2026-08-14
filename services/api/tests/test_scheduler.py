from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.db import create_engine, create_schema, create_session_factory
from app.models.tables import Notification, Reminder, ScheduledJob, WorkflowRun, utcnow
from app.services.scheduler import process_due_work


async def test_due_job_is_delivered_once_as_a_durable_reminder(
    settings: Settings,
) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        workflow = WorkflowRun(
            user_id="scheduler-user",
            goal="Follow up with Rahul",
            plan=["Wait for a reply", "Remind me to follow up"],
            status="active",
            current_step=1,
            context={},
        )
        session.add(workflow)
        await session.flush()
        job = ScheduledJob(
            user_id="scheduler-user",
            job_type="follow_up_reminder",
            payload={"message": "Follow up with Rahul"},
            run_at=utcnow() - timedelta(minutes=1),
            status="scheduled",
            workflow_run_id=workflow.id,
        )
        session.add(job)
        await session.commit()
        job_id = job.id
        workflow_id = workflow.id

    await process_due_work(session_factory)
    await process_due_work(session_factory)

    async with session_factory() as session:
        delivered_job = await session.get(ScheduledJob, job_id)
        delivered_workflow = await session.get(WorkflowRun, workflow_id)
        reminders = (
            await session.scalars(
                select(Reminder).where(
                    Reminder.user_id == "scheduler-user",
                    Reminder.title == "Follow up with Rahul",
                )
            )
        ).all()
        reminder_count = await session.scalar(select(func.count(Reminder.id)))
        notes = (
            await session.scalars(
                select(Notification).where(Notification.user_id == "scheduler-user")
            )
        ).all()

    assert delivered_job is not None
    assert delivered_job.status == "completed"
    assert delivered_job.attempts == 1
    assert delivered_job.completed_at is not None
    assert reminder_count == 1
    assert len(reminders) == 1
    assert reminders[0].status == "due"
    assert delivered_workflow is not None
    assert delivered_workflow.status == "awaiting_input"
    assert job_id in delivered_workflow.context["delivered_job_ids"]
    assert len(notes) == 1
    assert notes[0].kind == "follow_up"
    assert notes[0].status == "unread"
    assert notes[0].speak_queued is True

    await engine.dispose()


async def test_future_job_remains_scheduled(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        job = ScheduledJob(
            user_id="scheduler-user",
            job_type="custom",
            payload={"message": "Check this later"},
            run_at=utcnow() + timedelta(hours=1),
            status="scheduled",
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    await process_due_work(session_factory)

    async with session_factory() as session:
        untouched = await session.get(ScheduledJob, job_id)
        reminder_count = await session.scalar(select(func.count(Reminder.id)))

    assert untouched is not None
    assert untouched.status == "scheduled"
    assert untouched.attempts == 0
    assert reminder_count == 0

    await engine.dispose()


async def test_due_reminder_writes_an_inbox_item(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        session.add(
            Reminder(
                user_id="reminder-user",
                title="Stand up",
                notes="Daily standup",
                due_at=utcnow() - timedelta(minutes=2),
                timezone_name="UTC",
                status="pending",
            )
        )
        await session.commit()

    await process_due_work(session_factory)

    async with session_factory() as session:
        reminder = (
            await session.scalars(select(Reminder).where(Reminder.user_id == "reminder-user"))
        ).one()
        note = (
            await session.scalars(
                select(Notification).where(Notification.user_id == "reminder-user")
            )
        ).one()

    assert reminder.status == "due"
    assert note.kind == "reminder"
    assert note.title == "Stand up"
    assert note.status == "unread"
    assert note.speak_queued is True

    await engine.dispose()


async def test_quiet_hours_keep_inbox_but_skip_speech(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)

    from app.models.tables import UserSettings

    async with session_factory() as session:
        session.add(
            UserSettings(
                user_id="quiet-user",
                timezone_name="UTC",
                quiet_hours_start="00:00",
                quiet_hours_end="23:59",
                spoken_delivery_enabled=True,
                max_interruptions_per_day=8,
            )
        )
        session.add(
            Reminder(
                user_id="quiet-user",
                title="Late ping",
                due_at=utcnow() - timedelta(minutes=1),
                status="pending",
            )
        )
        await session.commit()

    await process_due_work(session_factory)

    async with session_factory() as session:
        note = (
            await session.scalars(
                select(Notification).where(Notification.user_id == "quiet-user")
            )
        ).one()

    assert note.status == "unread"
    assert note.speak_queued is False
    assert note.interruption is False
    assert note.speak_text is None

    await engine.dispose()
