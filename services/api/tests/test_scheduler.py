from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.config import Settings
from app.db import create_engine, create_schema, create_session_factory
from app.models.tables import Reminder, ScheduledJob, WorkflowRun, utcnow
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
