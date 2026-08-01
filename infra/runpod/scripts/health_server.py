from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def http_ok(url: str) -> tuple[bool, str]:
    try:
        with urlopen(url, timeout=3) as response:  # noqa: S310
            return 200 <= response.status < 300, f"http_{response.status}"
    except (HTTPError, URLError, TimeoutError) as error:
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


def readiness() -> tuple[bool, dict[str, object]]:
    checks: dict[str, tuple[bool, str]] = {
        "speaches": http_ok("http://127.0.0.1:8000/health"),
        "ollama": http_ok("http://127.0.0.1:11434/api/version"),
        "chatterbox": http_ok("http://127.0.0.1:8004/v1/audio/voices"),
        "voice_worker": process_ok("voice-worker"),
    }
    marker = Path("/workspace/runtime/models-ready").is_file()
    ready = marker and all(value[0] for value in checks.values())
    return ready, {
        "status": "ready" if ready else "starting",
        "models_ready": marker,
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
