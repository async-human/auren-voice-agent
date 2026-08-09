from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import re
import uuid
from email.message import EmailMessage
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.services import approvals, google_oauth
from app.services.action_payloads import protect_arguments
from app.services.google_http import request as google_request
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec

GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_ADDRESS_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_address(value: str) -> str:
    cleaned = value.strip()
    if "\r" in cleaned or "\n" in cleaned or not _ADDRESS_RE.fullmatch(cleaned):
        raise ValueError("must be one valid email address")
    return cleaned


def _clean_subject(value: str) -> str:
    cleaned = value.strip()
    if "\r" in cleaned or "\n" in cleaned:
        raise ValueError("must not contain line breaks")
    return cleaned


class SearchEmailsArgs(BaseModel):
    query: str = Field(
        default="in:inbox",
        max_length=500,
        description="Gmail search query. Defaults to the inbox.",
    )
    max_results: int = Field(default=5, ge=1, le=20)
    include_body: bool = Field(
        default=False,
        description="Include a safely truncated plain-text body for reading or summarization.",
    )


class ReadEmailArgs(BaseModel):
    message_id: str = Field(min_length=1, max_length=128)


class DraftEmailArgs(BaseModel):
    operation_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable id used to identify this exact outbound message version.",
    )
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    draft_id: str | None = Field(
        default=None,
        max_length=128,
        description="Existing Gmail draft id when revising a prior draft.",
    )

    _validate_to = field_validator("to")(_clean_address)
    _validate_subject = field_validator("subject")(_clean_subject)


class SendEmailArgs(BaseModel):
    operation_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        min_length=16,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)
    draft_id: str | None = Field(default=None, max_length=128)
    draft_content_hash: str | None = Field(default=None, min_length=64, max_length=64)

    _validate_to = field_validator("to")(_clean_address)
    _validate_subject = field_validator("subject")(_clean_subject)

    @model_validator(mode="after")
    def _draft_version_is_complete(self) -> SendEmailArgs:
        if bool(self.draft_id) != bool(self.draft_content_hash):
            raise ValueError("draft_id and draft_content_hash must be supplied together")
        return self


def _decode_urlsafe(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _raw_hash(raw: str) -> str:
    return hashlib.sha256(_decode_urlsafe(raw)).hexdigest()


def _rfc_message_id(operation_id: str) -> str:
    return f"<auren-{operation_id}@auren.local>"


def _mime_raw(to: str, subject: str, body: str, operation_id: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = _rfc_message_id(operation_id)
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("name") or "").lower(): str(item.get("value") or "")
        for item in payload.get("headers", [])
        if isinstance(item, dict)
    }


