from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.tools.base import ToolError
from app.tools.calendar import _find_open_slots, _parse_iso


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
