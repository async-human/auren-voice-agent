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
        self.assertEqual(
            [event["status"] for event in events], ["started", "completed"]
        )
        self.assertEqual(events[0]["displayName"], "Current time")
        self.assertIn("timezone", str(events[0]["decisionSummary"]).lower())
        self.assertEqual(events[-1]["resultSummary"], "It is 2:30 PM.")
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
        self.assertEqual(
            events[-1]["inputSummary"],
            "To: person@example.com · Subject: No subject",
        )

    async def test_created_artifact_is_linked_without_exposing_content(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Created report.docx.",
                "data": {
                    "artifact": {
                        "id": "a" * 32,
                        "filename": "report.docx",
                        "format": "docx",
                        "download_url": f"/v1/artifacts/{'a' * 32}/download",
                    }
                },
            },
        )
        try:
            await gateway.invoke(
                "create_document",
                "user-1",
                {"title": "Report", "content": "PRIVATE REPORT CONTENT", "format": "docx"},
            )
        finally:
            await gateway.aclose()

        self.assertEqual(events[-1]["artifactId"], "a" * 32)
        self.assertEqual(events[-1]["artifactFilename"], "report.docx")
        self.assertNotIn("PRIVATE REPORT CONTENT", json.dumps(events))

    async def test_email_activity_never_publishes_the_message_body(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Draft created.",
                "data": {"pending": True, "action_id": "action-private"},
            },
        )

        try:
            await gateway.invoke(
                "draft_email",
                "user-1",
                {
                    "to": "person@example.com",
                    "subject": "Quarterly plan",
                    "body": "PRIVATE-BODY-CONTENT",
                },
            )
        finally:
            await gateway.aclose()

        self.assertNotIn("PRIVATE-BODY-CONTENT", json.dumps(events))
        self.assertIn("person@example.com", str(events[-1]["inputSummary"]))

    async def test_workflow_plan_and_progress_are_linked_to_tool_events(self) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Workflow started.",
                "data": {
                    "workflow_id": "workflow-1",
                    "goal": "Research and send a report",
                    "plan": [
                        "Research sources",
                        "Create report",
                        "Send after approval",
                    ],
                },
            },
        )
        try:
            await gateway.invoke(
                "start_workflow",
                "user-1",
                {
                    "goal": "Research and send a report",
                    "plan": [
                        "Research sources",
                        "Create report",
                        "Send after approval",
                    ],
                },
            )
            await gateway.client.aclose()
            gateway._client = httpx.AsyncClient(  # noqa: SLF001
                base_url="https://tools.example",
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "summary": "Found current sources.",
                            "data": {"results": []},
                        },
                    )
                ),
            )
            await gateway.invoke(
                "search_web", "user-1", {"query": "current STT models"}
            )
        finally:
            await gateway.aclose()

        workflow_events = [
            event for event in events if event["tool"] == "start_workflow"
        ]
        search_events = [event for event in events if event["tool"] == "search_web"]
        self.assertEqual(workflow_events[-1]["workflowId"], "workflow-1")
        self.assertEqual(
            workflow_events[-1]["workflowPlan"],
            ["Research sources", "Create report", "Send after approval"],
        )
        self.assertEqual(search_events[0]["workflowId"], "workflow-1")
        self.assertEqual(search_events[0]["inputSummary"], "Query: current STT models")

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
            event for event in events if event["invocationId"] == original_invocation_id
        ]
        self.assertEqual(
            [event["status"] for event in resolved],
            ["started", "awaiting_approval", "completed"],
        )

    async def test_latest_voice_confirmation_resolves_activity_from_result_action_id(
        self,
    ) -> None:
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

    async def test_failed_confirmation_marks_original_pending_activity_failed(
        self,
    ) -> None:
        gateway, events = await self.make_gateway(
            {
                "ok": True,
                "summary": "Confirmation required.",
                "data": {"pending": True, "action_id": "action-failed"},
            },
        )
        try:
            await gateway.invoke("create_calendar_event", "user-1", {})
            original_invocation_id = str(events[-1]["invocationId"])
            await gateway.client.aclose()
            gateway._client = httpx.AsyncClient(  # noqa: SLF001
                base_url="https://tools.example",
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={
                            "ok": False,
                            "summary": "Confirmed, but execution failed.",
                        },
                    )
                ),
            )
            with self.assertRaises(ToolError):
                await gateway.invoke("confirm_pending_action", "user-1", {})
        finally:
            await gateway.aclose()

        resolved = [
            event for event in events if event["invocationId"] == original_invocation_id
        ]
        self.assertEqual(
            [event["status"] for event in resolved],
            ["started", "awaiting_approval", "failed"],
        )

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
