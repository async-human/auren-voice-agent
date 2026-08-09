import asyncio
from collections.abc import AsyncIterable, AsyncIterator
import io
import json
import logging
import os
import re
import time

import av
import httpx
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
    inference,
    room_io,
)
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import openai, silero

from memory import (
    TranscriptBuffer,
    distill_with_llm,
    fetch_context,
    fetch_due_reminders,
    flush_session,
)
from screen_reader import ScreenReader
from session_state import SessionTracker
from stt_provider import STTConfig, build_stt
from tools import ToolGateway, build_tools

if os.getenv("AUREN_ENV", "development") != "production":
    load_dotenv(".env.local")


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


LIVEKIT_AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "auren-agent")
for livekit_variable in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
    require_env(livekit_variable)

STT_CONFIG = STTConfig.from_env()
LLM_BASE_URL = require_env("LLM_BASE_URL").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
CHATTERBOX_BASE_URL = require_env("CHATTERBOX_BASE_URL").rstrip("/")
CHATTERBOX_MODEL = os.getenv("CHATTERBOX_MODEL", "chatterbox-turbo")
CHATTERBOX_VOICE = os.getenv("CHATTERBOX_VOICE", "Olivia.wav")
CHATTERBOX_TIMEOUT_SECONDS = max(
    10.0, float(os.getenv("CHATTERBOX_TIMEOUT_SECONDS", "30"))
)
CHATTERBOX_MAX_SEGMENT_CHARS = max(
    60, int(os.getenv("CHATTERBOX_MAX_SEGMENT_CHARS", "120"))
)
CHATTERBOX_RETRY_ATTEMPTS = max(
    1, int(os.getenv("CHATTERBOX_RETRY_ATTEMPTS", "1"))
)
LLM_MAX_COMPLETION_TOKENS = max(
    64, int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "220"))
)
# Qwen3 defaults to "thinking" which can burn minutes of tokens before speech.
LLM_DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

TOOL_GATEWAY_BASE_URL = os.getenv("TOOL_GATEWAY_BASE_URL", "").rstrip("/")
TOOL_GATEWAY_TOKEN = os.getenv("TOOL_GATEWAY_TOKEN")
TOOL_GATEWAY_TIMEOUT_SECONDS = float(os.getenv("TOOL_GATEWAY_TIMEOUT_SECONDS", "20"))

INSTRUCTIONS = (
    "You are Auren, a warm, highly capable personal voice assistant. "
    "Respond directly and naturally. Keep routine voice responses concise, "
    "usually one to three sentences. Ask a short clarifying question when "
    "the user's intent is ambiguous. Adapt your tone and phrasing to the "
    "conversation instead of using canned closers. Do not use emoji, emoticons, "
    "or decorative symbols. Do not end every response with a question. Use the "
    "user's name sparingly; never prefix routine responses with it or repeat it "
    "as a conversational habit. "
    "Speak English only. "
    "Never expose hidden reasoning. "
    "/no_think"
)

TOOL_INSTRUCTIONS = (
    " You convert spoken objectives into completed, verified outcomes. "
    "Workflow loop: understand the goal, recall preferences from memory, ask only "
    "for missing details, start_workflow with a short plan, execute tools, request "
    "confirmation for consequential actions, verify success from tool results, then "
    "complete_workflow and remember durable facts. "
    "Never tell the user how they could do something in Google Calendar or Gmail — "
    "do it with tools when connected. If Google is not connected, say so and ask "
    "them to connect Google in Auren. "
    "You have calendar tools (list_calendar_events, find_free_slots, "
    "create_calendar_event), email tools (search_emails, draft_email, send_email), "
    "reminders, notes, web search, page context, screen reading, and memory. "
    "create_calendar_event and send_email require confirmation: when a tool returns "
    "pending=true, read the preview and ask the user to confirm. The voice controller "
    "handles the user's next explicit confirmation or rejection; do not try to execute "
    "that decision yourself. Never claim an email was sent or an event was created "
    "unless the authoritative result says verified or succeeded after confirmation. "
    "For follow-ups like 'remind me tomorrow if she doesn't reply', call "
    "schedule_followup. Check list_reminders with status due for fired reminders. "
    "Use memory before asking preference questions. Keep voice replies concise."
)

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\u2600-\u27BF"
    "\u200D\uFE0F"
    "]"
)


