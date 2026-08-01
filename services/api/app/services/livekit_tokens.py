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


def create_voice_session(settings: Settings, user_id: str | None = None) -> VoiceSession:
    nonce = uuid.uuid4().hex
    room_name = f"auren-{nonce}"
    resolved_user_id = user_id or f"anonymous-{nonce}"

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
                metadata=json.dumps({"source": "auren-web", "userId": resolved_user_id}),
            )
        ]
    )

    token = (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(f"user-{nonce}")
        .with_name("Auren user")
        .with_metadata(json.dumps({"userId": resolved_user_id}))
        .with_ttl(timedelta(minutes=settings.livekit_token_ttl_minutes))
        .with_grants(grants)
        .with_room_config(room_config)
    )

    return VoiceSession(
        server_url=settings.livekit_url,
        participant_token=token.to_jwt(),
        room_name=room_name,
        user_id=resolved_user_id,
    )
