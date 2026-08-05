from __future__ import annotations

import base64
import json

from httpx import AsyncClient


def _user_id_from_voice_token(token: str) -> str:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    return json.loads(claims["metadata"])["userId"]


async def test_page_context_user_routes(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    article = (
        "Solar sails are a real propulsion idea. " * 40
        + "The article argues that light pressure can accelerate probes over years."
    )
    upsert = await client.put(
        "/v1/page-context",
        headers=auth_headers,
        json={
            "url": "https://example.com/solar-sails",
            "title": "How solar sails work",
            "text": article,
            "source": "extension",
        },
    )
    assert upsert.status_code == 200
    meta = upsert.json()
    assert meta["present"] is True
    assert meta["title"] == "How solar sails work"
    assert meta["char_count"] > 100

    listed = await client.get("/v1/page-context", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["present"] is True

    voice = await client.post("/v1/voice/token", headers=auth_headers)
    assert voice.status_code == 200
    user_id = _user_id_from_voice_token(voice.json()["participantToken"])

    tool_response = await client.post(
        "/v1/tools/invoke",
        json={"tool": "get_page_context", "user_id": user_id, "arguments": {}},
    )
    assert tool_response.status_code == 200
    body = tool_response.json()
    assert body["ok"] is True
    assert "How solar sails work" in body["summary"]
    assert "light pressure" in body["summary"]
    assert body["data"]["present"] is True

    cleared = await client.delete("/v1/page-context", headers=auth_headers)
    assert cleared.status_code == 204

    empty = await client.post(
        "/v1/tools/invoke",
        json={"tool": "get_page_context", "user_id": user_id, "arguments": {}},
    )
    assert empty.status_code == 200
    assert empty.json()["data"]["present"] is False
    assert "No active page" in empty.json()["summary"]


async def test_get_page_context_without_upload_is_speakable(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/tools/invoke",
        json={"tool": "get_page_context", "user_id": "nobody", "arguments": {}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["present"] is False
    assert "extension" in body["summary"].lower()
