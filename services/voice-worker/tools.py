"""Voice tools backed by the Railway tool gateway.

The worker deliberately owns no business logic: every tool forwards to the
FastAPI gateway, which holds the database, credentials and audit trail. That
keeps the GPU pod a replaceable inference runtime.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx
from livekit.agents import ToolError, function_tool

logger = logging.getLogger("auren.tools")


class ToolGateway:
    def __init__(self, base_url: str, token: str | None, timeout: float = 20.0) -> None:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auren-Service-Token"] = token
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def invoke(self, tool: str, user_id: str, arguments: dict[str, Any]) -> str:
        """Run a tool and return a sentence the model can speak."""
        payload = {
            "tool": tool,
            "user_id": user_id,
            "arguments": {key: value for key, value in arguments.items() if value is not None},
        }

        try:
            response = await self._client.post("/v1/tools/invoke", json=payload)
            response.raise_for_status()
        except httpx.HTTPError as error:
            logger.warning("Tool gateway call failed for %s: %s", tool, error)
            raise ToolError("I could not reach my tools just now.") from error

        body = response.json()
        if not body.get("ok", False):
            raise ToolError(body.get("summary") or "That did not work.")
        return body["summary"]

    async def aclose(self) -> None:
        await self._client.aclose()


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
        status: Literal["pending", "completed", "all"] = "pending",
        limit: int = 10,
    ) -> str:
        """List the user's reminders, soonest first.

        Args:
            status: Which reminders to include.
            limit: Maximum number to read back.
        """
        return await gateway.invoke(
            "list_reminders", user_id, {"status": status, "limit": limit}
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
        """Search the live web for current information or recent events.

        Args:
            query: What to search for.
            max_results: How many results to consider.
        """
        return await gateway.invoke(
            "search_web", user_id, {"query": query, "max_results": max_results}
        )

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
        recall,
        remember,
        forget,
    ]