def _clean_assistant_text(text: str) -> str:
    cleaned = _EMOJI_RE.sub("", text)
    cleaned = re.sub(r"\s+([,.;!?])", r"\1", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


async def _iter_tts_segments(text: AsyncIterable[str]) -> AsyncIterator[str]:
    """Yield sentence-sized text so Chatterbox can begin before the LLM finishes."""
    buffer = ""
    async for chunk in text:
        buffer += chunk
        while buffer:
            sentence = re.search(r"(?<=[.!?])(?:\s+|$)", buffer)
            if sentence and sentence.end() <= CHATTERBOX_MAX_SEGMENT_CHARS:
                split_at = sentence.end()
            elif len(buffer) >= CHATTERBOX_MAX_SEGMENT_CHARS:
                split_at = buffer.rfind(" ", 0, CHATTERBOX_MAX_SEGMENT_CHARS + 1)
                if split_at <= 0:
                    split_at = CHATTERBOX_MAX_SEGMENT_CHARS
            else:
                break

            segment = _clean_assistant_text(buffer[:split_at])
            buffer = buffer[split_at:].lstrip()
            if segment:
                yield segment

    final = _clean_assistant_text(buffer)
    if final:
        yield final


def _decode_audio_frames(audio_bytes: bytes) -> list[rtc.AudioFrame]:
    """Decode encoded Chatterbox audio into LiveKit's mono 24 kHz PCM boundary."""
    frames: list[rtc.AudioFrame] = []
    container = av.open(io.BytesIO(audio_bytes))
    resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)

    for decoded_frame in container.decode(audio=0):
        for frame in resampler.resample(decoded_frame):
            pcm = frame.to_ndarray().tobytes()
            if not pcm:
                continue
            frames.append(
                rtc.AudioFrame(
                    data=pcm,
                    sample_rate=24000,
                    num_channels=1,
                    samples_per_channel=len(pcm) // 2,
                )
            )

    for frame in resampler.resample(None):
        pcm = frame.to_ndarray().tobytes()
        if not pcm:
            continue
        frames.append(
            rtc.AudioFrame(
                data=pcm,
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=len(pcm) // 2,
            )
        )
    return frames


def _is_weather_request(text: str) -> bool:
    return bool(re.search(r"\b(weather|forecast|temperature|rain(?:ing)?)\b", text, re.I))


def _is_web_search_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"google(?:\s+search)?|search\s+google|"
            r"search (?:the )?(?:web|online|internet)|"
            r"look (?:it|this|that) up(?: online)?|"
            r"news|headlines|"
            r"latest (?:news|updates?|market|scores?)|"
            r"current (?:market|news)|"
            r"(?:stock|share) market|"
            r"sensex|nifty|"
            r"market (?:perform(?:ed|ance)?|today|today'?s)"
            r")\b",
            text,
            re.I,
        )
    )


def _is_confirm_request(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(yes|yeah|yep|confirm|confirmed|go ahead|do it|approve|ok|okay|"
            r"sounds good|please proceed|send it|schedule it)\s*[.!?]?\s*",
            text,
            re.I,
        )
    )


def _is_reject_request(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(no|nope|cancel|reject|stop|don't|do not|never mind|nevermind)\s*[.!?]?\s*",
            text,
            re.I,
        )
    )


