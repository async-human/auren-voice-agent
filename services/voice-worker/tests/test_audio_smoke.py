from __future__ import annotations

from array import array
import importlib.util
import io
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch
import wave


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "runpod"
    / "scripts"
    / "audio_smoke_test.py"
)
SPEC = importlib.util.spec_from_file_location("audio_smoke_test", SCRIPT)
assert SPEC and SPEC.loader
audio_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audio_smoke)


def wav_fixture(*, amplitude: int = 5000, sample_rate: int = 24000) -> bytes:
    samples = array(
        "h",
        (
            int(amplitude * math.sin(2 * math.pi * 440 * index / sample_rate))
            for index in range(int(sample_rate * 0.3))
        ),
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(1)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(samples.tobytes())
    return output.getvalue()


class ActiveAudioSmokeTests(unittest.TestCase):
    def test_inspect_wav_accepts_non_silent_24khz_mono_audio(self) -> None:
        metrics = audio_smoke.inspect_wav(wav_fixture())

        self.assertEqual(metrics["sample_rate"], 24000)
        self.assertEqual(metrics["channels"], 1)
        self.assertGreater(metrics["rms"], 40)

    def test_inspect_wav_rejects_silence(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "effectively silent"):
            audio_smoke.inspect_wav(wav_fixture(amplitude=0))

    def test_transcribe_checks_expected_phrase_words(self) -> None:
        response = json.dumps(
            {"text": "The quick brown fox jumps over the lazy dog."}
        ).encode()
        with patch.object(
            audio_smoke,
            "_request",
            return_value=(response, "application/json"),
        ) as mocked_request:
            transcript, latency = audio_smoke.transcribe(wav_fixture())

        self.assertIn("quick brown fox", transcript)
        self.assertGreaterEqual(latency, 0)
        request = mocked_request.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "Auren-Voice-Agent/1.0")
        self.assertEqual(request.get_header("Accept"), "application/json")

    def test_transcribe_rejects_unrelated_output(self) -> None:
        response = json.dumps({"text": "Completely unrelated output."}).encode()
        with patch.object(
            audio_smoke,
            "_request",
            return_value=(response, "application/json"),
        ):
            with self.assertRaisesRegex(RuntimeError, "round-trip mismatch"):
                audio_smoke.transcribe(wav_fixture())


if __name__ == "__main__":
    unittest.main()
