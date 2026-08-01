from __future__ import annotations

from pydantic import ValidationError

from app.tools import clock, notes, reminders, weather, web_search
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec

SPECS: tuple[ToolSpec, ...] = (
    clock.SPEC,
    weather.SPEC,
    reminders.CREATE_SPEC,
    reminders.LIST_SPEC,
    notes.SAVE_SPEC,
    notes.SEARCH_SPEC,
    web_search.SPEC,
)

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in SPECS}


def get_spec(name: str) -> ToolSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool '{name}'")
    return spec


async def invoke(context: ToolContext, name: str, arguments: dict) -> ToolResult:
    spec = get_spec(name)
    try:
        args = spec.args_model.model_validate(arguments)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc']) or 'input'}: {issue['msg']}"
            for issue in error.errors()
        )
        raise ToolError(f"Invalid arguments for {name}. {problems}") from error

    return await spec.handler(context, args)
