"""Google OAuth connect + token refresh for Calendar and Gmail."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.tables import OAuthConnection, utcnow
from app.security.token_crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"

SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.modify",
)


def google_configured(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def authorize_url(settings: Settings, *, state: str) -> str:
    if not google_configured(settings):
        raise RuntimeError("Google OAuth is not configured")
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


async def exchange_code(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    code: str,
) -> dict[str, Any]:
    response = await http.post(
        GOOGLE_TOKEN,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    return response.json()


async def refresh_access_token(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    refresh_token: str,
) -> dict[str, Any]:
    response = await http.post(
        GOOGLE_TOKEN,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    response.raise_for_status()
    return response.json()


async def upsert_connection(
    session: AsyncSession,
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    user_id: str,
    token_payload: dict[str, Any],
) -> OAuthConnection:
    access = token_payload["access_token"]
    refresh = token_payload.get("refresh_token")
    expires_in = int(token_payload.get("expires_in") or 3600)

    email = None
    try:
        info = await http.get(
            GOOGLE_USERINFO,
            headers={"Authorization": f"Bearer {access}"},
        )
        if info.status_code == 200:
            email = info.json().get("email")
    except httpx.HTTPError:
        logger.warning("Could not fetch Google userinfo")

    existing = await session.scalar(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "google",
        )
    )
    if existing is None:
        existing = OAuthConnection(user_id=user_id, provider="google")
        session.add(existing)

    existing.account_email = email
    existing.scopes = " ".join(SCOPES)
    existing.access_token_encrypted = encrypt_secret(settings, access)
    if refresh:
        existing.refresh_token_encrypted = encrypt_secret(settings, refresh)
    existing.expires_at = utcnow() + timedelta(seconds=max(expires_in - 60, 60))
    await session.commit()
    await session.refresh(existing)
    return existing


async def get_connection(session: AsyncSession, user_id: str) -> OAuthConnection | None:
    return await session.scalar(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "google",
        )
    )


async def delete_connection(session: AsyncSession, user_id: str) -> bool:
    row = await get_connection(session, user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def valid_access_token(
    session: AsyncSession,
    settings: Settings,
    http: httpx.AsyncClient,
    user_id: str,
) -> str:
    connection = await get_connection(session, user_id)
    if connection is None:
        raise RuntimeError(
            "Google is not connected. Ask the user to connect Google in Auren settings."
        )

    expires = connection.expires_at
    if expires is not None and expires.tzinfo is None:
        from datetime import timezone

        expires = expires.replace(tzinfo=timezone.utc)

    if expires is None or expires > utcnow():
        return decrypt_secret(settings, connection.access_token_encrypted)

    if not connection.refresh_token_encrypted:
        raise RuntimeError("Google access expired. Ask the user to reconnect Google.")

    refresh = decrypt_secret(settings, connection.refresh_token_encrypted)
    payload = await refresh_access_token(settings, http, refresh_token=refresh)
    connection.access_token_encrypted = encrypt_secret(settings, payload["access_token"])
    connection.expires_at = utcnow() + timedelta(seconds=int(payload.get("expires_in") or 3600) - 60)
    await session.commit()
    return payload["access_token"]
