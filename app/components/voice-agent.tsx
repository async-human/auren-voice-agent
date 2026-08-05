"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import Link from "next/link";
import { UserButton, useAuth } from "@clerk/nextjs";
import MarkdownMessage from "./markdown-message";
import MemoryPanel from "./memory-panel";

type Phase = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "paused";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type InterimMessage = Pick<Message, "role" | "text">;
type ToolActivityStatus = "started" | "completed" | "failed";
type ToolActivity = {
  tool: string;
  invocationId: string;
  status: ToolActivityStatus;
};

const toolLabels: Record<string, string> = {
  get_current_time: "Current time",
  get_weather: "Weather",
  create_reminder: "Reminder",
  list_reminders: "Reminders",
  save_note: "Notes",
  search_notes: "Note search",
  search_web: "Web search",
  get_page_context: "Page reader",
  check_tool_status: "Tool status",
  recall: "Memory recall",
  remember: "Memory",
  forget: "Memory",
};

type PageContextMeta = {
  present: boolean;
  title?: string | null;
  url?: string | null;
  char_count?: number;
};

function toolLabel(tool: string): string {
  return toolLabels[tool] ?? tool.replaceAll("_", " ");
}

const emojiPattern = /[\p{Extended_Pictographic}\u200d\ufe0f]/gu;

