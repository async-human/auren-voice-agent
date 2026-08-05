from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.services import approvals
from app.tools import (
    actions,
    calendar,
    clock,
    email_tools,
    memory,
    notes,
    page_context,
    reminders,
    weather,
    web_search,
    workflows,
)
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec

SPECS: tuple[ToolSpec, ...] = (
    clock.SPEC,
    weather.SPEC,
    reminders.CREATE_SPEC,
    reminders.LIST_SPEC,
    notes.SAVE_SPEC,
    notes.SEARCH_SPEC,
    web_search.SPEC,
    web_search.STATUS_SPEC,
    page_context.SPEC,
    memory.RECALL_SPEC,
    memory.REMEMBER_SPEC,
    memory.FORGET_SPEC,
    calendar.LIST_SPEC,
    calendar.FREE_SPEC,
    calendar.CREATE_SPEC,
    email_tools.SEARCH_SPEC,
    email_tools.DRAFT_SPEC,
    email_tools.SEND_SPEC,
    actions.LIST_PENDING_SPEC,
    actions.CONFIRM_SPEC,
    actions.REJECT_SPEC,
    workflows.START_SPEC,
    workflows.UPDATE_SPEC,
    workflows.COMPLETE_SPEC,
    workflows.SCHEDULE_FOLLOWUP_SPEC,
)

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in SPECS}


def get_spec(name: str) -> ToolSpec:
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool '{name}'")
    return spec


async def execute_handler(
    context: ToolContext, name: str, arguments: dict[str, Any]
) -> ToolResult:
    """Run a tool handler without the confirmation gate (post-approval)."""
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


async def invoke(
    context: ToolContext,
    name: str,
    arguments: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    confirmed: bool = False,
) -> ToolResult:
    spec = get_spec(name)

    if spec.confirmation_required and not confirmed:
        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc']) or 'input'}: {issue['msg']}"
                for issue in error.errors()
            )
            raise ToolError(f"Invalid arguments for {name}. {problems}") from error

        preview = _build_preview(name, args.model_dump())
        action = await approvals.propose_action(
            context.session,
            user_id=context.user_id,
            tool=name,
            arguments=args.model_dump(),
            preview=preview,
            idempotency_key=idempotency_key,
        )
        return ToolResult(
            summary=(
                f"I prepared this but need your confirmation before doing it: {preview} "
                "Say 'confirm' to proceed or 'cancel' to reject."
            ),
            data={
                "pending": True,
                "action_id": action.id,
                "confirm_token": action.confirm_token,
                "tool": name,
                "preview": preview,
            },
        )

    result = await execute_handler(context, name, arguments)
    await approvals.record_audit(
        context.session,
        user_id=context.user_id,
        tool=name,
        event_type="executed",
        actor="agent",
        arguments=arguments,
        summary=result.summary,
        details=result.data,
    )
    return result


def _build_preview(tool: str, arguments: dict[str, Any]) -> str:
    if tool == "create_calendar_event":
        attendees = arguments.get("attendees") or []
        who = f" with {', '.join(attendees)}" if attendees else ""
        meet = " plus Google Meet" if arguments.get("add_meet_link", True) else ""
        return (
            f"Create calendar event '{arguments.get('title')}' at "
            f"{arguments.get('start_at')} for {arguments.get('duration_minutes', 30)} minutes"
            f"{who}{meet}."
        )
    if tool == "send_email":
        body = (arguments.get("body") or "")[:280]
        return (
            f"Send email to {arguments.get('to')} subject '{arguments.get('subject')}'. "
            f"Body: {body}"
        )
    return f"Run {tool} with {arguments}"
