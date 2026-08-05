"""Ephemeral active-tab page text for voice explanation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import (
    PageContextMetaResponse,
    PageContextResponse,
    PageContextUpsertRequest,
)
from app.models.tables import PageContext, utcnow

# Keep enough text for a long article while staying under the API body cap.
MAX_STORED_CHARS = 100_000
# Enough for the local voice LLM to explain without overflowing context.
MAX_TOOL_CHARS = 24_000
DEFAULT_TTL = timedelta(minutes=30)


def _clip(text: str, limit: int) -> tuple[str, bool]:
    cleaned = " ".join(text.split()).strip()
    if len(cleaned) <= limit:
        return cleaned, False
    return cleaned[: limit - 1].rstrip() + "…", True


def _as_utc(value: datetime) -> datetime:
    """SQLite often round-trips timezone-aware values as naive UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def upsert_page_context(
    session: AsyncSession,
    user_id: str,
    payload: PageContextUpsertRequest,
) -> PageContextMetaResponse:
    text, truncated = _clip(payload.text, MAX_STORED_CHARS)
    now = utcnow()
    expires_at = now + DEFAULT_TTL
    title = (payload.title or "").strip() or "Untitled page"
    url = payload.url.strip()

    existing = await session.get(PageContext, user_id)
    if existing is None:
        existing = PageContext(user_id=user_id)
        session.add(existing)

    existing.url = url
    existing.title = title[:500]
    existing.text = text
    existing.char_count = len(text)
    existing.truncated = truncated
    existing.source = (payload.source or "extension").strip()[:32]
    existing.created_at = now
    existing.expires_at = expires_at
    await session.commit()
    await session.refresh(existing)
    return _meta(existing)


async def get_page_context(
    session: AsyncSession,
    user_id: str,
    *,
    include_text: bool = True,
    max_chars: int = MAX_TOOL_CHARS,
) -> PageContextResponse:
    row = await _active_row(session, user_id)
    if row is None:
        return PageContextResponse(present=False)

    text: str | None = None
    truncated = row.truncated
    if include_text:
        text, clipped = _clip(row.text, max_chars)
        truncated = truncated or clipped

    return PageContextResponse(
        present=True,
        url=row.url,
        title=row.title,
        text=text,
        char_count=row.char_count,
        truncated=truncated,
        source=row.source,
        created_at=row.created_at.isoformat() if row.created_at else None,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )


async def get_page_context_meta(
    session: AsyncSession,
    user_id: str,
) -> PageContextMetaResponse:
    row = await _active_row(session, user_id)
    if row is None:
        return PageContextMetaResponse(present=False)
    return _meta(row)


async def clear_page_context(session: AsyncSession, user_id: str) -> bool:
    row = await session.get(PageContext, user_id)
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


async def _active_row(session: AsyncSession, user_id: str) -> PageContext | None:
    row = await session.get(PageContext, user_id)
    if row is None:
        return None
    if _as_utc(row.expires_at) <= utcnow():
        await session.delete(row)
        await session.commit()
        return None
    return row


def _meta(row: PageContext) -> PageContextMetaResponse:
    return PageContextMetaResponse(
        present=True,
        url=row.url,
        title=row.title,
        char_count=row.char_count,
        truncated=row.truncated,
        created_at=row.created_at.isoformat() if row.created_at else None,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )
