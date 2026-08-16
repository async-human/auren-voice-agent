from __future__ import annotations

import base64
import json
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

from httpx import AsyncClient
import pytest

from app.config import Settings
from app.routers import voice


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
    assert body["sttProvider"] == "whisper"

    claims = _decode_jwt(body["participantToken"])
    grants = claims["video"]
    assert grants["roomJoin"] is True
    assert grants["canPublish"] is True
    assert grants["canSubscribe"] is True
    assert grants["canPublishData"] is True
    assert grants["room"].startswith("auren-")
    assert claims["sub"].startswith("user-")

    metadata = json.loads(claims["metadata"])
    user_id = metadata["userId"]
    assert not user_id.startswith("anonymous-")
    assert metadata["sttProvider"] == "whisper"

    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "auren-agent"
    assert json.loads(dispatch["metadata"])["sttProvider"] == "whisper"


async def test_voice_stt_options_are_authenticated_and_config_driven(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    unauthorized = await client.get("/v1/voice/stt-options")
    response = await client.get("/v1/voice/stt-options", headers=auth_headers)

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    body = response.json()
    assert body["default_provider"] == "whisper"
    assert [provider["id"] for provider in body["providers"]] == [
        "whisper",
        "qwen",
    ]
    assert all(provider["realtime"] is False for provider in body["providers"])


async def test_stt_options_filter_unhealthy_companion_provider(settings) -> None:
    worker_response = SimpleNamespace(
        json=lambda: {"stt_available_providers": ["whisper"]}
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                http_client=SimpleNamespace(get=AsyncMock(return_value=worker_response))
            )
        )
    )
    checked_settings = settings.model_copy(
        update={"voice_worker_health_url": "https://worker.test/health/ready"}
    )

    providers = await voice._available_stt_providers(request, checked_settings)

    assert providers == ["whisper"]


def test_production_requires_worker_health_for_multiple_stt_providers(settings) -> None:
    values = settings.model_dump()
    values.update(
        auren_env="production",
        stt_available_providers="whisper,qwen",
        voice_worker_health_url=None,
    )

    with pytest.raises(ValueError, match="VOICE_WORKER_HEALTH_URL"):
        Settings(**values)


async def test_voice_token_carries_selected_stt_provider(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/voice/token",
        headers=auth_headers,
        json={"stt_provider": "qwen"},
    )

    assert response.status_code == 200
    assert response.json()["sttProvider"] == "qwen"
    claims = _decode_jwt(response.json()["participantToken"])
    assert json.loads(claims["metadata"])["sttProvider"] == "qwen"
    dispatch = claims["roomConfig"]["agents"][0]
    assert json.loads(dispatch["metadata"])["sttProvider"] == "qwen"


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
        "check_tool_status",
        "get_page_context",
        "recall",
        "remember",
        "forget",
        "list_calendar_events",
        "find_free_slots",
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "search_emails",
        "read_email",
        "trash_email",
        "draft_email",
        "send_email",
        "list_pending_actions",
        "confirm_pending_action",
        "reject_pending_action",
        "start_workflow",
        "update_workflow",
        "complete_workflow",
        "schedule_followup",
        "create_document",
        "create_spreadsheet",
        "create_presentation",
        "list_artifacts",
    }
    for tool in response.json():
        assert tool["domain"]
        assert tool["operation"]
        assert tool["risk"] in {"read", "write", "consequential", "destructive"}
        assert isinstance(tool["parallel_safe"], bool)


async def test_authenticated_capability_catalog_is_complete(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/v1/capabilities", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    catalog_tools = {
        tool["name"]
        for group in body["capabilities"]
        for tool in group["tools"]
    }
    gateway_tools = {tool["name"] for tool in (await client.get("/v1/tools")).json()}
    assert catalog_tools == gateway_tools
    assert {group["domain"] for group in body["capabilities"]} >= {
        "artifacts",
        "calendar",
        "gmail",
        "research",
        "workflows",
    }


async def test_capability_catalog_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/v1/capabilities")
    assert response.status_code == 401


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
