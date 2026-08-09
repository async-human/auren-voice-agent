from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.dependencies import get_session, get_settings
from app.models.schemas import ArtifactItem, ArtifactListResponse
from app.models.tables import User
from app.security.auth import require_user
from app.services import artifacts as artifact_service

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("", response_model=ArtifactListResponse)
async def list_my_artifacts(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ArtifactListResponse:
    found = await artifact_service.list_artifacts(session, user.id, limit=limit)
    return ArtifactListResponse(
        artifacts=[ArtifactItem.model_validate(artifact_service.artifact_dict(item)) for item in found]
    )


@router.get("/{artifact_id}", response_model=ArtifactItem)
async def get_my_artifact(
    artifact_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> ArtifactItem:
    artifact = await artifact_service.get_artifact(session, user.id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    return ArtifactItem.model_validate(artifact_service.artifact_dict(artifact))


@router.get("/{artifact_id}/download")
async def download_my_artifact(
    artifact_id: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    artifact = await artifact_service.get_artifact(session, user.id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    try:
        content = await artifact_service.read_artifact(settings, artifact)
    except artifact_service.ArtifactError as error:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(error)) from error
    encoded = quote(artifact.filename)
    return Response(
        content=content,
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{artifact.filename}"; filename*=UTF-8\'\'{encoded}'
            ),
            "Cache-Control": "private, no-store",
            "X-Artifact-SHA256": artifact.sha256,
        },
    )
