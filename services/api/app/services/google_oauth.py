"""Google OAuth connect + token refresh for Calendar and Gmail."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.tables import OAuthConnection, OAuthState, utcnow
from app.security.token_crypto import decrypt_secret, encrypt_secret
from app.services.google_http import request as google_request

logger = logging.getLogger(__name__)

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_PRIMARY_CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary"

OAUTH_STATE_TTL = timedelta(minutes=10)

SCOPES = (
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/calendar.events",
    # gmail.modify is the narrowest Gmail scope that supports reading,
    # drafting/sending, and reversible move-to-Trash operations. It explicitly
    # does not permit immediate permanent deletion.
    "https://www.googleapis.com/auth/gmail.modify",
)

REQUIRED_DATA_SCOPES = frozenset(SCOPES[3:])


class GoogleAuthError(RuntimeError):
    """A Google connection problem safe to explain to the user."""


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
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{GOOGLE_AUTH}?{urlencode(params)}"


async def exchange_code(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    code: str,
) -> dict[str, Any]:
    response = await google_request(
        http,
        "POST",
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
    response = await google_request(
        http,
        "POST",
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
    timezone_name = None
    try:
        info = await google_request(
            http,
            "GET",
            GOOGLE_USERINFO,
            headers={"Authorization": f"Bearer {access}"},
        )
        if info.status_code == 200:
            email = info.json().get("email")
    except httpx.HTTPError:
        logger.warning("Could not fetch Google userinfo")
    try:
        calendar = await google_request(
            http,
            "GET",
            GOOGLE_PRIMARY_CALENDAR,
            headers={"Authorization": f"Bearer {access}"},
        )
        if calendar.status_code == 200:
            timezone_name = calendar.json().get("timeZone")
    except httpx.HTTPError:
        logger.warning("Could not fetch Google Calendar timezone")

    existing = await session.scalar(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "google",
        )
    )
    if existing is None:
        existing = OAuthConnection(user_id=user_id, provider="google")
        session.add(existing)

    granted_scopes = set(str(token_payload.get("scope") or " ".join(SCOPES)).split())
    missing = REQUIRED_DATA_SCOPES - granted_scopes
    if missing:
        raise RuntimeError(
            "Google did not grant the permissions Auren needs: " + ", ".join(sorted(missing))
        )

    existing.account_email = email
    if timezone_name:
        existing.timezone_name = timezone_name
    existing.scopes = " ".join(sorted(granted_scopes))
    existing.access_token_encrypted = encrypt_secret(settings, access)
    if refresh:
        existing.refresh_token_encrypted = encrypt_secret(settings, refresh)
    existing.expires_at = utcnow() + timedelta(seconds=max(expires_in - 60, 0))
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


async def connection_timezone(
    session: AsyncSession,
    user_id: str,
    *,
    default: str,
) -> str:
    row = await get_connection(session, user_id)
    return row.timezone_name if row and row.timezone_name else default


async def delete_connection(session: AsyncSession, user_id: str) -> bool:
    row = await get_connection(session, user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


def _state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


async def store_oauth_state(
    session: AsyncSession,
    *,
    state: str,
    user_id: str,
) -> None:
    now = utcnow()
    await session.execute(delete(OAuthState).where(OAuthState.expires_at <= now))
    session.add(
        OAuthState(
            state_hash=_state_hash(state),
            user_id=user_id,
            provider="google",
            expires_at=now + OAUTH_STATE_TTL,
        )
    )
    await session.commit()


async def consume_oauth_state(session: AsyncSession, *, state: str) -> str | None:
    result = await session.execute(
        delete(OAuthState)
        .where(
            OAuthState.state_hash == _state_hash(state),
            OAuthState.provider == "google",
        )
        .returning(OAuthState.user_id, OAuthState.expires_at)
    )
    row = result.first()
    await session.commit()
    if row is None:
        return None
    expires = row.expires_at
    if expires.tzinfo is None:

        expires = expires.replace(tzinfo=UTC)
    return row.user_id if expires > utcnow() else None


async def revoke_connection(
    session: AsyncSession,
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    user_id: str,
) -> tuple[bool, bool]:
    """Revoke Google access and always remove Auren's local credentials."""
    row = await get_connection(session, user_id)
    if row is None:
        return False, False
    encrypted = row.refresh_token_encrypted or row.access_token_encrypted
    token = decrypt_secret(settings, encrypted)
    revoked = False
    try:
        response = await google_request(
            http,
            "POST",
            GOOGLE_REVOKE,
            params={"token": token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        revoked = response.status_code == 200
        if not revoked:
            logger.warning("Google token revocation returned %s", response.status_code)
    except httpx.HTTPError:
        logger.warning("Google token revocation request failed", exc_info=True)
    await session.delete(row)
    await session.commit()
    return True, revoked


async def valid_access_token(
    session: AsyncSession,
    settings: Settings,
    http: httpx.AsyncClient,
    user_id: str,
) -> str:
    connection = await session.scalar(
        select(OAuthConnection)
        .where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "google",
        )
        .with_for_update()
    )
    if connection is None:
        raise GoogleAuthError(
            "Google is not connected. Ask the user to connect Google in Auren settings."
        )

    granted_scopes = set((connection.scopes or "").split())
    missing_scopes = REQUIRED_DATA_SCOPES - granted_scopes
    if missing_scopes:
        await session.commit()
        raise GoogleAuthError(
            "Google permissions have changed. Ask the user to disconnect and reconnect "
            "Google in Auren settings."
        )

    expires = connection.expires_at
    if expires is not None and expires.tzinfo is None:

        expires = expires.replace(tzinfo=UTC)

    if expires is None or expires > utcnow():
        try:
            token = decrypt_secret(settings, connection.access_token_encrypted)
        except ValueError as error:
            raise GoogleAuthError(
                "Google credentials could not be decrypted. Ask the user to reconnect Google."
            ) from error
        await session.commit()
        return token

    if not connection.refresh_token_encrypted:
        raise GoogleAuthError("Google access expired. Ask the user to reconnect Google.")

    try:
        refresh = decrypt_secret(settings, connection.refresh_token_encrypted)
    except ValueError as error:
        raise GoogleAuthError(
            "Google credentials could not be decrypted. Ask the user to reconnect Google."
        ) from error
    try:
        payload = await refresh_access_token(settings, http, refresh_token=refresh)
    except httpx.HTTPStatusError as error:
        if error.response.status_code in {400, 401}:
            raise GoogleAuthError(
                "Google access was revoked or expired. Ask the user to reconnect Google."
            ) from error
        raise
    connection.access_token_encrypted = encrypt_secret(settings, payload["access_token"])
    expires_in = int(payload.get("expires_in") or 3600)
    connection.expires_at = utcnow() + timedelta(seconds=max(expires_in - 60, 0))
    await session.commit()
    return payload["access_token"]
