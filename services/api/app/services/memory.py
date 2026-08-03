"""Conversation persistence and personal memory for Auren."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    MemoryContextResponse,
    MemoryItem,
    SessionFlushRequest,
    SessionFlushResponse,
)
from app.models.tables import (
    ConversationSession,
    ConversationTurn,
    Memory,
    MemoryEvent,
    MemoryEvidence,
    User,
    UserProfile,
    utcnow,
)

ACTIVE_MEMORY_LIMIT = 12
RECALL_LIMIT = 8
LAST_CONVERSATION_TURN_LIMIT = 8

_LAST_CONVERSATION_RE = re.compile(
    r"\b("
    r"last (conversation|session|chat|time|talk)|"
    r"previous (conversation|session|chat)|"
    r"what did we (discuss|talk)|"
    r"what were we (discussing|talking)|"
    r"our last (conversation|chat|session)|"
    r"earlier (today|conversation|session|chat)"
    r")\b",
    flags=re.IGNORECASE,
)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _memory_item(memory: Memory) -> MemoryItem:
    return MemoryItem(
        id=memory.id,
        content=memory.content,
        memory_type=memory.memory_type,
        status=memory.status,
        structured_value=memory.structured_value,
        confidence=memory.confidence,
        importance=memory.importance,
        sensitivity=memory.sensitivity,
        source=memory.source,
        created_at=_iso(memory.created_at),
        updated_at=_iso(memory.updated_at),
        last_used_at=_iso(memory.last_used_at),
        last_confirmed_at=_iso(memory.last_confirmed_at),
        source_session_id=memory.source_session_id,
        valid_from=_iso(memory.valid_from),
        valid_until=_iso(memory.valid_until),
        superseded_by_id=memory.superseded_by_id,
    )


def build_greeting(display_name: str | None, last_summary: str | None) -> str:
    name = (display_name or "").strip().split()[0] if display_name else ""
    snippet = (last_summary or "").strip()
    if len(snippet) > 120:
        snippet = f"{snippet[:117]}..."
    if name and snippet:
        return f"Welcome back, {name}. Last time we talked about {snippet}. What should we pick up?"
    if name:
        return f"Hello {name}. I’m Auren — what can I help you with?"
    if snippet:
        return f"Welcome back. Last time we talked about {snippet}. Where shall we begin?"
    return "Hello. I’m Auren — what can I help you with?"


def build_instructions_block(
    display_name: str | None,
    profile_summary: str | None,
    preferences: str | None,
    memories: list[Memory],
    last_summary: str | None,
) -> str:
    lines = [
        "Personal context for this user (use naturally; do not recite as a list):",
    ]
    if display_name:
        lines.append(f"- Name: {display_name}")
    if profile_summary:
        lines.append(f"- Profile: {profile_summary}")
    if preferences:
        lines.append(f"- Preferences: {preferences}")
    if last_summary:
        lines.append(f"- Previous conversation: {last_summary}")
    if memories:
        lines.append("- Things to remember:")
        for memory in memories:
            lines.append(f"  • {memory.content}")
    else:
        lines.append("- No durable memories stored yet.")
    lines.append(
        "Greet them by name when you know it. Refer to prior context only when relevant. "
        "If they ask what you discussed last time, answer from 'Previous conversation' "
        "when present; otherwise call recall with query 'last conversation'. "
        "If they ask you to forget something, use the forget tool."
    )
    return "\n".join(lines)


def looks_like_last_conversation_query(query: str) -> bool:
    return bool(_LAST_CONVERSATION_RE.search(query or ""))


async def get_last_session(
    session: AsyncSession, user_id: str
) -> ConversationSession | None:
    return await session.scalar(
        select(ConversationSession)
        .where(ConversationSession.user_id == user_id)
        .where(ConversationSession.summary.is_not(None))
        .where(ConversationSession.ended_at.is_not(None))
        .order_by(ConversationSession.ended_at.desc())
        .limit(1)
    )


async def get_session_turns(
    session: AsyncSession, session_id: str, *, limit: int = LAST_CONVERSATION_TURN_LIMIT
) -> list[ConversationTurn]:
    rows = (
        await session.scalars(
            select(ConversationTurn)
            .where(ConversationTurn.session_id == session_id)
            .order_by(ConversationTurn.sequence.asc())
            .limit(limit)
        )
    ).all()
    return list(rows)


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_context(session: AsyncSession, user_id: str) -> MemoryContextResponse:
    user = await get_user(session, user_id)
    profile = await session.get(UserProfile, user_id)

    memories = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.status == "active")
            .where(Memory.deleted_at.is_(None))
            .order_by(Memory.created_at.desc())
            .limit(ACTIVE_MEMORY_LIMIT)
        )
    ).all()

    last_session = await get_last_session(session, user_id)

    display_name = user.display_name if user else None
    profile_summary = profile.summary if profile else None
    preferences = profile.preferences if profile else None
    last_summary = last_session.summary if last_session else None

    return MemoryContextResponse(
        user_id=user_id,
        display_name=display_name,
        profile_summary=profile_summary,
        preferences=preferences,
        memories=[_memory_item(memory) for memory in memories],
        last_session_summary=last_summary,
        greeting=build_greeting(display_name, last_summary),
        instructions_block=build_instructions_block(
            display_name, profile_summary, preferences, list(memories), last_summary
        ),
    )


async def flush_session(
    session: AsyncSession, payload: SessionFlushRequest
) -> SessionFlushResponse:
    conversation = ConversationSession(
        user_id=payload.user_id,
        room_name=payload.room_name,
        ended_at=utcnow(),
        summary=(payload.summary or None),
        topics=payload.topics or None,
        outcomes=payload.outcomes or None,
        open_threads=payload.open_threads or None,
        importance=payload.importance,
    )
    session.add(conversation)
    await session.flush()

    turns_saved = 0
    for turn in payload.turns:
        text = turn.text.strip()
        if not text:
            continue
        session.add(
            ConversationTurn(
                session_id=conversation.id,
                user_id=payload.user_id,
                role=turn.role,
                text=text,
                sequence=turn.sequence,
            )
        )
        turns_saved += 1

    if payload.profile_summary or payload.preferences:
        profile = await session.get(UserProfile, payload.user_id)
        if profile is None:
            profile = UserProfile(user_id=payload.user_id)
            session.add(profile)
        if payload.profile_summary:
            profile.summary = payload.profile_summary.strip()
        if payload.preferences:
            profile.preferences = payload.preferences.strip()
        profile.updated_at = utcnow()

    memories_saved = 0
    existing = {
        row.strip().lower()
        for row in await session.scalars(
            select(Memory.content)
            .where(Memory.user_id == payload.user_id)
            .where(Memory.deleted_at.is_(None))
        )
    }
    for item in payload.memories:
        content = item.content.strip()
        if not content or content.lower() in existing:
            continue
        memory = Memory(
            user_id=payload.user_id,
            memory_type="semantic",
            status="active",
            content=content,
            confidence=0.7,
            importance=0.5,
            sensitivity="normal",
            source="autonomous",
            source_session_id=conversation.id,
        )
        session.add(memory)
        await session.flush()
        session.add_all(
            [
                MemoryEvidence(
                    memory_id=memory.id,
                    user_id=payload.user_id,
                    session_id=conversation.id,
                    relation="supports",
                ),
                MemoryEvent(
                    memory_id=memory.id,
                    user_id=payload.user_id,
                    event_type="created",
                    actor="system",
                    details={"source": "session_distillation"},
                ),
            ]
        )
        existing.add(content.lower())
        memories_saved += 1

    await session.commit()
    return SessionFlushResponse(
        session_id=conversation.id,
        turns_saved=turns_saved,
        memories_saved=memories_saved,
    )


async def list_memories(session: AsyncSession, user_id: str) -> list[MemoryItem]:
    rows = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.status == "active")
            .where(Memory.deleted_at.is_(None))
            .order_by(Memory.created_at.desc())
            .limit(100)
        )
    ).all()
    return [_memory_item(row) for row in rows]


async def forget_memory(
    session: AsyncSession, user_id: str, memory_id: str
) -> Memory | None:
    memory = await session.get(Memory, memory_id)
    if memory is None or memory.user_id != user_id or memory.deleted_at is not None:
        return None
    now = utcnow()
    memory.status = "deleted"
    memory.deleted_at = now
    memory.updated_at = now
    session.add(
        MemoryEvent(
            memory_id=memory.id,
            user_id=user_id,
            event_type="deleted",
            actor="user",
        )
    )
    await session.commit()
    return memory


async def remember_fact(
    session: AsyncSession,
    user_id: str,
    content: str,
    source_session_id: str | None = None,
) -> Memory:
    cleaned = content.strip()
    memory = Memory(
        user_id=user_id,
        memory_type="semantic",
        status="active",
        content=cleaned,
        confidence=1.0,
        importance=0.8,
        sensitivity="normal",
        source="explicit",
        source_session_id=source_session_id,
        last_confirmed_at=utcnow(),
    )
    session.add(memory)
    await session.flush()
    session.add(
        MemoryEvent(
            memory_id=memory.id,
            user_id=user_id,
            event_type="created",
            actor="user",
            details={"source": "remember_tool"},
        )
    )

    # If the user is telling us their name, keep it on the user row so the next
    # greeting can use it without waiting for a distillation pass.
    name = _extract_preferred_name(cleaned)
    if name:
        user = await get_user(session, user_id)
        if user is not None and user.display_name != name:
            user.display_name = name

    await session.commit()
    await session.refresh(memory)
    return memory


def _extract_preferred_name(content: str) -> str | None:
    match = re.search(
        r"(?:my name is|i am|i'm|call me)\s+([A-Za-z][A-Za-z\-']{1,40})",
        content,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip().title()


async def search_memories(
    session: AsyncSession, user_id: str, query: str, limit: int = RECALL_LIMIT
) -> list[Memory]:
    pattern = f"%{query.strip()}%"
    rows = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.status == "active")
            .where(Memory.deleted_at.is_(None))
            .where(Memory.content.ilike(pattern))
            .order_by(Memory.created_at.desc())
            .limit(limit)
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.last_used_at = now
    if rows:
        await session.commit()
    return list(rows)


async def forget_by_query(
    session: AsyncSession, user_id: str, query: str
) -> list[Memory]:
    pattern = f"%{query.strip()}%"
    matches = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.status == "active")
            .where(Memory.deleted_at.is_(None))
            .where(Memory.content.ilike(pattern))
            .order_by(Memory.created_at.desc())
            .limit(20)
        )
    ).all()
    now = utcnow()
    for memory in matches:
        memory.status = "deleted"
        memory.deleted_at = now
        memory.updated_at = now
        session.add(
            MemoryEvent(
                memory_id=memory.id,
                user_id=user_id,
                event_type="deleted",
                actor="user",
                details={"query": query},
            )
        )
    if matches:
        await session.commit()
    return list(matches)
