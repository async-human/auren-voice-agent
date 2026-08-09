from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from http_headers import build_http_headers
from stt_settings import STTConfig, available_stt_providers


STT_CONFIG = STTConfig.from_env()
STT_CONFIGS = {
    provider: STTConfig.from_env(provider) for provider in available_stt_providers()
}


def http_ok(url: str, *, api_key: str | None = None) -> tuple[bool, str]:
    request = Request(url, headers=build_http_headers(api_key=api_key))
    try:
        with urlopen(request, timeout=3) as response:  # noqa: S310
            return 200 <= response.status < 300, f"http_{response.status}"
    except HTTPError as error:
        return False, f"http_{error.code}"
    except (URLError, TimeoutError) as error:
        return False, type(error).__name__


def process_ok(name: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["supervisorctl", "-c", "/etc/supervisor/conf.d/auren.conf", "status", name],
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    output = (result.stdout or result.stderr).strip()
    return result.returncode == 0 and "RUNNING" in output, output


def active_sessions() -> int:
    """Live conversations reported by the worker; stale or missing counts as 0."""
    path = Path(
        os.getenv("AUREN_SESSION_STATE_FILE", "/workspace/runtime/active-sessions.json")
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return 0
    if time.time() - float(payload.get("updated_at", 0)) > 90:
        return 0
    return int(payload.get("active_sessions", 0))


def audio_smoke() -> tuple[bool, dict[str, object]]:
    path = Path(
        os.getenv("AUREN_AUDIO_SMOKE_FILE", "/workspace/runtime/audio-smoke.json")
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError) as error:
        return False, {"status": "missing", "detail": type(error).__name__}
    return payload.get("status") == "passed", payload


def readiness() -> tuple[bool, dict[str, object]]:
    checks: dict[str, tuple[bool, str]] = {
        **{
            f"stt_{provider}": http_ok(config.health_url, api_key=config.api_key)
            for provider, config in STT_CONFIGS.items()
        },
        "ollama": http_ok("http://127.0.0.1:11434/api/version"),
        "chatterbox": http_ok("http://127.0.0.1:8004/v1/audio/voices"),
        "voice_worker": process_ok("voice-worker"),
    }
    marker = Path("/workspace/runtime/models-ready").is_file()
    audio_ready, audio_result = audio_smoke()
    ready = marker and audio_ready and all(value[0] for value in checks.values())
    return ready, {
        "status": "ready" if ready else "starting",
        "models_ready": marker,
        "stt_provider": STT_CONFIG.provider,
        "stt_model": STT_CONFIG.model,
        "stt_available_providers": list(STT_CONFIGS),
        "active_sessions": active_sessions(),
        "audio_smoke": audio_result,
        "checks": {
            name: {"ok": value[0], "detail": value[1]} for name, value in checks.items()
        },
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health/live":
            self.respond(200, {"status": "alive"})
            return
        if self.path == "/health/ready":
            ready, body = readiness()
            self.respond(200 if ready else 503, body)
            return
        self.respond(404, {"error": "not_found"})

    def respond(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


if __name__ == "__main__":
    port = int(os.getenv("AUREN_HEALTH_PORT", "9090"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
