from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models.tables import Artifact

MIME_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class ArtifactError(Exception):
    pass


def artifact_dict(artifact: Artifact) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "kind": artifact.kind,
        "format": artifact.format,
        "title": artifact.title,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "size_bytes": artifact.size_bytes,
        "sha256": artifact.sha256,
        "metadata": artifact.artifact_metadata or {},
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
        "download_url": f"/v1/artifacts/{artifact.id}/download",
    }


async def create_artifact(
    session: AsyncSession,
    settings: Settings,
    *,
    user_id: str,
    kind: str,
    output_format: str,
    title: str,
    content: bytes,
    metadata: dict[str, Any] | None = None,
) -> Artifact:
    if not content:
        raise ArtifactError("The generated artifact was empty.")
    if len(content) > settings.artifact_max_bytes:
        raise ArtifactError(
            f"The generated artifact exceeds the {settings.artifact_max_bytes // (1024 * 1024)} MB limit."
        )
    mime_type = MIME_TYPES.get(output_format)
    if mime_type is None:
        raise ArtifactError(f"Unsupported artifact format: {output_format}")

    artifact_id = uuid.uuid4().hex
    filename = f"{_safe_filename(title)}.{output_format}"
    user_key = hashlib.sha256(user_id.encode()).hexdigest()[:20]
    storage_key = f"{user_key}/{artifact_id}.{output_format}"
    target = _storage_path(settings, storage_key)
    await asyncio.to_thread(_atomic_write, target, content)

    artifact = Artifact(
        id=artifact_id,
        user_id=user_id,
        kind=kind,
        format=output_format,
        title=title.strip(),
        filename=filename,
        mime_type=mime_type,
        storage_key=storage_key,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        artifact_metadata=metadata or {},
    )
    session.add(artifact)
    try:
        await session.commit()
        await session.refresh(artifact)
    except Exception:
        await asyncio.to_thread(lambda: target.unlink(missing_ok=True))
        raise
    return artifact


async def list_artifacts(
    session: AsyncSession,
    user_id: str,
    *,
    limit: int = 20,
) -> list[Artifact]:
    result = await session.scalars(
        select(Artifact)
        .where(Artifact.user_id == user_id)
        .order_by(Artifact.created_at.desc())
        .limit(limit)
    )
    return list(result)


async def get_artifact(
    session: AsyncSession,
    user_id: str,
    artifact_id: str,
) -> Artifact | None:
    artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.user_id != user_id:
        return None
    return artifact


async def read_artifact(
    settings: Settings,
    artifact: Artifact,
) -> bytes:
    path = _storage_path(settings, artifact.storage_key)
    try:
        content = await asyncio.to_thread(path.read_bytes)
    except FileNotFoundError as error:
        raise ArtifactError("The artifact file is no longer available in storage.") from error
    if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise ArtifactError("The artifact failed its integrity check.")
    return content


def _storage_path(settings: Settings, storage_key: str) -> Path:
    root = Path(settings.artifact_storage_dir).expanduser().resolve()
    target = (root / storage_key).resolve()
    if root not in target.parents:
        raise ArtifactError("Invalid artifact storage key.")
    return target


def _atomic_write(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_filename(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip()).strip("-._")
    return (cleaned or "auren-artifact")[:120]
