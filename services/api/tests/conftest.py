from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Callable

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from jwt.algorithms import RSAAlgorithm

# app.main exposes the production ASGI application at import time. Supply
# deterministic, non-secret settings before importing it so the test suite is
# hermetic on clean machines and CI runners.
os.environ.update(
    {
        "AUREN_ENV": "development",
        "LIVEKIT_URL": "wss://example.livekit.cloud",
        "LIVEKIT_API_KEY": "test-key",
        "LIVEKIT_API_SECRET": "test-secret-test-secret-test-32",
        "DEV_USER_ID": "test-user",
    }
)

from app.config import Settings
from app.dependencies import get_http_client
from app.main import create_app

SERVICE_TOKEN = "test-service-token"
ISSUER = "https://clerk.test"
KEY_ID = "test-key-1"


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def jwks(signing_key: rsa.RSAPrivateKey) -> dict:
    key = json.loads(RSAAlgorithm.to_jwk(signing_key.public_key()))
    key.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})
    return {"keys": [key]}


@pytest.fixture
def make_token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _make(
        subject: str = "user_clerk_alice",
        issuer: str = ISSUER,
        expires_in: int = 300,
        kid: str = KEY_ID,
        **claims,
    ) -> str:
        now = int(time.time())
        payload = {
            "sub": subject,
            "iss": issuer,
            "iat": now,
            "exp": now + expires_in,
            **claims,
        }
        return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": kid})

    return _make


@pytest.fixture
def auth_headers(make_token: Callable[..., str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token()}"}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        auren_env="development",
        cors_origins="http://localhost:3000",
        livekit_url="wss://example.livekit.cloud",
        livekit_api_key="devkey",
        livekit_api_secret="devsecret-devsecret-devsecret-32b",
        livekit_agent_name="auren-agent",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        tool_gateway_token=SERVICE_TOKEN,
        default_timezone="UTC",
        clerk_issuer=ISSUER,
        artifact_storage_dir=str(tmp_path / "artifacts"),
    )


@pytest.fixture
async def client(settings: Settings, jwks: dict) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)

    def serve_jwks(_request: Request) -> Response:
        return Response(200, json=jwks)

    outbound = AsyncClient(transport=MockTransport(serve_jwks))
    app.dependency_overrides[get_http_client] = lambda: outbound

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Auren-Service-Token": SERVICE_TOKEN},
    ) as async_client:
        async with app.router.lifespan_context(app):
            yield async_client

    await outbound.aclose()
