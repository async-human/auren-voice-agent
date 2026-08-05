"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Room, RoomEvent, Track } from "livekit-client";
import Link from "next/link";
import { UserButton, useAuth } from "@clerk/nextjs";
import MarkdownMessage from "./markdown-message";

const MemoryPanel = dynamic(() => import("./memory-panel"), { ssr: false });
const ConnectionsPanel = dynamic(() => import("./connections-panel"), { ssr: false });

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
  list_calendar_events: "Calendar",
  find_free_slots: "Free slots",
  create_calendar_event: "Schedule event",
  search_emails: "Email search",
  draft_email: "Draft email",
  send_email: "Send email",
  confirm_pending_action: "Confirm action",
  reject_pending_action: "Cancel action",
  list_pending_actions: "Pending actions",
  start_workflow: "Workflow",
  update_workflow: "Workflow",
  complete_workflow: "Workflow",
  schedule_followup: "Follow-up",
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
  const [isConnectionsOpen, setIsConnectionsOpen] = useState(false);
  const [toolActivity, setToolActivity] = useState<ToolActivity | null>(null);
  const [pageContext, setPageContext] = useState<PageContextMeta | null>(null);
  const [isScreenSharing, setIsScreenSharing] = useState(false);
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
    setIsScreenSharing(false);
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

    const poll = () => {
      if (document.visibilityState === "hidden") return;
      void refreshPageContext();
    };

    poll();
    const timer = setInterval(poll, 15000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
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

      const commitTranscription = (role: Message["role"], rawText: string) => {
        const text =
          role === "assistant" ? cleanAssistantText(rawText) : rawText.trim();
        if (!text) return;

        setMessages((current) => {
          const last = current.at(-1);
          // Typed sends already insert the user bubble; skip the stream echo.
          if (
            role === "user" &&
            last?.role === "user" &&
            last.text.trim() === text
          ) {
            return current;
          }
          return [...current, { id: nextId.current++, role, text }];
        });
        setInterim(null);
        setPhase(role === "user" ? "thinking" : "listening");
        if (role === "assistant") {
          setNotice("Just stop talking when you’re done");
        }
      };

      const localIdentity = () => room.localParticipant.identity;

      const isLocalMicTrack = (trackId: string | undefined) => {
        if (!trackId) return false;
        for (const publication of room.localParticipant.trackPublications.values()) {
          if (
            publication.trackSid === trackId ||
            publication.track?.sid === trackId
          ) {
            return true;
          }
        }
        return false;
      };

      const resolveTranscriptRole = (
        participantIdentity: string | undefined,
        attrs: Record<string, string>,
      ): Message["role"] => {
        // Agents often publish user STT with the agent identity. Prefer track /
        // on-behalf attributes over the stream sender identity.
        const onBehalf = attrs["lk.publish_on_behalf"];
        if (onBehalf && onBehalf === localIdentity()) return "user";

        const transcribedTrack =
          attrs["lk.transcribed_track_id"] || attrs["transcribed_track_id"];
        if (isLocalMicTrack(transcribedTrack)) return "user";

        if (participantIdentity && participantIdentity === localIdentity()) {
          return "user";
        }
        return "assistant";
      };

      room.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
        if (topic === "auren.transcript") {
          try {
            const event = JSON.parse(new TextDecoder().decode(payload)) as {
              type?: string;
              role?: Message["role"];
              text?: string;
              final?: boolean;
            };
            if (
              event.type !== "transcript" ||
              (event.role !== "user" && event.role !== "assistant") ||
              typeof event.text !== "string"
            ) {
              return;
            }
            if (event.final === false) {
              const text =
                event.role === "assistant"
                  ? cleanAssistantText(event.text)
                  : event.text.trim();
              if (text) {
                setInterim({ role: event.role, text });
                if (event.role === "user") setPhase("listening");
              }
              return;
            }
            commitTranscription(event.role, event.text);
          } catch {
            // Ignore malformed transcript packets.
          }
          return;
        }

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

      // Agents publish STT + replies on the lk.transcription text stream.
      // TranscriptionReceived is deprecated and no longer fires for AgentSession.
      room.registerTextStreamHandler("lk.transcription", async (reader, participantInfo) => {
        try {
          const message = await reader.readAll();
          const attrs = (reader.info.attributes ?? {}) as Record<string, string>;
          const finalFlag = attrs["lk.transcription_final"];
          // Missing flag = treat as final so agent text-only replies still land.
          const isFinal = !(finalFlag === false || finalFlag === "false");
          const role = resolveTranscriptRole(participantInfo.identity, attrs);
          const text =
            role === "assistant" ? cleanAssistantText(message) : message.trim();
          if (!text) return;

          if (!isFinal) {
            setInterim({ role, text });
            if (role === "user") setPhase("listening");
            return;
          }
          commitTranscription(role, text);
        } catch (error) {
          console.error("Failed to read lk.transcription stream", error);
        }
      });

      // Legacy fallback for older agent builds that still emit track transcriptions.
      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const trackId = segments.find((segment) => segment.trackSid)?.trackSid;
        const role = resolveTranscriptRole(participant?.identity, {
          "lk.transcribed_track_id": trackId ?? "",
        });
        const partialText = segments
          .filter((segment) => !segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        const partial =
          role === "assistant" ? cleanAssistantText(partialText) : partialText;
        if (partial) {
          setInterim({ role, text: partial });
          if (role === "user") setPhase("listening");
        }

        const receivedText = segments
          .filter((segment) => segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        if (!receivedText) return;
        commitTranscription(role, receivedText);
      });

      room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const agentIsSpeaking = speakers.some(
          (speaker) => speaker.identity !== room.localParticipant.identity,
        );
        if (agentIsSpeaking) setPhase("speaking");
      });

      room.on(RoomEvent.ParticipantDisconnected, (participant) => {
        if (participant.identity === room.localParticipant.identity) return;
        // Agent job crashed or left — mic stays up but nothing will transcribe.
        setFailure(
          "Auren’s voice worker disconnected. End the call, confirm the RunPod agent is healthy, then try again.",
        );
        setNotice("Voice worker left the room");
        setPhase("paused");
      });

      room.on(RoomEvent.Disconnected, () => {
        if (toolActivityTimerRef.current) {
          clearTimeout(toolActivityTimerRef.current);
          toolActivityTimerRef.current = null;
        }
        roomRef.current = null;
        setToolActivity(null);
        setIsScreenSharing(false);
        setPhase("idle");
        setNotice("Disconnected");
      });

      room.on(RoomEvent.LocalTrackPublished, (publication) => {
        if (publication.source === Track.Source.ScreenShare) {
          setIsScreenSharing(true);
        }
      });
      room.on(RoomEvent.LocalTrackUnpublished, (publication) => {
        if (publication.source === Track.Source.ScreenShare) {
          setIsScreenSharing(false);
        }
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

  const toggleScreenShare = useCallback(async () => {
    let room = roomRef.current;
    if (!room) {
      setFailure(null);
      room = await startSession(true);
      if (!room) return;
    }

    try {
      const nextEnabled = !isScreenSharing;
      await room.localParticipant.setScreenShareEnabled(nextEnabled, {
        audio: false,
        contentHint: "detail",
      });
      setIsScreenSharing(nextEnabled);
      setNotice(
        nextEnabled
          ? "Screen shared — ask Auren what you’re looking at"
          : "Screen share stopped",
      );
    } catch (error) {
      console.error("Screen share toggle failed", error);
      setIsScreenSharing(false);
      setFailure(
        error instanceof Error && /Permission|NotAllowed/i.test(error.message)
          ? "Screen share was blocked. Allow screen sharing in the browser, then try again."
          : describeFailure(error),
      );
    }
  }, [isScreenSharing, startSession]);

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
    // Instant scroll during live transcription; smooth only for settled messages.
    messagesElement.scrollTo({
      top: messagesElement.scrollHeight,
      behavior: interim ? "auto" : "smooth",
    });
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
            <button
              className="leave"
              type="button"
              onClick={() => setIsConnectionsOpen(true)}
            >
              Connect
            </button>
            {phase !== "idle" && (
              <button className="leave" onClick={() => void disconnect()}>
                End call
              </button>
            )}
            <UserButton />
          </div>
        </header>

        {isMemoryOpen && (
          <MemoryPanel open={isMemoryOpen} onClose={() => setIsMemoryOpen(false)} />
        )}
        {isConnectionsOpen && (
          <ConnectionsPanel
            open={isConnectionsOpen}
            onClose={() => setIsConnectionsOpen(false)}
          />
        )}

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
              onClick={() => void toggleScreenShare()}
              aria-pressed={isScreenSharing}
              disabled={phase === "connecting"}
              className={isScreenSharing ? "screenShareActive" : undefined}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="2" y="4" width="20" height="14" rx="2" />
                <path d="M8 20h8M12 18v2" />
              </svg>
              <span>{isScreenSharing ? "Sharing screen" : "Share screen"}</span>
            </button>
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
