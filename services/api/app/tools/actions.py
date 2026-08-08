from __future__ import annotations

from pydantic import BaseModel, Field

from app.services import approvals
from app.tools import registry
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class ConfirmArgs(BaseModel):
    action_id: str | None = Field(
        default=None,
        description="Pending action id. Omit to confirm the latest pending action.",
    )


class RejectArgs(BaseModel):
    action_id: str | None = None
    reason: str | None = Field(default=None, max_length=500)


class ListPendingArgs(BaseModel):
    limit: int = Field(default=5, ge=1, le=20)


async def execute_pending_action(
    context: ToolContext,
    *,
    action_id: str | None = None,
    actor: str = "user",
) -> ToolResult:
    action = await approvals.claim_pending(
        context.session,
        context.user_id,
        action_id=action_id,
        actor=actor,
    )
    if action is None:
        raise ToolError(
            "There is no pending action to confirm, or it is already being processed."
        )

    try:
        result = await registry.execute_handler(context, action.tool, action.arguments)
    except Exception as error:  # noqa: BLE001
        await approvals.resolve_action(
            context.session,
            action,
            status="failed",
            actor="system",
            result_summary=str(error),
        )
        raise ToolError(f"Confirmed, but execution failed: {error}") from error

    await approvals.resolve_action(
        context.session,
        action,
        status="executed",
        actor="system",
        result_summary=result.summary,
    )
    await approvals.record_audit(
        context.session,
        user_id=context.user_id,
        tool=action.tool,
        event_type="verified",
        actor="system",
        pending_action_id=action.id,
        summary=result.summary,
        details={"verified": bool((result.data or {}).get("verified", True))},
    )
    return ToolResult(
        summary=f"Confirmed and completed: {result.summary}",
        data={"action_id": action.id, "tool": action.tool, **(result.data or {})},
    )


async def confirm_pending_action(context: ToolContext, args: ConfirmArgs) -> ToolResult:
    return await execute_pending_action(
        context,
        action_id=args.action_id,
        actor="user",
    )

async def reject_pending_action(context: ToolContext, args: RejectArgs) -> ToolResult:
    action = await approvals.get_pending(
        context.session, context.user_id, action_id=args.action_id
    )
    if action is None:
        raise ToolError("There is no pending action to reject.")
    await approvals.resolve_action(
        context.session,
        action,
        status="rejected",
        actor="user",
        result_summary=args.reason or "User rejected",
    )
    return ToolResult(
        summary=f"Cancelled pending {action.tool}.",
        data={"action_id": action.id, "tool": action.tool},
    )


async def list_pending_actions(context: ToolContext, args: ListPendingArgs) -> ToolResult:
    rows = await approvals.list_pending(context.session, context.user_id, limit=args.limit)
    if not rows:
        return ToolResult(summary="No actions waiting for confirmation.", data={"actions": []})
    spoken = "; ".join(f"{row.tool}: {row.preview[:120]}" for row in rows)
    return ToolResult(
        summary=f"{len(rows)} pending confirmation(s). {spoken}",
        data={
            "actions": [
                {
                    "id": row.id,
                    "tool": row.tool,
                    "preview": row.preview,
                    "confirm_token": row.confirm_token,
                    "expires_at": row.expires_at.isoformat() if row.expires_at else None,
                }
                for row in rows
            ]
        },
    )


CONFIRM_SPEC = ToolSpec(
    name="confirm_pending_action",
    description=(
        "Confirm and execute the latest pending consequential action "
        "(or a specific action_id). Use when the user says yes/confirm/go ahead."
    ),
    args_model=ConfirmArgs,
    handler=confirm_pending_action,
)

REJECT_SPEC = ToolSpec(
    name="reject_pending_action",
    description="Reject a pending consequential action when the user says no/cancel.",
    args_model=RejectArgs,
    handler=reject_pending_action,
)

LIST_PENDING_SPEC = ToolSpec(
    name="list_pending_actions",
    description="List actions waiting for the user's confirmation.",
    args_model=ListPendingArgs,
    handler=list_pending_actions,
)
