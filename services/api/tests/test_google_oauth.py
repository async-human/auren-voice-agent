from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.tables import Base, OAuthConnection, utcnow
from app.security.token_crypto import encrypt_secret
from app.services import google_oauth
from app.services.google_http import request as google_request


async def _session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oauth.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory(), engine


def test_authorization_url_uses_incremental_least_privilege_scopes(settings) -> None:
    configured = settings.model_copy(
        update={"google_client_id": "client", "google_client_secret": "secret"}
    )
    query = parse_qs(urlparse(google_oauth.authorize_url(configured, state="state-1")).query)
    scopes = set(query["scope"][0].split())

    assert query["access_type"] == ["offline"]
    assert query["include_granted_scopes"] == ["true"]
    assert query["state"] == ["state-1"]
    assert "https://www.googleapis.com/auth/calendar.events" in scopes
    assert "https://www.googleapis.com/auth/gmail.modify" in scopes
    assert "https://mail.google.com/" not in scopes
    assert "https://www.googleapis.com/auth/calendar" not in scopes


async def test_oauth_state_is_durable_one_time_and_user_bound(tmp_path) -> None:
    session, engine = await _session(tmp_path)
    try:
        await google_oauth.store_oauth_state(
            session,
            state="secret-browser-state",
            user_id="user-1",
        )
        assert (
            await google_oauth.consume_oauth_state(session, state="secret-browser-state")
            == "user-1"
        )
        assert await google_oauth.consume_oauth_state(
            session, state="secret-browser-state"
        ) is None
    finally:
        await session.close()
        await engine.dispose()


async def test_disconnect_revokes_google_token_then_deletes_local_credentials(
    settings,
    tmp_path,
) -> None:
    session, engine = await _session(tmp_path)
    session.add(
        OAuthConnection(
            user_id="user-1",
            provider="google",
            account_email="user@example.com",
            scopes="scope",
            access_token_encrypted=encrypt_secret(settings, "access-token"),
            refresh_token_encrypted=encrypt_secret(settings, "refresh-token"),
            expires_at=utcnow(),
        )
    )
    await session.commit()
    seen_token = None

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal seen_token
        seen_token = request.url.params.get("token")
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        removed, revoked = await google_oauth.revoke_connection(
            session,
            settings,
            http,
            user_id="user-1",
        )
        assert (removed, revoked) == (True, True)
        assert seen_token == "refresh-token"
        assert await google_oauth.get_connection(session, "user-1") is None
    finally:
        await http.aclose()
        await session.close()
        await engine.dispose()


async def test_google_requests_retry_rate_limits_with_a_bound() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        response = await google_request(http, "GET", "https://google.example/test")
        assert response.status_code == 200
        assert calls == 2
    finally:
        await http.aclose()


async def test_google_requests_do_not_retry_non_idempotent_posts() -> None:
    calls = 0

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        response = await google_request(http, "POST", "https://google.example/send")
        assert response.status_code == 503
        assert calls == 1
    finally:
        await http.aclose()


async def test_existing_connection_without_modify_scope_must_reconnect(
    settings,
    tmp_path,
) -> None:
    session, engine = await _session(tmp_path)
    session.add(
        OAuthConnection(
            user_id="user-1",
            provider="google",
            scopes=(
                "https://www.googleapis.com/auth/calendar.events "
                "https://www.googleapis.com/auth/gmail.readonly "
                "https://www.googleapis.com/auth/gmail.compose"
            ),
            access_token_encrypted=encrypt_secret(settings, "access-token"),
            expires_at=utcnow(),
        )
    )
    await session.commit()
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500)))
    try:
        with pytest.raises(google_oauth.GoogleAuthError, match="permissions have changed"):
            await google_oauth.valid_access_token(session, settings, http, "user-1")
    finally:
        await http.aclose()
        await session.close()
        await engine.dispose()
