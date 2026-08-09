from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import google_oauth
from app.services.google_http import request as google_request
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec
from app.tools.clock import resolve_zone


class ListEventsArgs(BaseModel):
    period: Literal["today", "tomorrow", "upcoming", "custom"] = Field(
        default="upcoming",
        description="Calendar window in the user's timezone.",
    )
    days_ahead: int = Field(default=7, ge=1, le=60)
    query: str | None = Field(default=None, max_length=200)
    timezone: str | None = None
    start_at: str | None = Field(default=None, description="ISO 8601 custom range start")
    end_at: str | None = Field(default=None, description="ISO 8601 custom range end")

    @model_validator(mode="after")
    def _custom_range_is_complete(self) -> ListEventsArgs:
        if self.period == "custom" and not (self.start_at and self.end_at):
            raise ValueError("custom periods require both start_at and end_at")
        return self


class CreateEventArgs(BaseModel):
    event_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        min_length=5,
        max_length=1024,
        pattern=r"^[0-9a-v]+$",
        description="Stable Google Calendar id used to prevent duplicate event creation.",
    )
    title: str = Field(min_length=1, max_length=500)
    start_at: str = Field(description="ISO 8601 start time")
    duration_minutes: int = Field(default=30, ge=5, le=480)
    attendees: list[str] = Field(default_factory=list)
    description: str | None = Field(default=None, max_length=8000)
    add_meet_link: bool = True
    timezone: str | None = None

    @field_validator("attendees")
    @classmethod
    def _validate_attendees(cls, values: list[str]) -> list[str]:
        clean: list[str] = []
        for value in values:
            address = value.strip()
            if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", address):
                raise ValueError(f"'{value}' is not a valid attendee email address")
            clean.append(address)
        return clean


class FindFreeSlotsArgs(BaseModel):
    days_ahead: int = Field(default=7, ge=1, le=21)
    duration_minutes: int = Field(default=30, ge=15, le=180)
    workday_start_hour: int = Field(default=9, ge=0, le=23)
    workday_end_hour: int = Field(default=18, ge=1, le=23)
    timezone: str | None = None


def _parse_iso(value: str, default_timezone: str = "UTC") -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as error:
        raise ToolError(f"'{value}' is not a valid ISO 8601 date and time.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=resolve_zone(default_timezone))
    return parsed.astimezone(timezone.utc)


def _find_open_slots(
    *,
    now: datetime,
    end: datetime,
    busy_ranges: list[tuple[datetime, datetime]],
    timezone_name: str,
    duration_minutes: int,
    workday_start_hour: int,
    workday_end_hour: int,
    limit: int = 5,
) -> list[str]:
    if workday_start_hour >= workday_end_hour:
        raise ToolError("Working hours must end after they start.")

    zone = resolve_zone(timezone_name)
    local_now = now.astimezone(zone)
    local_end = end.astimezone(zone)
    cursor = local_now.replace(second=0, microsecond=0)
    cursor += timedelta(minutes=30 - (cursor.minute % 30))
    duration = timedelta(minutes=duration_minutes)
    slots: list[str] = []

    while cursor < local_end and len(slots) < limit:
        slot_end = cursor + duration
        workday_end = cursor.replace(
            hour=workday_end_hour,
            minute=0,
            second=0,
            microsecond=0,
        )
        within_workday = (
            cursor.weekday() < 5
            and cursor.hour >= workday_start_hour
            and slot_end <= workday_end
        )
        if within_workday:
            slot_start_utc = cursor.astimezone(timezone.utc)
            slot_end_utc = slot_end.astimezone(timezone.utc)
            overlap = any(
                slot_start_utc < busy_end and slot_end_utc > busy_start
                for busy_start, busy_end in busy_ranges
            )
            if not overlap:
                slots.append(cursor.isoformat())
        cursor += timedelta(minutes=30)

    return slots


def _event_window(
    args: ListEventsArgs,
    *,
    now: datetime,
    default_timezone: str,
) -> tuple[datetime, datetime, str, str]:
    zone_name = args.timezone or default_timezone
    zone = resolve_zone(zone_name)
    local_now = now.astimezone(zone)
    if args.period == "today":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_local = start_local + timedelta(days=1)
        label = "today"
    elif args.period == "tomorrow":
        start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        end_local = start_local + timedelta(days=1)
        label = "tomorrow"
    elif args.period == "custom":
        start_local = _parse_iso(args.start_at or "", zone_name).astimezone(zone)
        end_local = _parse_iso(args.end_at or "", zone_name).astimezone(zone)
        label = "the requested window"
    else:
        start_local = local_now
        end_local = local_now + timedelta(days=args.days_ahead)
        label = f"the next {args.days_ahead} days"
    if end_local <= start_local:
        raise ToolError("Calendar range end must be after its start.")
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        zone_name,
        label,
    )


