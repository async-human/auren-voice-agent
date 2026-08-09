from __future__ import annotations

import asyncio
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import artifact_generators, artifacts
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec

DocumentFormat = Literal["docx", "pdf", "md", "html", "txt"]
SpreadsheetFormat = Literal["xlsx", "csv"]
SpreadsheetValue = str | int | float | bool | None


def _clean_title(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("title must not be blank")
    return cleaned


class CreateDocumentArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(
        min_length=1,
        max_length=120_000,
        description="Report content in simple Markdown with headings and bullet lists.",
    )
    format: DocumentFormat = "docx"

    _validate_title = field_validator("title")(_clean_title)


class CreateSpreadsheetArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    columns: list[str] = Field(min_length=1, max_length=80)
    rows: list[list[SpreadsheetValue]] = Field(default_factory=list, max_length=5000)
    format: SpreadsheetFormat = "xlsx"

    _validate_title = field_validator("title")(_clean_title)

    @field_validator("columns")
    @classmethod
    def _valid_columns(cls, value: list[str]) -> list[str]:
        cleaned = [column.strip() for column in value]
        if any(not column or len(column) > 200 for column in cleaned):
            raise ValueError("column names must be between 1 and 200 characters")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("column names must be unique")
        return cleaned

    @model_validator(mode="after")
    def _rows_match_columns(self) -> CreateSpreadsheetArgs:
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError("every row must contain exactly one value per column")
        return self


class PresentationSlide(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    bullets: list[str] = Field(default_factory=list, max_length=12)
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("bullets")
    @classmethod
    def _valid_bullets(cls, value: list[str]) -> list[str]:
        cleaned = [bullet.strip() for bullet in value]
        if any(not bullet or len(bullet) > 1000 for bullet in cleaned):
            raise ValueError("bullets must be between 1 and 1000 characters")
        return cleaned


class CreatePresentationArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    slides: list[PresentationSlide] = Field(min_length=1, max_length=30)

    _validate_title = field_validator("title")(_clean_title)


class ListArtifactsArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)


async def create_document(
    context: ToolContext,
    args: CreateDocumentArgs,
) -> ToolResult:
    try:
        content = await asyncio.to_thread(
            artifact_generators.render_document,
            args.title,
            args.content,
            args.format,
        )
        artifact = await artifacts.create_artifact(
            context.session,
            context.settings,
            user_id=context.user_id,
            kind="document",
            output_format=args.format,
            title=args.title,
            content=content,
            metadata={"source": "agent", "content_type": "markdown"},
        )
    except artifacts.ArtifactError as error:
        raise ToolError(str(error)) from error
    return _created_result(artifact)


async def create_spreadsheet(
    context: ToolContext,
    args: CreateSpreadsheetArgs,
) -> ToolResult:
    try:
        content = await asyncio.to_thread(
            artifact_generators.render_spreadsheet,
            args.title,
            args.columns,
            args.rows,
            args.format,
        )
        artifact = await artifacts.create_artifact(
            context.session,
            context.settings,
            user_id=context.user_id,
            kind="spreadsheet",
            output_format=args.format,
            title=args.title,
            content=content,
            metadata={
                "source": "agent",
                "columns": len(args.columns),
                "rows": len(args.rows),
            },
        )
    except artifacts.ArtifactError as error:
        raise ToolError(str(error)) from error
    return _created_result(artifact)


async def create_presentation(
    context: ToolContext,
    args: CreatePresentationArgs,
) -> ToolResult:
    try:
        content = await asyncio.to_thread(
            artifact_generators.render_presentation,
            args.title,
            [slide.model_dump() for slide in args.slides],
        )
        artifact = await artifacts.create_artifact(
            context.session,
            context.settings,
            user_id=context.user_id,
            kind="presentation",
            output_format="pptx",
            title=args.title,
            content=content,
            metadata={"source": "agent", "slides": len(args.slides) + 1},
        )
    except artifacts.ArtifactError as error:
        raise ToolError(str(error)) from error
    return _created_result(artifact)


async def list_artifacts(
    context: ToolContext,
    args: ListArtifactsArgs,
) -> ToolResult:
    found = await artifacts.list_artifacts(context.session, context.user_id, limit=args.limit)
    if not found:
        return ToolResult(summary="You do not have any generated files yet.", data={"artifacts": []})
    files = [artifacts.artifact_dict(artifact) for artifact in found]
    spoken = "; ".join(f"{item['filename']} ({item['format'].upper()})" for item in files)
    return ToolResult(
        summary=f"Generated files, newest first: {spoken}",
        data={"artifacts": files},
    )


def _created_result(artifact) -> ToolResult:
    item = artifacts.artifact_dict(artifact)
    return ToolResult(
        summary=(
            f"Created {item['filename']} successfully. It is saved in your Auren files "
            "and ready to download or attach to an email."
        ),
        data={"artifact": item, "artifacts": [item]},
    )


CREATE_DOCUMENT_SPEC = ToolSpec(
    name="create_document",
    description=(
        "Create and save a polished document from Markdown content as DOCX, PDF, Markdown, "
        "HTML, or plain text. Returns an artifact id for download or email attachment."
    ),
    args_model=CreateDocumentArgs,
    handler=create_document,
)

CREATE_SPREADSHEET_SPEC = ToolSpec(
    name="create_spreadsheet",
    description=(
        "Create and save a structured XLSX workbook or CSV from columns and rows. "
        "Spreadsheet formula injection is neutralized."
    ),
    args_model=CreateSpreadsheetArgs,
    handler=create_spreadsheet,
)

CREATE_PRESENTATION_SPEC = ToolSpec(
    name="create_presentation",
    description="Create and save a PPTX presentation with a title slide and content slides.",
    args_model=CreatePresentationArgs,
    handler=create_presentation,
)

LIST_SPEC = ToolSpec(
    name="list_artifacts",
    description="List the user's generated documents, spreadsheets, and presentations.",
    args_model=ListArtifactsArgs,
    handler=list_artifacts,
)
