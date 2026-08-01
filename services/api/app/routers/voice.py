from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import Settings
from app.dependencies import get_settings
from app.models.schemas import VoiceTokenRequest, VoiceTokenResponse
from app.security.rate_limit import client_key
from app.services.livekit_tokens import create_voice_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])


@router.post("/token", response_model=VoiceTokenResponse)
async def create_token(
    request: Request,
    response: Response,
    payload: VoiceTokenRequest | None = None,
    settings: Settings = Depends(get_settings),
) -> VoiceTokenResponse:
    request.app.state.voice_token_limiter.check(client_key(request))

    try:
        session = create_voice_session(settings, payload.user_id if payload else None)
    except Exception:
        logger.exception("Failed to mint LiveKit token")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Voice service unavailable",
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return VoiceTokenResponse(
        serverUrl=session.server_url,
        participantToken=session.participant_token,
    )
