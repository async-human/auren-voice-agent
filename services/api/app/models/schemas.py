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
    sttProvider: Literal["whisper", "qwen"]


class VoiceTokenRequest(BaseModel):
    stt_provider: Literal["whisper", "qwen"] | None = None


class VoiceSTTOption(BaseModel):
    id: Literal["whisper", "qwen"]
    label: str
    description: str
    realtime: bool


class VoiceSTTOptionsResponse(BaseModel):
    default_provider: Literal["whisper", "qwen"]
    providers: list[VoiceSTTOption]


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


class PendingSpeechItem(BaseModel):
    id: str
    title: str
    speak_text: str
    kind: str


class UnreadNotificationItem(BaseModel):
    id: str
    kind: str
    title: str
    body: str


class MemoryContextResponse(BaseModel):
    user_id: str
    display_name: str | None = None
    email: str | None = None
    google_account_email: str | None = None
    profile_summary: str | None = None
    preferences: str | None = None
    memories: list[MemoryItem] = Field(default_factory=list)
    last_session_summary: str | None = None
    open_threads: list[str] = Field(default_factory=list)
    pending_speech: list[PendingSpeechItem] = Field(default_factory=list)
    unread_notifications: list[UnreadNotificationItem] = Field(default_factory=list)
    greeting: str
    instructions_block: str


class ConversationTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=12000)
    sequence: int = Field(ge=0)


class DistilledMemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    memory_type: MemoryType = "semantic"
    confidence: float = Field(default=0.7, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)


class FollowUpIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    run_in_minutes: int = Field(default=1440, ge=1, le=60 * 24 * 30)
    job_type: Literal["follow_up_reminder", "email_reply_check", "custom"] = (
        "follow_up_reminder"
    )
    query: str | None = Field(default=None, max_length=500)


class SessionFlushRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    room_name: str | None = Field(default=None, max_length=128)
    turns: list[ConversationTurnIn] = Field(default_factory=list, max_length=400)
    summary: str | None = Field(default=None, max_length=4000)
    topics: list[str] = Field(default_factory=list, max_length=20)
    outcomes: list[str] = Field(default_factory=list, max_length=20)
    open_threads: list[str] = Field(default_factory=list, max_length=20)
    follow_ups: list[FollowUpIn] = Field(default_factory=list, max_length=10)
    importance: float = Field(default=0.5, ge=0, le=1)
    profile_summary: str | None = Field(default=None, max_length=4000)
    preferences: str | None = Field(default=None, max_length=4000)
    memories: list[DistilledMemoryIn] = Field(default_factory=list, max_length=40)


class SessionFlushResponse(BaseModel):
    session_id: str
    turns_saved: int
    memories_saved: int
    memories_rejected: int = 0
    jobs_created: int = 0


class MemoryListResponse(BaseModel):
    memories: list[MemoryItem]


class UserSettingsResponse(BaseModel):
    timezone_name: str
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    max_interruptions_per_day: int
    spoken_delivery_enabled: bool
    pod_wake_enabled: bool
    pod_wake_available: bool = False
    autonomous_memory_enabled: bool
    semantic_memory_enabled: bool
    procedural_memory_enabled: bool
    prospective_memory_enabled: bool
    morning_brief_enabled: bool
    morning_brief_hour: int


class UserSettingsUpdate(BaseModel):
    timezone_name: str | None = Field(default=None, max_length=64)
    quiet_hours_start: str | None = Field(default=None, max_length=5)
    quiet_hours_end: str | None = Field(default=None, max_length=5)
    max_interruptions_per_day: int | None = Field(default=None, ge=0, le=50)
    spoken_delivery_enabled: bool | None = None
    pod_wake_enabled: bool | None = None
    autonomous_memory_enabled: bool | None = None
    semantic_memory_enabled: bool | None = None
    procedural_memory_enabled: bool | None = None
    prospective_memory_enabled: bool | None = None
    morning_brief_enabled: bool | None = None
    morning_brief_hour: int | None = Field(default=None, ge=0, le=23)


class NotificationItem(BaseModel):
    id: str
    kind: str
    title: str
    body: str
    status: str
    source_type: str | None = None
    source_id: str | None = None
    created_at: str | None = None
    read_at: str | None = None
    spoken_at: str | None = None


class NotificationListResponse(BaseModel):
    notifications: list[NotificationItem] = Field(default_factory=list)
    unread_count: int = 0


class SpeechAckRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    ids: list[str] = Field(default_factory=list, max_length=50)


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
