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


async def _available_stt_providers(request: Request, settings: Settings) -> list[str]:
    """Return providers verified by the worker, not merely configured names."""
    if not settings.voice_worker_health_url:
        return settings.stt_provider_list
    try:
        response = await request.app.state.http_client.get(
            settings.voice_worker_health_url,
            timeout=3.0,
        )
        payload = response.json()
        reported = payload.get("stt_available_providers") or []
        return [
            provider
            for provider in settings.stt_provider_list
            if provider in reported
        ]
    except Exception as error:  # noqa: BLE001 - availability must fail closed
        logger.warning("Could not verify voice worker STT availability: %s", error)
        return []


@router.get("/stt-options", response_model=VoiceSTTOptionsResponse)
async def stt_options(
    request: Request,
    settings: Settings = Depends(get_settings),
    _user: User = Depends(require_user),
) -> VoiceSTTOptionsResponse:
    providers = await _available_stt_providers(request, settings)
    if settings.stt_default_provider not in providers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The voice service is still starting",
        )
    return VoiceSTTOptionsResponse(
        default_provider=settings.stt_default_provider,
        providers=[
            VoiceSTTOption(
                id=provider,
                label=STT_OPTION_DETAILS[provider][0],
                description=STT_OPTION_DETAILS[provider][1],
                realtime=STT_OPTION_DETAILS[provider][2],
            )
            for provider in providers
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
    available_providers = await _available_stt_providers(request, settings)
    if stt_provider not in available_providers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"STT provider {stt_provider!r} is currently unavailable",
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
