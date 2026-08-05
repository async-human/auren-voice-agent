from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from pydantic import BaseModel, Field

from app.services import google_oauth
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class ListEventsArgs(BaseModel):
    days_ahead: int = Field(default=7, ge=1, le=60)
    query: str | None = Field(default=None, max_length=200)


class CreateEventArgs(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    start_at: str = Field(description="ISO 8601 start time")
    duration_minutes: int = Field(default=30, ge=5, le=480)
    attendees: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=8000)
    add_meet_link: bool = True
    timezone: str | None = None


class FindFreeSlotsArgs(BaseModel):
    days_ahead: int = Field(default=7, ge=1, le=21)
    duration_minutes: int = Field(default=30, ge=15, le=180)
    workday_start_hour: int = Field(default=9, ge=0, le=23)
    workday_end_hour: int = Field(default=18, ge=1, le=23)


def _parse_iso(value: str) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def list_calendar_events(context: ToolContext, args: ListEventsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    now = datetime.now(tz=timezone.utc)
    end = now + timedelta(days=args.days_ahead)
    params: dict[str, str | int | bool] = {
        "timeMin": now.isoformat().replace("+00:00", "Z"),
        "timeMax": end.isoformat().replace("+00:00", "Z"),
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 20,
    }
    if args.query:
        params["q"] = args.query
    response = await context.http.get(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 401:
        raise ToolError("Google Calendar authorization failed. Reconnect Google.")
    response.raise_for_status()
    items = response.json().get("items", [])
    if not items:
        return ToolResult(
            summary=f"No calendar events in the next {args.days_ahead} days.",
            data={"events": []},
        )
    lines = []
    events = []
    for item in items[:10]:
        start = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get("date")
        title = item.get("summary") or "Untitled"
        lines.append(f"{start}: {title}")
        events.append(
            {
                "id": item.get("id"),
                "title": title,
                "start": start,
                "htmlLink": item.get("htmlLink"),
            }
        )
    return ToolResult(
        summary="Upcoming events: " + "; ".join(lines),
        data={"events": events},
    )


async def create_calendar_event(context: ToolContext, args: CreateEventArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    start = _parse_iso(args.start_at)
    end = start + timedelta(minutes=args.duration_minutes)
    zone = args.timezone or context.settings.default_timezone
    body: dict = {
        "summary": args.title,
        "description": args.description or "",
        "start": {"dateTime": start.isoformat(), "timeZone": zone},
        "end": {"dateTime": end.isoformat(), "timeZone": zone},
    }
    if args.attendees:
        body["attendees"] = [{"email": email} for email in args.attendees]
    if args.add_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"auren-{int(start.timestamp())}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    response = await context.http.post(
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params={"conferenceDataVersion": 1 if args.add_meet_link else 0, "sendUpdates": "all"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
    )
    if response.status_code >= 400:
        raise ToolError(f"Could not create the calendar event: {response.text[:300]}")
    created = response.json()
    event_id = created.get("id")

    # Verify by re-reading.
    verify = await context.http.get(
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if verify.status_code != 200:
        raise ToolError("Event create reported success but verification failed.")
    verified = verify.json()
    meet = None
    for entry in verified.get("conferenceData", {}).get("entryPoints", []) or []:
        if entry.get("entryPointType") == "video":
            meet = entry.get("uri")
            break
    attendee_note = (
        f" Invited {', '.join(args.attendees)}." if args.attendees else ""
    )
    meet_note = f" Meet link: {meet}." if meet else ""
    return ToolResult(
        summary=(
            f"Verified calendar event '{verified.get('summary')}' at "
            f"{(verified.get('start') or {}).get('dateTime')}.{attendee_note}{meet_note}"
        ),
        data={
            "id": event_id,
            "htmlLink": verified.get("htmlLink"),
            "meet_link": meet,
            "verified": True,
        },
    )


async def find_free_slots(context: ToolContext, args: FindFreeSlotsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    now = datetime.now(tz=timezone.utc)
    end = now + timedelta(days=args.days_ahead)
    response = await context.http.post(
        "https://www.googleapis.com/calendar/v3/freeBusy",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": end.isoformat().replace("+00:00", "Z"),
            "items": [{"id": "primary"}],
        },
    )
    response.raise_for_status()
    busy = response.json().get("calendars", {}).get("primary", {}).get("busy", [])
    busy_ranges = [
        (_parse_iso(item["start"]), _parse_iso(item["end"])) for item in busy if "start" in item
    ]

    slots: list[str] = []
    cursor = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    duration = timedelta(minutes=args.duration_minutes)
    while cursor < end and len(slots) < 5:
        local = cursor.astimezone()
        if args.workday_start_hour <= local.hour < args.workday_end_hour:
            slot_end = cursor + duration
            overlap = any(cursor < b_end and slot_end > b_start for b_start, b_end in busy_ranges)
            if not overlap:
                slots.append(cursor.isoformat())
        cursor += timedelta(minutes=30)

    if not slots:
        return ToolResult(
            summary="I could not find an open slot in working hours for that window.",
            data={"slots": []},
        )
    return ToolResult(
        summary="Open slots: " + "; ".join(slots),
        data={"slots": slots},
    )


LIST_SPEC = ToolSpec(
    name="list_calendar_events",
    description="List upcoming Google Calendar events.",
    args_model=ListEventsArgs,
    handler=list_calendar_events,
)

CREATE_SPEC = ToolSpec(
    name="create_calendar_event",
    description=(
        "Create a Google Calendar event, optionally with attendees and a Meet link. "
        "Requires user confirmation before it runs."
    ),
    args_model=CreateEventArgs,
    handler=create_calendar_event,
    confirmation_required=True,
)

FREE_SPEC = ToolSpec(
    name="find_free_slots",
    description="Find free slots on the user's Google Calendar in working hours.",
    args_model=FindFreeSlotsArgs,
    handler=find_free_slots,
)
