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

    # Clerk issues the browser session token. The API verifies it offline
    # against the JWKS for this issuer, so it never calls Clerk on the hot path.
    clerk_issuer: str | None = None
    clerk_jwks_url: str | None = None
    clerk_jwks_cache_seconds: int = Field(default=3600, ge=60)

    # Escape hatch for offline development. Ignored in production so a stray
    # value can never bypass authentication on a deployed instance.
    dev_user_id: str | None = None

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

    # Google OAuth for Calendar + Gmail (credentials stay on the API host).
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:8081/v1/connections/google/callback"
    public_app_url: str = "http://localhost:3000"
    token_encryption_key: str | None = None
    scheduler_enabled: bool = True
    scheduler_poll_seconds: int = Field(default=30, ge=5)

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
    def resolved_jwks_url(self) -> str | None:
        if self.clerk_jwks_url:
            return self.clerk_jwks_url
        if self.clerk_issuer:
            return f"{self.clerk_issuer.rstrip('/')}/.well-known/jwks.json"
        return None

    @property
    def auth_configured(self) -> bool:
        return bool(self.clerk_issuer and self.resolved_jwks_url)

    @property
    def dev_user_fallback(self) -> str | None:
        """A fixed identity for offline work, never honoured in production."""
        if self.is_production:
            return None
        return self.dev_user_id

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.auren_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