async def list_calendar_events(context: ToolContext, args: ListEventsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    default_timezone = await google_oauth.connection_timezone(
        context.session,
        context.user_id,
        default=context.settings.default_timezone,
    )
    now = datetime.now(tz=timezone.utc)
    start, end, zone_name, label = _event_window(
        args,
        now=now,
        default_timezone=default_timezone,
    )
    params: dict[str, str | int | bool] = {
        "timeMin": start.isoformat().replace("+00:00", "Z"),
        "timeMax": end.isoformat().replace("+00:00", "Z"),
        "timeZone": zone_name,
        "singleEvents": True,
        "orderBy": "startTime",
        "maxResults": 20,
    }
    if args.query:
        params["q"] = args.query
    response = await google_request(
        context.http,
        "GET",
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code == 401:
        raise ToolError("Google Calendar authorization failed. Reconnect Google.")
    if response.status_code == 403:
        raise ToolError("Google Calendar denied permission. Reconnect Google.")
    if response.status_code >= 400:
        raise ToolError(f"Could not read Google Calendar: {response.text[:240]}")
    items = response.json().get("items", [])
    if not items:
        return ToolResult(
            summary=f"No calendar events for {label}.",
            data={
                "events": [],
                "period": args.period,
                "timezone": zone_name,
                "time_min": start.isoformat(),
                "time_max": end.isoformat(),
            },
        )
    lines = []
    events = []
    for item in items[:10]:
        event_start = (item.get("start") or {}).get("dateTime") or (
            item.get("start") or {}
        ).get("date")
        event_end = (item.get("end") or {}).get("dateTime") or (
            item.get("end") or {}
        ).get("date")
        title = item.get("summary") or "Untitled"
        lines.append(f"{event_start}: {title}")
        events.append(
            {
                "id": item.get("id"),
                "title": title,
                "start": event_start,
                "end": event_end,
                "all_day": bool((item.get("start") or {}).get("date")),
                "status": item.get("status"),
                "location": item.get("location"),
                "attendees": [
                    attendee.get("email")
                    for attendee in item.get("attendees", []) or []
                    if isinstance(attendee, dict) and attendee.get("email")
                ],
                "meet_link": item.get("hangoutLink"),
                "htmlLink": item.get("htmlLink"),
            }
        )
    return ToolResult(
        summary=f"Calendar events for {label}: " + "; ".join(lines),
        data={
            "events": events,
            "period": args.period,
            "timezone": zone_name,
            "time_min": start.isoformat(),
            "time_max": end.isoformat(),
        },
    )


async def create_calendar_event(context: ToolContext, args: CreateEventArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    zone_name = args.timezone or await google_oauth.connection_timezone(
        context.session,
        context.user_id,
        default=context.settings.default_timezone,
    )
    zone = resolve_zone(zone_name)
    start = _parse_iso(args.start_at, zone_name)
    if start < datetime.now(tz=timezone.utc) - timedelta(minutes=5):
        raise ToolError("Calendar events cannot be created in the past. Confirm the intended date.")
    end = start + timedelta(minutes=args.duration_minutes)
    body: dict = {
        "id": args.event_id,
        "summary": args.title,
        "description": args.description or "",
        "start": {
            "dateTime": start.astimezone(zone).isoformat(),
            "timeZone": zone_name,
        },
        "end": {
            "dateTime": end.astimezone(zone).isoformat(),
            "timeZone": zone_name,
        },
    }
    if args.attendees:
        body["attendees"] = [{"email": email} for email in args.attendees]
    if args.add_meet_link:
        body["conferenceData"] = {
            "createRequest": {
                "requestId": f"auren-{args.event_id}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }

    response = await google_request(
        context.http,
        "POST",
        "https://www.googleapis.com/calendar/v3/calendars/primary/events",
        params={
            "conferenceDataVersion": 1 if args.add_meet_link else 0,
            "sendUpdates": "all" if args.attendees else "none",
        },
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
    )
    if response.status_code < 400:
        event_id = response.json().get("id") or args.event_id
    elif response.status_code == 409 or response.status_code >= 500:
        # A stable client-generated id lets us resolve an ambiguous insert
        # response without ever issuing a second create request.
        event_id = args.event_id
    else:
        raise ToolError(f"Could not create the calendar event: {response.text[:300]}")

    # Verify by re-reading. This also recovers a successful insert whose response
    # was lost and surfaced as a transient server error.
    verify = await google_request(
        context.http,
        "GET",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if verify.status_code != 200:
        if response.status_code >= 400:
            raise ToolError(f"Could not create the calendar event: {response.text[:300]}")
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
    zone_name = args.timezone or await google_oauth.connection_timezone(
        context.session,
        context.user_id,
        default=context.settings.default_timezone,
    )
    response = await google_request(
        context.http,
        "POST",
        "https://www.googleapis.com/calendar/v3/freeBusy",
        retryable=True,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "timeMax": end.isoformat().replace("+00:00", "Z"),
            "items": [{"id": "primary"}],
        },
    )
    if response.status_code == 401:
        raise ToolError("Google Calendar authorization failed. Reconnect Google.")
    if response.status_code >= 400:
        raise ToolError(f"Could not check calendar availability: {response.text[:240]}")
    busy = response.json().get("calendars", {}).get("primary", {}).get("busy", [])
    busy_ranges = [
        (_parse_iso(item["start"]), _parse_iso(item["end"])) for item in busy if "start" in item
    ]

    slots = _find_open_slots(
        now=now,
        end=end,
        busy_ranges=busy_ranges,
        timezone_name=zone_name,
        duration_minutes=args.duration_minutes,
        workday_start_hour=args.workday_start_hour,
        workday_end_hour=args.workday_end_hour,
    )

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
    description=(
        "List Google Calendar events for today, tomorrow, an upcoming window, or an "
        "explicit custom range. Use period='today' for the user's whole local day."
    ),
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
