from __future__ import annotations

import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_http_client, get_session, get_settings
from app.models.tables import User
from app.security.auth import require_user
from app.services import google_oauth

router = APIRouter(prefix="/v1/connections", tags=["connections"])

@router.get("")
async def list_connections(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    connection = await google_oauth.get_connection(session, user.id)
    return {
        "google": {
            "connected": connection is not None,
            "account_email": connection.account_email if connection else None,
            "timezone": connection.timezone_name if connection else None,
            "scopes": connection.scopes if connection else None,
        }
    }


@router.get("/google/start")
async def google_start(
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not google_oauth.google_configured(settings):
        raise HTTPException(
            status_code=503,
            detail="Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET on the API.",
        )
    state = secrets.token_urlsafe(24)
    await google_oauth.store_oauth_state(session, state=state, user_id=user.id)
    return {"authorize_url": google_oauth.authorize_url(settings, state=state)}


@router.get("/google/callback")
async def google_callback(
    code: str | None = Query(default=None),
    state: str = Query(...),
    error: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    user_id = await google_oauth.consume_oauth_state(session, state=state)
    if not user_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    if error or not code:
        target = f"{settings.public_app_url.rstrip('/')}/talk?google=cancelled"
        return RedirectResponse(url=target)
    token_payload = await google_oauth.exchange_code(settings, http, code=code)
    await google_oauth.upsert_connection(
        session, settings, http, user_id=user_id, token_payload=token_payload
    )
    target = f"{settings.public_app_url.rstrip('/')}/talk?google=connected"
    return RedirectResponse(url=target)


@router.delete("/google")
async def google_disconnect(
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
    session: AsyncSession = Depends(get_session),
) -> dict:
    removed, revoked = await google_oauth.revoke_connection(
        session, settings, http, user_id=user.id
    )
    return {"disconnected": removed, "revoked": revoked}
