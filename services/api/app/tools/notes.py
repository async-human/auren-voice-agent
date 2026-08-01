from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import or_, select

from app.models.tables import Note
from app.tools.base import ToolContext, ToolResult, ToolSpec


class SaveNoteArgs(BaseModel):
    body: str = Field(min_length=1, max_length=8000, description="The note content.")
    title: str | None = Field(
        default=None,
        max_length=300,
        description="Short title. Derived from the body when omitted.",
    )


class SearchNotesArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Words to look for.")
    limit: int = Field(default=5, ge=1, le=20)


def _derive_title(body: str) -> str:
    first_line = body.strip().splitlines()[0]
    return first_line[:80] if len(first_line) <= 80 else f"{first_line[:77]}..."


async def save_note(context: ToolContext, args: SaveNoteArgs) -> ToolResult:
    note = Note(
        user_id=context.user_id,
        title=(args.title or _derive_title(args.body)).strip(),
        body=args.body.strip(),
    )
    context.session.add(note)
    await context.session.commit()

    return ToolResult(
        summary=f"Saved a note titled {note.title}.",
        data={"id": note.id, "title": note.title},
    )


async def search_notes(context: ToolContext, args: SearchNotesArgs) -> ToolResult:
    pattern = f"%{args.query.strip()}%"
    query = (
        select(Note)
        .where(Note.user_id == context.user_id)
        .where(or_(Note.title.ilike(pattern), Note.body.ilike(pattern)))
        .order_by(Note.created_at.desc())
        .limit(args.limit)
    )

    notes = (await context.session.scalars(query)).all()
    if not notes:
        return ToolResult(
            summary=f"I could not find any notes matching {args.query}.",
            data={"notes": []},
        )

    spoken = "; ".join(f"{note.title}: {note.body[:160]}" for note in notes)
    count = len(notes)
    noun = "note" if count == 1 else "notes"
    return ToolResult(
        summary=f"I found {count} {noun}. {spoken}",
        data={
            "notes": [
                {
                    "id": note.id,
                    "title": note.title,
                    "body": note.body,
                    "created_at": note.created_at.isoformat() if note.created_at else None,
                }
                for note in notes
            ]
        },
    )


SAVE_SPEC = ToolSpec(
    name="save_note",
    description="Save a note so the user can recall it later.",
    args_model=SaveNoteArgs,
    handler=save_note,
)

SEARCH_SPEC = ToolSpec(
    name="search_notes",
    description="Search the user's saved notes by keyword.",
    args_model=SearchNotesArgs,
    handler=search_notes,
)
