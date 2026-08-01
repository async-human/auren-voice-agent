"""Publish the number of live conversations for the RunPod idle watchdog.

The watchdog is a separate process, so the count is shared through a small JSON
file. It carries a heartbeat timestamp because a crashed worker would otherwise
leave a stale non-zero count behind and keep an idle Pod running forever: the
watchdog treats a stale file as idle rather than trusting the number in it.

Outside RunPod the target directory does not exist and the tracker disables
itself, so local development is unaffected.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("auren.session_state")

DEFAULT_STATE_FILE = "/workspace/runtime/active-sessions.json"
HEARTBEAT_SECONDS = 15.0


class SessionTracker:
    def __init__(
        self,
        path: str | None = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
    ) -> None:
        raw_path = path if path is not None else os.getenv(
            "AUREN_SESSION_STATE_FILE", DEFAULT_STATE_FILE
        )
        self._path = Path(raw_path) if raw_path else None
        self._heartbeat_seconds = heartbeat_seconds
        self._active = 0
        self._lock = asyncio.Lock()
        self._heartbeat: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return self._path is not None and self._path.parent.is_dir()

    @property
    def active_sessions(self) -> int:
        return self._active

    async def acquire(self) -> None:
        async with self._lock:
            self._active += 1
            self._publish()
            self._ensure_heartbeat()

    async def release(self) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            self._publish()

    def _ensure_heartbeat(self) -> None:
        if self._heartbeat is not None or not self.enabled:
            return
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            async with self._lock:
                self._publish()

    def _publish(self) -> None:
        if not self.enabled or self._path is None:
            return

        payload = json.dumps(
            {"active_sessions": self._active, "updated_at": time.time()}
        )
        temporary = self._path.with_name(f"{self._path.name}.tmp")
        try:
            temporary.write_text(payload, encoding="utf-8")
            temporary.replace(self._path)
        except OSError as error:
            logger.warning("Could not publish session state: %s", error)
