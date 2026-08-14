"""Deterministic checks before autonomous memory or follow-up jobs are persisted."""

from __future__ import annotations

import re

from app.models.schemas import DistilledMemoryIn
from app.models.tables import UserSettings

_SECRET_RE = re.compile(
    r"(api[_-]?key|password|passwd|secret\s*[:=]|bearer\s+[a-z0-9._\-]{8,}"
    r"|-----begin |sk-[a-z0-9]{10,}|xox[baprs]-)",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|bye|good night|good morning)[.!?]*$",
    re.IGNORECASE,
)


def is_restricted(content: str) -> bool:
    return bool(_SECRET_RE.search(content or ""))


def is_transient(content: str) -> bool:
    return bool(_TRANSIENT_RE.match((content or "").strip()))


def allow_autonomous_memory(policy: UserSettings, item: DistilledMemoryIn) -> str | None:
    """Return a rejection reason, or None if the candidate may be stored."""
    if not policy.autonomous_memory_enabled:
        return "autonomous_memory_disabled"
    memory_type = item.memory_type or "semantic"
    if memory_type == "semantic" and not policy.semantic_memory_enabled:
        return "semantic_memory_disabled"
    if memory_type == "procedural" and not policy.procedural_memory_enabled:
        return "procedural_memory_disabled"
    content = item.content.strip()
    if not content:
        return "empty"
    if is_restricted(content):
        return "restricted"
    if is_transient(content):
        return "transient"
    if item.confidence < 0.4:
        return "low_confidence"
    return None


def allow_autonomous_profile(policy: UserSettings) -> bool:
    return bool(policy.autonomous_memory_enabled and policy.semantic_memory_enabled)


def allow_prospective_jobs(policy: UserSettings) -> bool:
    return bool(policy.autonomous_memory_enabled and policy.prospective_memory_enabled)
