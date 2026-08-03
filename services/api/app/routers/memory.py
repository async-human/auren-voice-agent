from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_session
from app.models.schemas import (
    MemoryContextResponse,
    MemoryListResponse,
    SessionFlushRequest,
    SessionFlushResponse,
)
from app.models.tables import User
from app.security.auth import require_user
from app.security.service_auth import require_service_token
from app.services import memory as memory_service

worker_router = APIRouter(
    prefix="/v1/memory",
    tags=["memory"],
    dependencies=[Depends(require_service_token)],
)

user_router = APIRouter(prefix="/v1/memory", tags=["memory"])


@worker_router.get("/context", response_model=MemoryContextResponse)
async def worker_context(
    user_id: str = Query(min_length=1, max_length=128),
    session: AsyncSession = Depends(get_session),
) -> MemoryContextResponse:
    return await memory_service.get_context(session, user_id)


@worker_router.post("/sessions/flush", response_model=SessionFlushResponse)
async def worker_flush(
    payload: SessionFlushRequest,
    session: AsyncSession = Depends(get_session),
) -> SessionFlushResponse:
    return await memory_service.flush_session(session, payload)


@user_router.get("", response_model=MemoryListResponse)
async def list_my_memories(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> MemoryListResponse:
    return MemoryListResponse(memories=await memory_service.list_memories(session, user.id))


@user_router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_memory(
    memory_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    forgotten = await memory_service.forget_memory(session, user.id, memory_id)
    if forgotten is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
