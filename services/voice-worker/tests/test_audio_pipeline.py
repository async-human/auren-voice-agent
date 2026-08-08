from __future__ import annotations

from array import array
import io
import math
import os
import unittest
from unittest.mock import patch
import wave


os.environ.setdefault("LIVEKIT_URL", "wss://example.livekit.cloud")
os.environ.setdefault("LIVEKIT_API_KEY", "test-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-secret")
os.environ.setdefault("FASTER_WHISPER_BASE_URL", "http://127.0.0.1:8000/v1")
os.environ.setdefault("LLM_BASE_URL", "http://127.0.0.1:11434/v1")
os.environ.setdefault("CHATTERBOX_BASE_URL", "http://127.0.0.1:8004/v1")

import agent  # noqa: E402


def wav_fixture(*, sample_rate: int = 24000, duration: float = 0.3) -> bytes:
    samples = array(
        "h",
        (
            int(5000 * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(int(sample_rate * duration))
        ),
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(samples.tobytes())
    return output.getvalue()


class FakeResponse:
    is_error = False
    status_code = 200
    text = ""
    headers = {"content-type": "audio/wav"}
    content = wav_fixture()


class FakeAsyncClient:
    payloads: list[dict[str, object]] = []

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, _: str, *, json: dict[str, object]) -> FakeResponse:
        self.payloads.append(json)
        return FakeResponse()


class InvalidAudioResponse(FakeResponse):
    content = b"not audio" * 20


class InvalidAudioClient(FakeAsyncClient):
    async def post(self, _: str, *, json: dict[str, object]) -> InvalidAudioResponse:
        self.payloads.append(json)
        return InvalidAudioResponse()


async def text_stream(*parts: str):
    for part in parts:
        yield part


class AudioPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_segments_stream_at_sentence_boundaries(self) -> None:
        segments = [
            segment
            async for segment in agent._iter_tts_segments(
                text_stream("The first sentence. ", "The second sentence.")
            )
        ]
        self.assertEqual(segments, ["The first sentence.", "The second sentence."])

    async def test_tts_node_requests_wav_and_emits_pcm_frames(self) -> None:
        FakeAsyncClient.payloads.clear()
        auren = agent.Auren(instructions="Test agent")

        with patch.object(agent.httpx, "AsyncClient", FakeAsyncClient):
            frames = [
                frame
                async for frame in auren.tts_node(
                    text_stream("Auren is ready."),
                    model_settings={},
                )
            ]

        self.assertTrue(frames)
        self.assertEqual(FakeAsyncClient.payloads[0]["response_format"], "wav")
        self.assertEqual(frames[0].sample_rate, 24000)
        self.assertEqual(frames[0].num_channels, 1)
        self.assertGreater(sum(frame.samples_per_channel for frame in frames), 0)

    def test_decoder_rejects_invalid_audio(self) -> None:
        with self.assertRaises(Exception):
            agent._decode_audio_frames(b"not audio")

    async def test_tts_node_surfaces_invalid_audio_instead_of_failing_silently(
        self,
    ) -> None:
        auren = agent.Auren(instructions="Test agent")

        with patch.object(agent.httpx, "AsyncClient", InvalidAudioClient):
            with self.assertRaisesRegex(RuntimeError, "invalid audio"):
                _ = [
                    frame
                    async for frame in auren.tts_node(
                        text_stream("This must produce audio."),
                        model_settings={},
                    )
                ]


if __name__ == "__main__":
    unittest.main()
