import io
import json
import os

import av
import httpx
from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import Agent, AgentServer, AgentSession
from livekit.plugins import openai, silero

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
CHATTERBOX_BASE_URL = require_env("CHATTERBOX_BASE_URL").rstrip("/")


class Auren(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are Auren, a warm, highly capable personal voice assistant. "
                "Respond directly and naturally. Keep routine voice responses concise, "
                "usually one to three sentences. Ask a short clarifying question when "
                "the user's intent is ambiguous. Never expose hidden reasoning."
            )
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

        spoken_text = "".join(parts).strip()
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


@server.rtc_session(agent_name=LIVEKIT_AGENT_NAME)
async def auren_session(ctx: agents.JobContext):
    await ctx.connect()
    participant = await ctx.wait_for_participant()

    # Metadata is optional locally, but parsing it keeps the worker ready for
    # future user-scoped tools and memory.
    metadata = json.loads(participant.metadata or "{}")
    user_id = metadata.get("userId", "local-user")
    _ = user_id

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
            model=os.getenv("LLM_MODEL", "qwen3:8b"),
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

    await session.start(room=ctx.room, agent=Auren())
    await session.say(
        "I’m ready. What can I help you with?",
        allow_interruptions=True,
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
