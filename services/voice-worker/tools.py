"""Voice tools backed by the Railway tool gateway.

The worker deliberately owns no business logic: every tool forwards to the
FastAPI gateway, which holds the database, credentials and audit trail. That
keeps the GPU pod a replaceable inference runtime.
"""

from __future__ import annotations

import logging
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

import httpx
from livekit.agents import ToolError, function_tool

logger = logging.getLogger("auren.tools")
ToolEventValue = str | int
ToolEventHandler = Callable[[dict[str, ToolEventValue]], Awaitable[None]]


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
        self._pending_invocations: dict[str, tuple[str, str]] = {}

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def invoke(self, tool: str, user_id: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return a sentence the model can speak."""
        invocation_id = uuid.uuid4().hex
        started_at = time.monotonic()
        await self._notify(tool, invocation_id, "started")
        payload = {
            "tool": tool,
            "user_id": user_id,
            "arguments": {key: value for key, value in arguments.items() if value is not None},
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
            )
            raise ToolError("I could not reach my tools just now.") from error

        if not body.get("ok", False):
            detail = body.get("summary") or "That did not work."
            await self._notify(
                tool,
                invocation_id,
                "failed",
                duration_ms=_duration_ms(started_at),
            )
            raise ToolError(f"{tool} failed: {detail}")
        data = body.get("data")
        is_pending = isinstance(data, dict) and data.get("pending") is True
        status = "awaiting_approval" if is_pending else "completed"
        action_id = data.get("action_id") if isinstance(data, dict) else None
        if is_pending and isinstance(action_id, str):
            if len(self._pending_invocations) >= 64:
                oldest_action_id = next(iter(self._pending_invocations))
                self._pending_invocations.pop(oldest_action_id, None)
            self._pending_invocations[action_id] = (tool, invocation_id)
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
                    pending_tool, pending_invocation_id = pending_invocation
                    await self._notify(
                        pending_tool,
                        pending_invocation_id,
                        "completed" if tool == "confirm_pending_action" else "cancelled",
                    )
        await self._notify(
            tool,
            invocation_id,
            status,
            duration_ms=_duration_ms(started_at),
            action_id=action_id if isinstance(action_id, str) else None,
        )
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
        try:
            await self._on_event(event)
        except Exception as error:  # noqa: BLE001 - UI telemetry must not break tools
            logger.warning("Could not publish tool activity for %s: %s", tool, error)

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
    async def get_weather(location: str, units: Literal["metric", "imperial"] = "metric") -> str:
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
        return await gateway.invoke(
            "read_email", user_id, {"message_id": message_id}
        )

    @function_tool
    async def draft_email(
        to: str,
        subject: str,
        body: str,
        draft_id: str | None = None,
    ) -> str:
        """Create/update a real Gmail draft and prepare approval; never send it."""
        return await gateway.invoke(
            "draft_email",
            user_id,
            {"to": to, "subject": subject, "body": body, "draft_id": draft_id},
        )

    @function_tool
    async def send_email(
        to: str,
        subject: str,
        body: str,
        draft_id: str | None = None,
        draft_content_hash: str | None = None,
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
            },
        )

    @function_tool
    async def list_pending_actions(limit: int = 5) -> str:
        """List actions waiting for confirmation."""
        return await gateway.invoke(
            "list_pending_actions", user_id, {"limit": limit}
        )

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
        """Update workflow progress."""
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
    ) -> str:
        """Schedule a background follow-up that survives the voice session."""
        return await gateway.invoke(
            "schedule_followup",
            user_id,
            {
                "message": message,
                "run_in_minutes": run_in_minutes,
                "job_type": job_type,
                "workflow_id": workflow_id,
            },
        )

    @function_tool
    async def save_note(body: str, title: str | None = None) -> str:
        """Save a note so the user can recall it later.

        Args:
            body: The content to remember.
            title: Optional short title.
        """
        return await gateway.invoke("save_note", user_id, {"body": body, "title": title})

    @function_tool
    async def search_notes(query: str, limit: int = 5) -> str:
        """Search the user's saved notes.

        Args:
            query: Words to look for.
            limit: Maximum number of notes to return.
        """
        return await gateway.invoke("search_notes", user_id, {"query": query, "limit": limit})

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
        search_emails,
        read_email,
        draft_email,
        send_email,
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
