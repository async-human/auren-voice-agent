from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_session, get_settings
from app.models.schemas import UserSettingsResponse, UserSettingsUpdate
from app.models.tables import User
from app.security.auth import require_user
from app.services import user_settings as settings_service

router = APIRouter(prefix="/v1/settings", tags=["settings"])


@router.get("", response_model=UserSettingsResponse)
async def read_user_settings(
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    api_settings: Settings = Depends(get_settings),
) -> UserSettingsResponse:
    row = await settings_service.get_or_create(
        session, user.id, default_timezone=api_settings.default_timezone
    )
    await session.commit()
    return settings_service.to_response(
        row, pod_wake_available=api_settings.pod_wake_configured
    )


@router.put("", response_model=UserSettingsResponse)
async def put_settings(
    payload: UserSettingsUpdate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    api_settings: Settings = Depends(get_settings),
) -> UserSettingsResponse:
    try:
        row = await settings_service.update_settings(
            session, user.id, payload, api_settings
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return settings_service.to_response(
        row, pod_wake_available=api_settings.pod_wake_configured
    )
