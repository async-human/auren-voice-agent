from __future__ import annotations

import base64
import json

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.tables import Base, PendingAction
from app.services import action_execution, google_oauth
from app.services.action_payloads import ENCRYPTED_PREFIX
from app.tools.base import ToolContext, ToolError
from app.tools.email_tools import DraftEmailArgs, SearchEmailsArgs, draft_email, search_emails


def _encoded(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


async def _context(settings, tmp_path, transport):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'email.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return ToolContext("email-user", settings, http, session), engine


@pytest.fixture
def valid_google_token(monkeypatch):
    async def token(*_args, **_kwargs) -> str:
        return "google-access-token"

    monkeypatch.setattr(google_oauth, "valid_access_token", token)


async def test_search_emails_returns_the_actual_newest_message_with_body(
    settings,
    tmp_path,
    valid_google_token,
) -> None:
    seen_query = None

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        if request.url.path.endswith("/messages"):
            seen_query = request.url.params.get("q")
            return httpx.Response(200, json={"messages": [{"id": "old"}, {"id": "new"}]})
        message_id = request.url.path.rsplit("/", 1)[-1]
        is_new = message_id == "new"
        return httpx.Response(
            200,
            json={
                "id": message_id,
                "threadId": f"thread-{message_id}",
                "internalDate": "2000" if is_new else "1000",
                "snippet": "new snippet" if is_new else "old snippet",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Subject", "value": "Newest" if is_new else "Older"},
                        {"name": "Date", "value": "today"},
                    ],
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encoded("The complete email body")},
                        }
                    ],
                },
            },
        )

    context, engine = await _context(settings, tmp_path, respond)
    try:
        result = await search_emails(
            context,
            SearchEmailsArgs(query="in:inbox", max_results=1, include_body=True),
        )
        assert seen_query == "in:inbox"
        assert result.data["newest_first"] is True
        assert result.data["messages"][0]["id"] == "new"
        assert result.data["messages"][0]["body"] == "The complete email body"
    finally:
        await context.http.aclose()
        await context.session.close()
        await engine.dispose()


async def test_real_gmail_draft_is_encrypted_pending_and_sent_only_after_approval(
    settings,
    tmp_path,
    valid_google_token,
) -> None:
    state: dict[str, object] = {"raw": None, "send_count": 0}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/drafts"):
            state["raw"] = json.loads(request.content)["message"]["raw"]
            return httpx.Response(200, json={"id": "draft-1"})
        if request.method == "GET" and path.endswith("/drafts/draft-1"):
            return httpx.Response(200, json={"id": "draft-1", "message": {"raw": state["raw"]}})
        if request.method == "POST" and path.endswith("/drafts/send"):
            state["send_count"] = int(state["send_count"]) + 1
            assert json.loads(request.content) == {"id": "draft-1"}
            return httpx.Response(200, json={"id": "sent-1", "threadId": "thread-1"})
        if request.method == "GET" and path.endswith("/messages/sent-1"):
            return httpx.Response(
                200,
                json={
                    "id": "sent-1",
                    "payload": {
                        "headers": [
                            {"name": "To", "value": "rahul@example.com"},
                            {"name": "Subject", "value": "Monday agenda"},
                        ]
                    },
                },
            )
        return httpx.Response(404, text=f"Unexpected {request.method} {path}")

    context, engine = await _context(settings, tmp_path, respond)
    try:
        proposed = await draft_email(
            context,
            DraftEmailArgs(
                to="rahul@example.com",
                subject="Monday agenda",
                body="Here is the agenda.",
            ),
        )
        assert proposed.data["pending"] is True
        assert state["send_count"] == 0

        pending = await context.session.scalar(
            select(PendingAction).where(PendingAction.id == proposed.data["action_id"])
        )
        assert pending is not None
        assert pending.status == "pending"
        assert str(pending.arguments["body"]).startswith(ENCRYPTED_PREFIX)
        assert "Here is the agenda" not in pending.preview

        completed = await action_execution.execute_pending_action(
            context,
            action_id=proposed.data["action_id"],
        )
        assert completed.data["verified"] is True
        assert state["send_count"] == 1

        with pytest.raises(ToolError, match="no pending action"):
            await action_execution.execute_pending_action(
                context,
                action_id=proposed.data["action_id"],
            )
        assert state["send_count"] == 1
    finally:
        await context.http.aclose()
        await context.session.close()
        await engine.dispose()


async def test_editing_a_gmail_draft_supersedes_the_old_send_approval(
    settings,
    tmp_path,
    valid_google_token,
) -> None:
    state: dict[str, str | None] = {"raw": None}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method in {"POST", "PUT"} and (
            path.endswith("/drafts") or path.endswith("/drafts/draft-1")
        ):
            state["raw"] = json.loads(request.content)["message"]["raw"]
            return httpx.Response(200, json={"id": "draft-1"})
        if request.method == "GET" and path.endswith("/drafts/draft-1"):
            return httpx.Response(200, json={"message": {"raw": state["raw"]}})
        return httpx.Response(404)

    context, engine = await _context(settings, tmp_path, respond)
    try:
        first = await draft_email(
            context,
            DraftEmailArgs(to="rahul@example.com", subject="Agenda", body="Version one"),
        )
        second = await draft_email(
            context,
            DraftEmailArgs(
                to="rahul@example.com",
                subject="Agenda",
                body="Version two",
                draft_id="draft-1",
            ),
        )
        old = await context.session.get(PendingAction, first.data["action_id"])
        current = await context.session.get(PendingAction, second.data["action_id"])
        assert old is not None and old.status == "superseded"
        assert current is not None and current.status == "pending"
    finally:
        await context.http.aclose()
        await context.session.close()
        await engine.dispose()
