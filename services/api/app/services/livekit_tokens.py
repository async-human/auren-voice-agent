from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import timedelta

from livekit import api

from app.config import Settings


@dataclass(frozen=True)
class VoiceSession:
    server_url: str
    participant_token: str
    room_name: str
    user_id: str
    stt_provider: str


def create_voice_session(
    settings: Settings,
    user_id: str,
    display_name: str | None = None,
    stt_provider: str = "whisper",
) -> VoiceSession:
    """Mint a participant token for an already-authenticated user.

    `user_id` is always our own identifier, resolved from a verified session.
    It is never accepted from the browser, because the worker uses it to scope
    every tool call and memory lookup.
    """
    if not user_id:
        raise ValueError("user_id is required to create a voice session")

    nonce = uuid.uuid4().hex
    room_name = f"auren-{nonce}"
    metadata = {"userId": user_id, "sttProvider": stt_provider}
    if display_name:
        metadata["displayName"] = display_name

    grants = api.VideoGrants(
        room=room_name,
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )

    room_config = api.RoomConfiguration(
        agents=[
            api.RoomAgentDispatch(
                agent_name=settings.livekit_agent_name,
                metadata=json.dumps({"source": "auren-web", **metadata}),
            )
        ]
    )

    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"user-{nonce}")
        .with_name(display_name or "Auren user")
        .with_metadata(json.dumps(metadata))
        .with_ttl(timedelta(minutes=settings.livekit_token_ttl_minutes))
        .with_grants(grants)
        .with_room_config(room_config)
    )

    return VoiceSession(
        server_url=settings.livekit_url,
        participant_token=token.to_jwt(),
        room_name=room_name,
        user_id=user_id,
        stt_provider=stt_provider,
    )
