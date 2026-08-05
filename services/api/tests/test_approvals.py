from __future__ import annotations

from httpx import AsyncClient


async def test_consequential_tool_requires_confirmation(client: AsyncClient) -> None:
    # Without Google connected, create_calendar_event should still enter the
    # confirmation gate before attempting Google APIs.
    response = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "create_calendar_event",
            "user_id": "approval-user",
            "arguments": {
                "title": "Sync with Rahul",
                "start_at": "2026-08-10T18:00:00+05:30",
                "duration_minutes": 30,
                "attendees": ["rahul@example.com"],
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["pending"] is True
    assert "confirmation" in body["summary"].lower() or "confirm" in body["summary"].lower()
    assert body["data"]["action_id"]


async def test_send_email_requires_confirmation(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "send_email",
            "user_id": "approval-user",
            "arguments": {
                "to": "rahul@example.com",
                "subject": "Agenda",
                "body": "Sharing the agenda for Monday.",
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["pending"] is True
