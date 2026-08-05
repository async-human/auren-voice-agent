from __future__ import annotations

import base64
from email.message import EmailMessage

from pydantic import BaseModel, Field

from app.services import google_oauth
from app.tools.base import ToolContext, ToolError, ToolResult, ToolSpec


class SearchEmailsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=20)


class DraftEmailArgs(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


class SendEmailArgs(BaseModel):
    to: str = Field(min_length=3, max_length=320)
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=20000)


async def search_emails(context: ToolContext, args: SearchEmailsArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    listed = await context.http.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        params={"q": args.query, "maxResults": args.max_results},
        headers={"Authorization": f"Bearer {token}"},
    )
    if listed.status_code == 401:
        raise ToolError("Gmail authorization failed. Reconnect Google.")
    listed.raise_for_status()
    message_ids = [item["id"] for item in listed.json().get("messages", [])]
    if not message_ids:
        return ToolResult(summary=f"No emails matched '{args.query}'.", data={"messages": []})

    messages = []
    for message_id in message_ids[: args.max_results]:
        detail = await context.http.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        detail.raise_for_status()
        payload = detail.json()
        headers = {
            header["name"]: header["value"]
            for header in payload.get("payload", {}).get("headers", [])
        }
        messages.append(
            {
                "id": message_id,
                "from": headers.get("From"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "snippet": payload.get("snippet"),
            }
        )

    spoken = "; ".join(
        f"{item.get('subject') or 'No subject'} from {item.get('from') or 'unknown'}"
        for item in messages
    )
    return ToolResult(
        summary=f"Found {len(messages)} email(s): {spoken}",
        data={"messages": messages},
    )


async def draft_email(context: ToolContext, args: DraftEmailArgs) -> ToolResult:
    preview = (
        f"Draft email to {args.to}\nSubject: {args.subject}\n\n{args.body}"
    )
    return ToolResult(
        summary=(
            f"I drafted an email to {args.to} about '{args.subject}'. "
            "Say confirm send email when you want me to send it, or ask me to change it."
        ),
        data={
            "to": args.to,
            "subject": args.subject,
            "body": args.body,
            "preview": preview,
            "ready_to_send": True,
        },
    )


def _mime_raw(to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


async def send_email(context: ToolContext, args: SendEmailArgs) -> ToolResult:
    token = await google_oauth.valid_access_token(
        context.session, context.settings, context.http, context.user_id
    )
    raw = _mime_raw(args.to, args.subject, args.body)
    response = await context.http.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"raw": raw},
    )
    if response.status_code >= 400:
        raise ToolError(f"Could not send the email: {response.text[:300]}")
    sent = response.json()
    message_id = sent.get("id")

    # Verify it appears when fetched again.
    verify = await context.http.get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
        params={"format": "metadata", "metadataHeaders": ["To", "Subject"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    if verify.status_code != 200:
        raise ToolError("Send reported success but I could not verify the message.")
    return ToolResult(
        summary=f"Verified email sent to {args.to} with subject '{args.subject}'.",
        data={"id": message_id, "threadId": sent.get("threadId"), "verified": True},
    )


SEARCH_SPEC = ToolSpec(
    name="search_emails",
    description="Search the user's Gmail with a Gmail query string.",
    args_model=SearchEmailsArgs,
    handler=search_emails,
)

DRAFT_SPEC = ToolSpec(
    name="draft_email",
    description="Draft an email for the user to review. Does not send.",
    args_model=DraftEmailArgs,
    handler=draft_email,
)

SEND_SPEC = ToolSpec(
    name="send_email",
    description="Send an email via Gmail. Requires explicit user confirmation.",
    args_model=SendEmailArgs,
    handler=send_email,
    confirmation_required=True,
)
