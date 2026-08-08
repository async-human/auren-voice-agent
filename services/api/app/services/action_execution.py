from __future__ import annotations

from app.services import approvals
from app.tools import registry
from app.tools.base import ToolContext, ToolError, ToolResult


async def execute_pending_action(
    context: ToolContext,
    *,
    action_id: str | None = None,
    actor: str = "user",
) -> ToolResult:
    """Claim and execute a confirmed consequential action exactly once."""
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
