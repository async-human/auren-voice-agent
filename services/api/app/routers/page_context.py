from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.models.schemas import (
    PageContextMetaResponse,
    PageContextUpsertRequest,
)
from app.models.tables import User
from app.security.auth import require_user
from app.services import page_context as page_context_service

router = APIRouter(prefix="/v1/page-context", tags=["page-context"])


@router.put("", response_model=PageContextMetaResponse)
async def upsert_page_context(
    payload: PageContextUpsertRequest,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> PageContextMetaResponse:
    """Browser extension uploads the active tab's extracted article text."""
    return await page_context_service.upsert_page_context(session, user.id, payload)


@router.get("", response_model=PageContextMetaResponse)
async def get_page_context_meta(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> PageContextMetaResponse:
    """Return whether a fresh page is available (no full text)."""
    return await page_context_service.get_page_context_meta(session, user.id)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def clear_page_context(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await page_context_service.clear_page_context(session, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
