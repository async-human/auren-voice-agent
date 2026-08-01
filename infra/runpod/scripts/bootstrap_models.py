from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


READY_FILE = Path("/workspace/runtime/models-ready")
TIMEOUT_SECONDS = int(os.getenv("AUREN_BOOT_TIMEOUT_SECONDS", "1800"))


def wait_for_json(url: str, *, timeout: int = TIMEOUT_SECONDS) -> object:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:  # noqa: S310
                if 200 <= response.status < 300:
                    return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


def preload_whisper() -> None:
    model = os.getenv("FASTER_WHISPER_MODEL", "Systran/faster-whisper-large-v3")
    if os.getenv("STT_PRELOAD", "true").lower() != "true":
        return

    print(f"Preloading STT model {model}", flush=True)
    code = (
        "import sys; from huggingface_hub import snapshot_download; "
        "print(snapshot_download(repo_id=sys.argv[1]))"
    )
    subprocess.run(
        ["/opt/speaches/.venv/bin/python", "-u", "-c", code, model],
        check=True,
        env={**os.environ, "HF_HUB_ENABLE_HF_TRANSFER": "0"},
    )


def preload_ollama() -> None:
    model = os.getenv("LLM_MODEL", "qwen3:8b")
    wait_for_json("http://127.0.0.1:11434/api/version")
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


def main() -> None:
    READY_FILE.unlink(missing_ok=True)
    wait_for_json("http://127.0.0.1:8000/health")
    preload_whisper()
    preload_ollama()
    wait_for_json("http://127.0.0.1:8004/v1/audio/voices")
    READY_FILE.write_text("ready\n", encoding="utf-8")
    print("All Auren model services are ready", flush=True)


if __name__ == "__main__":
    main()
