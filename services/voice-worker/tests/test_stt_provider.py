from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from livekit.agents import stt
from livekit.plugins import openai

from stt_provider import (
    NemotronSTT,
    QwenSTT,
    STTConfig,
    available_stt_providers,
    build_stt,
    parse_qwen_transcript,
)


STT_ENV_NAMES = {
    "STT_PROVIDER",
    "STT_AVAILABLE_PROVIDERS",
    "STT_BASE_URL",
    "STT_MODEL",
    "STT_LANGUAGE",
    "STT_USE_REALTIME",
    "STT_API_KEY",
    "STT_HEALTH_URL",
    "FASTER_WHISPER_BASE_URL",
    "FASTER_WHISPER_MODEL",
    "FASTER_WHISPER_LANGUAGE",
    "FASTER_WHISPER_USE_REALTIME",
    "FASTER_WHISPER_API_KEY",
    "FASTER_WHISPER_HEALTH_URL",
    "QWEN_ASR_BASE_URL",
    "QWEN_ASR_MODEL",
    "QWEN_ASR_LANGUAGE",
    "QWEN_ASR_USE_REALTIME",
    "QWEN_ASR_API_KEY",
    "QWEN_ASR_HEALTH_URL",
    "NEMOTRON_ASR_BASE_URL",
    "NEMOTRON_ASR_MODEL",
    "NEMOTRON_ASR_LANGUAGE",
    "NEMOTRON_ASR_USE_REALTIME",
    "NEMOTRON_ASR_API_KEY",
    "NEMOTRON_ASR_HEALTH_URL",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
}


