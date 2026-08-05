from __future__ import annotations

from pydantic import BaseModel

from app.services import page_context as page_context_service
from app.tools.base import ToolContext, ToolResult, ToolSpec


class GetPageContextArgs(BaseModel):
    pass


async def get_page_context(context: ToolContext, _args: GetPageContextArgs) -> ToolResult:
    page = await page_context_service.get_page_context(
        context.session,
        context.user_id,
        include_text=True,
    )
    if not page.present or not page.text:
        return ToolResult(
            summary=(
                "No active page is available. Ask the user to open the article in "
                "Chrome and click Send to Auren in the Auren Page Reader extension, "
                "then ask again."
            ),
            data={"present": False},
        )

    title = page.title or "Untitled page"
    url = page.url or ""
    note = " The stored text was truncated." if page.truncated else ""
    summary = (
        f"Active page context for {title} ({url}). "
        f"Full extracted article text follows. Explain it conversationally in your "
        f"own words; do not read it verbatim unless asked.{note}\n\n{page.text}"
    )
    return ToolResult(
        summary=summary,
        data={
            "present": True,
            "url": url,
            "title": title,
            "char_count": page.char_count,
            "truncated": page.truncated,
        },
    )


SPEC = ToolSpec(
    name="get_page_context",
    description=(
        "Load the user's currently shared browser page or article text. Use this "
        "whenever they ask what is on their screen, what they are looking at, or "
        "to explain, summarise, or read the page, article, tab, or screen they "
        "sent from the Auren extension."
    ),
    args_model=GetPageContextArgs,
    handler=get_page_context,
)
