from __future__ import annotations

import base64
import json

from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


async def test_voice_token_shape_and_claims(client: AsyncClient) -> None:
    response = await client.post("/v1/voice/token", json={})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert body["serverUrl"] == "wss://example.livekit.cloud"

    claims = _decode_jwt(body["participantToken"])
    grants = claims["video"]
    assert grants["roomJoin"] is True
    assert grants["canPublish"] is True
    assert grants["canSubscribe"] is True
    assert grants["canPublishData"] is True
    assert grants["room"].startswith("auren-")
    assert claims["sub"].startswith("user-")
    assert json.loads(claims["metadata"])["userId"].startswith("anonymous-")

    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "auren-agent"


async def test_voice_token_is_rate_limited(client: AsyncClient) -> None:
    statuses = [(await client.post("/v1/voice/token", json={})).status_code for _ in range(12)]

    assert statuses.count(200) == 10
    assert statuses[-1] == 429


async def test_tool_routes_require_service_token(client: AsyncClient) -> None:
    response = await client.get("/v1/tools", headers={"X-Auren-Service-Token": "wrong"})

    assert response.status_code == 401


async def test_tool_listing_exposes_expected_tools(client: AsyncClient) -> None:
    response = await client.get("/v1/tools")

    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert names == {
        "get_current_time",
        "get_weather",
        "create_reminder",
        "list_reminders",
        "save_note",
        "search_notes",
        "search_web",
    }


def _decode_jwt(token: str) -> dict:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