class STTProviderTests(unittest.TestCase):
    def config(self, **values: str) -> STTConfig:
        environment = {key: value for key, value in os.environ.items() if key not in STT_ENV_NAMES}
        environment.update(values)
        with patch.dict(os.environ, environment, clear=True):
            return STTConfig.from_env()

    def selected_config(self, provider: str, **values: str) -> STTConfig:
        environment = {key: value for key, value in os.environ.items() if key not in STT_ENV_NAMES}
        environment.update(values)
        with patch.dict(os.environ, environment, clear=True):
            return STTConfig.from_env(provider)

    def test_whisper_is_the_backward_compatible_default(self) -> None:
        config = self.config(
            FASTER_WHISPER_BASE_URL="http://127.0.0.1:8000/v1",
            FASTER_WHISPER_MODEL="Systran/faster-whisper-large-v3",
        )

        self.assertEqual(config.provider, "whisper")
        self.assertEqual(config.model, "Systran/faster-whisper-large-v3")
        self.assertEqual(config.language, "en")
        self.assertFalse(config.detect_language)
        self.assertFalse(config.use_realtime)

    def test_qwen_uses_vllm_transcriptions_with_language_detection(self) -> None:
        config = self.config(
            STT_PROVIDER="qwen3-asr",
            STT_BASE_URL="http://qwen-asr.internal:8000",
        )

        self.assertEqual(config.provider, "qwen")
        self.assertEqual(config.base_url, "http://qwen-asr.internal:8000/v1")
        self.assertEqual(config.model, "Qwen/Qwen3-ASR-1.7B")
        self.assertEqual(config.language, "")
        self.assertTrue(config.detect_language)
        self.assertFalse(config.use_realtime)
        self.assertEqual(config.health_url, "http://qwen-asr.internal:8000/health")

    def test_nemotron_enables_native_realtime_by_default(self) -> None:
        config = self.config(
            STT_PROVIDER="nemotron-3.5",
            NEMOTRON_ASR_BASE_URL="http://nemotron.internal:8080/v1/",
        )

        self.assertEqual(config.provider, "nemotron")
        self.assertEqual(
            config.model,
            "nvidia/nemotron-3.5-asr-streaming-0.6b",
        )
        self.assertEqual(config.language, "auto")
        self.assertFalse(config.detect_language)
        self.assertTrue(config.use_realtime)

    def test_explicit_language_disables_qwen_auto_detection(self) -> None:
        config = self.config(
            STT_PROVIDER="qwen",
            STT_BASE_URL="https://asr.example.com/v1",
            STT_LANGUAGE="hi",
            STT_API_KEY="secret",
        )

        self.assertEqual(config.language, "hi")
        self.assertFalse(config.detect_language)
        self.assertEqual(config.api_key, "secret")

    def test_non_nemotron_realtime_configuration_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "supported only for Nemotron"):
            self.config(
                STT_PROVIDER="qwen",
                STT_BASE_URL="http://127.0.0.1:8001/v1",
                STT_USE_REALTIME="true",
            )

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Unsupported STT_PROVIDER"):
            self.config(STT_PROVIDER="unknown")

    def test_session_selection_uses_provider_specific_endpoint(self) -> None:
        config = self.selected_config(
            "qwen",
            STT_PROVIDER="whisper",
            STT_AVAILABLE_PROVIDERS="whisper,qwen,nemotron",
            STT_BASE_URL="http://127.0.0.1:8000/v1",
            QWEN_ASR_BASE_URL="http://qwen.internal:8001/v1",
            QWEN_ASR_LANGUAGE="hi",
            QWEN_ASR_API_KEY="qwen-secret",
        )

        self.assertEqual(config.provider, "qwen")
        self.assertEqual(config.base_url, "http://qwen.internal:8001/v1")
        self.assertEqual(config.language, "hi")
        self.assertEqual(config.api_key, "qwen-secret")

    def test_session_selection_keeps_nemotron_realtime(self) -> None:
        config = self.selected_config(
            "nemotron",
            STT_PROVIDER="whisper",
            STT_AVAILABLE_PROVIDERS="whisper,nemotron",
            STT_BASE_URL="http://127.0.0.1:8000/v1",
            NEMOTRON_ASR_BASE_URL="http://nemotron.internal:8080/v1",
            NEMOTRON_ASR_USE_REALTIME="true",
        )

        self.assertEqual(config.provider, "nemotron")
        self.assertTrue(config.use_realtime)

    def test_disabled_session_provider_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not enabled"):
            self.selected_config(
                "qwen",
                STT_PROVIDER="whisper",
                STT_AVAILABLE_PROVIDERS="whisper",
                STT_BASE_URL="http://127.0.0.1:8000/v1",
                QWEN_ASR_BASE_URL="http://qwen.internal:8001/v1",
            )

    def test_default_provider_must_be_available(self) -> None:
        environment = {key: value for key, value in os.environ.items() if key not in STT_ENV_NAMES}
        environment.update(
            STT_PROVIDER="whisper",
            STT_AVAILABLE_PROVIDERS="qwen,nemotron",
        )
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(RuntimeError, "must be included"):
                available_stt_providers()

    def test_factory_preserves_provider_capabilities(self) -> None:
        config = self.config(
            STT_PROVIDER="nemotron",
            STT_BASE_URL="http://127.0.0.1:8080/v1",
            STT_LANGUAGE="en-US",
        )

        proxy_overrides = {
            name: ""
            for name in (
                "ALL_PROXY",
                "all_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            )
        }
        with patch.dict(os.environ, proxy_overrides):
            client = build_stt(config)

        self.assertIsInstance(client, NemotronSTT)
        self.assertTrue(client.capabilities.streaming)
        self.assertTrue(client.capabilities.interim_results)
        self.assertEqual(str(client._client.base_url), "http://127.0.0.1:8080/v1/")

    def test_factory_uses_qwen_transcript_adapter(self) -> None:
        config = self.config(
            STT_PROVIDER="qwen",
            STT_BASE_URL="http://127.0.0.1:8011/v1",
        )

        with patch.dict(
            os.environ,
            {name: "" for name in STT_ENV_NAMES if "PROXY" in name.upper()},
        ):
            client = build_stt(config)

        self.assertIsInstance(client, QwenSTT)


class QwenTranscriptTests(unittest.IsolatedAsyncioTestCase):
    def test_parser_extracts_detected_language_and_spoken_text(self) -> None:
        text, language = parse_qwen_transcript(
            "language English<asr_text>Hey Auren, switch back to English."
        )

        self.assertEqual(text, "Hey Auren, switch back to English.")
        self.assertEqual(language, "English")

    def test_parser_handles_hindi_and_surrounding_whitespace(self) -> None:
        text, language = parse_qwen_transcript(
            "  language Hindi <asr_text>  ये कौन सा लैंग्वेज है मेरा?  "
        )

        self.assertEqual(text, "ये कौन सा लैंग्वेज है मेरा?")
        self.assertEqual(language, "Hindi")

    def test_parser_does_not_rewrite_plain_transcripts(self) -> None:
        text, language = parse_qwen_transcript(
            "Language English is what I want to discuss."
        )

        self.assertEqual(text, "Language English is what I want to discuss.")
        self.assertIsNone(language)

    async def test_qwen_client_normalizes_event_before_livekit_receives_it(self) -> None:
        raw_event = stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                stt.SpeechData(
                    text="language English<asr_text>Hello from Qwen.",
                    language="",
                )
            ],
        )
        proxy_overrides = {
            name: ""
            for name in (
                "ALL_PROXY",
                "all_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            )
        }
        with patch.dict(os.environ, proxy_overrides):
            client = QwenSTT(
                model="Qwen/Qwen3-ASR-1.7B",
                base_url="http://127.0.0.1:8011/v1",
                api_key="test-key",
                language="",
                detect_language=True,
                use_realtime=False,
            )

        with patch.object(
            openai.STT,
            "_recognize_impl",
            new=AsyncMock(return_value=raw_event),
        ):
            event = await client._recognize_impl([])

        alternative = event.alternatives[0]
        self.assertEqual(alternative.text, "Hello from Qwen.")
        self.assertEqual(alternative.metadata["detected_language"], "English")
        self.assertEqual(alternative.metadata["provider"], "qwen")


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_json(self, payload: dict[str, object]) -> None:
        self.messages.append(payload)


class FakeSession:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.url = ""
        self.headers: dict[str, str] = {}

    async def ws_connect(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> FakeWebSocket:
        self.url = url
        self.headers = headers
        return self.websocket


class NemotronRealtimeProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_nemo_speech_flat_session_update_schema(self) -> None:
        config = STTConfig(
            provider="nemotron",
            model="nvidia/nemotron-3.5-asr-streaming-0.6b",
            base_url="http://127.0.0.1:8080/v1",
            api_key="test-key",
            language="en-US",
            detect_language=False,
            use_realtime=True,
            health_url="http://127.0.0.1:8080/health",
        )
        proxy_overrides = {
            name: ""
            for name in (
                "ALL_PROXY",
                "all_proxy",
                "HTTP_PROXY",
                "http_proxy",
                "HTTPS_PROXY",
                "https_proxy",
            )
        }
        with patch.dict(os.environ, proxy_overrides):
            client = build_stt(config)

        websocket = FakeWebSocket()
        session = FakeSession(websocket)
        with patch.object(client, "_ensure_session", return_value=session):
            connected = await client._connect_ws(1.0)

        self.assertIs(connected, websocket)
        self.assertIn("/v1/realtime?model=", session.url)
        self.assertEqual(session.headers["Authorization"], "Bearer test-key")
        self.assertEqual(
            websocket.messages,
            [
                {
                    "type": "session.update",
                    "session": {
                        "sample_rate": 24000,
                        "language": "en-US",
                        "automatic_punctuation": True,
                        "endpointing_ms": 350,
                    },
                }
            ],
        )
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
