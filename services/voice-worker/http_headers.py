from __future__ import annotations


AUREN_USER_AGENT = "Auren-Voice-Agent/1.0"


def build_http_headers(
    *,
    api_key: str | None = None,
    accept: str = "application/json",
    content_type: str | None = None,
) -> dict[str, str]:
    """Return explicit headers accepted by authenticated inference proxies.

    Some managed proxies reject Python urllib's default user agent before the
    request reaches the protected upstream. Keeping this policy in one helper
    makes startup, readiness, and smoke-test behavior consistent.
    """
    headers = {
        "User-Agent": AUREN_USER_AGENT,
        "Accept": accept,
    }
    if api_key and api_key != "local":
        headers["Authorization"] = f"Bearer {api_key}"
    if content_type:
        headers["Content-Type"] = content_type
    return headers
