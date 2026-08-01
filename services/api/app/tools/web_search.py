from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.config import Settings
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="What to search for.")
    max_results: int = Field(default=3, ge=1, le=8)


@dataclass
class SearchHit:
    title: str
    snippet: str
    url: str


def resolve_provider(settings: Settings) -> str:
    if settings.web_search_provider != "auto":
        return settings.web_search_provider
    if settings.tavily_api_key:
        return "tavily"
    if settings.brave_search_api_key:
        return "brave"
    if settings.searxng_base_url:
        return "searxng"
    return "duckduckgo"


async def _tavily(context: ToolContext, args: WebSearchArgs) -> list[SearchHit]:
    response = await context.http.post(
        "https://api.tavily.com/search",
        json={
            "api_key": context.settings.tavily_api_key,
            "query": args.query,
            "max_results": args.max_results,
            "search_depth": "basic",
        },
    )
    response.raise_for_status()
    return [
        SearchHit(
            title=item.get("title", ""),
            snippet=item.get("content", ""),
            url=item.get("url", ""),
        )
        for item in response.json().get("results", [])[: args.max_results]
    ]


async def _brave(context: ToolContext, args: WebSearchArgs) -> list[SearchHit]:
    response = await context.http.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": args.query, "count": args.max_results},
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": context.settings.brave_search_api_key or "",
        },
    )
    response.raise_for_status()
    results = response.json().get("web", {}).get("results", [])
    return [
        SearchHit(
            title=item.get("title", ""),
            snippet=item.get("description", ""),
            url=item.get("url", ""),
        )
        for item in results[: args.max_results]
    ]


async def _searxng(context: ToolContext, args: WebSearchArgs) -> list[SearchHit]:
    base_url = (context.settings.searxng_base_url or "").rstrip("/")
    response = await context.http.get(
        f"{base_url}/search",
        params={"q": args.query, "format": "json"},
    )
    response.raise_for_status()
    return [
        SearchHit(
            title=item.get("title", ""),
            snippet=item.get("content", ""),
            url=item.get("url", ""),
        )
        for item in response.json().get("results", [])[: args.max_results]
    ]


async def _duckduckgo(context: ToolContext, args: WebSearchArgs) -> list[SearchHit]:
    """Keyless fallback via the DuckDuckGo Instant Answer API.

    It only returns encyclopaedic answers and related topics, so configure a
    real provider for general web queries.
    """
    response = await context.http.get(
        "https://api.duckduckgo.com/",
        params={"q": args.query, "format": "json", "no_html": 1, "skip_disambig": 1},
    )
    response.raise_for_status()
    payload = response.json()

    hits: list[SearchHit] = []
    if payload.get("AbstractText"):
        hits.append(
            SearchHit(
                title=payload.get("Heading", args.query),
                snippet=payload["AbstractText"],
                url=payload.get("AbstractURL", ""),
            )
        )

    for topic in payload.get("RelatedTopics", []):
        if len(hits) >= args.max_results:
            break
        if "Text" in topic:
            hits.append(
                SearchHit(
                    title=topic.get("Text", "")[:80],
                    snippet=topic.get("Text", ""),
                    url=topic.get("FirstURL", ""),
                )
            )

    return hits[: args.max_results]


PROVIDERS = {
    "tavily": _tavily,
    "brave": _brave,
    "searxng": _searxng,
    "duckduckgo": _duckduckgo,
}


async def search_web(context: ToolContext, args: WebSearchArgs) -> ToolResult:
    provider = resolve_provider(context.settings)
    hits = await PROVIDERS[provider](context, args)

    if not hits:
        raise ToolError(f"I could not find anything useful about {args.query}.")

    spoken = " ".join(f"{hit.title}. {hit.snippet}" for hit in hits)
    return ToolResult(
        summary=spoken[:1200],
        data={
            "provider": provider,
            "results": [
                {"title": hit.title, "snippet": hit.snippet, "url": hit.url} for hit in hits
            ],
        },
    )


SPEC = ToolSpec(
    name="search_web",
    description=(
        "Search the live web for current information, news, or facts that may "
        "have changed since training."
    ),
    args_model=WebSearchArgs,
    handler=search_web,
)
