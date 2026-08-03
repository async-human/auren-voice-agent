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
from livekit.agents.llm import ChatMessage
from livekit.plugins import openai, silero

from memory import TranscriptBuffer, distill_with_llm, fetch_context, flush_session
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

TOOL_GATEWAY_BASE_URL = os.getenv("TOOL_GATEWAY_BASE_URL", "").rstrip("/")
TOOL_GATEWAY_TOKEN = os.getenv("TOOL_GATEWAY_TOKEN")
TOOL_GATEWAY_TIMEOUT_SECONDS = float(os.getenv("TOOL_GATEWAY_TIMEOUT_SECONDS", "20"))

INSTRUCTIONS = (
    "You are Auren, a warm, highly capable personal voice assistant. "
    "Respond directly and naturally. Keep routine voice responses concise, "
    "usually one to three sentences. Ask a short clarifying question when "
    "the user's intent is ambiguous. Adapt your tone and phrasing to the "
    "conversation instead of using canned closers. Do not use emoji, emoticons, "
    "or decorative symbols. Do not end every response with a question. "
    "Never expose hidden reasoning."
)

TOOL_INSTRUCTIONS = (
    " You can check the time, look up weather, search the live web, and save or "
    "recall the user's reminders, notes, and personal memories. Use a tool whenever "
    "the answer depends on the current time, live data, or something the user asked "
    "you to remember; do not guess. Check the current time before scheduling anything "
    "relative to today. When the user shares a durable personal fact, call remember. "
    "When they ask you to forget something, call forget. When they ask what you "
    "discussed last time, answer from Previous conversation in personal context if "
    "present; otherwise call recall with query 'last conversation'. Speak tool "
    "results conversationally instead of reading them out verbatim, and say so "
    "plainly when a tool could not help."
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


class Auren(Agent):
    def __init__(self, instructions: str, tools: list | None = None) -> None:
        super().__init__(
            instructions=instructions,
            tools=tools or [],
        )

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
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{base_url}/audio/speech",
                json={
                    "model": os.getenv("CHATTERBOX_MODEL", "chatterbox-turbo"),
                    "input": spoken_text,
                    "voice": os.getenv("CHATTERBOX_VOICE", "Olivia.wav"),
                    "response_format": "mp3",
                },
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
        gateway = ToolGateway(
            TOOL_GATEWAY_BASE_URL,
            TOOL_GATEWAY_TOKEN,
            TOOL_GATEWAY_TIMEOUT_SECONDS,
        )
        tools = build_tools(gateway, user_id)
        memory_context = await fetch_context(gateway.client, user_id)
        if memory_context:
            context_block = memory_context.get("instructions_block") or ""
            greeting = memory_context.get("greeting") or greeting
            display_name = memory_context.get("display_name") or display_name

        async def persist_memory_then_close() -> None:
            # Keep the gateway open until flush finishes. Separate aclose
            # callbacks can race and drop the session summary.
            try:
                distilled = await distill_with_llm(
                    llm_base_url=LLM_BASE_URL,
                    llm_model=LLM_MODEL,
                    dialog=transcript.as_dialog(),
                )
                await flush_session(
                    gateway.client,
                    user_id=user_id,
                    room_name=room_name,
                    buffer=transcript,
                    distilled=distilled,
                )
            finally:
                await gateway.aclose()

        ctx.add_shutdown_callback(persist_memory_then_close)
    else:
        logging.warning("TOOL_GATEWAY_BASE_URL is not set; Auren is running without tools")

    instructions = INSTRUCTIONS + (TOOL_INSTRUCTIONS if tools else "")
    if context_block:
        instructions = f"{instructions}\n\n{context_block}"

    session = AgentSession(
        vad=silero.VAD.load(),
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
            model=os.getenv("CHATTERBOX_MODEL", "chatterbox-turbo"),
            voice=os.getenv("CHATTERBOX_VOICE", "Olivia.wav"),
            base_url=CHATTERBOX_BASE_URL,
            api_key="local",
            response_format="mp3",
        ),
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

    await session.start(room=ctx.room, agent=Auren(instructions=instructions, tools=tools))
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    agents.cli.run_app(server)
