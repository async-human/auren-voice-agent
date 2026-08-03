from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    environment: str


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


class MemoryItem(BaseModel):
    id: str
    content: str
    created_at: str | None = None
    last_used_at: str | None = None
    source_session_id: str | None = None


class MemoryContextResponse(BaseModel):
    user_id: str
    display_name: str | None = None
    profile_summary: str | None = None
    preferences: str | None = None
    memories: list[MemoryItem] = Field(default_factory=list)
    last_session_summary: str | None = None
    greeting: str
    instructions_block: str


class ConversationTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=12000)
    sequence: int = Field(ge=0)


class DistilledMemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class SessionFlushRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    room_name: str | None = Field(default=None, max_length=128)
    turns: list[ConversationTurnIn] = Field(default_factory=list, max_length=400)
    summary: str | None = Field(default=None, max_length=4000)
    profile_summary: str | None = Field(default=None, max_length=4000)
    preferences: str | None = Field(default=None, max_length=4000)
    memories: list[DistilledMemoryIn] = Field(default_factory=list, max_length=40)


class SessionFlushResponse(BaseModel):
    session_id: str
    turns_saved: int
    memories_saved: int


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]
