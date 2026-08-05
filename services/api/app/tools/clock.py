from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class CurrentTimeArgs(BaseModel):
    timezone: str | None = Field(
        default=None,
        description="IANA timezone name such as Asia/Kolkata or America/New_York.",
    )


_TIMEZONE_ALIASES = {
    "IST": "Asia/Kolkata",
    "INDIA": "Asia/Kolkata",
    "INDIAN": "Asia/Kolkata",
    "BST": "Europe/London",
    "GMT": "Etc/GMT",
    "UTC": "UTC",
}


def resolve_zone(name: str) -> ZoneInfo:
    cleaned = (name or "").strip()
    resolved = _TIMEZONE_ALIASES.get(cleaned.upper(), cleaned)
    try:
        return ZoneInfo(resolved)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ToolError(
            f"'{name}' is not a recognised timezone. Use an IANA name such as "
            "Asia/Kolkata."
        ) from error


def spoken_datetime(moment: datetime) -> str:
    clock = moment.strftime("%I:%M %p").lstrip("0")
    day = str(moment.day)
    return f"{clock} on {moment.strftime('%A')}, {day} {moment.strftime('%B %Y')}"


async def get_current_time(context: ToolContext, args: CurrentTimeArgs) -> ToolResult:
    zone_name = args.timezone or context.settings.default_timezone
    now = datetime.now(tz=resolve_zone(zone_name))

    return ToolResult(
        summary=f"It is {spoken_datetime(now)} in {zone_name}.",
        data={
            "iso": now.isoformat(),
            "timezone": zone_name,
            "utc_offset": now.strftime("%z"),
        },
    )


SPEC = ToolSpec(
    name="get_current_time",
    description=(
        "Get the current date and time in a timezone. Use this before creating "
        "reminders or answering questions about today's date."
    ),
    args_model=CurrentTimeArgs,
    handler=get_current_time,
)
