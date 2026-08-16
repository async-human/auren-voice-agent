"""Personal memory helpers for the voice worker.

The worker buffers the transcript, asks the local LLM to distill durable facts
at session end, then posts everything to the Railway gateway. Context for the
next greeting is fetched from that same gateway at session start.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger("auren.memory")

DISTILL_PROMPT = """\
You extract durable personal memory from a voice conversation with Auren.

Return ONLY valid JSON with this shape:
{
  "summary": "one short sentence about what this conversation was about",
  "profile_summary": "optional updated 1-2 sentence profile of the user, or null",
  "preferences": "optional short preference notes, or null",
  "topics": ["short topic labels"],
  "outcomes": ["decisions or results from this conversation"],
  "open_threads": ["unresolved commitments or questions to pick up later"],
  "follow_ups": [
    {
      "message": "what to check or remind about",
      "run_in_minutes": 1440,
      "job_type": "follow_up_reminder",
      "query": null
    }
  ],
  "memories": ["short durable facts worth remembering across sessions"]
}

Rules:
- Only include facts the user clearly stated or confirmed.
- Prefer stable facts (name, job, preferences, people, ongoing projects).
- Do not invent details. Do not include one-off chit-chat.
- Keep each memory under 160 characters.
- Return at most 8 memories. Use an empty list when nothing durable was said.
- open_threads are unfinished user commitments, not assistant suggestions.
- follow_ups only when the user asked to be reminded or to check later.
- For "if they don't reply", use job_type=email_reply_check and a Gmail query.
- Do not include passwords, API keys, or tokens.
"""


@dataclass
class TranscriptBuffer:
    turns: list[dict[str, Any]] = field(default_factory=list)

    def add(self, role: str, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned or role not in {"user", "assistant"}:
            return
        self.turns.append(
            {"role": role, "text": cleaned, "sequence": len(self.turns)}
        )

    def as_dialog(self) -> str:
        lines = []
        for turn in self.turns:
            speaker = "User" if turn["role"] == "user" else "Auren"
            lines.append(f"{speaker}: {turn['text']}")
        return "\n".join(lines)


def extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        return payload if isinstance(payload, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None


async def fetch_context(
    client: httpx.AsyncClient, user_id: str
) -> dict[str, Any] | None:
    try:
        response = await client.get("/v1/memory/context", params={"user_id": user_id})
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        logger.warning("Could not load memory context: %s", error)
        return None


async def fetch_due_reminders(
    client: httpx.AsyncClient, user_id: str
) -> str | None:
    """Load due reminders once so a new voice session can surface them."""
    try:
        response = await client.post(
            "/v1/tools/invoke",
            json={
                "tool": "list_reminders",
                "user_id": user_id,
                "arguments": {"status": "due", "limit": 5},
            },
        )
        response.raise_for_status()
        payload = response.json()
        reminders = ((payload.get("data") or {}).get("reminders") or [])
        if not payload.get("ok") or not reminders:
            return None
        return str(payload.get("summary") or "").strip() or None
    except (httpx.HTTPError, ValueError, TypeError) as error:
        logger.warning("Could not load due reminders: %s", error)
        return None


async def distill_with_llm(
    *,
    llm_base_url: str,
    llm_model: str,
    dialog: str,
) -> dict[str, Any]:
    if not dialog.strip():
        return {
            "summary": None,
            "profile_summary": None,
            "preferences": None,
            "topics": [],
            "outcomes": [],
            "open_threads": [],
            "follow_ups": [],
            "memories": [],
        }

    payload = {
        "model": llm_model,
        "messages": [
            {"role": "system", "content": DISTILL_PROMPT},
            {"role": "user", "content": dialog[:12000]},
        ],
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                f"{llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": "Bearer ollama"},
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    except Exception as error:  # noqa: BLE001 - distillation must never break hangup
        logger.warning("Memory distillation failed: %s", error)
        return {
            "summary": _fallback_summary(dialog),
            "profile_summary": None,
            "preferences": None,
            "topics": [],
            "outcomes": [],
            "open_threads": [],
            "follow_ups": [],
            "memories": [],
        }

    parsed = extract_json_object(content) or {}
    memories = parsed.get("memories") or []
    if not isinstance(memories, list):
        memories = []
    return {
        "summary": _as_optional_str(parsed.get("summary")) or _fallback_summary(dialog),
        "profile_summary": _as_optional_str(parsed.get("profile_summary")),
        "preferences": _as_optional_str(parsed.get("preferences")),
        "topics": _as_str_list(parsed.get("topics"), limit=12),
        "outcomes": _as_str_list(parsed.get("outcomes"), limit=12),
        "open_threads": _as_str_list(parsed.get("open_threads"), limit=12),
        "follow_ups": _as_follow_ups(parsed.get("follow_ups")),
        "memories": [
            {"content": item.strip()}
            for item in memories
            if isinstance(item, str) and item.strip()
        ][:8],
    }


async def flush_session(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    room_name: str | None,
    buffer: TranscriptBuffer,
    distilled: dict[str, Any],
) -> bool:
    if not buffer.turns and not distilled.get("memories"):
        return False
    payload = {
        "user_id": user_id,
        "room_name": room_name,
        "turns": buffer.turns,
        "summary": distilled.get("summary"),
        "profile_summary": distilled.get("profile_summary"),
        "preferences": distilled.get("preferences"),
        "topics": distilled.get("topics") or [],
        "outcomes": distilled.get("outcomes") or [],
        "open_threads": distilled.get("open_threads") or [],
        "follow_ups": distilled.get("follow_ups") or [],
        "memories": distilled.get("memories") or [],
    }
    try:
        response = await client.post("/v1/memory/sessions/flush", json=payload)
        response.raise_for_status()
        logger.info(
            "Flushed memory session for %s: %s",
            user_id,
            response.json(),
        )
        return True
    except httpx.HTTPError as error:
        logger.warning("Could not flush memory session: %s", error)
        return False


async def ack_speech(
    client: httpx.AsyncClient, user_id: str, ids: list[str]
) -> None:
    if not ids:
        return
    try:
        response = await client.post(
            "/v1/notifications/speech-ack",
            json={"user_id": user_id, "ids": ids[:50]},
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        logger.warning("Could not ack spoken notifications: %s", error)


def _as_str_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip()[:400])
        if len(items) >= limit:
            break
    return items


def _as_follow_ups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed = {"follow_up_reminder", "email_reply_check", "custom"}
    items: list[dict[str, Any]] = []
    for raw in value[:10]:
        if not isinstance(raw, dict):
            continue
        message = _as_optional_str(raw.get("message"))
        if not message:
            continue
        minutes = raw.get("run_in_minutes", 1440)
        try:
            run_in_minutes = int(minutes)
        except (TypeError, ValueError):
            run_in_minutes = 1440
        run_in_minutes = max(1, min(run_in_minutes, 60 * 24 * 30))
        job_type = str(raw.get("job_type") or "follow_up_reminder")
        if job_type not in allowed:
            job_type = "follow_up_reminder"
        item: dict[str, Any] = {
            "message": message[:2000],
            "run_in_minutes": run_in_minutes,
            "job_type": job_type,
        }
        query = _as_optional_str(raw.get("query"))
        if query:
            item["query"] = query[:500]
        items.append(item)
    return items


def _as_optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"null", "none"}:
        return None
    return cleaned


def _fallback_summary(dialog: str) -> str | None:
    for line in dialog.splitlines():
        if line.startswith("User:"):
            text = line.removeprefix("User:").strip()
            if text:
                return text[:180]
    return None
