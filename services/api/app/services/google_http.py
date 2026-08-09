"""Bounded retry policy for transient Google API failures."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


async def request(
    http: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_attempts: int = 3,
    retryable: bool | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Retry rate limits and transient failures without retrying user errors."""
    if retryable is None:
        retryable = method.upper() in {"GET", "HEAD", "PUT", "DELETE", "OPTIONS"}
    if not retryable:
        max_attempts = 1
    for attempt in range(max_attempts):
        try:
            response = await http.request(method, url, **kwargs)
        except httpx.TransportError:
            if attempt + 1 >= max_attempts:
                raise
        else:
            if response.status_code not in RETRYABLE_STATUSES or attempt + 1 >= max_attempts:
                return response
            retry_after = response.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = min(float(retry_after), 5.0)
            else:
                delay = 0.2 * (2**attempt)
            await asyncio.sleep(delay)
            continue
        await asyncio.sleep(0.2 * (2**attempt))
    raise RuntimeError("Google request retry loop ended unexpectedly")
