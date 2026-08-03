from __future__ import annotations

import base64
import json
from collections.abc import Callable

from httpx import AsyncClient


async def _user_id(client: AsyncClient, token: str) -> str:
    response = await client.post(
        "/v1/voice/token", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    claims = _decode_jwt(response.json()["participantToken"])
    return json.loads(claims["metadata"])["userId"]


async def test_memory_flush_and_context(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    token = make_token(subject="user_clerk_alice", name="Alice Example")
    user_id = await _user_id(client, token)

    flush = await client.post(
        "/v1/memory/sessions/flush",
        json={
            "user_id": user_id,
            "room_name": "auren-test",
            "turns": [
                {"role": "user", "text": "My name is Alice and I work on robotics.", "sequence": 0},
                {"role": "assistant", "text": "Great to meet you, Alice.", "sequence": 1},
            ],
            "summary": "Alice introduced herself and her robotics work",
            "profile_summary": "Alice works on robotics.",
            "preferences": "Prefers concise answers.",
            "memories": [
                {"content": "Works on robotics"},
                {"content": "Prefers concise answers"},
            ],
        },
    )
    assert flush.status_code == 200
    body = flush.json()
    assert body["turns_saved"] == 2
    assert body["memories_saved"] == 2

    context = await client.get("/v1/memory/context", params={"user_id": user_id})
    assert context.status_code == 200
    payload = context.json()
    assert payload["display_name"] == "Alice Example"
    assert "Alice" in payload["greeting"]
    assert "robotics" in (payload["last_session_summary"] or "").lower()
    assert len(payload["memories"]) == 2
    assert "Works on robotics" in payload["instructions_block"]


async def test_user_can_list_and_forget_memories(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    token = make_token(subject="user_clerk_bob", name="Bob Builder")
    user_id = await _user_id(client, token)

    await client.post(
        "/v1/memory/sessions/flush",
        json={
            "user_id": user_id,
            "turns": [{"role": "user", "text": "I live in Pune.", "sequence": 0}],
            "summary": "Bob said he lives in Pune",
            "memories": [{"content": "Lives in Pune"}],
        },
    )

    listed = await client.get("/v1/memory", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    memories = listed.json()["memories"]
    assert len(memories) == 1
    memory_id = memories[0]["id"]

    deleted = await client.delete(
        f"/v1/memory/{memory_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert deleted.status_code == 204

    listed_again = await client.get("/v1/memory", headers={"Authorization": f"Bearer {token}"})
    assert listed_again.json()["memories"] == []

    context = await client.get("/v1/memory/context", params={"user_id": user_id})
    assert context.json()["memories"] == []


async def test_recall_remember_forget_tools(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    token = make_token(subject="user_clerk_cara", name="Cara")
    user_id = await _user_id(client, token)

    remembered = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "remember",
            "user_id": user_id,
            "arguments": {"content": "Favourite tea is masala chai"},
        },
    )
    assert remembered.status_code == 200
    assert remembered.json()["ok"] is True

    recalled = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "recall",
            "user_id": user_id,
            "arguments": {"query": "tea"},
        },
    )
    assert recalled.json()["ok"] is True
    assert "masala chai" in recalled.json()["summary"]

    forgotten = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "forget",
            "user_id": user_id,
            "arguments": {"query": "tea"},
        },
    )
    assert forgotten.json()["ok"] is True

    empty = await client.post(
        "/v1/tools/invoke",
        json={
            "tool": "recall",
            "user_id": user_id,
            "arguments": {"query": "tea"},
        },
    )
    assert empty.json()["ok"] is True
    assert "do not have anything remembered" in empty.json()["summary"]


def _decode_jwt(token: str) -> dict:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
