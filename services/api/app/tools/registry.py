from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.services import approvals
from app.services.action_payloads import (
    protect_arguments,
    redact_arguments,
    redact_details,
)
from app.tools import (
    actions,
    artifacts,
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
from app.tools.base import CapabilityMeta, ToolContext, ToolError, ToolResult, ToolSpec

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
    calendar.UPDATE_SPEC,
    calendar.DELETE_SPEC,
    email_tools.SEARCH_SPEC,
    email_tools.READ_SPEC,
    email_tools.TRASH_SPEC,
    email_tools.DRAFT_SPEC,
    email_tools.SEND_SPEC,
    actions.LIST_PENDING_SPEC,
    actions.CONFIRM_SPEC,
    actions.REJECT_SPEC,
    workflows.START_SPEC,
    workflows.UPDATE_SPEC,
    workflows.COMPLETE_SPEC,
    workflows.SCHEDULE_FOLLOWUP_SPEC,
    artifacts.CREATE_DOCUMENT_SPEC,
    artifacts.CREATE_SPREADSHEET_SPEC,
    artifacts.CREATE_PRESENTATION_SPEC,
    artifacts.LIST_SPEC,
)

REGISTRY: dict[str, ToolSpec] = {spec.name: spec for spec in SPECS}

CAPABILITIES: dict[str, CapabilityMeta] = {
    "get_current_time": CapabilityMeta("time", "read_current_time", "read", True, True),
    "get_weather": CapabilityMeta("weather", "read_forecast", "read", True, True),
    "create_reminder": CapabilityMeta("reminders", "create", "write", True, False),
    "list_reminders": CapabilityMeta("reminders", "list", "read", True, True),
    "save_note": CapabilityMeta("notes", "create", "write", True, False),
    "search_notes": CapabilityMeta("notes", "search", "read", True, True),
    "search_web": CapabilityMeta(
        "research", "search_web", "read", True, True, requires_connection="web_search"
    ),
    "check_tool_status": CapabilityMeta("system", "check_health", "read", True, True),
    "get_page_context": CapabilityMeta(
        "browser", "read_page", "read", True, True, requires_connection="browser_extension"
    ),
    "recall": CapabilityMeta("memory", "search", "read", True, True),
    "remember": CapabilityMeta("memory", "create", "write", True, False),
    "forget": CapabilityMeta("memory", "delete", "destructive", False, False),
    "list_calendar_events": CapabilityMeta(
        "calendar", "list_events", "read", True, True, requires_connection="google"
    ),
    "find_free_slots": CapabilityMeta(
        "calendar", "find_availability", "read", True, True, requires_connection="google"
    ),
    "create_calendar_event": CapabilityMeta(
        "calendar", "create_event", "consequential", True, False, "google"
    ),
    "update_calendar_event": CapabilityMeta(
        "calendar", "update_event", "consequential", True, False, "google"
    ),
    "delete_calendar_event": CapabilityMeta(
        "calendar", "delete_event", "destructive", False, False, "google"
    ),
    "search_emails": CapabilityMeta(
        "gmail", "search_messages", "read", True, True, "google"
    ),
    "read_email": CapabilityMeta("gmail", "read_message", "read", True, True, "google"),
    "trash_email": CapabilityMeta(
        "gmail", "move_to_trash", "consequential", True, False, "google"
    ),
    "draft_email": CapabilityMeta(
        "gmail", "create_draft", "write", True, False, "google"
    ),
    "send_email": CapabilityMeta(
        "gmail", "send_message", "consequential", False, False, "google"
    ),
    "list_pending_actions": CapabilityMeta("approvals", "list", "read", True, True),
    "confirm_pending_action": CapabilityMeta(
        "approvals", "confirm", "consequential", False, False
    ),
    "reject_pending_action": CapabilityMeta("approvals", "reject", "write", True, False),
    "start_workflow": CapabilityMeta("workflows", "create", "write", True, False),
    "update_workflow": CapabilityMeta("workflows", "update", "write", True, False),
    "complete_workflow": CapabilityMeta("workflows", "complete", "write", True, False),
    "schedule_followup": CapabilityMeta("workflows", "schedule", "write", True, False),
    "create_document": CapabilityMeta(
        "artifacts",
        "create_document",
        "write",
        True,
        True,
        "artifact_storage",
        ("docx", "pdf", "md", "html", "txt"),
    ),
    "create_spreadsheet": CapabilityMeta(
        "artifacts",
        "create_spreadsheet",
        "write",
        True,
        True,
        "artifact_storage",
        ("xlsx", "csv"),
    ),
    "create_presentation": CapabilityMeta(
        "artifacts",
        "create_presentation",
        "write",
        True,
        True,
        "artifact_storage",
        ("pptx",),
    ),
    "list_artifacts": CapabilityMeta("artifacts", "list", "read", True, True),
}

if set(CAPABILITIES) != set(REGISTRY):
    missing = sorted(set(REGISTRY) - set(CAPABILITIES))
    extra = sorted(set(CAPABILITIES) - set(REGISTRY))
    raise RuntimeError(f"Capability catalog mismatch. Missing={missing}; extra={extra}")


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
) -> ToolResult:
    spec = get_spec(name)

    if spec.confirmation_required:
        try:
            args = spec.args_model.model_validate(arguments)
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in issue['loc']) or 'input'}: {issue['msg']}"
                for issue in error.errors()
            )
            raise ToolError(f"Invalid arguments for {name}. {problems}") from error

        if spec.prepare is not None:
            proposal = await spec.prepare(context, args)
            raw_arguments = proposal.arguments
            preview = proposal.preview
            audit_preview = proposal.audit_preview or preview
            effective_idempotency_key = idempotency_key or proposal.idempotency_key
        else:
            raw_arguments = args.model_dump()
            preview = build_preview(name, raw_arguments)
            audit_preview = build_audit_preview(name, raw_arguments)
            effective_idempotency_key = idempotency_key
        stored_arguments = protect_arguments(context.settings, name, raw_arguments)
        action = await approvals.propose_action(
            context.session,
            user_id=context.user_id,
            tool=name,
            arguments=stored_arguments,
            preview=audit_preview,
            idempotency_key=effective_idempotency_key,
        )
        return ToolResult(
            summary=(
                f"I prepared this but need your confirmation before doing it: {preview} "
                "Say 'confirm' to proceed or 'cancel' to reject."
            ),
            data={
                "pending": True,
                "action_id": action.id,
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
        arguments=redact_arguments(name, arguments),
        summary=result.summary,
        details=redact_details(name, result.data),
    )
    return result


def build_preview(tool: str, arguments: dict[str, Any]) -> str:
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
        body = arguments.get("body") or ""
        attachments = arguments.get("artifact_ids") or []
        return (
            f"Send email to {arguments.get('to')} subject '{arguments.get('subject')}'. "
            f"Attachments: {len(attachments)}. Body: {body}"
        )
    return f"Run {tool} with {arguments}"


def build_audit_preview(tool: str, arguments: dict[str, Any]) -> str:
    if tool == "send_email":
        return (
            f"Send email to {arguments.get('to')} subject "
            f"'{arguments.get('subject')}'. Body encrypted; review in authenticated UI."
        )
    return build_preview(tool, arguments)
