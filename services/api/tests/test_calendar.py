from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx
import pytest

from app.services import google_oauth
from app.tools.base import ToolContext, ToolError
from app.tools.calendar import (
    CreateEventArgs,
    ListEventsArgs,
    _event_window,
    _find_open_slots,
    _parse_iso,
    create_calendar_event,
)


def test_parse_iso_interprets_naive_time_in_the_configured_timezone() -> None:
    parsed = _parse_iso("2026-08-10T09:00:00", "Asia/Kolkata")

    assert parsed == datetime(2026, 8, 10, 3, 30, tzinfo=timezone.utc)


def test_parse_iso_preserves_the_instant_of_an_offset_timestamp() -> None:
    parsed = _parse_iso("2026-08-10T09:00:00-04:00", "Asia/Kolkata")

    assert parsed == datetime(2026, 8, 10, 13, 0, tzinfo=timezone.utc)


def test_parse_iso_returns_a_tool_error_for_invalid_input() -> None:
    with pytest.raises(ToolError, match="valid ISO 8601"):
        _parse_iso("tomorrow morning", "UTC")


def test_open_slots_use_the_user_timezone_and_skip_busy_periods() -> None:
    slots = _find_open_slots(
        now=datetime(2026, 8, 10, 2, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 10, 8, 0, tzinfo=timezone.utc),
        busy_ranges=[
            (
                datetime(2026, 8, 10, 3, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
            )
        ],
        timezone_name="Asia/Kolkata",
        duration_minutes=30,
        workday_start_hour=9,
        workday_end_hour=18,
        limit=1,
    )

    assert slots == ["2026-08-10T09:30:00+05:30"]


def test_open_slots_reject_inverted_working_hours() -> None:
    with pytest.raises(ToolError, match="end after they start"):
        _find_open_slots(
            now=datetime(2026, 8, 10, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, tzinfo=timezone.utc),
            busy_ranges=[],
            timezone_name="UTC",
            duration_minutes=30,
            workday_start_hour=18,
            workday_end_hour=9,
        )


def test_today_window_uses_local_midnight_not_the_next_24_hours() -> None:
    start, end, timezone_name, label = _event_window(
        ListEventsArgs(period="today", timezone="Asia/Kolkata"),
        now=datetime(2026, 8, 9, 10, 30, tzinfo=timezone.utc),
        default_timezone="UTC",
    )

    assert timezone_name == "Asia/Kolkata"
    assert label == "today"
    assert start == datetime(2026, 8, 8, 18, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 9, 18, 30, tzinfo=timezone.utc)


def test_custom_calendar_window_rejects_missing_boundary() -> None:
    with pytest.raises(ValueError, match="custom periods require"):
        ListEventsArgs(period="custom", start_at="2026-08-10T09:00:00+05:30")


def test_open_slots_begin_at_the_next_half_hour() -> None:
    slots = _find_open_slots(
        now=datetime(2026, 8, 10, 3, 40, tzinfo=timezone.utc),
        end=datetime(2026, 8, 10, 6, 0, tzinfo=timezone.utc),
        busy_ranges=[],
        timezone_name="UTC",
        duration_minutes=30,
        workday_start_hour=0,
        workday_end_hour=23,
        limit=1,
    )

    assert slots == ["2026-08-10T04:00:00+00:00"]


async def test_ambiguous_calendar_insert_is_verified_by_stable_event_id(
    settings,
    monkeypatch,
) -> None:
    async def token(*_args, **_kwargs) -> str:
        return "token"

    async def timezone_name(*_args, **_kwargs) -> str:
        return "Asia/Kolkata"

    monkeypatch.setattr(google_oauth, "valid_access_token", token)
    monkeypatch.setattr(google_oauth, "connection_timezone", timezone_name)
    posts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            assert json.loads(request.content)["id"] == "abc123"
            return httpx.Response(503, text="ambiguous upstream response")
        if request.method == "GET" and request.url.path.endswith("/events/abc123"):
            return httpx.Response(
                200,
                json={
                    "id": "abc123",
                    "summary": "Project sync",
                    "start": {"dateTime": "2099-08-10T18:00:00+05:30"},
                },
            )
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    context = ToolContext("user", settings, http, object())  # type: ignore[arg-type]
    try:
        result = await create_calendar_event(
            context,
            CreateEventArgs(
                event_id="abc123",
                title="Project sync",
                start_at="2099-08-10T18:00:00+05:30",
                add_meet_link=False,
            ),
        )
        assert result.data["verified"] is True
        assert posts == 1
    finally:
        await http.aclose()