def _plain_body(payload: dict[str, Any], *, limit: int = 12000) -> str:
    plain: list[str] = []
    rich: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType") or "")
        data = (part.get("body") or {}).get("data")
        if isinstance(data, str) and data:
            try:
                decoded = _decode_urlsafe(data).decode("utf-8", errors="replace")
            except (ValueError, TypeError):
                decoded = ""
            if mime_type == "text/plain":
                plain.append(decoded)
            elif mime_type == "text/html":
                rich.append(html.unescape(_TAG_RE.sub(" ", decoded)))
        for child in part.get("parts", []) or []:
            if isinstance(child, dict):
                visit(child)

    visit(payload)
    text = "\n".join(plain or rich)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _message_data(payload: dict[str, Any], *, include_body: bool) -> dict[str, Any]:
    message_payload = payload.get("payload") or {}
    headers = _headers(message_payload)
    result: dict[str, Any] = {
        "id": payload.get("id"),
        "threadId": payload.get("threadId"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject") or "No subject",
        "date": headers.get("date"),
        "internalDate": payload.get("internalDate"),
        "snippet": payload.get("snippet"),
    }
    if include_body:
        result["body"] = _plain_body(message_payload)
    return result


def _raise_gmail_error(status_code: int, body: str, operation: str) -> None:
    if status_code == 401:
        raise ToolError("Gmail authorization failed. Reconnect Google.")
    if status_code == 403:
        raise ToolError(f"Gmail denied permission to {operation}. Reconnect Google.")
    if status_code >= 400:
        raise ToolError(f"Gmail could not {operation}: {body[:240]}")


async def search_emails(context: ToolContext, args: SearchEmailsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    query = args.query.strip() or "in:inbox"
    candidate_count = min(20, max(10, args.max_results * 2))
    listed = await google_request(
        context.http,
        "GET",
        f"{GMAIL_BASE}/messages",
        params={"q": query, "maxResults": candidate_count},
        headers={"Authorization": f"Bearer {token}"},
    )
    _raise_gmail_error(listed.status_code, listed.text, "search email")
    message_ids = [
        item.get("id")
        for item in listed.json().get("messages", [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not message_ids:
        return ToolResult(summary=f"No emails matched '{query}'.", data={"messages": []})

    async def fetch_message(message_id: str) -> dict[str, Any]:
        detail = await google_request(
            context.http,
            "GET",
            f"{GMAIL_BASE}/messages/{message_id}",
            params={
                "format": "full" if args.include_body else "metadata",
                "metadataHeaders": ["From", "To", "Subject", "Date"],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        _raise_gmail_error(detail.status_code, detail.text, "read email metadata")
        return _message_data(detail.json(), include_body=args.include_body)

    messages = await asyncio.gather(*(fetch_message(message_id) for message_id in message_ids))
    messages.sort(key=lambda item: int(item.get("internalDate") or 0), reverse=True)
    messages = messages[: args.max_results]
    spoken = "; ".join(
        f"{item.get('subject') or 'No subject'} from {item.get('from') or 'unknown'}"
        for item in messages
    )
    return ToolResult(
        summary=f"Found {len(messages)} email(s), newest first: {spoken}",
        data={"messages": messages, "query": query, "newest_first": True},
    )


async def read_email(context: ToolContext, args: ReadEmailArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    response = await google_request(
        context.http,
        "GET",
        f"{GMAIL_BASE}/messages/{args.message_id}",
        params={"format": "full"},
        headers={"Authorization": f"Bearer {token}"},
    )
    _raise_gmail_error(response.status_code, response.text, "read email")
    message = _message_data(response.json(), include_body=True)
    return ToolResult(
        summary=(
            f"Email '{message.get('subject')}' from {message.get('from')}. "
            f"Body: {(message.get('body') or message.get('snippet') or '')[:1200]}"
        ),
        data={"messages": [message]},
    )


async def draft_email(context: ToolContext, args: DraftEmailArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    raw = _mime_raw(args.to, args.subject, args.body, args.operation_id)
    url = f"{GMAIL_BASE}/drafts" + (f"/{args.draft_id}" if args.draft_id else "")
    response = await google_request(
        context.http,
        "PUT" if args.draft_id else "POST",
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": {"raw": raw}},
    )
    _raise_gmail_error(
        response.status_code,
        response.text,
        "update the draft" if args.draft_id else "create the draft",
    )
    draft_id = str(response.json().get("id") or args.draft_id or "")
    if not draft_id:
        raise ToolError("Gmail created a draft without returning its id.")

    verify = await google_request(
        context.http,
        "GET",
        f"{GMAIL_BASE}/drafts/{draft_id}",
        params={"format": "raw"},
        headers={"Authorization": f"Bearer {token}"},
    )
    _raise_gmail_error(verify.status_code, verify.text, "verify the draft")
    verified_raw = ((verify.json().get("message") or {}).get("raw"))
    if not isinstance(verified_raw, str) or not verified_raw:
        raise ToolError("Gmail did not return the draft content for verification.")
    content_hash = _raw_hash(verified_raw)

    await approvals.supersede_pending(
        context.session,
        user_id=context.user_id,
        tool="send_email",
        argument_key="draft_id",
        argument_value=draft_id,
    )
    send_arguments = {
        "operation_id": args.operation_id,
        "to": args.to,
        "subject": args.subject,
        "body": args.body,
        "draft_id": draft_id,
        "draft_content_hash": content_hash,
    }
    action = await approvals.propose_action(
        context.session,
        user_id=context.user_id,
        tool="send_email",
        arguments=protect_arguments(context.settings, "send_email", send_arguments),
        preview=(
            f"Send Gmail draft {draft_id} to {args.to} with subject '{args.subject}'. "
            "The body is encrypted in Auren's approval record."
        ),
        idempotency_key=(
            "gmail-draft:"
            + hashlib.sha256(f"{draft_id}:{content_hash}".encode("utf-8")).hexdigest()
        ),
    )
    preview = f"To: {args.to}\nSubject: {args.subject}\n\n{args.body}"
    return ToolResult(
        summary=(
            f"I created and verified a Gmail draft to {args.to} with subject "
            f"'{args.subject}'. It has not been sent. Review it, then say confirm to send "
            "this exact version or cancel to keep it as a draft."
        ),
        data={
            "pending": True,
            "action_id": action.id,
            "tool": "send_email",
            "draft_id": draft_id,
            "content_hash": content_hash,
            "preview": preview,
            "ready_to_send": True,
        },
    )


async def send_email(context: ToolContext, args: SendEmailArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if args.draft_id:
        current = await google_request(
            context.http,
            "GET",
            f"{GMAIL_BASE}/drafts/{args.draft_id}",
            params={"format": "raw"},
            headers={"Authorization": f"Bearer {token}"},
        )
        _raise_gmail_error(current.status_code, current.text, "verify the approved draft")
        current_raw = ((current.json().get("message") or {}).get("raw"))
        if not isinstance(current_raw, str) or _raw_hash(current_raw) != args.draft_content_hash:
            raise ToolError(
                "The Gmail draft changed after approval was prepared. Review the updated draft "
                "and approve it again."
            )
        response = await google_request(
            context.http,
            "POST",
            f"{GMAIL_BASE}/drafts/send",
            headers=headers,
            json={"id": args.draft_id},
        )
    else:
        response = await google_request(
            context.http,
            "POST",
            f"{GMAIL_BASE}/messages/send",
            headers=headers,
            json={"raw": _mime_raw(args.to, args.subject, args.body, args.operation_id)},
        )
    if response.status_code >= 500:
        # Never blindly retry a send. Search by our stable RFC Message-ID to
        # resolve the ambiguous outcome without risking a duplicate email.
        lookup = await google_request(
            context.http,
            "GET",
            f"{GMAIL_BASE}/messages",
            params={"q": f"rfc822msgid:{_rfc_message_id(args.operation_id)}", "maxResults": 1},
            headers={"Authorization": f"Bearer {token}"},
        )
        matches = lookup.json().get("messages", []) if lookup.status_code == 200 else []
        if matches and isinstance(matches[0], dict) and matches[0].get("id"):
            sent = matches[0]
        else:
            raise ToolError(
                "Gmail returned an ambiguous send result. I did not retry because that could "
                "send a duplicate. Check Sent before asking me to try again."
            )
    else:
        _raise_gmail_error(response.status_code, response.text, "send the email")
        sent = response.json()
    message_id = sent.get("id")
    if not message_id:
        raise ToolError("Gmail reported success without returning a sent message id.")

    verify = await google_request(
        context.http,
        "GET",
        f"{GMAIL_BASE}/messages/{message_id}",
        params={"format": "metadata", "metadataHeaders": ["To", "Subject"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    _raise_gmail_error(verify.status_code, verify.text, "verify the sent email")
    sent_headers = _headers((verify.json().get("payload") or {}))
    if sent_headers.get("subject") != args.subject or args.to.lower() not in sent_headers.get(
        "to", ""
    ).lower():
        raise ToolError("Gmail sent a message, but its verified recipient or subject differed.")
    return ToolResult(
        summary=f"Verified email sent to {args.to} with subject '{args.subject}'.",
        data={
            "id": message_id,
            "threadId": sent.get("threadId"),
            "draft_id": args.draft_id,
            "verified": True,
        },
    )


SEARCH_SPEC = ToolSpec(
    name="search_emails",
    description=(
        "Search Gmail and return matching messages newest first. Use query='in:inbox', "
        "max_results=1, include_body=true for the most recent inbox email."
    ),
    args_model=SearchEmailsArgs,
    handler=search_emails,
)

READ_SPEC = ToolSpec(
    name="read_email",
    description="Read the full plain-text content of one Gmail message by message_id.",
    args_model=ReadEmailArgs,
    handler=read_email,
)

DRAFT_SPEC = ToolSpec(
    name="draft_email",
    description=(
        "Create or update a real Gmail draft, verify it, and prepare an approval-bound send. "
        "This never sends immediately."
    ),
    args_model=DraftEmailArgs,
    handler=draft_email,
)

SEND_SPEC = ToolSpec(
    name="send_email",
    description="Send an email or a version-bound Gmail draft. Always requires confirmation.",
    args_model=SendEmailArgs,
    handler=send_email,
    confirmation_required=True,
)
