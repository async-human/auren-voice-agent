import asyncio
import io
import json
import logging
import os
import re

import av
import httpx
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession, ConversationItemAddedEvent
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import openai, silero

from memory import TranscriptBuffer, distill_with_llm, fetch_context, flush_session
from screen_reader import ScreenReader
from session_state import SessionTracker
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

FASTER_WHISPER_BASE_URL = require_env("FASTER_WHISPER_BASE_URL").rstrip("/")
LLM_BASE_URL = require_env("LLM_BASE_URL").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
CHATTERBOX_BASE_URL = require_env("CHATTERBOX_BASE_URL").rstrip("/")
CHATTERBOX_MODEL = os.getenv("CHATTERBOX_MODEL", "chatterbox-turbo")
CHATTERBOX_VOICE = os.getenv("CHATTERBOX_VOICE", "Olivia.wav")

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
    "Never expose hidden reasoning."
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
    "pending=true, read the preview and ask the user to confirm. On yes/confirm/"
    "go ahead, call confirm_pending_action. On no/cancel, call reject_pending_action. "
    "Never claim an email was sent or an event was created unless the tool result "
    "says verified or succeeded after confirmation. "
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


def _extract_location(text: str, *, allow_plain: bool = False) -> str | None:
    patterns = (
        r"\b(?:i (?:live|stay) in|my (?:location|city) is|location is|city is)\s+"
        r"([A-Za-z][A-Za-z .'-]{1,60})",
        r"\b(?:weather|forecast|temperature)(?:\s+\w+){0,3}\s+"
        r"(?:in|for|at)\s+([A-Za-z][A-Za-z .'-]{1,60})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = re.split(r"[?!,;]|\b(?:today|now|currently)\b", match.group(1), 1)[
                0
            ].strip(" .")
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
    ) -> None:
        super().__init__(
            instructions=instructions,
            tools=tools or [],
        )
        self._gateway = gateway
        self._user_id = user_id
        self._screen_reader = screen_reader
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
        location = _extract_location(
            text,
            allow_plain=self._awaiting_weather_location and not weather_requested,
        )
        if weather_requested and location is None:
            self._awaiting_weather_location = True
            return

        if location and (weather_requested or self._awaiting_weather_location):
            self._awaiting_weather_location = False
            results: list[str] = []
            if "remember" in text.lower():
                try:
                    results.append(
                        await self._gateway.invoke(
                            "remember",
                            self._user_id,
                            {"content": f"Lives in {location}"},
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
            turn_ctx.add_message(
                role="system",
                content=(
                    "Authoritative live tool results for this turn: "
                    + " ".join(results)
                    + " Answer directly from these results. Do not call the same "
                    "tool again and do not claim it is unavailable after success."
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
        parts: list[str] = []
        async for chunk in text:
            parts.append(chunk)

        spoken_text = _clean_assistant_text("".join(parts))
        if not spoken_text:
            return

        base_url = CHATTERBOX_BASE_URL
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/audio/speech",
                json={
                    "model": CHATTERBOX_MODEL,
                    "input": spoken_text,
                    "voice": CHATTERBOX_VOICE,
                    "response_format": "mp3",
                },
            )
            if response.is_error:
                logging.error(
                    "Chatterbox TTS failed (%s): %s",
                    response.status_code,
                    response.text[:500],
                )
                response.raise_for_status()

        container = av.open(io.BytesIO(response.content))
        resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)

        for decoded_frame in container.decode(audio=0):
            for frame in resampler.resample(decoded_frame):
                pcm = frame.to_ndarray().tobytes()
                yield rtc.AudioFrame(
                    data=pcm,
                    sample_rate=24000,
                    num_channels=1,
                    samples_per_channel=len(pcm) // 2,
                )

        for frame in resampler.resample(None):
            pcm = frame.to_ndarray().tobytes()
            yield rtc.AudioFrame(
                data=pcm,
                sample_rate=24000,
                num_channels=1,
                samples_per_channel=len(pcm) // 2,
            )


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
    greeting = (
        f"Hello {display_name.split()[0]}. I’m Auren — what can I help you with?"
        if display_name
        else "Hello. I’m Auren — what can I help you with?"
    )
    transcript = TranscriptBuffer()

    if TOOL_GATEWAY_BASE_URL:
        async def publish_tool_activity(event: dict[str, str]) -> None:
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
            logging.info(
                "Loaded memory context for %s (previous conversation: %s)",
                user_id,
                bool(memory_context.get("last_session_summary")),
            )

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

    session = AgentSession(
        vad=silero.VAD.load(),
        # Match the known-good Speaches setup: REST Whisper with plugin defaults.
        # Do not enable realtime/websocket STT — Speaches only exposes /audio/transcriptions.
        stt=openai.STT(
            model=os.getenv(
                "FASTER_WHISPER_MODEL",
                "Systran/faster-whisper-large-v3",
            ),
            base_url=FASTER_WHISPER_BASE_URL,
            api_key="local",
        ),
        llm=openai.LLM(
            model=LLM_MODEL,
            base_url=LLM_BASE_URL,
            api_key="ollama",
        ),
        # The custom tts_node above owns synthesis. Keeping this configured
        # preserves LiveKit's audio-output capability detection.
        tts=openai.TTS(
            model=CHATTERBOX_MODEL,
            voice=CHATTERBOX_VOICE,
            base_url=CHATTERBOX_BASE_URL,
            api_key="local",
            response_format="mp3",
        ),
    )

    agent = Auren(
        instructions=instructions,
        tools=tools,
        gateway=gateway,
        user_id=user_id,
        screen_reader=screen_reader,
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

    await session.start(
        room=ctx.room,
        agent=agent,
    )
    # Never let greeting TTS take down the whole session — otherwise the room
    # stays connected while STT/LLM die with the crashed agent job.
    try:
        await session.say(greeting, allow_interruptions=True)
    except Exception:
        logging.exception(
            "Greeting TTS failed (model=%s). Session continues so the user can still talk.",
            CHATTERBOX_MODEL,
        )


if __name__ == "__main__":
    agents.cli.run_app(server)
