from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from stt_provider import NemotronSTT, STTConfig, build_stt


STT_ENV_NAMES = {
    "STT_PROVIDER",
    "STT_BASE_URL",
    "STT_MODEL",
    "STT_LANGUAGE",
    "STT_USE_REALTIME",
    "STT_API_KEY",
    "STT_HEALTH_URL",
    "FASTER_WHISPER_BASE_URL",
    "FASTER_WHISPER_MODEL",
    "QWEN_ASR_BASE_URL",
    "QWEN_ASR_MODEL",
    "NEMOTRON_ASR_BASE_URL",
    "NEMOTRON_ASR_MODEL",
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
