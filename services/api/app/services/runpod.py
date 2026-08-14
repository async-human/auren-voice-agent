"""Start a stopped RunPod so queued speech can be spoken on the next session."""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

logger = logging.getLogger("auren.runpod")

START_URL = "https://rest.runpod.io/v1/pods/{pod_id}/start"


async def start_pod(settings: Settings, http: httpx.AsyncClient | None) -> bool:
    if http is None or not settings.pod_wake_configured:
        return False
    assert settings.runpod_pod_id is not None
    assert settings.runpod_api_key is not None
    try:
        response = await http.post(
            START_URL.format(pod_id=settings.runpod_pod_id),
            headers={"Authorization": f"Bearer {settings.runpod_api_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as error:
        logger.warning("RunPod start failed: %s", error)
        return False
    if 200 <= response.status_code < 300:
        logger.info("RunPod start accepted for %s", settings.runpod_pod_id)
        return True
    logger.warning(
        "RunPod start rejected HTTP %s: %s",
        response.status_code,
        response.text[:200],
    )
    return False
