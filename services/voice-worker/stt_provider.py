from __future__ import annotations

import asyncio
import re
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


_QWEN_TRANSCRIPT_PREFIX = re.compile(
    r"^\s*language\s+(?P<language>[^<\r\n]+?)\s*<asr_text>\s*",
    flags=re.IGNORECASE,
)


def parse_qwen_transcript(raw_text: str) -> tuple[str, str | None]:
    """Split Qwen3-ASR's raw language envelope from the spoken text."""
    match = _QWEN_TRANSCRIPT_PREFIX.match(raw_text)
    if not match:
        return raw_text.strip(), None
    return raw_text[match.end() :].strip(), match.group("language").strip()


class QwenSTT(openai.STT):
    """OpenAI-compatible Qwen client that normalizes its structured output."""

    async def _recognize_impl(self, *args: object, **kwargs: object):
        event = await super()._recognize_impl(*args, **kwargs)
        for alternative in event.alternatives:
            text, detected_language = parse_qwen_transcript(alternative.text)
            alternative.text = text
            if detected_language:
                alternative.metadata = {
                    **(alternative.metadata or {}),
                    "detected_language": detected_language,
                    "provider": "qwen",
                }
        return event


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
    client_type = {
        "qwen": QwenSTT,
        "nemotron": NemotronSTT,
    }.get(config.provider, openai.STT)
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
    "QwenSTT",
    "STTConfig",
    "SUPPORTED_STT_PROVIDERS",
    "available_stt_providers",
    "build_stt",
    "default_stt_provider",
    "normalize_stt_provider",
    "parse_qwen_transcript",
]
