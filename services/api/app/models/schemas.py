from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    environment: str


class VoiceTokenRequest(BaseModel):
    user_id: str | None = Field(default=None, max_length=128)


class VoiceTokenResponse(BaseModel):
    serverUrl: str  # noqa: N815 - the browser client expects camelCase.
    participantToken: str  # noqa: N815


class ToolDescription(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    confirmation_required: bool


class ToolInvocation(BaseModel):
    tool: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolInvocationResponse(BaseModel):
    tool: str
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    error: str | None = None
