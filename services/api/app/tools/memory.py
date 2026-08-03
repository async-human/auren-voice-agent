from __future__ import annotations

from pydantic import BaseModel, Field

from app.services import memory as memory_service
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class RecallArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "What to look up. Use 'last conversation' when the user asks what "
            "you discussed previously."
        ),
    )
    limit: int = Field(default=5, ge=1, le=20)


class RememberArgs(BaseModel):
    content: str = Field(
        min_length=1,
        max_length=1000,
        description="A durable fact the user wants remembered.",
    )


class ForgetArgs(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=200,
        description="Words matching the memory to forget.",
    )


async def recall(context: ToolContext, args: RecallArgs) -> ToolResult:
    if memory_service.looks_like_last_conversation_query(args.query):
        last = await memory_service.get_last_session(context.session, context.user_id)
        if last is None or not (last.summary or "").strip():
            return ToolResult(
                summary="I do not have a summary of our last conversation yet.",
                data={"last_session_summary": None, "turns": []},
            )
        turns = await memory_service.get_session_turns(context.session, last.id)
        highlights = [
            f"{'You' if turn.role == 'user' else 'I'} said: {turn.text}"
            for turn in turns[:4]
            if (turn.text or "").strip()
        ]
        spoken = f"Last time we talked about {last.summary.strip()}"
        if highlights:
            spoken += ". Highlights: " + "; ".join(highlights)
        spoken += "."
        return ToolResult(
            summary=spoken,
            data={
                "last_session_summary": last.summary,
                "session_id": last.id,
                "turns": [
                    {"role": turn.role, "text": turn.text, "sequence": turn.sequence}
                    for turn in turns
                ],
            },
        )

    rows = await memory_service.search_memories(
        context.session, context.user_id, args.query, limit=args.limit
    )
    if not rows:
        return ToolResult(
            summary=f"I do not have anything remembered about {args.query}.",
            data={"memories": []},
        )
    spoken = "; ".join(row.content for row in rows)
    count = len(rows)
    noun = "memory" if count == 1 else "memories"
    return ToolResult(
        summary=f"I found {count} {noun}: {spoken}",
        data={
            "memories": [
                {"id": row.id, "content": row.content} for row in rows
            ]
        },
    )


async def remember(context: ToolContext, args: RememberArgs) -> ToolResult:
    memory = await memory_service.remember_fact(
        context.session, context.user_id, args.content
    )
    return ToolResult(
        summary=f"I will remember that {memory.content}",
        data={"id": memory.id, "content": memory.content},
    )


async def forget(context: ToolContext, args: ForgetArgs) -> ToolResult:
    forgotten = await memory_service.forget_by_query(
        context.session, context.user_id, args.query
    )
    if not forgotten:
        raise ToolError(f"I could not find a memory matching {args.query} to forget.")
    if len(forgotten) == 1:
        return ToolResult(
            summary=f"Forgotten: {forgotten[0].content}",
            data={"forgotten": [{"id": forgotten[0].id, "content": forgotten[0].content}]},
        )
    return ToolResult(
        summary=f"I forgot {len(forgotten)} related memories.",
        data={
            "forgotten": [
                {"id": row.id, "content": row.content} for row in forgotten
            ]
        },
    )


RECALL_SPEC = ToolSpec(
    name="recall",
    description=(
        "Search durable personal memories, or recall what was discussed in the "
        "previous conversation when the query is about the last session."
    ),
    args_model=RecallArgs,
    handler=recall,
)

REMEMBER_SPEC = ToolSpec(
    name="remember",
    description="Save a durable personal fact the user asked you to remember.",
    args_model=RememberArgs,
    handler=remember,
)

FORGET_SPEC = ToolSpec(
    name="forget",
    description="Forget a durable personal memory matching the user's request.",
    args_model=ForgetArgs,
    handler=forget,
)
