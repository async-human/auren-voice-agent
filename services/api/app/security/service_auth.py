from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status

SERVICE_TOKEN_HEADER = "X-Auren-Service-Token"


async def require_service_token(
    request: Request,
    x_auren_service_token: str | None = Header(default=None),
) -> None:
    """Authenticate the voice worker (or any internal caller) for tool routes."""
    settings = request.app.state.settings
    expected = settings.tool_gateway_token

    if not expected:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Tool gateway is not configured",
            )
        return

    if not x_auren_service_token or not hmac.compare_digest(x_auren_service_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )
