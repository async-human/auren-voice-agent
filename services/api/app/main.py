from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import Settings, get_settings
from app.db import create_engine, create_schema, create_session_factory
from app.routers import health, tools, voice
from app.security.rate_limit import SlidingWindowRateLimiter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("auren.api")

MAX_BODY_BYTES = 16 * 1024

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, *, hsts: bool) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        if self._hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
            return JSONResponse({"error": "Payload too large"}, status_code=413)
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    engine = create_engine(settings)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    await create_schema(engine)

    app.state.http_client = httpx.AsyncClient(
        timeout=settings.outbound_http_timeout_seconds,
        headers={"User-Agent": "auren-api/0.1"},
        follow_redirects=True,
    )

    if not settings.tool_gateway_token:
        logger.warning(
            "TOOL_GATEWAY_TOKEN is not set; tool routes are unauthenticated in %s",
            settings.auren_env,
        )

    logger.info("Auren API ready in %s", settings.auren_env)
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(
        title="Auren API",
        version="0.1.0",
        docs_url=None if resolved.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.voice_token_limiter = SlidingWindowRateLimiter(
        limit=resolved.voice_token_rate_limit,
        window_seconds=resolved.voice_token_rate_window_seconds,
    )

    app.add_middleware(SecurityHeadersMiddleware, hsts=resolved.is_production)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-Auren-Service-Token"],
    )

    app.include_router(health.router)
    app.include_router(voice.router)
    app.include_router(tools.router)

    @app.exception_handler(Exception)
    async def unhandled_error(_request: Request, error: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=error)
        return JSONResponse({"error": "Internal server error"}, status_code=500)

    return app


app = create_app()
