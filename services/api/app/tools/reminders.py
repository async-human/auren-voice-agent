from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.tables import Reminder
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec
from app.tools.clock import resolve_zone, spoken_datetime


class CreateReminderArgs(BaseModel):
    title: str = Field(min_length=1, max_length=500, description="What to be reminded about.")
    due_at: str | None = Field(
        default=None,
        description="Absolute time as ISO 8601, for example 2026-08-02T09:00:00.",
    )
    remind_in_minutes: int | None = Field(
        default=None,
        ge=1,
        le=60 * 24 * 365,
        description="Relative time from now. Use this for phrases like 'in twenty minutes'.",
    )
    timezone: str | None = Field(
        default=None,
        description="IANA timezone the due time is expressed in.",
    )
    notes: str | None = Field(default=None, max_length=2000)


class ListRemindersArgs(BaseModel):
    status: Literal["pending", "due", "completed", "all"] = "pending"
    limit: int = Field(default=10, ge=1, le=50)
    timezone: str | None = Field(default=None, description="Timezone used to read times back.")


def _resolve_due_at(args: CreateReminderArgs, zone_name: str) -> datetime | None:
    if args.remind_in_minutes is not None:
        return datetime.now(tz=timezone.utc) + timedelta(minutes=args.remind_in_minutes)

    if not args.due_at:
        return None

    raw = args.due_at.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ToolError(
            "I could not understand that time. Give me an ISO 8601 timestamp or a number of minutes."
        ) from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_zone(zone_name))
    return parsed.astimezone(timezone.utc)


async def create_reminder(context: ToolContext, args: CreateReminderArgs) -> ToolResult:
    zone_name = args.timezone or context.settings.default_timezone
    zone = resolve_zone(zone_name)
    due_at = _resolve_due_at(args, zone_name)

    reminder = Reminder(
        user_id=context.user_id,
        title=args.title.strip(),
        notes=args.notes,
        due_at=due_at,
        timezone_name=zone_name,
    )
    context.session.add(reminder)
    await context.session.commit()

    if due_at is None:
        summary = f"Saved a reminder to {reminder.title}, with no time set."
    else:
        summary = f"Saved a reminder to {reminder.title} for {spoken_datetime(due_at.astimezone(zone))}."

    return ToolResult(
        summary=summary,
        data={
            "id": reminder.id,
            "title": reminder.title,
            "due_at": due_at.isoformat() if due_at else None,
            "timezone": zone_name,
            "status": reminder.status,
        },
    )


async def list_reminders(context: ToolContext, args: ListRemindersArgs) -> ToolResult:
    zone_name = args.timezone or context.settings.default_timezone
    zone = resolve_zone(zone_name)

    query = select(Reminder).where(Reminder.user_id == context.user_id)
    if args.status != "all":
        query = query.where(Reminder.status == args.status)
    query = query.order_by(Reminder.due_at.is_(None), Reminder.due_at).limit(args.limit)

    reminders = (await context.session.scalars(query)).all()
    if not reminders:
        return ToolResult(summary="You have no reminders saved.", data={"reminders": []})

    spoken: list[str] = []
    payload = []
    for reminder in reminders:
        due = reminder.due_at
        if due is not None:
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            spoken.append(f"{reminder.title} at {spoken_datetime(due.astimezone(zone))}")
        else:
            spoken.append(reminder.title)
        payload.append(
            {
                "id": reminder.id,
                "title": reminder.title,
                "due_at": due.isoformat() if due else None,
                "status": reminder.status,
            }
        )

    count = len(spoken)
    noun = "reminder" if count == 1 else "reminders"
    return ToolResult(
        summary=f"You have {count} {noun}: " + "; ".join(spoken) + ".",
        data={"reminders": payload},
    )


CREATE_SPEC = ToolSpec(
    name="create_reminder",
    description="Save a reminder for the user, optionally with a due time.",
    args_model=CreateReminderArgs,
    handler=create_reminder,
)

LIST_SPEC = ToolSpec(
    name="list_reminders",
    description="List the user's saved reminders, soonest first.",
    args_model=ListRemindersArgs,
    handler=list_reminders,
)
