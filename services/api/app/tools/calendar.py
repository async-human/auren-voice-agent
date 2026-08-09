from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import google_oauth
from app.services.google_http import request as google_request
from app.tools.base import ActionProposal, ToolContext, ToolError, ToolResult, ToolSpec
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


class UpdateEventArgs(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    start_at: str | None = Field(default=None, description="New ISO 8601 start time")
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    attendees: list[str] | None = None
    description: str | None = Field(default=None, max_length=8000)
    timezone: str | None = None

    @field_validator("attendees")
    @classmethod
    def _validate_attendees(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return CreateEventArgs._validate_attendees(values)

    @model_validator(mode="after")
    def _has_change(self) -> UpdateEventArgs:
        editable = {"title", "start_at", "duration_minutes", "attendees", "description"}
        if not (self.model_fields_set & editable) and "_approval_snapshot" not in (
            self.model_extra or {}
        ):
            raise ValueError("at least one event change is required")
        return self


class DeleteEventArgs(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=1024)
    delete_series: bool = Field(
        default=False,
        description="For a recurring occurrence, delete the entire series instead.",
    )
    notify_attendees: bool = Field(
        default=True,
        description="Send cancellation updates when the event has attendees.",
    )


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
    return parsed.astimezone(UTC)


def _calendar_error(response: httpx.Response, operation: str) -> None:
    if response.status_code == 401:
        raise ToolError("Google Calendar authorization failed. Reconnect Google.")
    if response.status_code == 403:
        raise ToolError(f"Google Calendar denied permission to {operation}. Reconnect Google.")
    if response.status_code == 404:
        raise ToolError("That calendar event no longer exists.")
    if response.status_code >= 400:
        raise ToolError(f"Google Calendar could not {operation}: {response.text[:240]}")


async def _calendar_token(context: ToolContext) -> str:
    return await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )


async def _event_timezone(context: ToolContext, requested: str | None) -> str:
    if requested:
        resolve_zone(requested)
        return requested
    return await google_oauth.connection_timezone(
        context.session,
        context.user_id,
        default=context.settings.default_timezone,
    )


async def _fetch_event(
    context: ToolContext,
    event_id: str,
    *,
    token: str | None = None,
) -> dict[str, Any]:
    access_token = token or await _calendar_token(context)
    response = await google_request(
        context.http,
        "GET",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    _calendar_error(response, "read the event")
    payload = response.json()
    if payload.get("status") == "cancelled":
        raise ToolError("That calendar event has already been cancelled.")
    return payload


def _event_start(event: dict[str, Any]) -> str:
    start = event.get("start") or {}
    return str(start.get("dateTime") or start.get("date") or "unknown time")


def _event_end(event: dict[str, Any]) -> str:
    end = event.get("end") or {}
    return str(end.get("dateTime") or end.get("date") or "unknown time")


def _event_attendees(event: dict[str, Any]) -> list[str]:
    return [
        str(item.get("email"))
        for item in event.get("attendees", []) or []
        if isinstance(item, dict) and item.get("email")
    ]


def _readable_time(value: str, timezone_name: str) -> str:
    if "T" not in value:
        return f"{value} (all day)"
    local = _parse_iso(value, timezone_name).astimezone(resolve_zone(timezone_name))
    return local.strftime("%d %B %Y at %I:%M %p %Z")


def _snapshot(args: BaseModel) -> dict[str, Any]:
    extra = args.model_extra or {}
    value = extra.get("_approval_snapshot")
    if not isinstance(value, dict):
        raise ToolError("This action is missing its verified approval snapshot. Prepare it again.")
    return value


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
            slot_start_utc = cursor.astimezone(UTC)
            slot_end_utc = slot_end.astimezone(UTC)
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
        start_local.astimezone(UTC),
        end_local.astimezone(UTC),
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
    now = datetime.now(tz=UTC)
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


async def prepare_create_calendar_event(
    context: ToolContext, args: CreateEventArgs
) -> ActionProposal:
    zone_name = await _event_timezone(context, args.timezone)
    zone = resolve_zone(zone_name)
    start = _parse_iso(args.start_at, zone_name)
    if start < datetime.now(tz=UTC) - timedelta(minutes=5):
        raise ToolError("Calendar events cannot be created in the past. Confirm the intended date.")
    normalized = args.model_dump()
    normalized["start_at"] = start.astimezone(zone).isoformat()
    normalized["timezone"] = zone_name
    attendees = f" with {', '.join(args.attendees)}" if args.attendees else ""
    meet = " and add Google Meet" if args.add_meet_link else ""
    preview = (
        f"Create calendar event '{args.title}' on "
        f"{_readable_time(normalized['start_at'], zone_name)} for "
        f"{args.duration_minutes} minutes{attendees}{meet}."
    )
    return ActionProposal(
        arguments=normalized,
        preview=preview,
        idempotency_key=(
            "calendar-create:"
            + hashlib.sha256(args.event_id.encode("utf-8")).hexdigest()
        ),
    )


async def prepare_update_calendar_event(
    context: ToolContext, args: UpdateEventArgs
) -> ActionProposal:
    token = await _calendar_token(context)
    current = await _fetch_event(context, args.event_id, token=token)
    timezone_name = await _event_timezone(
        context,
        args.timezone or (current.get("start") or {}).get("timeZone"),
    )
    patch: dict[str, Any] = {}
    changes: list[str] = []
    fields = args.model_fields_set

    if "title" in fields and args.title is not None:
        patch["summary"] = args.title
        changes.append(f"title to '{args.title}'")
    if "description" in fields:
        patch["description"] = args.description or ""
        changes.append("description")
    if "attendees" in fields:
        attendees = args.attendees or []
        patch["attendees"] = [{"email": email} for email in attendees]
        changes.append(
            "attendees to " + (", ".join(attendees) if attendees else "none")
        )

    if "start_at" in fields or "duration_minutes" in fields:
        current_start_text = _event_start(current)
        current_end_text = _event_end(current)
        if "T" not in current_start_text or "T" not in current_end_text:
            raise ToolError("Changing the time of all-day events is not supported yet.")
        start = (
            _parse_iso(args.start_at, timezone_name)
            if args.start_at
            else _parse_iso(current_start_text, timezone_name)
        )
        if start < datetime.now(tz=UTC) - timedelta(minutes=5):
            raise ToolError("Calendar events cannot be moved into the past.")
        current_duration = _parse_iso(current_end_text, timezone_name) - _parse_iso(
            current_start_text, timezone_name
        )
        duration = timedelta(minutes=args.duration_minutes) if args.duration_minutes else current_duration
        if duration <= timedelta(0) or duration > timedelta(hours=8):
            raise ToolError("The updated event duration must be between 5 minutes and 8 hours.")
        zone = resolve_zone(timezone_name)
        end = start + duration
        patch["start"] = {
            "dateTime": start.astimezone(zone).isoformat(),
            "timeZone": timezone_name,
        }
        patch["end"] = {
            "dateTime": end.astimezone(zone).isoformat(),
            "timeZone": timezone_name,
        }
        changes.append(
            "time to "
            + _readable_time(patch["start"]["dateTime"], timezone_name)
            + f" for {round(duration.total_seconds() / 60)} minutes"
        )

    if not patch:
        raise ToolError("No calendar event changes were provided.")

    snapshot = {
        "event_id": args.event_id,
        "etag": current.get("etag"),
        "original_title": current.get("summary") or "Untitled",
        "original_start": _event_start(current),
        "patch": patch,
        "timezone": timezone_name,
    }
    arguments = args.model_dump()
    arguments["_approval_snapshot"] = snapshot
    preview = (
        f"Update calendar event '{snapshot['original_title']}' at "
        f"{_readable_time(snapshot['original_start'], timezone_name)}: "
        + "; ".join(changes)
        + "."
    )
    digest = hashlib.sha256(
        json.dumps(patch, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ActionProposal(
        arguments=arguments,
        preview=preview,
        idempotency_key=(
            "calendar-update:"
            + hashlib.sha256(
                f"{args.event_id}:{current.get('etag')}:{digest}".encode()
            ).hexdigest()
        ),
    )


async def prepare_delete_calendar_event(
    context: ToolContext, args: DeleteEventArgs
) -> ActionProposal:
    token = await _calendar_token(context)
    selected = await _fetch_event(context, args.event_id, token=token)
    target_id = args.event_id
    is_series = False
    recurring_id = selected.get("recurringEventId")
    if args.delete_series and recurring_id:
        target_id = str(recurring_id)
        selected = await _fetch_event(context, target_id, token=token)
        is_series = True
    elif args.delete_series and selected.get("recurrence"):
        is_series = True

    timezone_name = await _event_timezone(
        context, (selected.get("start") or {}).get("timeZone")
    )
    snapshot = {
        "event_id": target_id,
        "etag": selected.get("etag"),
        "title": selected.get("summary") or "Untitled",
        "start": _event_start(selected),
        "attendees": _event_attendees(selected),
        "is_series": is_series,
    }
    arguments = args.model_dump()
    arguments["event_id"] = target_id
    arguments["_approval_snapshot"] = snapshot
    scope = "entire recurring series" if is_series else "calendar event"
    notify = " Attendees will be notified." if snapshot["attendees"] and args.notify_attendees else ""
    preview = (
        f"Delete {scope} '{snapshot['title']}' at "
        f"{_readable_time(snapshot['start'], timezone_name)}.{notify}"
    )
    return ActionProposal(
        arguments=arguments,
        preview=preview,
        idempotency_key=(
            "calendar-delete:"
            + hashlib.sha256(
                f"{target_id}:{selected.get('etag')}:{int(args.notify_attendees)}".encode()
            ).hexdigest()
        ),
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
    if start < datetime.now(tz=UTC) - timedelta(minutes=5):
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


def _event_matches_patch(event: dict[str, Any], patch: dict[str, Any]) -> bool:
    for key, expected in patch.items():
        if key == "attendees":
            actual = set(_event_attendees(event))
            wanted = {
                str(item.get("email"))
                for item in expected
                if isinstance(item, dict) and item.get("email")
            }
            if actual != wanted:
                return False
        elif key in {"start", "end"}:
            actual_time = (event.get(key) or {}).get("dateTime")
            expected_time = (expected or {}).get("dateTime")
            if not actual_time or not expected_time:
                return False
            if _parse_iso(actual_time) != _parse_iso(expected_time):
                return False
        elif event.get(key, "") != expected:
            return False
    return True


async def update_calendar_event(context: ToolContext, args: UpdateEventArgs) -> ToolResult:
    snapshot = _snapshot(args)
    event_id = str(snapshot.get("event_id") or args.event_id)
    expected_etag = snapshot.get("etag")
    patch = snapshot.get("patch")
    if not isinstance(patch, dict) or not patch:
        raise ToolError("The approved calendar update is incomplete. Prepare it again.")

    token = await _calendar_token(context)
    current = await _fetch_event(context, event_id, token=token)
    if expected_etag and current.get("etag") != expected_etag:
        raise ToolError(
            "The calendar event changed after you reviewed it. Please review and approve it again."
        )

    response: httpx.Response | None = None
    try:
        response = await google_request(
            context.http,
            "PATCH",
            f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
            params={"sendUpdates": "all" if "attendees" in patch else "none"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                **({"If-Match": str(expected_etag)} if expected_etag else {}),
            },
            json=patch,
        )
    except httpx.TransportError:
        # The write may have reached Google. Verify state before reporting an
        # ambiguous result and never issue a second PATCH automatically.
        pass
    if response is not None:
        if response.status_code == 412:
            raise ToolError(
                "The calendar event changed after you reviewed it. Please review and approve it again."
            )
        if response.status_code < 500:
            _calendar_error(response, "update the event")
    verified = await _fetch_event(context, event_id, token=token)
    if not _event_matches_patch(verified, patch):
        if response is None or response.status_code >= 500:
            raise ToolError(
                "Google returned an ambiguous calendar update result. I did not retry; "
                "check the event before trying again."
            )
        raise ToolError("Calendar reported success, but the updated event could not be verified.")
    return ToolResult(
        summary=(
            f"Verified calendar event '{verified.get('summary') or 'Untitled'}' was updated."
        ),
        data={
            "id": event_id,
            "htmlLink": verified.get("htmlLink"),
            "verified": True,
        },
    )


async def delete_calendar_event(context: ToolContext, args: DeleteEventArgs) -> ToolResult:
    snapshot = _snapshot(args)
    event_id = str(snapshot.get("event_id") or args.event_id)
    expected_etag = snapshot.get("etag")
    token = await _calendar_token(context)

    current_response = await google_request(
        context.http,
        "GET",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if current_response.status_code in {404, 410}:
        return ToolResult(
            summary=f"Verified calendar event '{snapshot.get('title') or 'Untitled'}' is already deleted.",
            data={"id": event_id, "verified": True, "already_deleted": True},
        )
    _calendar_error(current_response, "read the event before deletion")
    current = current_response.json()
    if expected_etag and current.get("etag") != expected_etag:
        raise ToolError(
            "The calendar event changed after you reviewed it. Please review and approve deletion again."
        )

    attendees = snapshot.get("attendees") or []
    response = await google_request(
        context.http,
        "DELETE",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        params={
            "sendUpdates": "all" if attendees and args.notify_attendees else "none"
        },
        headers={
            "Authorization": f"Bearer {token}",
            **({"If-Match": str(expected_etag)} if expected_etag else {}),
        },
    )
    if response.status_code == 412:
        raise ToolError(
            "The calendar event changed after you reviewed it. Please review and approve deletion again."
        )
    if response.status_code not in {200, 204, 404, 410}:
        _calendar_error(response, "delete the event")

    verify = await google_request(
        context.http,
        "GET",
        f"https://www.googleapis.com/calendar/v3/calendars/primary/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if verify.status_code not in {404, 410} and not (
        verify.status_code == 200 and verify.json().get("status") == "cancelled"
    ):
        raise ToolError("Calendar deletion was not verified.")
    title = str(snapshot.get("title") or "Untitled")
    scope = "recurring series" if snapshot.get("is_series") else "event"
    return ToolResult(
        summary=f"Verified calendar {scope} '{title}' was deleted.",
        data={"id": event_id, "verified": True, "deleted": True},
    )


async def find_free_slots(context: ToolContext, args: FindFreeSlotsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    now = datetime.now(tz=UTC)
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
    prepare=prepare_create_calendar_event,
)

UPDATE_SPEC = ToolSpec(
    name="update_calendar_event",
    description=(
        "Update an existing Google Calendar event selected by event_id. First list events "
        "to resolve the exact id. Requires confirmation bound to the current event version."
    ),
    args_model=UpdateEventArgs,
    handler=update_calendar_event,
    confirmation_required=True,
    prepare=prepare_update_calendar_event,
)

DELETE_SPEC = ToolSpec(
    name="delete_calendar_event",
    description=(
        "Delete an existing Google Calendar event selected by event_id. First list events "
        "and clarify one occurrence versus the whole recurring series. Requires confirmation."
    ),
    args_model=DeleteEventArgs,
    handler=delete_calendar_event,
    confirmation_required=True,
    prepare=prepare_delete_calendar_event,
)

FREE_SPEC = ToolSpec(
    name="find_free_slots",
    description="Find free slots on the user's Google Calendar in working hours.",
    args_model=FindFreeSlotsArgs,
    handler=find_free_slots,
)
