from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

SERVICE_TOKEN = "test-service-token"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        auren_env="development",
        cors_origins="http://localhost:3000",
        livekit_url="wss://example.livekit.cloud",
        livekit_api_key="devkey",
        livekit_api_secret="devsecret-devsecret-devsecret",
        livekit_agent_name="auren-agent",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        tool_gateway_token=SERVICE_TOKEN,
        default_timezone="UTC",
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Auren-Service-Token": SERVICE_TOKEN},
    ) as async_client:
        async with app.router.lifespan_context(app):
            yield async_client
