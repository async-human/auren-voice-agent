"""Protect consequential action payloads and keep audits free of message bodies."""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.security.token_crypto import decrypt_secret, encrypt_secret

ENCRYPTED_PREFIX = "auren-encrypted-v1:"


def protect_arguments(
    settings: Settings,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    protected = dict(arguments)
    if tool == "send_email" and isinstance(protected.get("body"), str):
        protected["body"] = ENCRYPTED_PREFIX + encrypt_secret(settings, protected["body"])
    return protected


def reveal_arguments(
    settings: Settings,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    revealed = dict(arguments)
    body = revealed.get("body")
    if tool == "send_email" and isinstance(body, str) and body.startswith(ENCRYPTED_PREFIX):
        revealed["body"] = decrypt_secret(settings, body[len(ENCRYPTED_PREFIX) :])
    return revealed


def redact_arguments(tool: str, arguments: dict[str, Any] | None) -> dict[str, Any] | None:
    if arguments is None:
        return None
    redacted = dict(arguments)
    if tool in {"draft_email", "send_email"} and "body" in redacted:
        redacted["body"] = "[encrypted or omitted]"
    return redacted


def redact_details(tool: str, details: dict[str, Any] | None) -> dict[str, Any] | None:
    if details is None:
        return None
    if tool == "draft_email":
        return {
            key: value
            for key, value in details.items()
            if key not in {"body", "preview", "content_hash"}
        }
    if tool in {"search_emails", "read_email"}:
        messages = details.get("messages")
        if isinstance(messages, list):
            return {
                **{key: value for key, value in details.items() if key != "messages"},
                "messages": [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"body", "snippet"}
                    }
                    for item in messages
                    if isinstance(item, dict)
                ],
            }
    return details