function cleanAssistantText(text: string): string {
  return text
    .replace(emojiPattern, "")
    .replace(/[ \t]+([,.;!?])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

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
  if (/Sign in to continue|401/.test(message)) {
    return "Your session expired. Sign in again to keep talking to Auren.";
  }
  if (/Authentication is not configured/i.test(message)) {
    return "The API has no identity provider configured. Set CLERK_ISSUER on the API, or DEV_USER_ID for offline work.";
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
  const { getToken } = useAuth();
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
  const [isMemoryOpen, setIsMemoryOpen] = useState(false);
  const [toolActivity, setToolActivity] = useState<ToolActivity | null>(null);
  const [pageContext, setPageContext] = useState<PageContextMeta | null>(null);
  const roomRef = useRef<Room | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const audioElementsRef = useRef<HTMLMediaElement[]>([]);
  const voiceMutedRef = useRef(false);
  const nextId = useRef(1);
  const toolActivityTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;

    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((publication) => {
        publication.track?.detach().forEach((element) => element.remove());
      });
    });
    audioElementsRef.current = [];
    if (toolActivityTimerRef.current) {
      clearTimeout(toolActivityTimerRef.current);
      toolActivityTimerRef.current = null;
    }
    await room.disconnect();
    roomRef.current = null;
    setInterim(null);
    setToolActivity(null);
    setPageContext(null);
    setIsMicMuted(false);
    setPhase("idle");
    setNotice("Session ended");
  }, []);

  const refreshPageContext = useCallback(async () => {
    const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiBaseUrl) return;
    try {
      const token = await getToken();
      if (!token) return;
      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/page-context`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) return;
      const body = (await response.json()) as PageContextMeta;
      setPageContext(body.present ? body : null);
    } catch {
      // Page-context indicator is optional UI; ignore transient failures.
    }
  }, [getToken]);

  useEffect(() => {
    if (phase === "idle" || phase === "connecting") return;
    void refreshPageContext();
    const timer = setInterval(() => {
      void refreshPageContext();
    }, 8000);
    return () => clearInterval(timer);
  }, [phase, refreshPageContext]);

  const startSession = useCallback(async (enableMicrophone = true) => {
    if (roomRef.current) return roomRef.current;
    setPhase("connecting");
    setNotice("Securing a realtime session");

    try {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) {
        throw new Error("NEXT_PUBLIC_API_URL is not configured");
      }

      // The API derives the user from this token; it is never sent in the body.
      const sessionToken = await getToken();
      if (!sessionToken) {
        throw new Error("Sign in to continue");
      }

      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/voice/token`, {
        method: "POST",
        headers: { Authorization: `Bearer ${sessionToken}` },
      });
      const connection = (await response.json()) as {
        serverUrl?: string;
        participantToken?: string;
        detail?: string;
        error?: string;
      };

      if (!response.ok || !connection.serverUrl || !connection.participantToken) {
        throw new Error(
          connection.detail || connection.error || "Voice service unavailable",
        );
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

      room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        if (topic !== "auren.tool") return;
        try {
          const event = JSON.parse(new TextDecoder().decode(payload)) as Partial<ToolActivity> & {
            type?: string;
          };
          if (
            event.type !== "tool_activity" ||
            typeof event.tool !== "string" ||
            typeof event.invocationId !== "string" ||
            !["started", "completed", "failed"].includes(event.status ?? "")
          ) {
            return;
          }

          if (toolActivityTimerRef.current) {
            clearTimeout(toolActivityTimerRef.current);
            toolActivityTimerRef.current = null;
          }
          const activity = {
            tool: event.tool,
            invocationId: event.invocationId,
            status: event.status as ToolActivityStatus,
          };
          setToolActivity(activity);

          if (activity.status !== "started") {
            toolActivityTimerRef.current = setTimeout(() => {
              setToolActivity((current) =>
                current?.invocationId === activity.invocationId ? null : current,
              );
              toolActivityTimerRef.current = null;
            }, 1800);
          }
        } catch {
          // Ignore unrelated or malformed data-channel messages.
        }
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const role =
          participant?.identity === room.localParticipant.identity
            ? "user"
            : "assistant";
        const partialText = segments
          .filter((segment) => !segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        const partial =
          role === "assistant" ? cleanAssistantText(partialText) : partialText;
        setInterim(partial ? { role, text: partial } : null);

        const receivedText = segments
          .filter((segment) => segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        const finalText =
          role === "assistant" ? cleanAssistantText(receivedText) : receivedText;
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
        if (toolActivityTimerRef.current) {
          clearTimeout(toolActivityTimerRef.current);
          toolActivityTimerRef.current = null;
        }
        roomRef.current = null;
        setToolActivity(null);
        setPhase("idle");
        setNotice("Disconnected");
      });

      await room.connect(connection.serverUrl, connection.participantToken);
      await room.startAudio();
      await room.localParticipant.setMicrophoneEnabled(enableMicrophone);
      setIsMicMuted(!enableMicrophone);
      setPhase(enableMicrophone ? "listening" : "paused");
      setNotice(
        enableMicrophone
          ? "Just stop talking when you’re done"
          : "Microphone off — type your message or tap to speak",
      );
      return room;
    } catch (error) {
      console.error("Auren session failed to start", error);
      roomRef.current = null;
      setPhase("idle");
      setNotice("");
      setFailure(describeFailure(error));
      return null;
    }
  }, [getToken]);

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

  const toggleTypeInput = useCallback(async () => {
    const nextOpen = !isTypeOpen;
    setIsTypeOpen(nextOpen);
    if (nextOpen && !roomRef.current) {
      setFailure(null);
      await startSession(false);
    }
  }, [isTypeOpen, startSession]);

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
          <div className="account">
            <Link className="leave" href="/">
              Home
            </Link>
            <button
              className="leave"
              type="button"
              onClick={() => setIsMemoryOpen(true)}
            >
              Memory
            </button>
            {phase !== "idle" && (
              <button className="leave" onClick={() => void disconnect()}>
                End call
              </button>
            )}
            <UserButton />
          </div>
        </header>

        <MemoryPanel open={isMemoryOpen} onClose={() => setIsMemoryOpen(false)} />

        <section className="stage" aria-live="polite">
          <div
            className={`conversation ${hasConversation ? "" : "conversationEmpty"}`}
            ref={messagesRef}
          >
            {!hasConversation && (
              <div className="emptyConversation">
                <span>New conversation</span>
                <h1>What’s on your mind?</h1>
                <p>Speak naturally, or switch to typing whenever you prefer.</p>
                {pageContext?.present && (
                  <p className="pageContextHint">
                    Page ready: {pageContext.title || "Shared article"}. Ask Auren to explain it.
                  </p>
                )}
              </div>
            )}
            {hasConversation && pageContext?.present && (
              <p className="pageContextHint pageContextHintInline">
                Shared page: {pageContext.title || "Article"}
              </p>
            )}
            {messages.map((message) => (
              <article className={`chatMessage ${message.role}`} key={message.id}>
                <span className="chatSpeaker">
                  {message.role === "user" ? "You" : "Auren"}
                </span>
                {message.role === "assistant" ? (
                  <MarkdownMessage text={message.text} />
                ) : (
                  <p className="chatBubble" dir="ltr">{message.text}</p>
                )}
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
        </section>

        <section className="dock">
          <div className="toolActivitySlot" aria-live="polite" aria-atomic="true">
            {toolActivity && (
              <div
                className={`toolActivity toolActivity-${toolActivity.status}`}
                key={`${toolActivity.invocationId}-${toolActivity.status}`}
              >
                <span className="toolActivityMark" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                <span>
                  {toolActivity.status === "started"
                    ? `Using ${toolLabel(toolActivity.tool)}`
                    : toolActivity.status === "completed"
                      ? `${toolLabel(toolActivity.tool)} complete`
                      : `${toolLabel(toolActivity.tool)} unavailable`}
                </span>
              </div>
            )}
          </div>
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

          <div className="controlRow">
            {phase !== "idle" && phase !== "connecting" && (
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
            )}
            <button
              onClick={() => void toggleTypeInput()}
              aria-pressed={isTypeOpen}
              disabled={phase === "connecting"}
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
