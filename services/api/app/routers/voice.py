from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import Settings
from app.dependencies import get_settings
from app.models.schemas import (
    VoiceSTTOption,
    VoiceSTTOptionsResponse,
    VoiceTokenRequest,
    VoiceTokenResponse,
)
from app.models.tables import User
from app.security.auth import require_user
from app.services.livekit_tokens import create_voice_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/voice", tags=["voice"])

STT_OPTION_DETAILS = {
    "whisper": ("Whisper", "Mature and dependable multilingual transcription.", False),
    "qwen": ("Qwen3-ASR", "Accuracy-focused multilingual transcription.", False),
}


@router.get("/stt-options", response_model=VoiceSTTOptionsResponse)
async def stt_options(
    settings: Settings = Depends(get_settings),
    _user: User = Depends(require_user),
) -> VoiceSTTOptionsResponse:
    return VoiceSTTOptionsResponse(
        default_provider=settings.stt_default_provider,
        providers=[
            VoiceSTTOption(
                id=provider,
                label=STT_OPTION_DETAILS[provider][0],
                description=STT_OPTION_DETAILS[provider][1],
                realtime=STT_OPTION_DETAILS[provider][2],
            )
            for provider in settings.stt_provider_list
        ],
    )


@router.post("/token", response_model=VoiceTokenResponse)
async def create_token(
    request: Request,
    response: Response,
    payload: VoiceTokenRequest | None = None,
    settings: Settings = Depends(get_settings),
    user: User = Depends(require_user),
) -> VoiceTokenResponse:
    # Keyed by user rather than by IP now that every caller is authenticated.
    request.app.state.voice_token_limiter.check(f"user:{user.id}")

    stt_provider = (
        payload.stt_provider
        if payload and payload.stt_provider
        else settings.stt_default_provider
    )
    if stt_provider not in settings.stt_provider_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"STT provider {stt_provider!r} is not available",
        )

    try:
        session = create_voice_session(
            settings,
            user.id,
            user.display_name,
            stt_provider=stt_provider,
        )
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
        sttProvider=stt_provider,
    )