def _is_screen_read_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"what(?:'s| is) on (?:my|the) screen|"
            r"what (?:am i|i(?:'?m| am) looking at)|"
            r"looking at (?:on )?(?:my |the )?screen|"
            r"(?:see|read|explain|describe|summarise|summarize) (?:my |the )?screen|"
            r"read (?:this|what(?:'s| is) on) (?:my |the )?screen|"
            r"can you see (?:my |the )?screen|"
            r"look at (?:my |the )?screen"
            r")\b",
            text,
            re.I,
        )
    )


def _is_page_explain_request(text: str) -> bool:
    return bool(
        re.search(
            r"\b("
            r"(?:explain|summarise|summarize|read|review|walk me through|"
            r"go through|tell me about|what(?:'s| is) .{0,40}about)"
            r".{0,40}\b(?:this|the|my|current|active)\b.{0,20}"
            r"\b(?:page|article|tab|post|essay|piece)\b|"
            r"\b(?:page|article|tab)\b.{0,20}"
            r"\b(?:i (?:just )?sent|i shared|from the extension)\b|"
            r"\bactive (?:tab|page)\b|"
            r"\bexplain (?:this|that)\b"
            r")",
            text,
            re.I,
        )
    )


_HOME_LOCATION_RE = re.compile(
    r"(?:Lives in|I(?:'?m| am)? (?:based|living|staying) in|"
    r"I (?:live|stay) in|I(?:'?m| am) from|"
    r"my (?:home )?city is|my location is|location is|city is|"
    r"(?:based|living|staying) in)\s+([A-Za-z][A-Za-z .'-]{1,60})",
    re.I,
)


_BAD_LOCATION_TOKENS = frozenset(
    {
        "a",
        "an",
        "the",
        "my",
        "your",
        "our",
        "this",
        "that",
        "here",
        "there",
        "home",
        "city",
        "location",
        "known",
        "above",
        "below",
    }
)


def _clean_location_candidate(raw: str) -> str | None:
    candidate = re.split(r"[?!,;]|\b(?:today|now|currently)\b", raw, 1)[0].strip(" .")
    if not candidate:
        return None
    words = [part for part in re.split(r"\s+", candidate.lower()) if part]
    if not words or any(word in _BAD_LOCATION_TOKENS for word in words):
        return None
    return candidate


_SOFT_LOCATION_RE = re.compile(
    r"(?:weather (?:in|for)|checking weather in|timezone[^.?\n]{0,40}\bfor)\s+"
    r"([A-Za-z][A-Za-z .'-]{1,40})",
    re.I,
)
_SOFT_POSSESSIVE_WEATHER_RE = re.compile(
    r"\b([A-Z][a-z]{2,40})'s weather\b"
)


def _location_from_memory_context(memory_context: dict | None) -> str | None:
    """Pull a remembered home city from session memory context."""
    if not memory_context:
        return None
    blobs: list[str] = []
    for item in memory_context.get("memories") or []:
        if isinstance(item, dict):
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                blobs.append(content)
        elif isinstance(item, str) and item.strip():
            blobs.append(item)
    for key in (
        "preferences",
        "profile_summary",
        "instructions_block",
        "last_session_summary",
    ):
        value = memory_context.get(key)
        if isinstance(value, str) and value.strip():
            blobs.append(value)

    # Prefer explicit home-location facts.
    for blob in blobs:
        match = _HOME_LOCATION_RE.search(blob)
        if match:
            cleaned = _clean_location_candidate(match.group(1))
            if cleaned:
                return cleaned

    # Fall back to location cues from prior weather/reminder context.
    for blob in blobs:
        match = _SOFT_LOCATION_RE.search(blob) or _SOFT_POSSESSIVE_WEATHER_RE.search(
            blob
        )
        if match:
            cleaned = _clean_location_candidate(match.group(1))
            if cleaned and cleaned.lower() not in {"the", "your", "this", "that"}:
                return cleaned
    return None


