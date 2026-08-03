from __future__ import annotations

import base64
import json
from collections.abc import Callable

from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "development"}


async def test_voice_token_shape_and_claims(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post("/v1/voice/token", headers=auth_headers)

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

    user_id = json.loads(claims["metadata"])["userId"]
    assert not user_id.startswith("anonymous-")

    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "auren-agent"


async def test_voice_token_requires_authentication(client: AsyncClient) -> None:
    response = await client.post("/v1/voice/token")

    assert response.status_code == 401


async def test_identity_is_stable_across_sessions(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    first = await _mint(client, make_token(subject="user_clerk_alice"))
    second = await _mint(client, make_token(subject="user_clerk_alice"))
    other = await _mint(client, make_token(subject="user_clerk_bob"))

    assert first == second, "the same person must keep the same id"
    assert other != first, "different people must not share an id"


async def test_user_id_in_the_body_is_ignored(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    honest = await _mint(client, auth_headers["Authorization"].removeprefix("Bearer "))

    response = await client.post(
        "/v1/voice/token",
        headers=auth_headers,
        json={"user_id": "somebody-elses-id"},
    )

    assert response.status_code == 200
    claims = _decode_jwt(response.json()["participantToken"])
    assert json.loads(claims["metadata"])["userId"] == honest


async def test_expired_and_foreign_tokens_are_rejected(
    client: AsyncClient, make_token: Callable[..., str]
) -> None:
    expired = make_token(expires_in=-60)
    wrong_issuer = make_token(issuer="https://attacker.test")
    unknown_key = make_token(kid="not-a-real-key")

    for token in (expired, wrong_issuer, unknown_key):
        response = await client.post(
            "/v1/voice/token", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


async def test_voice_token_is_rate_limited(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    statuses = [
        (await client.post("/v1/voice/token", headers=auth_headers)).status_code
        for _ in range(12)
    ]

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
        "recall",
        "remember",
        "forget",
    }


async def _mint(client: AsyncClient, token: str) -> str:
    response = await client.post(
        "/v1/voice/token", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    claims = _decode_jwt(response.json()["participantToken"])
    return json.loads(claims["metadata"])["userId"]


def _decode_jwt(token: str) -> dict:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))
