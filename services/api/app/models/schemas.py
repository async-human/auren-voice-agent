from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryType = Literal["semantic", "procedural"]
MemoryStatus = Literal["candidate", "active", "superseded", "rejected", "deleted"]
MemorySensitivity = Literal["normal", "sensitive", "restricted"]
MemorySource = Literal["autonomous", "explicit", "imported", "derived"]


class HealthResponse(BaseModel):
    status: str
    environment: str


class VoiceTokenResponse(BaseModel):
    serverUrl: str
    participantToken: str


class ToolDescription(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    confirmation_required: bool
    domain: str
    operation: str
    risk: Literal["read", "write", "consequential", "destructive"]
    reversible: bool
    parallel_safe: bool
    requires_connection: str | None = None
    produces: list[str] = Field(default_factory=list)
    version: str


class CapabilityGroup(BaseModel):
    domain: str
    tools: list[ToolDescription]


class CapabilityCatalogResponse(BaseModel):
    version: str
    capabilities: list[CapabilityGroup]


class ArtifactItem(BaseModel):
    id: str
    kind: str
    format: str
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    download_url: str


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactItem] = Field(default_factory=list)


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ToolInvocationResponse(BaseModel):
    tool: str
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    error: str | None = None


class MemoryItem(BaseModel):
    id: str
    content: str
    memory_type: MemoryType
    status: MemoryStatus
    structured_value: dict[str, Any] | None = None
    confidence: float
    importance: float
    sensitivity: MemorySensitivity
    source: MemorySource
    created_at: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None
    last_confirmed_at: str | None = None
    source_session_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by_id: str | None = None


class MemoryContextResponse(BaseModel):
    user_id: str
    display_name: str | None = None
    email: str | None = None
    google_account_email: str | None = None
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
    topics: list[str] = Field(default_factory=list, max_length=20)
    outcomes: list[str] = Field(default_factory=list, max_length=20)
    open_threads: list[str] = Field(default_factory=list, max_length=20)
    importance: float = Field(default=0.5, ge=0, le=1)
    profile_summary: str | None = Field(default=None, max_length=4000)
    preferences: str | None = Field(default=None, max_length=4000)
    memories: list[DistilledMemoryIn] = Field(default_factory=list, max_length=40)


class SessionFlushResponse(BaseModel):
    session_id: str
    turns_saved: int
    memories_saved: int


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class PageContextUpsertRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=500)
    text: str = Field(min_length=1, max_length=120_000)
    source: str = Field(default="extension", max_length=32)


class PageContextResponse(BaseModel):
    present: bool
    url: str | None = None
    title: str | None = None
    text: str | None = None
    char_count: int = 0
    truncated: bool = False
    source: str | None = None
    created_at: str | None = None
    expires_at: str | None = None


class PageContextMetaResponse(BaseModel):
    present: bool
    url: str | None = None
    title: str | None = None
    char_count: int = 0
    truncated: bool = False
    created_at: str | None = None
    expires_at: str | None = None