def _extract_location(text: str, *, allow_plain: bool = False) -> str | None:
    patterns = (
        r"\b(?:i(?:'?m| am)? (?:based|living|staying) in|i (?:live|stay) in|"
        r"i(?:'?m| am) from|my (?:location|city|home city) is|location is|city is)\s+"
        r"([A-Za-z][A-Za-z .'-]{1,60})",
        r"\b(?:weather|forecast|temperature)(?:\s+\w+){0,3}\s+"
        r"(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,60})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = _clean_location_candidate(match.group(1))
            if candidate:
                return candidate

    plain = text.strip(" .?!,")
    excluded = {"why", "thanks", "thank you", "yes", "no", "okay", "ok"}
    if (
        allow_plain
        and plain.lower() not in excluded
        and 1 <= len(plain.split()) <= 4
        and re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,60}", plain)
    ):
        return plain
    return None


class Auren(Agent):
    def __init__(
        self,
        instructions: str,
        tools: list | None = None,
        gateway: ToolGateway | None = None,
        user_id: str | None = None,
        screen_reader: ScreenReader | None = None,
        default_location: str | None = None,
    ) -> None:
        super().__init__(
            instructions=instructions,
            tools=tools or [],
        )
        self._gateway = gateway
        self._user_id = user_id
        self._screen_reader = screen_reader
        self._default_location = (default_location or "").strip() or None
        self._awaiting_weather_location = False

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        """Deterministically resolve obvious live-data intents before generation."""
        text = _message_text(new_message)
        if not text:
            return

        if self._gateway is not None and self._user_id is not None and _is_confirm_request(text):
            try:
                result = await self._gateway.invoke(
                    "confirm_pending_action", self._user_id, {}
                )
            except Exception as error:  # noqa: BLE001
                result = f"confirm_pending_action failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative confirmation result: "
                    f"{result} Report the verified outcome. Do not claim success "
                    "unless the tool confirms it."
                ),
            )
            await self.update_chat_ctx(turn_ctx)
            return

        if self._gateway is not None and self._user_id is not None and _is_reject_request(text):
            try:
                result = await self._gateway.invoke(
                    "reject_pending_action", self._user_id, {}
                )
            except Exception as error:  # noqa: BLE001
                result = f"reject_pending_action failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=f"Authoritative rejection result: {result}",
            )
            await self.update_chat_ctx(turn_ctx)
            return

        if self._screen_reader is not None and _is_screen_read_request(text):
            try:
                result = await self._screen_reader.read_screen()
            except Exception as error:  # noqa: BLE001 - speakable failure
                result = f"screen read failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative live screen read for this turn: "
                    f"{result} Answer from this capture like a person looking at "
                    "their screen with them. Explain naturally. Do not claim you "
                    "cannot see the screen if content was captured. If no share is "
                    "available, ask them to tap Share screen in the talk UI."
                ),
            )
            await self.update_chat_ctx(turn_ctx)
            return

        if self._gateway is None or self._user_id is None:
            return

        weather_requested = _is_weather_request(text)
        was_awaiting_location = self._awaiting_weather_location
        stated_home = bool(_HOME_LOCATION_RE.search(text))
        location_from_utterance = _extract_location(
            text,
            allow_plain=was_awaiting_location and not weather_requested,
        )
        if location_from_utterance is None and stated_home:
            home_match = _HOME_LOCATION_RE.search(text)
            if home_match:
                location_from_utterance = _clean_location_candidate(home_match.group(1))
        location = location_from_utterance or (
            self._default_location if weather_requested else None
        )
        if weather_requested and location is None:
            self._awaiting_weather_location = True
            return

        if location and (weather_requested or was_awaiting_location):
            used_remembered_location = (
                weather_requested and location_from_utterance is None
            )
            self._awaiting_weather_location = False
            results: list[str] = []
            should_remember = stated_home or (
                was_awaiting_location and bool(location_from_utterance)
            )
            if should_remember:
                self._default_location = location_from_utterance or location
                try:
                    results.append(
                        await self._gateway.invoke(
                            "remember",
                            self._user_id,
                            {"content": f"Lives in {self._default_location}"},
                        )
                    )
                except Exception as error:  # noqa: BLE001 - weather should still run
                    results.append(f"remember failed: {error}")
            try:
                results.append(
                    await self._gateway.invoke(
                        "get_weather",
                        self._user_id,
                        {"location": location, "units": "metric"},
                    )
                )
            except Exception as error:  # noqa: BLE001 - give the model the real failure
                results.append(f"get_weather failed: {error}")
            memory_note = (
                f" Used remembered home location '{location}'."
                if used_remembered_location
                else ""
            )
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative live tool results for this turn: "
                    + " ".join(results)
                    + memory_note
                    + " Answer directly from these results. Do not call the same "
                    "tool again and do not claim it is unavailable after success."
                ),
            )
            await self.update_chat_ctx(turn_ctx)
            return

        # Persist home location even when the user is not asking for weather.
        if stated_home and location_from_utterance:
            self._default_location = location_from_utterance
            try:
                remember_result = await self._gateway.invoke(
                    "remember",
                    self._user_id,
                    {"content": f"Lives in {location_from_utterance}"},
                )
            except Exception as error:  # noqa: BLE001 - soft-fail memory write
                remember_result = f"remember failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative memory update for this turn: "
                    f"{remember_result} Acknowledge briefly that you'll remember "
                    f"their home location as {location_from_utterance}."
                ),
            )
            await self.update_chat_ctx(turn_ctx)
            return

        if _is_page_explain_request(text):
            try:
                result = await self._gateway.invoke(
                    "get_page_context",
                    self._user_id,
                    {},
                )
            except Exception as error:  # noqa: BLE001 - expose only tool's safe error
                result = f"get_page_context failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative page context for this turn: "
                    f"{result} Explain the page naturally in your own words like a "
                    "thoughtful friend. Cover the core idea and important points. "
                    "Do not read it verbatim unless asked. Do not call "
                    "get_page_context again. If no page is present, tell the user "
                    "to send it with the Auren Page Reader extension."
                ),
            )
            await self.update_chat_ctx(turn_ctx)
            return

        if _is_web_search_request(text):
            try:
                result = await self._gateway.invoke(
                    "search_web",
                    self._user_id,
                    {"query": text, "max_results": 3},
                )
            except Exception as error:  # noqa: BLE001 - expose only tool's safe error
                result = f"search_web failed: {error}"
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative live tool result for this turn: "
                    f"{result} Answer from this result. Do not call search_web again "
                    "and do not claim it is unavailable after success."
                ),
            )
            await self.update_chat_ctx(turn_ctx)

    async def tts_node(self, text, model_settings):
        """Use Chatterbox directly and emit decoded PCM frames to LiveKit.

        Chatterbox exposes an OpenAI-compatible HTTP endpoint, but its encoded
        responses are not decoded correctly by some LiveKit OpenAI plugin versions.
        This node deliberately owns the decoding boundary.
        """
        timeout = httpx.Timeout(CHATTERBOX_TIMEOUT_SECONDS, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async for spoken_text in _iter_tts_segments(text):
                started_at = time.monotonic()
                audio_bytes: bytes | None = None
                last_error = "unknown error"
                for attempt in range(1, CHATTERBOX_RETRY_ATTEMPTS + 1):
                    try:
                        response = await client.post(
                            f"{CHATTERBOX_BASE_URL}/audio/speech",
                            json={
                                "model": CHATTERBOX_MODEL,
                                "input": spoken_text,
                                "voice": CHATTERBOX_VOICE,
                                # WAV avoids an unnecessary lossy encode/decode hop.
                                "response_format": "wav",
                            },
                        )
                        if response.is_error:
                            last_error = f"HTTP {response.status_code}"
                            logging.error(
                                "Chatterbox TTS failed attempt=%d status=%s body=%s",
                                attempt,
                                response.status_code,
                                response.text[:500],
                            )
                            if response.status_code < 500 and response.status_code != 429:
                                break
                        elif not response.headers.get("content-type", "").startswith(
                            "audio/"
                        ):
                            last_error = "non-audio response"
                            logging.error(
                                "Chatterbox TTS returned non-audio content type %s",
                                response.headers.get("content-type"),
                            )
                            break
                        elif len(response.content) < 128:
                            last_error = "undersized audio response"
                            logging.error(
                                "Chatterbox TTS returned an empty/undersized payload"
                            )
                            break
                        else:
                            audio_bytes = response.content
                            break
                    except Exception as error:
                        last_error = type(error).__name__
                        logging.exception(
                            "Chatterbox TTS request failed attempt=%d model=%s",
                            attempt,
                            CHATTERBOX_MODEL,
                        )

                    if attempt < CHATTERBOX_RETRY_ATTEMPTS:
                        await asyncio.sleep(0.25 * (2 ** (attempt - 1)))

                if audio_bytes is None:
                    logging.error(
                        "Chatterbox produced no audio after %d attempt(s); text_chars=%d",
                        CHATTERBOX_RETRY_ATTEMPTS,
                        len(spoken_text),
                    )
                    raise RuntimeError(
                        "Chatterbox TTS failed after "
                        f"{CHATTERBOX_RETRY_ATTEMPTS} attempt(s): {last_error}"
                    )

                try:
                    frames = _decode_audio_frames(audio_bytes)
                except Exception as error:
                    logging.exception(
                        "Failed to decode Chatterbox audio; text_chars=%d",
                        len(spoken_text),
                    )
                    raise RuntimeError("Chatterbox returned invalid audio") from error

                if not frames:
                    logging.error(
                        "Chatterbox payload decoded to zero PCM frames; text_chars=%d",
                        len(spoken_text),
                    )
                    raise RuntimeError("Chatterbox returned audio with no PCM frames")

                audio_seconds = sum(
                    frame.samples_per_channel / frame.sample_rate for frame in frames
                )
                logging.info(
                    "TTS segment ready chars=%d request_seconds=%.3f audio_seconds=%.3f frames=%d",
                    len(spoken_text),
                    time.monotonic() - started_at,
                    audio_seconds,
                    len(frames),
                )
                for frame in frames:
                    yield frame


server = AgentServer()
session_tracker = SessionTracker()


def _message_text(item: ChatMessage) -> str:
    text = getattr(item, "text_content", None)
    if callable(text):
        text = text()
    if isinstance(text, str) and text.strip():
        return text.strip()
    parts: list[str] = []
    for content in item.content or []:
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return " ".join(parts).strip()


@server.rtc_session(agent_name=LIVEKIT_AGENT_NAME)
async def auren_session(ctx: agents.JobContext):
    await ctx.connect()

    # Published for the RunPod idle watchdog so it never stops the Pod during a
    # quiet moment in a live conversation.
    await session_tracker.acquire()
    ctx.add_shutdown_callback(session_tracker.release)

    participant = await ctx.wait_for_participant()

    # The Railway API puts the caller's id in the participant token so tools and
    # memory stay scoped to one user.
    metadata = json.loads(participant.metadata or "{}")
    user_id = metadata.get("userId", "local-user")
    display_name = metadata.get("displayName")
    room_name = ctx.room.name if ctx.room else None

    gateway: ToolGateway | None = None
    tools: list = []
    context_block = ""
    default_location: str | None = None
    greeting = (
        f"Hello {display_name.split()[0]}. I’m Auren — what can I help you with?"
        if display_name
        else "Hello. I’m Auren — what can I help you with?"
    )
    transcript = TranscriptBuffer()

    if TOOL_GATEWAY_BASE_URL:
        async def publish_tool_activity(event: dict[str, str | int]) -> None:
            payload = json.dumps({"type": "tool_activity", **event}).encode("utf-8")
            await ctx.room.local_participant.publish_data(
                payload,
                reliable=True,
                topic="auren.tool",
            )

        gateway = ToolGateway(
            TOOL_GATEWAY_BASE_URL,
            TOOL_GATEWAY_TOKEN,
            TOOL_GATEWAY_TIMEOUT_SECONDS,
            on_event=publish_tool_activity,
        )
        tools = build_tools(gateway, user_id)
        memory_context = await fetch_context(gateway.client, user_id)
        if memory_context:
            context_block = memory_context.get("instructions_block") or ""
            greeting = memory_context.get("greeting") or greeting
            display_name = memory_context.get("display_name") or display_name
            default_location = _location_from_memory_context(memory_context)
            logging.info(
                "Loaded memory context for %s (previous conversation: %s, home_location: %s)",
                user_id,
                bool(memory_context.get("last_session_summary")),
                default_location or "none",
            )

        due_reminders = await fetch_due_reminders(gateway.client, user_id)
        if due_reminders:
            context_block = (
                f"{context_block}\n\n"
                f"Due reminders at session start: {due_reminders} "
                "Mention these proactively before moving to a new request."
            ).strip()
            greeting = f"{greeting} Before we begin, {due_reminders}"

        persist_lock = asyncio.Lock()
        memory_persisted = False
        gateway_closed = False

        async def persist_memory() -> bool:
            nonlocal memory_persisted
            async with persist_lock:
                if memory_persisted:
                    return True
                if gateway_closed or not transcript.turns:
                    logging.info(
                        "Skipping memory flush for %s (gateway_closed=%s, turns=%d)",
                        user_id,
                        gateway_closed,
                        len(transcript.turns),
                    )
                    return False

                logging.info(
                    "Persisting conversation for %s with %d turns",
                    user_id,
                    len(transcript.turns),
                )
                distilled = await distill_with_llm(
                    llm_base_url=LLM_BASE_URL,
                    llm_model=LLM_MODEL,
                    dialog=transcript.as_dialog(),
                )
                memory_persisted = await flush_session(
                    gateway.client,
                    user_id=user_id,
                    room_name=room_name,
                    buffer=transcript,
                    distilled=distilled,
                )
                return memory_persisted

        async def persist_memory_then_close() -> None:
            nonlocal gateway_closed
            try:
                await persist_memory()
            finally:
                gateway_closed = True
                await gateway.aclose()

        def persist_when_user_leaves(disconnected: rtc.RemoteParticipant) -> None:
            if disconnected.identity != participant.identity:
                return
            logging.info("User %s disconnected; flushing conversation", user_id)
            asyncio.create_task(persist_memory())

        ctx.room.on("participant_disconnected", persist_when_user_leaves)
        ctx.add_shutdown_callback(persist_memory_then_close)
    else:
        logging.warning("TOOL_GATEWAY_BASE_URL is not set; Auren is running without tools")

    instructions = INSTRUCTIONS + (TOOL_INSTRUCTIONS if tools else "")
    if context_block:
        instructions = f"{instructions}\n\n{context_block}"

    screen_reader = ScreenReader()
    screen_reader.attach(ctx.room)

    llm_extra_body: dict[str, object] = {}
    if LLM_DISABLE_THINKING:
        # Ollama versions disagree on the key; send both common forms.
        llm_extra_body = {
            "think": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }

    session = AgentSession(
        # Slightly snappier end-of-utterance than plugin defaults (0.55s silence).
        vad=silero.VAD.load(
            min_silence_duration=0.35,
            activation_threshold=0.55,
            prefix_padding_duration=0.3,
        ),
        turn_handling={
            # Keep endpointing local and deterministic on RunPod. The audio model
            # understands completed thoughts better than VAD-only silence timers.
            "turn_detection": inference.TurnDetector(version="v1-mini"),
            "endpointing": {"mode": "dynamic", "min_delay": 0.25, "max_delay": 1.2},
            "interruption": {
                "resume_false_interruption": True,
                "false_interruption_timeout": 1.0,
            },
            "preemptive_generation": {"enabled": True, "preemptive_tts": True},
        },
        stt=build_stt(STT_CONFIG),
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key="ollama",
            # Voice replies should stay short; long generations feel like hangups.
            max_completion_tokens=LLM_MAX_COMPLETION_TOKENS,
            temperature=0.6,
            timeout=httpx.Timeout(45.0, connect=5.0),
            **({"extra_body": llm_extra_body} if llm_extra_body else {}),
        ),
        # The custom tts_node above owns synthesis. Keeping this configured
        # preserves LiveKit's audio-output capability detection.
        tts=openai.TTS(
            model=CHATTERBOX_MODEL,
            voice=CHATTERBOX_VOICE,
            base_url=CHATTERBOX_BASE_URL,
            api_key="local",
            response_format="wav",
        ),
    )
    logging.info(
        "Voice latency profile stt_provider=%s stt_model=%s stt_realtime=%s "
        "stt_language=%s llm=%s think_disabled=%s "
        "tts_timeout=%.0fs tts_segment_chars=%s endpoint_max=1.2s",
        STT_CONFIG.provider,
        STT_CONFIG.model,
        STT_CONFIG.use_realtime,
        STT_CONFIG.language or "auto",
        LLM_MODEL,
        LLM_DISABLE_THINKING,
        CHATTERBOX_TIMEOUT_SECONDS,
        CHATTERBOX_MAX_SEGMENT_CHARS,
    )

    agent = Auren(
        instructions=instructions,
        tools=tools,
        gateway=gateway,
        user_id=user_id,
        screen_reader=screen_reader,
        default_location=default_location,
    )

    async def publish_transcript(
        role: str, text: str, *, final: bool = True
    ) -> None:
        """Reliable UI path — lk.transcription often mis-labels the speaker."""
        try:
            payload = json.dumps(
                {
                    "type": "transcript",
                    "role": role,
                    "text": text,
                    "final": final,
                }
            ).encode("utf-8")
            await ctx.room.local_participant.publish_data(
                payload,
                reliable=True,
                topic="auren.transcript",
            )
        except Exception:
            logging.exception("Failed to publish %s transcript to UI", role)

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(event: UserInputTranscribedEvent) -> None:
        text = (event.transcript or "").strip()
        if not text:
            return
        logging.info(
            "STT user_input_transcribed final=%s text=%r",
            event.is_final,
            text[:160],
        )
        asyncio.create_task(
            publish_transcript("user", text, final=event.is_final)
        )

    @session.on("conversation_item_added")
    def _on_conversation_item(event: ConversationItemAddedEvent) -> None:
        item = event.item
        if not isinstance(item, ChatMessage):
            return
        role = item.role if item.role in {"user", "assistant"} else None
        if role is None:
            return
        text = _message_text(item)
        if text:
            if role == "assistant":
                text = _clean_assistant_text(text)
            transcript.add(role, text)
            # User finals are already pushed from user_input_transcribed.
            if role == "assistant":
                asyncio.create_task(publish_transcript(role, text, final=True))

    await session.start(
        room=ctx.room,
        agent=agent,
        # Deliver assistant/user text immediately. Syncing to TTS audio makes the
        # UI look stuck on "thinking" whenever Chatterbox is slow or down.
        room_options=room_io.RoomOptions(
            audio_input=True,
            audio_output=True,
            text_input=True,
            text_output=room_io.TextOutputOptions(sync_transcription=False),
        ),
    )

    async def _speak_greeting() -> None:
        try:
            await session.say(greeting, allow_interruptions=True)
        except Exception:
            logging.exception(
                "Greeting TTS failed (model=%s). Session continues so the user can still talk.",
                CHATTERBOX_MODEL,
            )

    # Never block the session on greeting audio.
    asyncio.create_task(_speak_greeting())


if __name__ == "__main__":
    agents.cli.run_app(server)
