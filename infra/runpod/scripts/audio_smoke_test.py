"""Active startup probe for the selected Chatterbox -> STT audio path."""

from __future__ import annotations

from array import array
import io
import json
import math
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import wave

from http_headers import build_http_headers
from stt_settings import STTConfig


RESULT_FILE = Path(
    os.getenv("AUREN_AUDIO_SMOKE_FILE", "/workspace/runtime/audio-smoke.json")
)
TIMEOUT_SECONDS = float(os.getenv("AUREN_AUDIO_SMOKE_TIMEOUT_SECONDS", "180"))
SMOKE_TEXT = "The quick brown fox jumps over the lazy dog."
EXPECTED_WORDS = {"quick", "brown", "fox", "lazy", "dog"}
STT_CONFIG = STTConfig.from_env()


def _request(request: Request, *, timeout: float = TIMEOUT_SECONDS) -> tuple[bytes, str]:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            content_type = response.headers.get("Content-Type", "")
            return response.read(), content_type
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {error.code} from {request.full_url}: {body}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Request failed for {request.full_url}: {error}") from error


def synthesize() -> tuple[bytes, float]:
    base_url = os.getenv("CHATTERBOX_BASE_URL", "http://127.0.0.1:8004/v1").rstrip(
        "/"
    )
    payload = json.dumps(
        {
            "model": os.getenv("CHATTERBOX_MODEL", "chatterbox-turbo"),
            "input": SMOKE_TEXT,
            "voice": os.getenv("CHATTERBOX_VOICE", "Olivia.wav"),
            "response_format": "wav",
            "seed": 7,
        }
    ).encode("utf-8")
    request = Request(
        f"{base_url}/audio/speech",
        data=payload,
        headers=build_http_headers(
            accept="audio/wav",
            content_type="application/json",
        ),
        method="POST",
    )
    started = time.monotonic()
    audio, content_type = _request(request)
    latency = time.monotonic() - started
    if not content_type.startswith("audio/"):
        raise RuntimeError(f"TTS returned non-audio content type {content_type!r}")
    if len(audio) < 128:
        raise RuntimeError(f"TTS returned only {len(audio)} bytes")
    return audio, latency


def inspect_wav(audio: bytes) -> dict[str, float | int]:
    try:
        with wave.open(io.BytesIO(audio), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            pcm = source.readframes(frame_count)
    except (EOFError, wave.Error) as error:
        raise RuntimeError(f"TTS payload is not a valid WAV file: {error}") from error

    if channels != 1 or sample_width != 2 or sample_rate != 24000:
        raise RuntimeError(
            "Unexpected WAV format "
            f"channels={channels} width={sample_width} rate={sample_rate}"
        )
    duration = frame_count / sample_rate
    if duration < 0.25:
        raise RuntimeError(f"TTS audio is too short ({duration:.3f}s)")

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        raise RuntimeError("TTS WAV contains no PCM samples")
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    peak = max(abs(sample) for sample in samples)
    if rms < 40 or peak < 200:
        raise RuntimeError(f"TTS WAV is effectively silent (rms={rms:.1f}, peak={peak})")

    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "duration_seconds": round(duration, 3),
        "rms": round(rms, 1),
        "peak": peak,
        "bytes": len(audio),
    }


def _multipart(audio: bytes) -> tuple[bytes, str]:
    boundary = f"----auren-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def field(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode(),
                b"\r\n",
            ]
        )

    field("model", STT_CONFIG.model)
    if STT_CONFIG.language:
        field("language", STT_CONFIG.language)
    field("response_format", "json")
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="audio-smoke.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), boundary


def transcribe(audio: bytes) -> tuple[str, float]:
    body, boundary = _multipart(audio)
    headers = build_http_headers(
        api_key=STT_CONFIG.api_key,
        content_type=f"multipart/form-data; boundary={boundary}",
    )
    request = Request(
        f"{STT_CONFIG.base_url}/audio/transcriptions",
        data=body,
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    response, content_type = _request(request)
    latency = time.monotonic() - started
    if "json" not in content_type:
        raise RuntimeError(f"STT returned unexpected content type {content_type!r}")
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as error:
        raise RuntimeError("STT returned invalid JSON") from error
    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise RuntimeError("STT returned an empty transcript")
    words = set(re.findall(r"[a-z]+", transcript.lower()))
    overlap = words & EXPECTED_WORDS
    if len(overlap) < 4:
        raise RuntimeError(
            f"STT round-trip mismatch: expected common phrase words, got {transcript!r}"
        )
    return transcript, latency


def write_result(payload: dict[str, object]) -> None:
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = RESULT_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(RESULT_FILE)


def main() -> None:
    started = time.monotonic()
    result: dict[str, object] = {
        "status": "failed",
        "checked_at": time.time(),
    }
    try:
        audio, tts_latency = synthesize()
        audio_metrics = inspect_wav(audio)
        transcript, stt_latency = transcribe(audio)
        result.update(
            {
                "status": "passed",
                "stt_provider": STT_CONFIG.provider,
                "stt_model": STT_CONFIG.model,
                "tts_latency_seconds": round(tts_latency, 3),
                "stt_latency_seconds": round(stt_latency, 3),
                "roundtrip_seconds": round(time.monotonic() - started, 3),
                "audio": audio_metrics,
                "transcript": transcript,
            }
        )
    except Exception as error:  # noqa: BLE001 - startup must persist the reason
        result["error"] = str(error)
        write_result(result)
        raise

    write_result(result)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
