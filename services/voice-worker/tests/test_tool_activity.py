from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx
from livekit.agents import ToolError

from tools import ToolEventValue, ToolGateway


class ToolActivityTests(unittest.IsolatedAsyncioTestCase):
    async def make_gateway(
        self,
        payload: object,
    ) -> tuple[ToolGateway, list[dict[str, ToolEventValue]]]:
        events: list[dict[str, ToolEventValue]] = []

        async def on_event(event: dict[str, ToolEventValue]) -> None:
            events.append(event)

        def respond(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=payload)

        proxy_overrides = {
            name: ""
            for name in (
                "ALL_PROXY",
                "all_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            )
        }
        with patch.dict(os.environ, proxy_overrides):
            gateway = ToolGateway("https://tools.example", None, on_event=on_event)
        await gateway.client.aclose()
        gateway._client = httpx.AsyncClient(  # noqa: SLF001 - inject deterministic transport
            base_url="https://tools.example",
            transport=httpx.MockTransport(respond),
        )
        return gateway, events

    async def test_success_emits_started_and_completed_with_duration(self) -> None:
        gateway, events = await self.make_gateway(
            {"ok": True, "summary": "It is 2:30 PM.", "data": {}},
        )

        try:
            result = await gateway.invoke("get_current_time", "user-1", {})
        finally:
            await gateway.aclose()

        self.assertEqual(json.loads(result)["summary"], "It is 2:30 PM.")
        self.assertEqual([event["status"] for event in events], ["started", "completed"])
        self.assertIsInstance(events[-1]["durationMs"], int)
        self.assertGreaterEqual(events[-1]["durationMs"], 0)

    async def test_pending_action_emits_awaiting_approval(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Confirmation required.",
                "data": {"pending": True, "action_id": "action-1"},
            },
        )

        try:
            await gateway.invoke("send_email", "user-1", {"to": "person@example.com"})
        finally:
            await gateway.aclose()

        self.assertEqual(events[-1]["status"], "awaiting_approval")
        self.assertEqual(events[-1]["actionId"], "action-1")
        self.assertNotIn("to", events[-1])

    async def test_voice_confirmation_resolves_the_original_activity(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Confirmation required.",
                "data": {"pending": True, "action_id": "action-1"},
            },
        )

        try:
            await gateway.invoke("send_email", "user-1", {})
            original_invocation_id = str(events[-1]["invocationId"])
            await gateway.client.aclose()
            gateway._client = httpx.AsyncClient(  # noqa: SLF001
                base_url="https://tools.example",
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "summary": "Email sent.",
                            "data": {"action_id": "action-1"},
                        },
                    ),
                ),
            )
            await gateway.invoke(
                "confirm_pending_action",
                "user-1",
                {"action_id": "action-1"},
            )
        finally:
            await gateway.aclose()

        resolved = [
            event
            for event in events
            if event["invocationId"] == original_invocation_id
        ]
        self.assertEqual(
            [event["status"] for event in resolved],
            ["started", "awaiting_approval", "completed"],
        )

    async def test_latest_voice_confirmation_resolves_activity_from_result_action_id(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Draft ready for approval.",
                "data": {"pending": True, "action_id": "action-2"},
            },
        )
        try:
            await gateway.invoke("draft_email", "user-1", {})
            original_invocation_id = str(events[-1]["invocationId"])
            await gateway.client.aclose()
            gateway._client = httpx.AsyncClient(  # noqa: SLF001
                base_url="https://tools.example",
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "summary": "Email sent.",
                            "data": {"action_id": "action-2", "verified": True},
                        },
                    )
                ),
            )
            await gateway.invoke("confirm_pending_action", "user-1", {})
        finally:
            await gateway.aclose()

        resolved = [
            event for event in events if event["invocationId"] == original_invocation_id
        ]
        self.assertEqual(
            [event["status"] for event in resolved],
            ["started", "awaiting_approval", "completed"],
        )

    async def test_tool_error_emits_failed(self) -> None:
        gateway, events = await self.make_gateway(
            {"ok": False, "summary": "Not available.", "error": "Not available."},
        )

        try:
            with self.assertRaises(ToolError):
                await gateway.invoke("get_weather", "user-1", {"location": "Pune"})
        finally:
            await gateway.aclose()

        self.assertEqual(events[-1]["status"], "failed")
        self.assertIsInstance(events[-1]["durationMs"], int)

    async def test_malformed_gateway_response_still_emits_failed(self) -> None:
        gateway, events = await self.make_gateway(["unexpected"])

        try:
            with self.assertRaises(ToolError):
                await gateway.invoke("search_web", "user-1", {"query": "Auren"})
        finally:
            await gateway.aclose()

        self.assertEqual(events[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
