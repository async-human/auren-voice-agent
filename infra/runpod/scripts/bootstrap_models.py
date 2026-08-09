from __future__ import annotations

from array import array
import io
import json
import math
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import uuid
import wave


READY_FILE = Path("/workspace/runtime/models-ready")
TIMEOUT_SECONDS = int(os.getenv("AUREN_BOOT_TIMEOUT_SECONDS", "1800"))
FASTER_WHISPER_BASE_URL = os.getenv(
    "FASTER_WHISPER_BASE_URL", "http://127.0.0.1:8000/v1"
).rstrip("/")
FASTER_WHISPER_MODEL = os.getenv(
    "FASTER_WHISPER_MODEL", "Systran/faster-whisper-medium.en"
)


def wait_for_json(url: str, *, timeout: int = TIMEOUT_SECONDS) -> object:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def preload_whisper() -> None:
    if os.getenv("STT_PRELOAD", "true").lower() != "true":
        return

    print(f"Preloading STT model {FASTER_WHISPER_MODEL}", flush=True)
    code = (
        "import sys; from huggingface_hub import snapshot_download; "
        "print(snapshot_download(repo_id=sys.argv[1]))"
    )
    subprocess.run(
        ["/opt/speaches/.venv/bin/python", "-u", "-c", code, FASTER_WHISPER_MODEL],
        check=True,
        env={**os.environ, "HF_HUB_ENABLE_HF_TRANSFER": "0"},
    )


def _tiny_wav(seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a short non-silent WAV so Speaches actually loads the model."""
    frame_count = int(sample_rate * seconds)
    samples = array("h")
    for index in range(frame_count):
        # Quiet 440Hz tone — enough energy for Whisper to accept the clip.
        value = int(4000 * math.sin(2 * math.pi * 440 * (index / sample_rate)))
        samples.append(value)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def warmup_speaches_stt() -> None:
    """Load Whisper into GPU before Ollama/Chatterbox fill VRAM."""
    print(f"Warming Speaches STT with {FASTER_WHISPER_MODEL}", flush=True)
    audio = _tiny_wav()
    boundary = f"----auren-boot-{uuid.uuid4().hex}"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="model"\r\n\r\n',
            FASTER_WHISPER_MODEL.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="language"\r\n\r\n',
            b"en\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="response_format"\r\n\r\n',
            b"json\r\n",
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="warmup.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            audio,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    request = Request(
        f"{FASTER_WHISPER_BASE_URL}/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    deadline = time.monotonic() + min(TIMEOUT_SECONDS, 600)
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(request, timeout=120) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    payload = json.loads(response.read().decode("utf-8"))
                    print(
                        "Speaches STT warm OK "
                        f"(text={str(payload.get('text', ''))[:80]!r})",
                        flush=True,
                    )
                    return
        except HTTPError as error:
            body_text = error.read().decode("utf-8", errors="replace")[:800]
            last_error = RuntimeError(
                f"HTTP {error.code} from Speaches transcriptions: {body_text}"
            )
            print(f"Speaches warm attempt failed: {last_error}", flush=True)
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            print(f"Speaches warm attempt failed: {error}", flush=True)
        # Rebuild request body each retry (urlopen consumes it).
        request = Request(
            f"{FASTER_WHISPER_BASE_URL}/audio/transcriptions",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        time.sleep(5)
    raise RuntimeError(f"Timed out warming Speaches STT: {last_error}")


def _pull_and_warm_ollama(model: str) -> None:
    print(f"Pulling Ollama model {model}", flush=True)
    subprocess.run(["/usr/local/bin/ollama", "pull", model], check=True)

    payload = json.dumps(
        {
            "model": model,
            "prompt": "Reply only with OK.",
            "stream": False,
            "keep_alive": -1,
            "think": False,
        }
    ).encode("utf-8")
    request = Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=300) as response:  # noqa: S310
        if response.status != 200:
            raise RuntimeError(f"Ollama warmup returned HTTP {response.status}")


def preload_ollama() -> None:
    wait_for_json("http://127.0.0.1:11434/api/version")
    _pull_and_warm_ollama(os.getenv("LLM_MODEL", "qwen3:8b"))

    # Vision is optional and expensive; keep GPU free for Whisper + Chatterbox.
    if os.getenv("VISION_PRELOAD", "false").lower() == "true":
        vision_model = os.getenv("VISION_MODEL", "qwen2.5vl:3b")
        if vision_model and vision_model != os.getenv("LLM_MODEL", "qwen3:8b"):
            _pull_and_warm_ollama(vision_model)


def verify_audio_pipeline() -> None:
    """Warm both audio models and fail startup unless TTS -> STT succeeds."""
    print("Running active TTS -> STT audio smoke test", flush=True)
    subprocess.run(
        ["/usr/bin/python3", "/opt/auren/bin/audio_smoke_test.py"],
        check=True,
    )


def main() -> None:
    READY_FILE.unlink(missing_ok=True)
    wait_for_json("http://127.0.0.1:8000/health")
    preload_whisper()
    # Load Whisper while the GPU is still empty. Doing this after Ollama/Chatterbox
    # often yields HTTP 500 from /v1/audio/transcriptions (CUDA OOM).
    warmup_speaches_stt()
    preload_ollama()
    wait_for_json("http://127.0.0.1:8004/v1/audio/voices")
    verify_audio_pipeline()
    READY_FILE.write_text("ready\n", encoding="utf-8")
    print("All Auren model services are ready", flush=True)


if __name__ == "__main__":
    main()
