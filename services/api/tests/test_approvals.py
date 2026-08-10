from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from pytest import MonkeyPatch

from app.tools import registry
from app.tools.base import ToolResult


async def test_consequential_tool_requires_confirmation(client: AsyncClient) -> None:
    # Without Google connected, create_calendar_event should still enter the
    # confirmation gate before attempting Google APIs.
    future_start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
        microsecond=0
    )
    response = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "create_calendar_event",
            "user_id": "approval-user",
            "arguments": {
                "title": "Sync with Rahul",
                "start_at": future_start.isoformat(),
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

async def test_caller_cannot_bypass_confirmation_gate(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "send_email",
            "user_id": "approval-user",
            "arguments": {
                "to": "rahul@example.com",
                "subject": "Agenda",
                "body": "This must never send without a separate confirmation.",
            },
            "confirmed": True,
        },
    )

    assert response.status_code == 422


async def test_idempotency_key_reuses_the_same_pending_action(
    client: AsyncClient,
) -> None:
    payload = {
        "tool": "send_email",
        "user_id": "idempotent-user",
        "arguments": {
            "to": "rahul@example.com",
            "subject": "Agenda",
            "body": "Sharing the agenda for Monday.",
        },
        "idempotency_key": "email-agenda-2026-08-10",
    }

    first = await client.post("/v1/tools/invoke", json=payload)
    second = await client.post("/v1/tools/invoke", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["action_id"] == second.json()["data"]["action_id"]


async def test_concurrent_confirmations_execute_the_action_once(
    client: AsyncClient,
    monkeypatch: MonkeyPatch,
) -> None:
    calls = 0
    original = registry.REGISTRY["send_email"]

    async def fake_send(_context, _args) -> ToolResult:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return ToolResult(summary="Email sent.", data={"verified": True})

    monkeypatch.setitem(
        registry.REGISTRY,
        "send_email",
        replace(original, handler=fake_send),
    )
    proposed = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "send_email",
            "user_id": "concurrent-user",
            "arguments": {
                "to": "rahul@example.com",
                "subject": "Agenda",
                "body": "Sharing the agenda for Monday.",
            },
        },
    )
    action_id = proposed.json()["data"]["action_id"]
    confirmation = {
        "tool": "confirm_pending_action",
        "user_id": "concurrent-user",
        "arguments": {"action_id": action_id},
    }

    first, second = await asyncio.gather(
        client.post("/v1/tools/invoke", json=confirmation),
        client.post("/v1/tools/invoke", json=confirmation),
    )

    assert calls == 1
    assert sorted([first.json()["ok"], second.json()["ok"]]) == [False, True]
    succeeded = first.json() if first.json()["ok"] else second.json()
    assert succeeded["data"]["verified"] is True
