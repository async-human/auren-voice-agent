"""Gateway-owned consent, quiet hours, and delivery policy."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.schemas import UserSettingsResponse, UserSettingsUpdate
from app.models.tables import ScheduledJob, UserSettings, utcnow

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_zone(name: str | None, fallback: str = "UTC") -> ZoneInfo:
    for candidate in (name, fallback, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    match = _HHMM.fullmatch(value.strip())
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))


def validate_hhmm(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if parse_hhmm(cleaned) is None:
        raise ValueError("Quiet hours must use HH:MM in 24-hour time, for example 22:00.")
    return cleaned


def in_quiet_hours(now: datetime, row: UserSettings) -> bool:
    start = parse_hhmm(row.quiet_hours_start)
    end = parse_hhmm(row.quiet_hours_end)
    if start is None or end is None:
        return False
    local = now.astimezone(parse_zone(row.timezone_name))
    current = local.replace(second=0, microsecond=0).time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def local_day_start(now: datetime, row: UserSettings) -> datetime:
    zone = parse_zone(row.timezone_name)
    local = now.astimezone(zone)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def next_local_hour(now: datetime, timezone_name: str, hour: int) -> datetime:
    zone = parse_zone(timezone_name)
    local = now.astimezone(zone).replace(minute=0, second=0, microsecond=0)
    candidate = local.replace(hour=hour)
    if candidate <= now.astimezone(zone):
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def to_response(row: UserSettings, *, pod_wake_available: bool) -> UserSettingsResponse:
    return UserSettingsResponse(
        timezone_name=row.timezone_name,
        quiet_hours_start=row.quiet_hours_start,
        quiet_hours_end=row.quiet_hours_end,
        max_interruptions_per_day=row.max_interruptions_per_day,
        spoken_delivery_enabled=row.spoken_delivery_enabled,
        pod_wake_enabled=row.pod_wake_enabled,
        pod_wake_available=pod_wake_available,
        autonomous_memory_enabled=row.autonomous_memory_enabled,
        semantic_memory_enabled=row.semantic_memory_enabled,
        procedural_memory_enabled=row.procedural_memory_enabled,
        prospective_memory_enabled=row.prospective_memory_enabled,
        morning_brief_enabled=row.morning_brief_enabled,
        morning_brief_hour=row.morning_brief_hour,
    )


async def get_or_create(
    session: AsyncSession, user_id: str, *, default_timezone: str = "UTC"
) -> UserSettings:
    row = await session.get(UserSettings, user_id)
    if row is not None:
        return row
    row = UserSettings(user_id=user_id, timezone_name=default_timezone or "UTC")
    session.add(row)
    await session.flush()
    return row


async def update_settings(
    session: AsyncSession,
    user_id: str,
    payload: UserSettingsUpdate,
    api_settings: Settings,
) -> UserSettings:
    row = await get_or_create(
        session, user_id, default_timezone=api_settings.default_timezone
    )
    data = payload.model_dump(exclude_unset=True)
    if "quiet_hours_start" in data:
        data["quiet_hours_start"] = validate_hhmm(data["quiet_hours_start"])
    if "quiet_hours_end" in data:
        data["quiet_hours_end"] = validate_hhmm(data["quiet_hours_end"])
    if "timezone_name" in data and data["timezone_name"]:
        parse_zone(data["timezone_name"])
        data["timezone_name"] = data["timezone_name"].strip()
    for key, value in data.items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    await sync_morning_brief_job(session, row)
    await session.commit()
    await session.refresh(row)
    return row


async def sync_morning_brief_job(session: AsyncSession, row: UserSettings) -> None:
    existing = (
        await session.scalars(
            select(ScheduledJob).where(
                ScheduledJob.user_id == row.user_id,
                ScheduledJob.job_type == "morning_brief",
                ScheduledJob.status == "scheduled",
            )
        )
    ).all()
    if not row.morning_brief_enabled:
        for job in existing:
            job.status = "cancelled"
        return
    if existing:
        return
    session.add(
        ScheduledJob(
            user_id=row.user_id,
            job_type="morning_brief",
            payload={"message": "Morning brief"},
            run_at=next_local_hour(utcnow(), row.timezone_name, row.morning_brief_hour),
            status="scheduled",
        )
    )
