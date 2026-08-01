"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

type Phase = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "paused";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type InterimMessage = Pick<Message, "role" | "text">;

function describeFailure(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);

  if (/NEXT_PUBLIC_API_URL/.test(message)) {
    return "The API URL is not configured. Set NEXT_PUBLIC_API_URL and restart the frontend.";
  }
  if (/Failed to fetch|NetworkError|Load failed/i.test(message)) {
    return "Could not reach the Auren API. Start it with 'uv run uvicorn app.main:app --reload --port 8080' in services/api.";
  }
  if (/Permission|NotAllowed/i.test(message)) {
    return "Your browser blocked microphone access. Allow it for this site and try again.";
  }
  if (/signal|websocket|1006|timeout/i.test(message)) {
    return `Could not connect to LiveKit. ${message}`;
  }
  return message || "Something went wrong starting the session.";
}

const labels: Record<Phase, string> = {
  idle: "Start talking",
  connecting: "Joining…",
  listening: "Listening…",
  thinking: "One moment",
  speaking: "Auren is speaking",
  paused: "Paused — tap to resume",
};

export default function VoiceAgent() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [notice, setNotice] = useState("Your microphone stays in the LiveKit session");
  const [messages, setMessages] = useState<Message[]>([]);
  const [interim, setInterim] = useState<InterimMessage | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [isTypeOpen, setIsTypeOpen] = useState(false);
  const [isVoiceMuted, setIsVoiceMuted] = useState(false);
  const [isMicMuted, setIsMicMuted] = useState(false);
  const roomRef = useRef<Room | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const audioElementsRef = useRef<HTMLMediaElement[]>([]);
  const voiceMutedRef = useRef(false);
  const nextId = useRef(1);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;

    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((publication) => {
        publication.track?.detach().forEach((element) => element.remove());
      });
    });
    audioElementsRef.current = [];
    await room.disconnect();
    roomRef.current = null;
    setInterim(null);
    setIsMicMuted(false);
    setPhase("idle");
    setNotice("Session ended");
  }, []);

  const startSession = useCallback(async () => {
    if (roomRef.current) return;
    setPhase("connecting");
    setNotice("Securing a realtime session");

    try {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) {
        throw new Error("NEXT_PUBLIC_API_URL is not configured");
      }

      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/voice/token`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const connection = (await response.json()) as {
        serverUrl?: string;
        participantToken?: string;
        error?: string;
      };

      if (!response.ok || !connection.serverUrl || !connection.participantToken) {
        throw new Error(connection.error || "Voice service unavailable");
      }

      const room = new Room({ adaptiveStream: true, dynacast: true });
      roomRef.current = room;

      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio) return;
        const audioElement = track.attach();
        audioElement.autoplay = true;
        audioElement.muted = voiceMutedRef.current;
        audioElement.style.display = "none";
        document.body.appendChild(audioElement);
        audioElementsRef.current.push(audioElement);
      });

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => {
          audioElementsRef.current = audioElementsRef.current.filter(
            (audioElement) => audioElement !== element,
          );
          element.remove();
        });
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const role =
          participant?.identity === room.localParticipant.identity
            ? "user"
            : "assistant";
        const partial = segments
          .filter((segment) => !segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        setInterim(partial ? { role, text: partial } : null);

        const finalText = segments
          .filter((segment) => segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        if (!finalText) return;

        setMessages((current) => [
          ...current,
          { id: nextId.current++, role, text: finalText },
        ]);
        setInterim(null);
        setPhase(role === "user" ? "thinking" : "listening");
      });

      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const agentIsSpeaking = speakers.some(
          (speaker) => speaker.identity !== room.localParticipant.identity,
        );
        if (agentIsSpeaking) setPhase("speaking");
      });

      room.on(RoomEvent.Disconnected, () => {
        roomRef.current = null;
        setPhase("idle");
        setNotice("Disconnected");
      });

      await room.connect(connection.serverUrl, connection.participantToken);
      await room.startAudio();
      await room.localParticipant.setMicrophoneEnabled(true);
      setPhase("listening");
      setNotice("Just stop talking when you’re done");
    } catch (error) {
      console.error("Auren session failed to start", error);
      roomRef.current = null;
      setPhase("idle");
      setNotice("");
      setFailure(describeFailure(error));
    }
  }, []);

  const toggleMicrophone = useCallback(async () => {
    const room = roomRef.current;
    if (!room) {
      setFailure(null);
      await startSession();
      return;
    }

    try {
      const nextMuted = !isMicMuted;
      await room.localParticipant.setMicrophoneEnabled(!nextMuted);
      setIsMicMuted(nextMuted);
      setPhase(nextMuted ? "paused" : "listening");
      setNotice(nextMuted ? "Your microphone is off" : "Just stop talking when you’re done");
    } catch (error) {
      console.error("Microphone toggle failed", error);
      setFailure(describeFailure(error));
    }
  }, [isMicMuted, startSession]);

  const toggleVoicePlayback = useCallback(() => {
    const nextMuted = !isVoiceMuted;
    voiceMutedRef.current = nextMuted;
    audioElementsRef.current.forEach((element) => {
      element.muted = nextMuted;
    });
    setIsVoiceMuted(nextMuted);
  }, [isVoiceMuted]);

  const sendTextMessage = useCallback(async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const room = roomRef.current;
    const text = draft.trim();
    if (!room || !text || isSending) return;

    setIsSending(true);
    try {
      await room.localParticipant.sendText(text, { topic: "lk.chat" });
      setMessages((current) => [
        ...current,
        { id: nextId.current++, role: "user", text },
      ]);
      setDraft("");
      setPhase("thinking");
      setNotice("Message sent — Auren is thinking");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Message could not be sent");
    } finally {
      setIsSending(false);
    }
  }, [draft, isSending]);

  useEffect(() => () => void disconnect(), [disconnect]);
  useEffect(() => {
    const messagesElement = messagesRef.current;
    if (!messagesElement) return;
    messagesElement.scrollTo({ top: messagesElement.scrollHeight, behavior: "smooth" });
  }, [interim, messages]);

  const latestMessage = messages.at(-1);
  const isWaitingForAuren =
    phase === "thinking" || (phase === "speaking" && latestMessage?.role === "user");
  const hasConversation =
    messages.length > 0 || Boolean(interim) || phase === "thinking" || phase === "speaking";

  return (
    <>
      <div className={`breath breath-${phase}`} aria-hidden="true" />
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <main className={`app phase-${phase}`}>
        <header className="top">
          <div className="who">
            <span className="mark" />
            <span className="whoName">
              Auren
              <small>Private voice intelligence</small>
            </span>
          </div>
          {phase !== "idle" && (
            <button className="leave" onClick={() => void disconnect()}>
              End call
            </button>
          )}
        </header>

        <section className="stage" aria-live="polite">
          <div className={`opening ${hasConversation ? "gone" : ""}`}>
            <div className="eyebrow">Voice companion</div>
            <h1>Hello. I’m <em>Auren.</em></h1>
            <p>
              Speak naturally or type a message. I’m here to help you work through
              questions, ideas, and everything in between.
            </p>
            <div className="hints">
              {[
                "Help me think through an idea",
                "Summarize something for me",
                "Let’s plan my day",
              ].map((hint) => (
                <button
                  key={hint}
                  onClick={() => {
                    setDraft(hint);
                    setIsTypeOpen(true);
                  }}
                >
                  “{hint}”
                </button>
              ))}
            </div>
          </div>

          {hasConversation && (
            <div className="conversation" ref={messagesRef}>
              {messages.map((message) => (
                <article className={`chatMessage ${message.role}`} key={message.id}>
                  <span className="chatSpeaker">
                    {message.role === "user" ? "You" : "Auren"}
                  </span>
                  <p className="chatBubble" dir="ltr">{message.text}</p>
                </article>
              ))}
              {interim && (
                <article className={`chatMessage ${interim.role} interimMessage`}>
                  <span className="chatSpeaker">
                    {interim.role === "user" ? "You" : "Auren"}
                  </span>
                  <p className="chatBubble" dir="ltr">{interim.text}</p>
                </article>
              )}
              {isWaitingForAuren && !interim && (
                <article className="chatMessage assistant interimMessage">
                  <span className="chatSpeaker">Auren</span>
                  <div className="chatBubble thinkingBubble" aria-label="Auren is thinking">
                    <span className="ellipsis">
                      <i /><i /><i />
                    </span>
                  </div>
                </article>
              )}
            </div>
          )}
        </section>

        <section className="dock">
          <button
            className="mic"
            onClick={() => void toggleMicrophone()}
            disabled={phase === "connecting"}
          >
            <span className="pip" />
            <span>{labels[phase]}</span>
          </button>

          {isTypeOpen && (
            <form className="typebar" onSubmit={sendTextMessage}>
              <label className="srOnly" htmlFor="message-input">Message Auren</label>
              <input
                id="message-input"
                type="text"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={roomRef.current ? "Type instead…" : "Start the call, then type…"}
                disabled={!roomRef.current || phase === "connecting"}
                autoComplete="off"
                autoFocus
              />
              <button
                type="submit"
                disabled={!roomRef.current || !draft.trim() || isSending}
              >
                Send
              </button>
            </form>
          )}

          {phase !== "idle" && phase !== "connecting" && (
            <div className="controlRow">
              <button
                onClick={toggleVoicePlayback}
                aria-pressed={isVoiceMuted}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <path d="M11 5 6 9H3v6h3l5 4V5z" />
                  {!isVoiceMuted && <path d="M15.5 9.5a4 4 0 0 1 0 5M18.5 7a8 8 0 0 1 0 10" />}
                </svg>
                <span>{isVoiceMuted ? "Voice off" : "Auren’s voice"}</span>
              </button>
              <button
                onClick={() => setIsTypeOpen((open) => !open)}
                aria-pressed={isTypeOpen}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true">
                  <rect x="2" y="6" width="20" height="12" rx="2" />
                  <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M8 14h8" />
                </svg>
                <span>Type</span>
              </button>
              {messages.length > 0 && (
                <button onClick={() => setMessages([])}>
                  <span>Clear</span>
                </button>
              )}
            </div>
          )}

          {failure ? (
            <p className="failure" role="alert">
              <span className="failureIcon" aria-hidden="true">!</span>
              {failure}
            </p>
          ) : (
            <p className="tip">{notice}</p>
          )}
        </section>
      </main>
    </>
  );
}
