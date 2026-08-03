"""Conversation persistence and personal memory for Auren."""

from __future__ import annotations

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
    User,
    UserProfile,
    utcnow,
)

ACTIVE_MEMORY_LIMIT = 12
RECALL_LIMIT = 8


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def _memory_item(memory: Memory) -> MemoryItem:
    return MemoryItem(
        id=memory.id,
        content=memory.content,
        created_at=_iso(memory.created_at),
        last_used_at=_iso(memory.last_used_at),
        source_session_id=memory.source_session_id,
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
        "If they ask you to forget something, use the forget tool."
    )
    return "\n".join(lines)


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_context(session: AsyncSession, user_id: str) -> MemoryContextResponse:
    user = await get_user(session, user_id)
    profile = await session.get(UserProfile, user_id)

    memories = (
        await session.scalars(
            select(Memory)
            .where(Memory.user_id == user_id)
            .where(Memory.deleted_at.is_(None))
            .order_by(Memory.created_at.desc())
            .limit(ACTIVE_MEMORY_LIMIT)
        )
    ).all()

    last_session = await session.scalar(
        select(ConversationSession)
        .where(ConversationSession.user_id == user_id)
        .where(ConversationSession.summary.is_not(None))
        .where(ConversationSession.ended_at.is_not(None))
        .order_by(ConversationSession.ended_at.desc())
        .limit(1)
    )

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
        session.add(
            Memory(
                user_id=payload.user_id,
                content=content,
                source_session_id=conversation.id,
            )
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
    memory.deleted_at = utcnow()
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
        content=cleaned,
        source_session_id=source_session_id,
    )
    session.add(memory)

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
    import re

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
            .where(Memory.deleted_at.is_(None))
            .where(Memory.content.ilike(pattern))
            .order_by(Memory.created_at.desc())
            .limit(20)
        )
    ).all()
    now = utcnow()
    for memory in matches:
        memory.deleted_at = now
    if matches:
        await session.commit()
    return list(matches)
