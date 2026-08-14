"""Voice tools backed by the Railway tool gateway.

The worker deliberately owns no business logic: every tool forwards to the
FastAPI gateway, which holds the database, credentials and audit trail. That
keeps the GPU pod a replaceable inference runtime.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from livekit.agents import ToolError, function_tool

logger = logging.getLogger("auren.tools")
ToolEventValue = Any
ToolEventHandler = Callable[[dict[str, ToolEventValue]], Awaitable[None]]

_PUBLIC_TEXT_LIMIT = 280

_TOOL_DISPLAY_NAMES = {
    "get_current_time": "Current time",
    "get_weather": "Weather",
    "create_reminder": "Create reminder",
    "list_reminders": "Reminders",
    "save_note": "Save note",
    "search_notes": "Search notes",
    "search_web": "Web search",
    "get_page_context": "Page reader",
    "list_calendar_events": "Calendar search",
    "find_free_slots": "Availability search",
    "create_calendar_event": "Create calendar event",
    "update_calendar_event": "Update calendar event",
    "delete_calendar_event": "Delete calendar event",
    "search_emails": "Email search",
    "read_email": "Read email",
    "trash_email": "Move email to Trash",
    "draft_email": "Create email draft",
    "send_email": "Send email",
    "create_document": "Create document",
    "create_spreadsheet": "Create spreadsheet",
    "create_presentation": "Create presentation",
    "list_artifacts": "Generated files",
    "confirm_pending_action": "Confirm approved action",
    "reject_pending_action": "Reject pending action",
    "list_pending_actions": "Pending actions",
    "start_workflow": "Plan workflow",
    "update_workflow": "Update workflow",
    "complete_workflow": "Complete workflow",
    "schedule_followup": "Schedule follow-up",
    "check_tool_status": "Tool health",
    "recall": "Recall memory",
    "remember": "Save memory",
    "forget": "Forget memory",
}


def _public_text(value: object, *, limit: int = _PUBLIC_TEXT_LIMIT) -> str:
    """Normalize text sent to the UI and keep data-channel packets compact."""
    cleaned = " ".join(str(value or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _decision_summary(tool: str, arguments: dict[str, Any]) -> str:
    """Return a concise action rationale, never private model reasoning."""
    templates = {
        "get_current_time": "Resolving the current date and timezone before interpreting relative time.",
        "get_weather": "Checking live weather data for the requested location.",
        "create_reminder": "Turning the request into a durable reminder with an exact due time.",
        "list_reminders": "Checking saved reminders before answering or taking follow-up action.",
        "save_note": "Saving the requested information so it can be recalled later.",
        "search_notes": "Searching saved notes for context relevant to this request.",
        "search_web": "Searching current web sources because the answer needs live information.",
        "get_page_context": "Reading the shared page before explaining or acting on its content.",
        "list_calendar_events": "Checking the calendar first to resolve the exact events and times.",
        "find_free_slots": "Comparing calendar availability to identify suitable open times.",
        "create_calendar_event": "Preparing the exact event details before requesting permission to create it.",
        "update_calendar_event": "Preparing a version-bound calendar change so a stale approval cannot modify the wrong event.",
        "delete_calendar_event": "Preparing a guarded deletion for the exact event before requesting approval.",
        "search_emails": "Searching Gmail first to resolve the exact messages that match the request.",
        "read_email": "Opening the selected Gmail message so the response is based on its actual content.",
        "trash_email": "Preparing to move the exact message to Trash, pending explicit approval.",
        "draft_email": "Creating a reviewable Gmail draft before anything can be sent.",
        "send_email": "Preparing the exact recipient, subject, and content for approval before sending.",
        "create_document": "Turning the completed content into a polished, downloadable document.",
        "create_spreadsheet": "Structuring the requested data into a safe, downloadable spreadsheet.",
        "create_presentation": "Turning the completed narrative into a clear presentation deck.",
        "list_artifacts": "Checking the user's generated files before downloading or attaching one.",
        "confirm_pending_action": "Executing the exact action the user approved and verifying its result.",
        "reject_pending_action": "Cancelling the pending action so no external change is made.",
        "list_pending_actions": "Checking which consequential actions are currently waiting for a decision.",
        "start_workflow": "Turning the request into an explicit, user-visible execution plan.",
        "update_workflow": "Recording progress so the execution plan remains accurate.",
        "complete_workflow": "Closing the workflow only after its outcome has been checked.",
        "schedule_followup": "Creating a durable follow-up that can continue after this session.",
        "check_tool_status": "Verifying that the requested capability is available before relying on it.",
        "recall": "Checking durable memory for context that may prevent repeated questions.",
        "remember": "Saving only the durable fact the user asked Auren to remember.",
        "forget": "Locating the requested memory so it can be removed safely.",
    }
    return templates.get(
        tool,
        f"Using {_TOOL_DISPLAY_NAMES.get(tool, tool.replace('_', ' '))} to move the request forward.",
    )


def _input_summary(tool: str, arguments: dict[str, Any]) -> str | None:
    """Describe safe, useful inputs without exposing bodies, tokens, or raw IDs."""
    if tool == "search_web":
        return _public_text(f"Query: {arguments.get('query', '')}")
    if tool == "get_weather":
        return _public_text(f"Location: {arguments.get('location', '')}")
    if tool == "list_calendar_events":
        period = arguments.get("period") or "upcoming"
        query = arguments.get("query")
        return _public_text(
            f"Window: {period}" + (f" · Match: {query}" if query else "")
        )
    if tool == "find_free_slots":
        return _public_text(
            f"Next {arguments.get('days_ahead', 7)} days · {arguments.get('duration_minutes', 30)} minutes"
        )
    if tool in {"create_calendar_event", "update_calendar_event"}:
        title = arguments.get("title")
        start_at = arguments.get("start_at")
        attendees = arguments.get("attendees")
        attendee_count = len(attendees) if isinstance(attendees, list) else 0
        parts = [str(value) for value in (title, start_at) if value]
        if attendee_count:
            parts.append(
                f"{attendee_count} attendee{'s' if attendee_count != 1 else ''}"
            )
        return _public_text(" · ".join(parts)) or None
    if tool == "delete_calendar_event":
        return "Exact event selected" + (
            " · Entire recurring series" if arguments.get("delete_series") else ""
        )
    if tool == "search_emails":
        return _public_text(
            f"Query: {arguments.get('query') or 'in:inbox'} · Up to {arguments.get('max_results', 5)} results"
        )
    if tool == "read_email":
        return "Exact message selected"
    if tool == "trash_email":
        return "Exact message selected · Recoverable Trash action"
    if tool in {"draft_email", "send_email"}:
        recipient = arguments.get("to") or "recipient not resolved"
        subject = arguments.get("subject") or "No subject"
        artifacts = arguments.get("artifact_ids")
        count = len(artifacts) if isinstance(artifacts, list) else 0
        return _public_text(
            f"To: {recipient} · Subject: {subject}"
            + (f" · {count} attachment{'s' if count != 1 else ''}" if count else "")
        )
    if tool == "create_document":
        return _public_text(
            f"{arguments.get('title') or 'Untitled'} · {str(arguments.get('format') or 'docx').upper()}"
        )
    if tool == "create_spreadsheet":
        rows = arguments.get("rows")
        return _public_text(
            f"{arguments.get('title') or 'Untitled'} · {len(rows) if isinstance(rows, list) else 0} rows · "
            f"{str(arguments.get('format') or 'xlsx').upper()}"
        )
    if tool == "create_presentation":
        slides = arguments.get("slides")
        return _public_text(
            f"{arguments.get('title') or 'Untitled'} · {len(slides) if isinstance(slides, list) else 0} content slides"
        )
    if tool == "list_artifacts":
        return f"Up to {arguments.get('limit', 10)} recent files"
    if tool == "start_workflow":
        plan = arguments.get("plan")
        count = len(plan) if isinstance(plan, list) else 0
        return _public_text(f"{count} planned step{'s' if count != 1 else ''}")
    if tool == "update_workflow" and isinstance(arguments.get("current_step"), int):
        return f"Progress: {arguments['current_step']} step(s) completed"
    if tool == "complete_workflow":
        return "Final outcome verification"
    if tool == "create_reminder":
        return _public_text(arguments.get("title")) or None
    if tool in {"search_notes", "recall", "forget"}:
        return _public_text(f"Match: {arguments.get('query', '')}")
    if tool == "save_note":
        title = arguments.get("title")
        return _public_text(f"Title: {title}") if title else "User-requested note"
    if tool == "schedule_followup":
        return _public_text(f"In {arguments.get('run_in_minutes', 1440)} minutes")
    return None


def _result_summary(tool: str, body: dict[str, Any], status: str) -> str:
    summary = _public_text(body.get("summary") or "")
    if status == "awaiting_approval":
        return summary or "Prepared safely and waiting for your approval."
    if status == "failed":
        return summary or "The tool could not complete this step."
    if summary:
        return summary
    return f"{_TOOL_DISPLAY_NAMES.get(tool, tool.replace('_', ' '))} completed successfully."


class ToolGateway:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        timeout: float = 45.0,
        on_event: ToolEventHandler | None = None,
    ) -> None:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auren-Service-Token"] = token
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )
        self._on_event = on_event
        self._pending_invocations: dict[
            str, tuple[str, str, dict[str, ToolEventValue]]
        ] = {}
        self._active_workflow_id: str | None = None
        self._active_workflow_goal: str | None = None
        self._active_workflow_plan: list[str] = []
        self._active_workflow_step = 0

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def invoke(self, tool: str, user_id: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return a sentence the model can speak."""
        invocation_id = uuid.uuid4().hex
        started_at = time.monotonic()
        public_context = self._public_event_context(tool, arguments)
        await self._notify(
            tool,
            invocation_id,
            "started",
            details=public_context,
        )
        payload = {
            "tool": tool,
            "user_id": user_id,
            "arguments": {
                key: value for key, value in arguments.items() if value is not None
            },
        }

        try:
            response = await self._client.post("/v1/tools/invoke", json=payload)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Tool gateway returned a non-object response")
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Tool gateway call failed for %s: %s", tool, error)
            await self._notify(
                tool,
                invocation_id,
                "failed",
                duration_ms=_duration_ms(started_at),
                result_summary="Auren could not reach the tool service for this step.",
                details=public_context,
            )
            raise ToolError("I could not reach my tools just now.") from error

        if not body.get("ok", False):
            detail = body.get("summary") or "That did not work."
            if tool in {"confirm_pending_action", "reject_pending_action"}:
                resolved_action_id = arguments.get("action_id")
                if (
                    not isinstance(resolved_action_id, str)
                    and self._pending_invocations
                ):
                    resolved_action_id = next(reversed(self._pending_invocations))
                if isinstance(resolved_action_id, str):
                    pending_invocation = self._pending_invocations.pop(
                        resolved_action_id, None
                    )
                    if pending_invocation:
                        pending_tool, pending_invocation_id, pending_context = (
                            pending_invocation
                        )
                        await self._notify(
                            pending_tool,
                            pending_invocation_id,
                            "failed",
                            result_summary=_result_summary(tool, body, "failed"),
                            details=pending_context,
                        )
            await self._notify(
                tool,
                invocation_id,
                "failed",
                duration_ms=_duration_ms(started_at),
                result_summary=_result_summary(tool, body, "failed"),
                details=public_context,
            )
            raise ToolError(f"{tool} failed: {detail}")
        data = body.get("data")
        is_pending = isinstance(data, dict) and data.get("pending") is True
        status = "awaiting_approval" if is_pending else "completed"
        action_id = data.get("action_id") if isinstance(data, dict) else None
        workflow_details = self._update_workflow_context(tool, arguments, data)
        artifact_details = self._artifact_event_context(data)
        event_context = {**public_context, **workflow_details, **artifact_details}
        if is_pending and isinstance(action_id, str):
            if len(self._pending_invocations) >= 64:
                oldest_action_id = next(iter(self._pending_invocations))
                self._pending_invocations.pop(oldest_action_id, None)
            self._pending_invocations[action_id] = (tool, invocation_id, event_context)
        elif tool in {"confirm_pending_action", "reject_pending_action"}:
            resolved_action_id = arguments.get("action_id")
            if not isinstance(resolved_action_id, str) and isinstance(data, dict):
                resolved_action_id = data.get("action_id")
            if isinstance(resolved_action_id, str):
                pending_invocation = self._pending_invocations.pop(
                    resolved_action_id,
                    None,
                )
                if pending_invocation:
                    pending_tool, pending_invocation_id, pending_context = (
                        pending_invocation
                    )
                    await self._notify(
                        pending_tool,
                        pending_invocation_id,
                        "completed"
                        if tool == "confirm_pending_action"
                        else "cancelled",
                        result_summary=(
                            _result_summary(pending_tool, body, "completed")
                            if tool == "confirm_pending_action"
                            else "The user rejected this action. No external change was made."
                        ),
                        details=pending_context,
                    )
        await self._notify(
            tool,
            invocation_id,
            status,
            duration_ms=_duration_ms(started_at),
            action_id=action_id if isinstance(action_id, str) else None,
            result_summary=_result_summary(tool, body, status),
            details=event_context,
        )
        if tool == "complete_workflow" and status == "completed":
            self._active_workflow_id = None
            self._active_workflow_goal = None
            self._active_workflow_plan = []
            self._active_workflow_step = 0
        summary = str(body.get("summary") or "Done.")
        return json.dumps(
            {"tool": tool, "ok": True, "summary": summary, "data": data or {}},
            ensure_ascii=False,
        )

    async def _notify(
        self,
        tool: str,
        invocation_id: str,
        status: str,
        *,
        duration_ms: int | None = None,
        action_id: str | None = None,
        result_summary: str | None = None,
        details: dict[str, ToolEventValue] | None = None,
    ) -> None:
        if self._on_event is None:
            return
        event: dict[str, ToolEventValue] = {
            "tool": tool,
            "invocationId": invocation_id,
            "status": status,
        }
        if duration_ms is not None:
            event["durationMs"] = duration_ms
        if action_id is not None:
            event["actionId"] = action_id
        if result_summary is not None:
            event["resultSummary"] = _public_text(result_summary)
        if details:
            event.update(
                {key: value for key, value in details.items() if value is not None}
            )
        try:
            await self._on_event(event)
        except Exception as error:  # noqa: BLE001 - UI telemetry must not break tools
            logger.warning("Could not publish tool activity for %s: %s", tool, error)

    def _public_event_context(
        self,
        tool: str,
        arguments: dict[str, Any],
    ) -> dict[str, ToolEventValue]:
        context: dict[str, ToolEventValue] = {
            "displayName": _TOOL_DISPLAY_NAMES.get(
                tool, tool.replace("_", " ").title()
            ),
            "decisionSummary": _decision_summary(tool, arguments),
        }
        input_summary = _input_summary(tool, arguments)
        if input_summary:
            context["inputSummary"] = input_summary
        if self._active_workflow_id and tool != "start_workflow":
            context["workflowId"] = self._active_workflow_id
        if tool == "start_workflow":
            goal = _public_text(arguments.get("goal"), limit=500)
            plan = arguments.get("plan")
            context["workflowGoal"] = goal
            context["workflowPlan"] = [
                _public_text(step, limit=240)
                for step in (plan if isinstance(plan, list) else [])
                if _public_text(step, limit=240)
            ][:20]
            context["workflowStatus"] = "planning"
            context["workflowCurrentStep"] = 0
        elif tool in {"update_workflow", "complete_workflow"}:
            workflow_id = arguments.get("workflow_id")
            if isinstance(workflow_id, str):
                context["workflowId"] = workflow_id
            current_step = arguments.get("current_step")
            if isinstance(current_step, int):
                context["workflowCurrentStep"] = current_step
            if tool == "complete_workflow":
                context["workflowStatus"] = "completing"
            elif isinstance(arguments.get("status"), str):
                context["workflowStatus"] = arguments["status"]
        return context

    def _update_workflow_context(
        self,
        tool: str,
        arguments: dict[str, Any],
        data: object,
    ) -> dict[str, ToolEventValue]:
        details: dict[str, ToolEventValue] = {}
        payload = data if isinstance(data, dict) else {}
        if tool == "start_workflow":
            workflow_id = payload.get("workflow_id")
            if isinstance(workflow_id, str):
                self._active_workflow_id = workflow_id
                details["workflowId"] = workflow_id
            self._active_workflow_goal = _public_text(
                payload.get("goal") or arguments.get("goal"), limit=500
            )
            raw_plan = payload.get("plan") or arguments.get("plan") or []
            self._active_workflow_plan = [
                _public_text(step, limit=240)
                for step in raw_plan
                if isinstance(step, str) and _public_text(step, limit=240)
            ][:20]
            self._active_workflow_step = 0
            details.update(
                {
                    "workflowGoal": self._active_workflow_goal,
                    "workflowPlan": self._active_workflow_plan,
                    "workflowCurrentStep": 0,
                    "workflowStatus": "active",
                }
            )
        elif tool == "update_workflow":
            current_step = payload.get("current_step", arguments.get("current_step"))
            if isinstance(current_step, int):
                self._active_workflow_step = current_step
                details["workflowCurrentStep"] = current_step
            status = payload.get("status") or arguments.get("status") or "active"
            details["workflowStatus"] = _public_text(status, limit=32)
        elif tool == "complete_workflow":
            status = payload.get("status") or arguments.get("status") or "completed"
            details["workflowStatus"] = _public_text(status, limit=32)
            details["workflowCurrentStep"] = len(self._active_workflow_plan)
        return details

    @staticmethod
    def _artifact_event_context(data: object) -> dict[str, ToolEventValue]:
        payload = data if isinstance(data, dict) else {}
        artifact = payload.get("artifact")
        if not isinstance(artifact, dict):
            return {}
        result: dict[str, ToolEventValue] = {}
        for source, target in (
            ("id", "artifactId"),
            ("filename", "artifactFilename"),
            ("format", "artifactFormat"),
            ("download_url", "artifactDownloadUrl"),
        ):
            value = artifact.get(source)
            if isinstance(value, str):
                result[target] = value
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _duration_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def build_tools(gateway: ToolGateway, user_id: str) -> list:
    """Create the tool set for one session, bound to the calling user."""

    @function_tool
    async def get_current_time(timezone: str | None = None) -> str:
        """Get the current date and time.

        Call this before creating a reminder or answering anything about today.

        Args:
            timezone: IANA timezone name such as Asia/Kolkata. Omit to use the default.
        """
        return await gateway.invoke("get_current_time", user_id, {"timezone": timezone})

    @function_tool
    async def get_weather(
        location: str, units: Literal["metric", "imperial"] = "metric"
    ) -> str:
        """Get the current weather and today's forecast for a place.

        Args:
            location: City name, optionally with a country for ambiguous names.
            units: metric for Celsius, imperial for Fahrenheit.
        """
        return await gateway.invoke(
            "get_weather", user_id, {"location": location, "units": units}
        )

    @function_tool
    async def create_reminder(
        title: str,
        remind_in_minutes: int | None = None,
        due_at: str | None = None,
        timezone: str | None = None,
    ) -> str:
        """Save a reminder for the user.

        Args:
            title: What the user wants to be reminded about.
            remind_in_minutes: Minutes from now. Use this for 'in twenty minutes'.
            due_at: Absolute ISO 8601 time, for example 2026-08-02T09:00:00.
            timezone: IANA timezone the absolute time is expressed in.
        """
        return await gateway.invoke(
            "create_reminder",
            user_id,
            {
                "title": title,
                "remind_in_minutes": remind_in_minutes,
                "due_at": due_at,
                "timezone": timezone,
            },
        )

    @function_tool
    async def list_reminders(
        status: Literal["pending", "due", "completed", "all"] = "pending",
        limit: int = 10,
    ) -> str:
        """List the user's reminders, soonest first.

        Args:
            status: Which reminders to include. Use due for fired reminders.
            limit: Maximum number to read back.
        """
        return await gateway.invoke(
            "list_reminders", user_id, {"status": status, "limit": limit}
        )

    @function_tool
    async def list_calendar_events(
        period: Literal["today", "tomorrow", "upcoming", "custom"] = "upcoming",
        days_ahead: int = 7,
        query: str | None = None,
        timezone: str | None = None,
        start_at: str | None = None,
        end_at: str | None = None,
    ) -> str:
        """List calendar events in a precise local-time window.

        Use period=today for the user's complete local calendar day, including
        events earlier today. Use custom with ISO start_at and end_at.
        """
        return await gateway.invoke(
            "list_calendar_events",
            user_id,
            {
                "period": period,
                "days_ahead": days_ahead,
                "query": query,
                "timezone": timezone,
                "start_at": start_at,
                "end_at": end_at,
            },
        )

    @function_tool
    async def find_free_slots(
        days_ahead: int = 7,
        duration_minutes: int = 30,
        timezone: str | None = None,
    ) -> str:
        """Find free slots on the user's Google Calendar."""
        return await gateway.invoke(
            "find_free_slots",
            user_id,
            {
                "days_ahead": days_ahead,
                "duration_minutes": duration_minutes,
                "timezone": timezone,
            },
        )

    @function_tool
    async def create_calendar_event(
        title: str,
        start_at: str,
        duration_minutes: int = 30,
        attendees: list[str] | None = None,
        description: str | None = None,
        add_meet_link: bool = True,
        timezone: str | None = None,
    ) -> str:
        """Prepare/create a Google Calendar event. Requires user confirmation."""
        return await gateway.invoke(
            "create_calendar_event",
            user_id,
            {
                "title": title,
                "start_at": start_at,
                "duration_minutes": duration_minutes,
                "attendees": attendees or [],
                "description": description,
                "add_meet_link": add_meet_link,
                "timezone": timezone,
            },
        )

    @function_tool
    async def update_calendar_event(
        event_id: str,
        title: str | None = None,
        start_at: str | None = None,
        duration_minutes: int | None = None,
        attendees: list[str] | None = None,
        description: str | None = None,
        timezone: str | None = None,
    ) -> str:
        """Update one exact Google Calendar event. Requires confirmation.

        Resolve event_id with list_calendar_events first. Only pass fields the
        user explicitly asked to change.
        """
        return await gateway.invoke(
            "update_calendar_event",
            user_id,
            {
                "event_id": event_id,
                "title": title,
                "start_at": start_at,
                "duration_minutes": duration_minutes,
                "attendees": attendees,
                "description": description,
                "timezone": timezone,
            },
        )

    @function_tool
    async def delete_calendar_event(
        event_id: str,
        delete_series: bool = False,
        notify_attendees: bool = True,
    ) -> str:
        """Delete one exact Calendar event after confirmation.

        Resolve event_id first. Clarify occurrence versus series before setting
        delete_series=true.
        """
        return await gateway.invoke(
            "delete_calendar_event",
            user_id,
            {
                "event_id": event_id,
                "delete_series": delete_series,
                "notify_attendees": notify_attendees,
            },
        )

    @function_tool
    async def search_emails(
        query: str = "in:inbox",
        max_results: int = 5,
        include_body: bool = False,
    ) -> str:
        """Search Gmail newest-first.

        For the latest inbox email use query=in:inbox, max_results=1 and
        include_body=true.
        """
        return await gateway.invoke(
            "search_emails",
            user_id,
            {
                "query": query,
                "max_results": max_results,
                "include_body": include_body,
            },
        )

    @function_tool
    async def read_email(message_id: str) -> str:
        """Read one complete Gmail message returned by search_emails."""
        return await gateway.invoke("read_email", user_id, {"message_id": message_id})

    @function_tool
    async def trash_email(message_id: str) -> str:
        """Move one exact Gmail message to Trash after confirmation.

        Resolve message_id with search_emails first. This is reversible and is
        not immediate permanent deletion.
        """
        return await gateway.invoke("trash_email", user_id, {"message_id": message_id})

    @function_tool
    async def draft_email(
        to: str,
        subject: str,
        body: str,
        draft_id: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> str:
        """Create/update a Gmail draft with generated-file attachments; never send it."""
        return await gateway.invoke(
            "draft_email",
            user_id,
            {
                "to": to,
                "subject": subject,
                "body": body,
                "draft_id": draft_id,
                "artifact_ids": artifact_ids or [],
            },
        )

    @function_tool
    async def send_email(
        to: str,
        subject: str,
        body: str,
        draft_id: str | None = None,
        draft_content_hash: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> str:
        """Send email via Gmail. Requires explicit user confirmation."""
        return await gateway.invoke(
            "send_email",
            user_id,
            {
                "to": to,
                "subject": subject,
                "body": body,
                "draft_id": draft_id,
                "draft_content_hash": draft_content_hash,
                "artifact_ids": artifact_ids or [],
            },
        )

    @function_tool
    async def create_document(
        title: str,
        content: str,
        format: Literal["docx", "pdf", "md", "html", "txt"] = "docx",
    ) -> str:
        """Create a polished document from completed Markdown content and save it."""
        return await gateway.invoke(
            "create_document",
            user_id,
            {"title": title, "content": content, "format": format},
        )

    @function_tool
    async def create_spreadsheet(
        title: str,
        columns: list[str],
        rows: list[list[str | int | float | bool | None]],
        format: Literal["xlsx", "csv"] = "xlsx",
    ) -> str:
        """Create a structured spreadsheet and save it for download or email."""
        return await gateway.invoke(
            "create_spreadsheet",
            user_id,
            {"title": title, "columns": columns, "rows": rows, "format": format},
        )

    @function_tool
    async def create_presentation(
        title: str,
        slides: list[dict[str, Any]],
    ) -> str:
        """Create a PPTX deck. Each slide needs title, bullets, and optional notes."""
        return await gateway.invoke(
            "create_presentation",
            user_id,
            {"title": title, "slides": slides},
        )

    @function_tool
    async def list_artifacts(limit: int = 10) -> str:
        """List the user's generated documents, spreadsheets, and presentations."""
        return await gateway.invoke("list_artifacts", user_id, {"limit": limit})

    @function_tool
    async def list_pending_actions(limit: int = 5) -> str:
        """List actions waiting for confirmation."""
        return await gateway.invoke("list_pending_actions", user_id, {"limit": limit})

    @function_tool
    async def start_workflow(goal: str, plan: list[str] | None = None) -> str:
        """Start a durable multi-step outcome workflow."""
        return await gateway.invoke(
            "start_workflow", user_id, {"goal": goal, "plan": plan or []}
        )

    @function_tool
    async def update_workflow(
        workflow_id: str,
        status: str | None = None,
        current_step: int | None = None,
        note: str | None = None,
    ) -> str:
        """Update the user-visible workflow after meaningful progress.

        current_step is the number of completed plan steps. Use 1 after the
        first step completes, 2 after the second, and so on.
        """
        return await gateway.invoke(
            "update_workflow",
            user_id,
            {
                "workflow_id": workflow_id,
                "status": status,
                "current_step": current_step,
                "note": note,
            },
        )

    @function_tool
    async def complete_workflow(workflow_id: str, result_summary: str) -> str:
        """Mark a workflow completed after verifying outcomes."""
        return await gateway.invoke(
            "complete_workflow",
            user_id,
            {"workflow_id": workflow_id, "result_summary": result_summary},
        )

    @function_tool
    async def schedule_followup(
        message: str,
        run_in_minutes: int = 1440,
        job_type: str = "follow_up_reminder",
        workflow_id: str | None = None,
        query: str | None = None,
    ) -> str:
        """Schedule a background follow-up that survives the voice session.

        Args:
            message: What to remind or check.
            run_in_minutes: Delay before the job runs.
            job_type: follow_up_reminder, email_reply_check, or custom.
            workflow_id: Optional workflow to resume later.
            query: Gmail search for email_reply_check jobs.
        """
        arguments: dict[str, Any] = {
            "message": message,
            "run_in_minutes": run_in_minutes,
            "job_type": job_type,
            "workflow_id": workflow_id,
        }
        if query:
            arguments["query"] = query
        return await gateway.invoke(
            "schedule_followup",
            user_id,
            arguments,
        )

    @function_tool
    async def save_note(body: str, title: str | None = None) -> str:
        """Save a note so the user can recall it later.

        Args:
            body: The content to remember.
            title: Optional short title.
        """
        return await gateway.invoke(
            "save_note", user_id, {"body": body, "title": title}
        )

    @function_tool
    async def search_notes(query: str, limit: int = 5) -> str:
        """Search the user's saved notes.

        Args:
            query: Words to look for.
            limit: Maximum number of notes to return.
        """
        return await gateway.invoke(
            "search_notes", user_id, {"query": query, "limit": limit}
        )

    @function_tool
    async def search_web(query: str, max_results: int = 3) -> str:
        """Search the live web for current information, news, markets, or Google-style queries.

        Use this whenever the user asks for a Google search, online lookup, latest
        news, or any fact that may have changed recently. Never claim you cannot
        search Google; this tool is the live web search path.

        Args:
            query: What to search for.
            max_results: How many results to consider.
        """
        return await gateway.invoke(
            "search_web", user_id, {"query": query, "max_results": max_results}
        )

    @function_tool
    async def get_page_context() -> str:
        """Load the article or page the user sent from the Auren browser extension.

        Use this whenever they ask what is on their screen, what they are looking
        at, or to explain, summarise, read, or go through the current page,
        article, tab, or screen. Always call this before saying you cannot see
        the screen. Explain conversationally from the extracted text.
        """
        return await gateway.invoke("get_page_context", user_id, {})

    @function_tool
    async def check_tool_status(tool: Literal["web_search"] = "web_search") -> str:
        """Dynamically check whether a tool is currently available.

        Call this whenever the user asks whether a tool works or requests its status.

        Args:
            tool: The tool to probe.
        """
        return await gateway.invoke("check_tool_status", user_id, {"tool": tool})

    @function_tool
    async def recall(query: str, limit: int = 5) -> str:
        """Search durable personal memories, or recall the previous conversation.

        Use query 'last conversation' when the user asks what you discussed last time.

        Args:
            query: What to look up, or 'last conversation' for the prior session.
            limit: Maximum memories to return.
        """
        return await gateway.invoke("recall", user_id, {"query": query, "limit": limit})

    @function_tool
    async def remember(content: str) -> str:
        """Save a durable personal fact the user asked you to remember.

        Args:
            content: The fact to keep across sessions.
        """
        return await gateway.invoke("remember", user_id, {"content": content})

    @function_tool
    async def forget(query: str) -> str:
        """Forget a durable personal memory matching the user's request.

        Args:
            query: Words matching the memory to forget.
        """
        return await gateway.invoke("forget", user_id, {"query": query})

    return [
        get_current_time,
        get_weather,
        create_reminder,
        list_reminders,
        save_note,
        search_notes,
        search_web,
        get_page_context,
        list_calendar_events,
        find_free_slots,
        create_calendar_event,
        update_calendar_event,
        delete_calendar_event,
        search_emails,
        read_email,
        trash_email,
        draft_email,
        send_email,
        create_document,
        create_spreadsheet,
        create_presentation,
        list_artifacts,
        list_pending_actions,
        start_workflow,
        update_workflow,
        complete_workflow,
        schedule_followup,
        check_tool_status,
        recall,
        remember,
        forget,
    ]
