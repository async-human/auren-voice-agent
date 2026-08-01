#!/usr/bin/env python3
"""Stop the Pod after a sustained idle period.

GPU utilisation alone is not a safe idle signal for a voice agent: the GPU sits
at zero while a user is speaking or thinking, so a naive threshold would hang up
mid-conversation. The Pod is only stopped when every signal agrees for the whole
idle window:

  * the voice worker reports no active LiveKit sessions
  * model bootstrap has finished, so a slow start is never mistaken for idleness
  * average GPU utilisation stays under the threshold

A startup grace period covers the long first boot while models download.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections import deque
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

POLL_SECONDS = 30.0
GPU_WINDOW_SECONDS = 180.0
# Six missed 15s heartbeats. A stale file means the worker died, and a dead
# worker cannot be holding a conversation open.
SESSION_STALE_SECONDS = 90.0

READY_FILE = Path("/workspace/runtime/models-ready")
SESSION_FILE = Path(
    os.getenv("AUREN_SESSION_STATE_FILE", "/workspace/runtime/active-sessions.json")
)
STOP_URL = "https://rest.runpod.io/v1/pods/{pod_id}/stop"


def log(message: str) -> None:
    print(f"[idle-watchdog] {message}", flush=True)


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_number(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log(f"{name}={raw!r} is not a number; using {default}")
        return default


def gpu_utilisation() -> float | None:
    """Mean utilisation across all visible GPUs, or None if unreadable."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        log(f"Could not read GPU utilisation: {type(error).__name__}")
        return None

    if result.returncode != 0:
        log(f"nvidia-smi exited {result.returncode}")
        return None

    readings = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            readings.append(float(line))
        except ValueError:
            continue

    if not readings:
        return None
    return sum(readings) / len(readings)


def active_sessions() -> int:
    """Live conversations reported by the worker; stale or missing counts as 0."""
    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 0
    except (OSError, ValueError) as error:
        log(f"Could not read session state: {type(error).__name__}")
        return 0

    updated_at = float(payload.get("updated_at", 0))
    if time.time() - updated_at > SESSION_STALE_SECONDS:
        return 0
    return int(payload.get("active_sessions", 0))


def stop_pod(pod_id: str, api_key: str) -> bool:
    request = Request(
        STOP_URL.format(pod_id=pod_id),
        data=b"",
        headers={"Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310
            log(f"Stop request accepted with HTTP {response.status}")
            return 200 <= response.status < 300
    except HTTPError as error:
        log(f"Stop request rejected with HTTP {error.code}")
    except (URLError, TimeoutError) as error:
        log(f"Stop request failed: {type(error).__name__}")
    return False


def main() -> int:
    if not env_flag("AUTO_STOP_ENABLED"):
        log("AUTO_STOP_ENABLED is not set; idle shutdown is off")
        return 0

    pod_id = os.getenv("RUNPOD_POD_ID")
    api_key = os.getenv("RUNPOD_API_KEY")
    if not pod_id or not api_key:
        log("RUNPOD_POD_ID and RUNPOD_API_KEY are required; idle shutdown is off")
        return 0

    idle_seconds = env_number("AUTO_STOP_IDLE_MINUTES", 30.0) * 60.0
    grace_seconds = env_number("AUTO_STOP_STARTUP_GRACE_MINUTES", 20.0) * 60.0
    gpu_threshold = env_number("AUTO_STOP_GPU_THRESHOLD", 5.0)

    log(
        f"Armed: stop after {idle_seconds / 60:.0f}m idle, "
        f"GPU below {gpu_threshold:.0f}%, {grace_seconds / 60:.0f}m startup grace"
    )

    started = time.monotonic()
    samples: deque[tuple[float, float]] = deque()
    idle_since: float | None = None
    reported_grace_end = False

    while True:
        time.sleep(POLL_SECONDS)
        now = time.monotonic()

        if now - started < grace_seconds:
            continue
        if not reported_grace_end:
            log("Startup grace period over; monitoring for idleness")
            reported_grace_end = True

        utilisation = gpu_utilisation()
        if utilisation is not None:
            samples.append((now, utilisation))
        while samples and now - samples[0][0] > GPU_WINDOW_SECONDS:
            samples.popleft()

        sessions = active_sessions()
        bootstrapped = READY_FILE.is_file()
        average_gpu = sum(value for _, value in samples) / len(samples) if samples else None

        idle = (
            bootstrapped
            and sessions == 0
            and average_gpu is not None
            and average_gpu < gpu_threshold
        )

        if not idle:
            if idle_since is not None:
                log(
                    "Activity resumed "
                    f"(sessions={sessions}, gpu={average_gpu}, ready={bootstrapped})"
                )
            idle_since = None
            continue

        if idle_since is None:
            idle_since = now
            log(f"Idle since now (gpu average {average_gpu:.1f}%)")
            continue

        if now - idle_since >= idle_seconds:
            log(f"Idle for {(now - idle_since) / 60:.0f}m; stopping the Pod")
            if stop_pod(pod_id, api_key):
                return 0
            # Leave idle_since set so the next poll retries the stop request.
            log("Stop failed; will retry on the next poll")


if __name__ == "__main__":
    raise SystemExit(main())
