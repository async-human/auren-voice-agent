from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, Field

from app.models.tables import ScheduledJob, WorkflowRun, utcnow
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class StartWorkflowArgs(BaseModel):
    goal: str = Field(min_length=3, max_length=2000)
    plan: list[str] = Field(default_factory=list, max_length=20)


class UpdateWorkflowArgs(BaseModel):
    workflow_id: str
    status: str | None = None
    current_step: int | None = Field(default=None, ge=0, le=50)
    note: str | None = Field(default=None, max_length=2000)


class CompleteWorkflowArgs(BaseModel):
    workflow_id: str
    result_summary: str = Field(min_length=1, max_length=4000)
    status: str = Field(default="completed")


class ScheduleFollowupArgs(BaseModel):
    job_type: Literal["follow_up_reminder", "email_reply_check", "custom"] = (
        "follow_up_reminder"
    )
    run_in_minutes: int = Field(default=1440, ge=1, le=60 * 24 * 30)
    message: str = Field(min_length=1, max_length=2000)
    workflow_id: str | None = None
    context: dict = Field(default_factory=dict)


async def start_workflow(context: ToolContext, args: StartWorkflowArgs) -> ToolResult:
    run = WorkflowRun(
        user_id=context.user_id,
        goal=args.goal.strip(),
        plan=args.plan,
        status="active",
        current_step=0,
        context={},
    )
    context.session.add(run)
    await context.session.commit()
    await context.session.refresh(run)
    steps = "; ".join(f"{index + 1}. {step}" for index, step in enumerate(args.plan))
    return ToolResult(
        summary=(
            f"Started workflow {run.id} for goal: {run.goal}. "
            + (f"Plan: {steps}" if steps else "No explicit plan steps yet.")
        ),
        data={"workflow_id": run.id, "goal": run.goal, "plan": args.plan},
    )


async def update_workflow(context: ToolContext, args: UpdateWorkflowArgs) -> ToolResult:
    run = await context.session.get(WorkflowRun, args.workflow_id)
    if run is None or run.user_id != context.user_id:
        raise ToolError("Workflow not found.")
    if args.status:
        run.status = args.status
    if args.current_step is not None:
        run.current_step = args.current_step
    if args.note:
        context_data = dict(run.context or {})
        notes = list(context_data.get("notes") or [])
        notes.append(args.note)
        context_data["notes"] = notes[-20:]
        run.context = context_data
    await context.session.commit()
    return ToolResult(
        summary=f"Workflow {run.id} is now {run.status} at step {run.current_step}.",
        data={"workflow_id": run.id, "status": run.status, "current_step": run.current_step},
    )


async def complete_workflow(context: ToolContext, args: CompleteWorkflowArgs) -> ToolResult:
    run = await context.session.get(WorkflowRun, args.workflow_id)
    if run is None or run.user_id != context.user_id:
        raise ToolError("Workflow not found.")
    run.status = args.status if args.status in {"completed", "failed", "cancelled"} else "completed"
    run.result_summary = args.result_summary
    await context.session.commit()
    return ToolResult(
        summary=f"Workflow {run.id} marked {run.status}: {args.result_summary}",
        data={"workflow_id": run.id, "status": run.status},
    )


async def schedule_followup(context: ToolContext, args: ScheduleFollowupArgs) -> ToolResult:
    job = ScheduledJob(
        user_id=context.user_id,
        job_type=args.job_type,
        payload={"message": args.message, **args.context},
        run_at=utcnow() + timedelta(minutes=args.run_in_minutes),
        status="scheduled",
        workflow_run_id=args.workflow_id,
    )
    context.session.add(job)
    await context.session.commit()
    await context.session.refresh(job)
    return ToolResult(
        summary=(
            f"Scheduled a {args.job_type} for {args.run_in_minutes} minutes from now. "
            f"Job id {job.id}."
        ),
        data={"job_id": job.id, "run_at": job.run_at.isoformat()},
    )


START_SPEC = ToolSpec(
    name="start_workflow",
    description=(
        "Start a durable multi-step outcome workflow. Use for actionable goals like "
        "scheduling meetings, email follow-ups, or multi-tool tasks."
    ),
    args_model=StartWorkflowArgs,
    handler=start_workflow,
)

UPDATE_SPEC = ToolSpec(
    name="update_workflow",
    description="Update workflow status/step after progress or a blocker.",
    args_model=UpdateWorkflowArgs,
    handler=update_workflow,
)

COMPLETE_SPEC = ToolSpec(
    name="complete_workflow",
    description="Mark a workflow completed/failed after verifying outcomes.",
    args_model=CompleteWorkflowArgs,
    handler=complete_workflow,
)

SCHEDULE_FOLLOWUP_SPEC = ToolSpec(
    name="schedule_followup",
    description=(
        "Schedule a background follow-up that survives the voice session "
        "(for example remind tomorrow if no reply)."
    ),
    args_model=ScheduleFollowupArgs,
    handler=schedule_followup,
)
