"""Encrypt OAuth tokens at rest with Fernet."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


def _fernet(settings: Settings) -> Fernet:
    raw = (settings.token_encryption_key or settings.tool_gateway_token or "dev-only-key").encode(
        "utf-8"
    )
    digest = hashlib.sha256(raw).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(settings: Settings, value: str) -> str:
    return _fernet(settings).encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(settings: Settings, value: str) -> str:
    try:
        return _fernet(settings).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("Could not decrypt stored credential") from error
