"""Verify the browser's Clerk session token.

Verification is offline: the signing keys are fetched from the issuer's JWKS
endpoint and cached, so a sign-in never adds a network hop to the request path.
Only the identity provider's subject is trusted here; the caller is never
allowed to name the user it wants to act as.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWK, PyJWKSet

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_http_client, get_session, get_settings
from app.models.tables import User
from app.services.users import resolve_user

logger = logging.getLogger(__name__)

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in to continue",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass(frozen=True)
class VerifiedIdentity:
    """The provider's view of the caller, before it is mapped to a local user."""

    subject: str
    email: str | None = None
    display_name: str | None = None


class JwksCache:
    def __init__(self, url: str, ttl_seconds: int) -> None:
        self._url = url
        self._ttl = ttl_seconds
        self._keys: PyJWKSet | None = None
        self._fetched_at = 0.0

    async def key_for(self, kid: str, http: httpx.AsyncClient) -> PyJWK:
        keys = await self._load(http)
        try:
            return keys[kid]
        except KeyError:
            # Clerk rotates keys; one forced refresh beats rejecting valid tokens.
            keys = await self._load(http, force=True)
            try:
                return keys[kid]
            except KeyError as error:
                raise UNAUTHENTICATED from error

    async def _load(self, http: httpx.AsyncClient, force: bool = False) -> PyJWKSet:
        fresh = self._keys is not None and time.monotonic() - self._fetched_at < self._ttl
        if fresh and not force:
            assert self._keys is not None
            return self._keys

        try:
            response = await http.get(self._url)
            response.raise_for_status()
        except httpx.HTTPError as error:
            if self._keys is not None:
                logger.warning("Reusing cached JWKS after fetch failure: %s", error)
                return self._keys
            logger.error("Could not fetch JWKS from %s: %s", self._url, error)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is temporarily unavailable",
            ) from error

        self._keys = PyJWKSet.from_dict(response.json())
        self._fetched_at = time.monotonic()
        return self._keys


def get_jwks_cache(request: Request) -> JwksCache | None:
    return getattr(request.app.state, "jwks_cache", None)


def bearer_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def verify_identity(
    request: Request,
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> VerifiedIdentity:
    if not settings.auth_configured:
        fallback = settings.dev_user_fallback
        if fallback:
            # Leave display_name unset so remembered names are not overwritten
            # by the offline-development fallback on every request.
            return VerifiedIdentity(subject=fallback)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    token = bearer_token(request)
    if token is None:
        raise UNAUTHENTICATED

    cache = get_jwks_cache(request)
    if cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is not configured",
        )

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as error:
        raise UNAUTHENTICATED from error

    kid = header.get("kid")
    if not kid:
        raise UNAUTHENTICATED

    key = await cache.key_for(kid, http)

    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
            leeway=10,
        )
    except jwt.PyJWTError as error:
        logger.info("Rejected session token: %s", type(error).__name__)
        raise UNAUTHENTICATED from error

    subject = claims.get("sub")
    if not subject:
        raise UNAUTHENTICATED

    return VerifiedIdentity(
        subject=str(subject),
        email=claims.get("email"),
        display_name=claims.get("name") or claims.get("username"),
    )


async def require_user(
    identity: VerifiedIdentity = Depends(verify_identity),
    session: AsyncSession = Depends(get_session),
) -> User:
    return await resolve_user(
        session,
        external_id=identity.subject,
        email=identity.email,
        display_name=identity.display_name,
    )
