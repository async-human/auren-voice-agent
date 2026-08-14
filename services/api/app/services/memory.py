"""Conversation persistence and personal memory for Auren."""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    MemoryContextResponse,
    MemoryItem,
    PendingSpeechItem,
    SessionFlushRequest,
    SessionFlushResponse,
    UnreadNotificationItem,
)
from app.models.tables import (
    ConversationSession,
    ConversationTurn,
    Memory,
    MemoryEvent,
    MemoryEvidence,
    OAuthConnection,
    ScheduledJob,
    User,
    UserProfile,
    utcnow,
)
from app.services import memory_policy
from app.services import notifications as notification_service
from app.services import user_settings as settings_service

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
    snippet = (last_summary or "").strip()
    if len(snippet) > 120:
        snippet = f"{snippet[:117]}..."
    if snippet:
        return f"Boss. June online. Last time: {snippet}. Ready to pick it up?"
    return "Boss. June here — systems up. What are we doing?"


def build_instructions_block(
    display_name: str | None,
    email: str | None,
    google_account_email: str | None,
    profile_summary: str | None,
    preferences: str | None,
    memories: list[Memory],
    last_summary: str | None,
    open_threads: list[str] | None = None,
    unread_notifications: list | None = None,
) -> str:
    lines = [
        "Personal context for this user (use naturally; do not recite as a list):",
    ]
    if display_name:
        lines.append(f"- Name: {display_name}")
    if email:
        lines.append(f"- Authenticated sign-in email: {email}")
    if google_account_email:
        lines.append(f"- Connected Google account email: {google_account_email}")
    if profile_summary:
        lines.append(f"- Profile: {profile_summary}")
    if preferences:
        lines.append(f"- Preferences: {preferences}")
    if last_summary:
        lines.append(f"- Previous conversation: {last_summary}")
    if open_threads:
        lines.append("- Open threads from last time:")
        for thread in open_threads[:6]:
            lines.append(f"  • {thread}")
    if unread_notifications:
        lines.append("- Unread inbox items to mention before a new request:")
        for item in unread_notifications[:5]:
            title = getattr(item, "title", None) or item.get("title")
            body = getattr(item, "body", None) or item.get("body") or ""
            snippet = body[:160]
            lines.append(f"  • {title}: {snippet}")
    if memories:
        lines.append("- Things to remember:")
        for memory in memories:
            lines.append(f"  • {memory.content}")
    else:
        lines.append("- No durable memories stored yet.")
    lines.append(
        "Address the user as Boss, never by first name. Do not overuse it. "
        "You are June: dry, loyal, brief, a little wry — like FRIDAY, not a receptionist. "
        "Refer to prior context only when relevant. "
        "If they ask for weather/forecast and a home city appears under "
        "Things to remember or Profile, use that city instead of asking again. "
        "If they ask what you discussed last time, answer from 'Previous conversation' "
        "when present; otherwise call recall with query 'last conversation'. "
        "If they ask you to forget something, use the forget tool."
        " When the user says 'email me', 'invite me', or 'my email', use the "
        "authenticated sign-in email when present. If the connected Google account "
        "email differs, briefly ask which address to use. If no recipient was named, "
        "confirm the known address or ask for a different one; never invent an address. "
        "Creating an event on the user's primary calendar does not require adding their "
        "own address as an attendee unless they explicitly ask to invite themselves. "
        "If unread inbox items or due reminders are listed, mention them first. "
        "For 'remind me tomorrow if they don't reply', call schedule_followup with "
        "job_type=email_reply_check and a Gmail query. Never send email or change a "
        "calendar from a follow-up job; those still need confirmation."
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
    google_connection = await session.scalar(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "google",
        )
    )

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
    unread_rows, _unread_count = await notification_service.list_notifications(
        session, user_id, status="unread", limit=5
    )
    speech_rows = await notification_service.pending_speech(session, user_id)
    open_threads = list(last_session.open_threads or []) if last_session else []

    display_name = user.display_name if user else None
    email = user.email if user else None
    google_account_email = google_connection.account_email if google_connection else None
    profile_summary = profile.summary if profile else None
    preferences = profile.preferences if profile else None
    last_summary = last_session.summary if last_session else None
    unread_items = [
        UnreadNotificationItem(
            id=row.id, kind=row.kind, title=row.title, body=row.body
        )
        for row in unread_rows
    ]
    pending_speech = [
        PendingSpeechItem(
            id=row.id,
            title=row.title,
            speak_text=(row.speak_text or row.title),
            kind=row.kind,
        )
        for row in speech_rows
    ]
    greeting = build_greeting(display_name, last_summary)
    if pending_speech:
        spoken = " ".join(item.speak_text for item in pending_speech[:3])
        greeting = f"{greeting} {spoken}"
    elif unread_items:
        greeting = (
            f"{greeting} {len(unread_items)} item"
            f"{'' if len(unread_items) == 1 else 's'} waiting in the inbox, Boss."
        )

    return MemoryContextResponse(
        user_id=user_id,
        display_name=display_name,
        email=email,
        google_account_email=google_account_email,
        profile_summary=profile_summary,
        preferences=preferences,
        memories=[_memory_item(memory) for memory in memories],
        last_session_summary=last_summary,
        open_threads=open_threads,
        pending_speech=pending_speech,
        unread_notifications=unread_items,
        greeting=greeting,
        instructions_block=build_instructions_block(
            display_name,
            email,
            google_account_email,
            profile_summary,
            preferences,
            list(memories),
            last_summary,
            open_threads,
            unread_items,
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

    policy = await settings_service.get_or_create(session, payload.user_id)
    if payload.profile_summary or payload.preferences:
        if memory_policy.allow_autonomous_profile(policy):
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
    memories_rejected = 0
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
        reason = memory_policy.allow_autonomous_memory(policy, item)
        if reason is not None:
            memories_rejected += 1
            continue
        memory = Memory(
            user_id=payload.user_id,
            memory_type=item.memory_type or "semantic",
            status="candidate" if (item.memory_type == "procedural") else "active",
            content=content,
            confidence=item.confidence,
            importance=item.importance,
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

    jobs_created = 0
    if memory_policy.allow_prospective_jobs(policy):
        for follow in payload.follow_ups:
            payload_data = {"message": follow.message}
            if follow.query:
                payload_data["query"] = follow.query
            session.add(
                ScheduledJob(
                    user_id=payload.user_id,
                    job_type=follow.job_type,
                    payload=payload_data,
                    run_at=utcnow() + timedelta(minutes=follow.run_in_minutes),
                    status="scheduled",
                )
            )
            jobs_created += 1

    await session.commit()
    return SessionFlushResponse(
        session_id=conversation.id,
        turns_saved=turns_saved,
        memories_saved=memories_saved,
        memories_rejected=memories_rejected,
        jobs_created=jobs_created,
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
