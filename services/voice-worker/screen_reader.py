"""Capture the user's shared screen and read it with a local vision model."""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from typing import Any

import httpx
from livekit import rtc

logger = logging.getLogger("auren.screen")

VISION_BASE_URL = os.getenv("VISION_BASE_URL", os.getenv("LLM_BASE_URL", "")).rstrip("/")
VISION_MODEL = os.getenv("VISION_MODEL", "qwen2.5vl:3b")
VISION_TIMEOUT_SECONDS = float(os.getenv("VISION_TIMEOUT_SECONDS", "90"))

READ_PROMPT = (
    "You are reading the user's shared computer screen. Extract all readable text "
    "you can see, preserving reading order. Then give a short plain-language "
    "summary of what the screen is showing. Prefer the main document, article, "
    "or app content over browser chrome, ads, and UI chrome. If text is cut off "
    "at the edges, note that only the visible portion is available."
)


class ScreenReader:
    """Keep the latest screen-share frame and describe it on demand."""

    def __init__(self) -> None:
        self._latest_jpeg: bytes | None = None
        self._has_track = False
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def is_sharing(self) -> bool:
        return self._has_track and self._latest_jpeg is not None

    def attach(self, room: rtc.Room) -> None:
        room.on("track_subscribed", self._on_track_subscribed)
        room.on("track_unsubscribed", self._on_track_unsubscribed)

        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                track = publication.track
                if track is None:
                    continue
                if self._is_screen_track(publication, track):
                    self._start_stream(track)

    def _on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        _participant: rtc.RemoteParticipant,
    ) -> None:
        if self._is_screen_track(publication, track):
            self._start_stream(track)

    def _on_track_unsubscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        _participant: rtc.RemoteParticipant,
    ) -> None:
        if self._is_screen_track(publication, track):
            self._has_track = False
            self._latest_jpeg = None
            logger.info("Screen share ended")

    @staticmethod
    def _is_screen_track(
        publication: rtc.RemoteTrackPublication | rtc.TrackPublication,
        track: rtc.Track,
    ) -> bool:
        if track.kind != rtc.TrackKind.KIND_VIDEO:
            return False
        source = getattr(publication, "source", None)
        if source == rtc.TrackSource.SOURCE_SCREENSHARE:
            return True
        name = (getattr(publication, "name", None) or "").lower()
        if "screen" in name:
            return True
        # Until a separate camera path exists, treat the user's video track as
        # the shared surface (browser screen-share).
        return True

    def _start_stream(self, track: rtc.Track) -> None:
        self._has_track = True
        task = asyncio.create_task(self._consume(track))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("Watching screen-share video track")

    async def _consume(self, track: rtc.Track) -> None:
        stream = rtc.VideoStream(track, format=rtc.VideoBufferType.RGBA)
        try:
            async for event in stream:
                frame = event.frame
                jpeg = _frame_to_jpeg(frame)
                if jpeg is None:
                    continue
                async with self._lock:
                    self._latest_jpeg = jpeg
        except Exception as error:  # noqa: BLE001 - track end is normal
            logger.info("Screen video stream closed: %s", error)
        finally:
            await stream.aclose()

    async def read_screen(self) -> str:
        if not VISION_BASE_URL:
            return (
                "Screen reading is not configured. Set VISION_BASE_URL / LLM_BASE_URL "
                "and VISION_MODEL on the voice worker."
            )

        async with self._lock:
            jpeg = self._latest_jpeg

        if jpeg is None:
            return (
                "No screen share is available yet. Ask the user to tap Share screen "
                "in the Auren talk UI and share the window or display they want read, "
                "then ask again."
            )

        encoded = base64.b64encode(jpeg).decode("ascii")
        try:
            text = await _describe_with_ollama(encoded)
        except Exception as error:  # noqa: BLE001 - speakable failure for the model
            logger.warning("Vision read failed: %s", error)
            return (
                f"I could see the shared screen but failed to read it ({error}). "
                "Make sure the vision model is pulled on the pod."
            )

        if not text.strip():
            return "I captured the shared screen but could not extract readable text."

        return (
            "Live screen capture (visible viewport only — content below the fold "
            "is not visible unless the user scrolls). Readable content follows:\n\n"
            f"{text.strip()}"
        )


def _frame_to_jpeg(frame: rtc.VideoFrame) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        logger.error("Pillow is required for screen reading")
        return None

    try:
        rgba = frame.convert(rtc.VideoBufferType.RGBA)
        image = Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data))
        # Downscale large desktops so the VLM stays responsive.
        max_side = 1280
        if max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=75, optimize=True)
        return buffer.getvalue()
    except Exception as error:  # noqa: BLE001
        logger.warning("Could not encode screen frame: %s", error)
        return None


async def _describe_with_ollama(image_b64: str) -> str:
    # Prefer the native Ollama chat endpoint; OpenAI-compatible image parts vary.
    ollama_host = VISION_BASE_URL
    if ollama_host.endswith("/v1"):
        ollama_host = ollama_host[: -len("/v1")]

    payload = {
        "model": VISION_MODEL,
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": READ_PROMPT,
                "images": [image_b64],
            }
        ],
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=VISION_TIMEOUT_SECONDS) as client:
        response = await client.post(f"{ollama_host}/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
    message = body.get("message") or {}
    content = message.get("content") or body.get("response") or ""
    return str(content).strip()
