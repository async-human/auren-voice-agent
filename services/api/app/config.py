from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
SearchProvider = Literal["auto", "tavily", "brave", "searxng", "duckduckgo"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    auren_env: Environment = "development"
    port: int = 8080
    cors_origins: str = "http://localhost:3000"

    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    livekit_agent_name: str = "auren-agent"
    livekit_token_ttl_minutes: int = 10

    database_url: str = "sqlite+aiosqlite:///./auren.db"

    # Shared secret the voice worker presents when calling the tool gateway.
    # Required in production so tools cannot be invoked by arbitrary callers.
    tool_gateway_token: str | None = None

    default_timezone: str = "UTC"
    outbound_http_timeout_seconds: float = 15.0

    web_search_provider: SearchProvider = "auto"
    tavily_api_key: str | None = None
    brave_search_api_key: str | None = None
    searxng_base_url: str | None = None

    voice_token_rate_limit: int = Field(default=10, ge=1)
    voice_token_rate_window_seconds: int = Field(default=60, ge=1)

    @field_validator("livekit_url")
    @classmethod
    def _require_websocket_url(cls, value: str) -> str:
        if not value.startswith("wss://") and not value.startswith("ws://"):
            raise ValueError("LIVEKIT_URL must use wss:// (or ws:// for local development)")
        return value

    @field_validator("database_url")
    @classmethod
    def _normalise_database_url(cls, value: str) -> str:
        # Railway, Neon and Supabase all hand out sync-style URLs.
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.auren_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
