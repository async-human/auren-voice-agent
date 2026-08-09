from __future__ import annotations

import asyncio
from urllib.parse import urlencode

import aiohttp
from livekit.plugins import openai

from stt_settings import (
    STTConfig,
    SUPPORTED_STT_PROVIDERS,
    available_stt_providers,
    default_stt_provider,
    normalize_stt_provider,
)


class NemotronSTT(openai.STT):
    """LiveKit STT client for NeMo-Speech.cpp's flat realtime session schema."""

    def __init__(self, *, language: str = "auto", **kwargs: object) -> None:
        # LiveKit normalizes BCP-47 locales such as en-US to their base language.
        # Nemotron uses the full locale as a prompt, so preserve the configured value.
        self._nemotron_language = language
        super().__init__(language=language, **kwargs)

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        query = urlencode({"model": self._opts.model})
        url = f"{str(self._client.base_url).rstrip('/')}/realtime?{query}"
        if url.startswith("http"):
            url = url.replace("http", "ws", 1)

        headers = {
            "User-Agent": "Auren LiveKit Agent",
            "Authorization": f"Bearer {self._client.api_key}",
        }
        session = self._ensure_session()
        websocket = await asyncio.wait_for(
            session.ws_connect(url, headers=headers),
            timeout,
        )
        await websocket.send_json(
            {
                "type": "session.update",
                "session": {
                    "sample_rate": 24000,
                    "language": self._nemotron_language or "auto",
                    "automatic_punctuation": True,
                    "endpointing_ms": 350,
                },
            }
        )
        return websocket


def build_stt(config: STTConfig) -> openai.STT:
    """Build the LiveKit STT client against the selected compatible endpoint."""
    client_type = NemotronSTT if config.provider == "nemotron" else openai.STT
    return client_type(
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        language=config.language,
        detect_language=config.detect_language,
        use_realtime=config.use_realtime,
    )


__all__ = [
    "NemotronSTT",
    "STTConfig",
    "SUPPORTED_STT_PROVIDERS",
    "available_stt_providers",
    "build_stt",
    "default_stt_provider",
    "normalize_stt_provider",
]
