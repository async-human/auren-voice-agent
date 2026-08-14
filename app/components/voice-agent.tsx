"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { ConnectionState, Room, RoomEvent, Track } from "livekit-client";
import Link from "next/link";
import { UserButton, useAuth } from "@clerk/nextjs";
import MarkdownMessage from "./markdown-message";

const MemoryPanel = dynamic(() => import("./memory-panel"), { ssr: false });
const ConnectionsPanel = dynamic(() => import("./connections-panel"), { ssr: false });
const ArtifactsPanel = dynamic(() => import("./artifacts-panel"), { ssr: false });
const InboxPanel = dynamic(() => import("./inbox-panel"), { ssr: false });

type Phase = "idle" | "connecting" | "listening" | "thinking" | "speaking" | "paused";
type SttProvider = "whisper" | "qwen";

type SttOption = {
  id: SttProvider;
  label: string;
  description: string;
  realtime: boolean;
};

const fallbackSttOptions: SttOption[] = [
  {
    id: "whisper",
    label: "Whisper",
    description: "Mature and dependable multilingual transcription.",
    realtime: false,
  },
  {
    id: "qwen",
    label: "Qwen3-ASR",
    description: "Accuracy-focused multilingual transcription.",
    realtime: false,
  },
];
const STT_PREFERENCE_KEY = "auren.stt-provider";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

type InterimMessage = Pick<Message, "role" | "text">;
type ToolActivityStatus =
  | "started"
  | "awaiting_approval"
  | "completed"
  | "cancelled"
  | "failed";
type ToolActivity = {
  tool: string;
  invocationId: string;
  status: ToolActivityStatus;
  durationMs?: number;
  actionId?: string;
  displayName?: string;
  decisionSummary?: string;
  inputSummary?: string;
  resultSummary?: string;
  workflowId?: string;
  workflowGoal?: string;
  workflowPlan?: string[];
  workflowCurrentStep?: number;
  workflowStatus?: string;
  artifactId?: string;
  artifactFilename?: string;
  artifactFormat?: string;
  receivedAt: number;
};

type WorkflowView = {
  id?: string;
  sourceInvocationId: string;
  goal: string;
  plan: string[];
  currentStep: number;
  status: string;
  resultSummary?: string;
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
  update_calendar_event: "Update event",
  delete_calendar_event: "Delete event",
  search_emails: "Email search",
  read_email: "Read email",
  trash_email: "Move email to Trash",
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
  create_document: "Create document",
  create_spreadsheet: "Create spreadsheet",
  create_presentation: "Create presentation",
  list_artifacts: "Generated files",
};

const toolActions: Record<string, string> = {
  get_current_time: "Checking the time",
  get_weather: "Checking the weather",
  create_reminder: "Creating a reminder",
  list_reminders: "Loading your reminders",
  save_note: "Saving a note",
  search_notes: "Searching your notes",
  search_web: "Searching the web",
  get_page_context: "Reading the shared page",
  list_calendar_events: "Checking your calendar",
  find_free_slots: "Finding open time",
  create_calendar_event: "Preparing a calendar event",
  update_calendar_event: "Preparing a calendar update",
  delete_calendar_event: "Preparing to delete an event",
  search_emails: "Searching your email",
  read_email: "Reading your email",
  trash_email: "Preparing to move email to Trash",
  draft_email: "Preparing an email draft",
  send_email: "Preparing to send email",
  confirm_pending_action: "Completing the approved action",
  reject_pending_action: "Cancelling the pending action",
  list_pending_actions: "Checking pending actions",
  start_workflow: "Starting a workflow",
  update_workflow: "Updating the workflow",
  complete_workflow: "Completing the workflow",
  schedule_followup: "Scheduling a follow-up",
  check_tool_status: "Checking tool availability",
  recall: "Searching memory",
  remember: "Updating memory",
  forget: "Removing a memory",
  create_document: "Creating a document",
  create_spreadsheet: "Creating a spreadsheet",
  create_presentation: "Creating a presentation",
  list_artifacts: "Loading generated files",
};

const toolStatusLabels: Record<ToolActivityStatus, string> = {
  started: "In progress",
  awaiting_approval: "Approval needed",
  completed: "Completed",
  cancelled: "Cancelled",
  failed: "Needs attention",
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

function activityLabel(activity: ToolActivity): string {
  return activity.displayName || toolLabel(activity.tool);
}

function toolAction(tool: string): string {
  return toolActions[tool] ?? `Using ${toolLabel(tool)}`;
}

function toolKind(
  tool: string,
): "calendar" | "search" | "weather" | "memory" | "workflow" | "artifact" | "utility" {
  if (/calendar|slot|reminder|followup/.test(tool)) return "calendar";
  if (/search|page_context|email/.test(tool)) return "search";
  if (/weather/.test(tool)) return "weather";
  if (/recall|remember|forget|note/.test(tool)) return "memory";
  if (/workflow|pending_action/.test(tool)) return "workflow";
  if (/document|spreadsheet|presentation|artifact/.test(tool)) return "artifact";
  return "utility";
}

function formatToolTiming(activity: ToolActivity, now: number): string {
  if (activity.status === "awaiting_approval") return "Waiting";
  const duration =
    activity.status === "started"
      ? Math.max(0, now - activity.receivedAt)
      : activity.durationMs;
  if (duration === undefined) return "";
  if (duration < 1000) return activity.status === "started" ? "Now" : "<1s";
  const seconds = duration / 1000;
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
}

function workflowStepState(
  workflow: WorkflowView,
  index: number,
): "completed" | "active" | "pending" | "failed" {
  if (workflow.status === "failed") {
    return index < workflow.currentStep ? "completed" : index === workflow.currentStep ? "failed" : "pending";
  }
  if (workflow.status === "completed") return "completed";
  if (index < workflow.currentStep) return "completed";
  if (index === workflow.currentStep) return "active";
  return "pending";
}

function ToolGlyph({ tool }: { tool: string }) {
  const kind = toolKind(tool);
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {kind === "calendar" && (
        <>
          <rect x="4" y="5" width="16" height="15" rx="3" />
          <path d="M8 3v4M16 3v4M4 10h16M8 14h3M8 17h6" />
        </>
      )}
      {kind === "search" && (
        <>
          <circle cx="10.5" cy="10.5" r="5.5" />
          <path d="m15 15 4.5 4.5" />
        </>
      )}
      {kind === "weather" && (
        <path d="M7 18h10a4 4 0 0 0 .5-8 6 6 0 0 0-11-1.2A4.6 4.6 0 0 0 7 18Z" />
      )}
      {kind === "memory" && (
        <>
          <path d="M12 4c4.4 0 8 1.5 8 3.4s-3.6 3.4-8 3.4-8-1.5-8-3.4S7.6 4 12 4Z" />
          <path d="M4 7.4v4.5c0 1.9 3.6 3.4 8 3.4s8-1.5 8-3.4V7.4M4 12v4.6c0 1.9 3.6 3.4 8 3.4s8-1.5 8-3.4V12" />
        </>
      )}
      {kind === "workflow" && (
        <>
          <circle cx="6" cy="6" r="2" />
          <circle cx="18" cy="12" r="2" />
          <circle cx="6" cy="18" r="2" />
          <path d="M8 6h2a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M8 18h2a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3" />
        </>
      )}
      {kind === "artifact" && (
        <>
          <path d="M6 3h8l4 4v14H6z" />
          <path d="M14 3v5h5M9 12h6M9 16h6" />
        </>
      )}
      {kind === "utility" && (
        <>
          <circle cx="12" cy="12" r="7" />
          <path d="M12 8v4l2.5 2" />
        </>
      )}
    </svg>
  );
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
    return "Your session expired. Sign in again to keep talking to June.";
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
  speaking: "June is speaking",
  paused: "Paused — tap to resume",
};

export default function VoiceAgent() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
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
  const [isArtifactsOpen, setIsArtifactsOpen] = useState(false);
  const [isInboxOpen, setIsInboxOpen] = useState(false);
  const [inboxUnread, setInboxUnread] = useState(0);
  const [sttOptions, setSttOptions] = useState<SttOption[]>(fallbackSttOptions);
  const [selectedSttProvider, setSelectedSttProvider] =
    useState<SttProvider>("whisper");
  const [toolActivities, setToolActivities] = useState<ToolActivity[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowView | null>(null);
  const [activityNow, setActivityNow] = useState(() => Date.now());
  const [pageContext, setPageContext] = useState<PageContextMeta | null>(null);
  const [isScreenSharing, setIsScreenSharing] = useState(false);
  const roomRef = useRef<Room | null>(null);
  // Bumped on every disconnect so an in-flight connect cannot reattach after teardown.
  const sessionEpochRef = useRef(0);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const audioElementsRef = useRef<HTMLMediaElement[]>([]);
  const voiceMutedRef = useRef(false);
  const nextId = useRef(1);
  // Dedupe across lk.transcription + auren.transcript (same reply can arrive 2–3×).
  const recentTranscriptKeysRef = useRef<Map<string, number>>(new Map());

  const detachRoomMedia = useCallback((room: Room) => {
    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((publication) => {
        publication.track?.detach().forEach((element) => element.remove());
      });
    });
    audioElementsRef.current.forEach((element) => element.remove());
    audioElementsRef.current = [];
  }, []);

  const disconnect = useCallback(async () => {
    sessionEpochRef.current += 1;
    const room = roomRef.current;
    roomRef.current = null;
    if (!room) {
      setInterim(null);
      setToolActivities([]);
      setWorkflow(null);
      setPageContext(null);
      setIsScreenSharing(false);
      setIsMicMuted(false);
      setPhase("idle");
      return;
    }

    detachRoomMedia(room);
    recentTranscriptKeysRef.current.clear();
    try {
      // Calling disconnect while still Disconnected tries to send leave and
      // LiveKit logs "cannot send signal request before connected".
      if (room.state !== ConnectionState.Disconnected) {
        await room.disconnect();
      }
    } catch {
      // Teardown races (unmount during connect) are safe to ignore.
    }
    setInterim(null);
    setToolActivities([]);
    setWorkflow(null);
    setPageContext(null);
    setIsScreenSharing(false);
    setIsMicMuted(false);
    setPhase("idle");
    setNotice("Session ended");
  }, [detachRoomMedia]);

  useEffect(() => {
    if (!toolActivities.some((activity) => activity.status === "started")) return;
    setActivityNow(Date.now());
    const timer = window.setInterval(() => setActivityNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [toolActivities]);

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

  const refreshInboxUnread = useCallback(async () => {
    const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiBaseUrl) return;
    try {
      const token = await getToken();
      if (!token) return;
      const response = await fetch(
        `${apiBaseUrl.replace(/\/$/, "")}/v1/notifications?status=unread`,
        { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
      );
      if (!response.ok) return;
      const body = (await response.json()) as { unread_count: number };
      setInboxUnread(body.unread_count || 0);
    } catch {
      // Badge is optional; ignore transient failures.
    }
  }, [getToken]);

  useEffect(() => {
    void refreshInboxUnread();
    const timer = window.setInterval(() => void refreshInboxUnread(), 15000);
    return () => window.clearInterval(timer);
  }, [refreshInboxUnread]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) return;

    const loadSttOptions = async () => {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) return;
      try {
        const token = await getToken();
        if (!token) return;
        const response = await fetch(
          `${apiBaseUrl.replace(/\/$/, "")}/v1/voice/stt-options`,
          { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
        );
        if (!response.ok) return;
        const body = (await response.json()) as {
          default_provider: SttProvider;
          providers: SttOption[];
        };
        const available = Array.isArray(body.providers)
          ? body.providers.filter((option) =>
              ["whisper", "qwen"].includes(option.id),
            )
          : [];
        if (available.length === 0) return;
        setSttOptions(available);
        const remembered = window.localStorage.getItem(STT_PREFERENCE_KEY);
        const preferred = available.find((option) => option.id === remembered)?.id;
        const defaultProvider = available.find(
          (option) => option.id === body.default_provider,
        )?.id;
        setSelectedSttProvider(preferred ?? defaultProvider ?? available[0].id);
      } catch {
        // Provider discovery is optional; Whisper remains the safe fallback.
      }
    };
    void loadSttOptions();
  }, [getToken, isLoaded, isSignedIn]);

  const chooseSttProvider = useCallback(
    (provider: SttProvider) => {
      if (phase !== "idle") return;
      setSelectedSttProvider(provider);
      window.localStorage.setItem(STT_PREFERENCE_KEY, provider);
    },
    [phase],
  );

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
    const epoch = ++sessionEpochRef.current;
    setToolActivities([]);
    setWorkflow(null);
    setPhase("connecting");
    setNotice("Securing a realtime session");

    let room: Room | null = null;
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
      if (sessionEpochRef.current !== epoch) return null;

      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/voice/token`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${sessionToken}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ stt_provider: selectedSttProvider }),
      });
      const connection = (await response.json()) as {
        serverUrl?: string;
        participantToken?: string;
        sttProvider?: SttProvider;
        detail?: string;
        error?: string;
      };

      if (!response.ok || !connection.serverUrl || !connection.participantToken) {
        throw new Error(
          connection.detail || connection.error || "Voice service unavailable",
        );
      }
      if (sessionEpochRef.current !== epoch) return null;
      if (connection.sttProvider) {
        setSelectedSttProvider(connection.sttProvider);
      }

      // dynacast can pause publishing when subscribers flap; keep mic audio always on.
      const activeRoom = new Room({ adaptiveStream: true, dynacast: false });
      room = activeRoom;
      roomRef.current = activeRoom;

      const attachRemoteAudio = (track: Track) => {
        if (track.kind !== Track.Kind.Audio) return;
        const audioElement = track.attach();
        audioElement.autoplay = true;
        audioElement.setAttribute("playsinline", "true");
        audioElement.muted = voiceMutedRef.current;
        // display:none can prevent playback in some browsers; keep it in-layout but invisible.
        Object.assign(audioElement.style, {
          position: "fixed",
          width: "1px",
          height: "1px",
          opacity: "0",
          pointerEvents: "none",
          left: "0",
          bottom: "0",
        });
        document.body.appendChild(audioElement);
        audioElementsRef.current.push(audioElement);

        const tryPlay = async () => {
          try {
            await activeRoom.startAudio();
            if (!voiceMutedRef.current) {
              audioElement.muted = false;
              await audioElement.play();
            }
          } catch (error) {
            console.error("Auren voice playback blocked", error);
            setFailure(
              "Browser blocked June’s voice. Click “June’s voice” or tap anywhere on the page, then ask again.",
            );
          }
        };
        void tryPlay();
      };

      activeRoom.on(RoomEvent.TrackSubscribed, (track) => {
        attachRemoteAudio(track);
      });

      activeRoom.on(RoomEvent.TrackUnsubscribed, (track) => {
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

        const key = `${role}:${text.toLowerCase()}`;
        const now = Date.now();
        const lastSeen = recentTranscriptKeysRef.current.get(key);
        if (lastSeen && now - lastSeen < 12_000) {
          setInterim(null);
          return;
        }
        recentTranscriptKeysRef.current.set(key, now);
        // Bound the dedupe map so long sessions don't grow forever.
        if (recentTranscriptKeysRef.current.size > 80) {
          for (const [entryKey, seenAt] of recentTranscriptKeysRef.current) {
            if (now - seenAt > 12_000) {
              recentTranscriptKeysRef.current.delete(entryKey);
            }
          }
        }

        setMessages((current) => {
          const duplicate = current
            .slice(-6)
            .some(
              (message) =>
                message.role === role && message.text.trim() === text,
            );
          if (duplicate) return current;
          return [...current, { id: nextId.current++, role, text }];
        });
        setInterim(null);
        setPhase(role === "user" ? "thinking" : "listening");
        if (role === "user") {
          setNotice("Got it — thinking");
        } else {
          setNotice("June replied — voice may take a moment while speech synthesizes");
          void activeRoom.startAudio().catch(() => {
            // Autoplay unlock is best-effort; TrackSubscribed also retries play().
          });
        }
      };

      const localIdentity = () => activeRoom.localParticipant.identity;

      const isLocalMicTrack = (trackId: string | undefined) => {
        if (!trackId) return false;
        for (const publication of activeRoom.localParticipant.trackPublications.values()) {
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

      activeRoom.on(RoomEvent.DataReceived, (payload, _participant, _kind, topic) => {
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
                if (event.role === "user") {
                  setPhase("listening");
                  setNotice("Hearing you…");
                }
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
            ![
              "started",
              "awaiting_approval",
              "completed",
              "cancelled",
              "failed",
            ].includes(event.status ?? "")
          ) {
            return;
          }

          const receivedAt = Date.now();
          const workflowPlan = Array.isArray(event.workflowPlan)
            ? event.workflowPlan.filter(
                (step): step is string => typeof step === "string" && Boolean(step.trim()),
              )
            : undefined;
          const activity: ToolActivity = {
            tool: event.tool,
            invocationId: event.invocationId,
            status: event.status as ToolActivityStatus,
            durationMs:
              typeof event.durationMs === "number" ? event.durationMs : undefined,
            actionId:
              typeof event.actionId === "string" ? event.actionId : undefined,
            displayName:
              typeof event.displayName === "string" ? event.displayName : undefined,
            decisionSummary:
              typeof event.decisionSummary === "string"
                ? event.decisionSummary
                : undefined,
            inputSummary:
              typeof event.inputSummary === "string" ? event.inputSummary : undefined,
            resultSummary:
              typeof event.resultSummary === "string" ? event.resultSummary : undefined,
            workflowId:
              typeof event.workflowId === "string" ? event.workflowId : undefined,
            workflowGoal:
              typeof event.workflowGoal === "string" ? event.workflowGoal : undefined,
            workflowPlan,
            workflowCurrentStep:
              typeof event.workflowCurrentStep === "number"
                ? event.workflowCurrentStep
                : undefined,
            workflowStatus:
              typeof event.workflowStatus === "string"
                ? event.workflowStatus
                : undefined,
            artifactId:
              typeof event.artifactId === "string" ? event.artifactId : undefined,
            artifactFilename:
              typeof event.artifactFilename === "string"
                ? event.artifactFilename
                : undefined,
            artifactFormat:
              typeof event.artifactFormat === "string" ? event.artifactFormat : undefined,
            receivedAt,
          };
          setActivityNow(receivedAt);
          if (activity.tool === "start_workflow" && activity.workflowGoal) {
            setWorkflow((current) => ({
              id: activity.workflowId ?? current?.id,
              sourceInvocationId: activity.invocationId,
              goal: activity.workflowGoal ?? current?.goal ?? "Current request",
              plan: activity.workflowPlan ?? current?.plan ?? [],
              currentStep:
                activity.workflowCurrentStep ?? current?.currentStep ?? 0,
              status: activity.workflowStatus ?? current?.status ?? "planning",
              resultSummary: activity.resultSummary ?? current?.resultSummary,
            }));
          } else if (activity.workflowId) {
            setWorkflow((current) => {
              if (!current || (current.id && current.id !== activity.workflowId)) {
                return current;
              }
              return {
                ...current,
                id: activity.workflowId ?? current.id,
                currentStep:
                  activity.workflowCurrentStep ?? current.currentStep,
                status: activity.workflowStatus ?? current.status,
                resultSummary:
                  activity.tool === "complete_workflow"
                    ? activity.resultSummary ?? current.resultSummary
                    : current.resultSummary,
              };
            });
          }
          setToolActivities((current) => {
            const existing = current.find(
              (item) => item.invocationId === activity.invocationId,
            );
            const updated = {
              ...existing,
              ...activity,
              durationMs: activity.durationMs ?? existing?.durationMs,
              actionId: activity.actionId ?? existing?.actionId,
              displayName: activity.displayName ?? existing?.displayName,
              decisionSummary:
                activity.decisionSummary ?? existing?.decisionSummary,
              inputSummary: activity.inputSummary ?? existing?.inputSummary,
              resultSummary: activity.resultSummary ?? existing?.resultSummary,
              workflowId: activity.workflowId ?? existing?.workflowId,
              workflowGoal: activity.workflowGoal ?? existing?.workflowGoal,
              workflowPlan: activity.workflowPlan ?? existing?.workflowPlan,
              workflowCurrentStep:
                activity.workflowCurrentStep ?? existing?.workflowCurrentStep,
              workflowStatus:
                activity.workflowStatus ?? existing?.workflowStatus,
              receivedAt: existing?.receivedAt ?? activity.receivedAt,
            };
            const next = [
              updated,
              ...current.filter(
                (item) => item.invocationId !== activity.invocationId,
              ),
            ];
            const unsettled = next.filter(
              (item) =>
                item.status === "started" || item.status === "awaiting_approval",
            );
            const settled = next.filter(
              (item) =>
                item.status !== "started" &&
                item.status !== "awaiting_approval",
            );
            return [...unsettled, ...settled].slice(0, 10);
          });
        } catch {
          // Ignore unrelated or malformed data-channel messages.
        }
      });

      // Live transcripts for interim captions. Finals also arrive on
      // auren.transcript — commitTranscription dedupes identical copies.
      activeRoom.registerTextStreamHandler("lk.transcription", async (reader, participantInfo) => {
        try {
          const message = await reader.readAll();
          const attrs = reader.info.attributes ?? {};
          const finalFlag = attrs["lk.transcription_final"];
          const isFinal = finalFlag !== "false";
          const role = resolveTranscriptRole(
            participantInfo.identity,
            attrs as Record<string, string>,
          );
          const text =
            role === "assistant" ? cleanAssistantText(message) : message.trim();
          if (!text) return;

          if (!isFinal) {
            setInterim({ role, text });
            if (role === "user") {
              setPhase("listening");
              setNotice("Hearing you…");
            }
            return;
          }
          commitTranscription(role, text);
        } catch (error) {
          console.error("Failed to read lk.transcription stream", error);
        }
      });

      activeRoom.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
        const localSpeaking = speakers.some(
          (speaker) => speaker.identity === activeRoom.localParticipant.identity,
        );
        const agentIsSpeaking = speakers.some(
          (speaker) => speaker.identity !== activeRoom.localParticipant.identity,
        );
        if (agentIsSpeaking) {
          setPhase("speaking");
          setNotice("June is speaking");
          if (!voiceMutedRef.current) {
            void activeRoom.startAudio().catch(() => undefined);
            audioElementsRef.current.forEach((element) => {
              element.muted = false;
              void element.play().catch(() => undefined);
            });
          }
        } else if (localSpeaking && !voiceMutedRef.current) {
          setNotice("Hearing you…");
        }
      });

      activeRoom.on(RoomEvent.ParticipantDisconnected, (participant) => {
        if (participant.identity === activeRoom.localParticipant.identity) return;
        // Agent job crashed or left — mic stays up but nothing will transcribe.
        setFailure(
          "Auren’s voice worker disconnected. End the call, confirm the RunPod agent is healthy, then try again.",
        );
        setNotice("Voice worker left the room");
        setPhase("paused");
      });

      activeRoom.on(RoomEvent.Disconnected, () => {
        roomRef.current = null;
        setToolActivities([]);
        setIsScreenSharing(false);
        setPhase("idle");
        setNotice("Disconnected");
      });

      activeRoom.on(RoomEvent.LocalTrackPublished, (publication) => {
        if (publication.source === Track.Source.ScreenShare) {
          setIsScreenSharing(true);
        }
      });
      activeRoom.on(RoomEvent.LocalTrackUnpublished, (publication) => {
        if (publication.source === Track.Source.ScreenShare) {
          setIsScreenSharing(false);
        }
      });

      await activeRoom.connect(connection.serverUrl, connection.participantToken);
      if (sessionEpochRef.current !== epoch || roomRef.current !== activeRoom) {
        // User left / component unmounted while connect was in flight.
        detachRoomMedia(activeRoom);
        if (activeRoom.state !== ConnectionState.Disconnected) {
          await activeRoom.disconnect().catch(() => undefined);
        }
        return null;
      }
      await activeRoom.startAudio();
      // Attach any agent audio that was already live when we joined.
      for (const participant of activeRoom.remoteParticipants.values()) {
        for (const publication of participant.trackPublications.values()) {
          if (publication.track) attachRemoteAudio(publication.track);
        }
      }
      await activeRoom.localParticipant.setMicrophoneEnabled(enableMicrophone);
      if (sessionEpochRef.current !== epoch || roomRef.current !== activeRoom) {
        detachRoomMedia(activeRoom);
        if (activeRoom.state !== ConnectionState.Disconnected) {
          await activeRoom.disconnect().catch(() => undefined);
        }
        return null;
      }
      setIsMicMuted(!enableMicrophone);
      setPhase(enableMicrophone ? "listening" : "paused");
      setNotice(
        enableMicrophone
          ? "Just stop talking when you’re done"
          : "Microphone off — type your message or tap to speak",
      );
      return activeRoom;
    } catch (error) {
      if (roomRef.current === room) {
        roomRef.current = null;
      }
      // Disconnect during connect surfaces as AbortError — not a real failure.
      if (sessionEpochRef.current !== epoch) {
        setPhase("idle");
        return null;
      }
      const aborted =
        (error instanceof DOMException && error.name === "AbortError") ||
        (error instanceof Error && /abort/i.test(error.message));
      if (!aborted) {
        console.error("Auren session failed to start", error);
        setFailure(describeFailure(error));
      }
      setPhase("idle");
      setNotice("");
      return null;
    }
  }, [detachRoomMedia, getToken, selectedSttProvider]);

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
          ? "Screen shared — ask June what you’re looking at"
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

  const handleActionResolved = useCallback(
    (actionId: string, decision: "confirm" | "reject") => {
      setToolActivities((current) =>
        current.map((activity) =>
          activity.actionId === actionId &&
          activity.status === "awaiting_approval"
            ? {
                ...activity,
                status: decision === "confirm" ? "completed" : "cancelled",
                resultSummary:
                  decision === "confirm"
                    ? "The approved action completed successfully."
                    : "You rejected this action. No external change was made.",
              }
            : activity,
        ),
      );
    },
    [],
  );

  const toggleVoicePlayback = useCallback(() => {
    const nextMuted = !isVoiceMuted;
    voiceMutedRef.current = nextMuted;
    const room = roomRef.current;
    if (!nextMuted && room) {
      void room.startAudio().catch(() => undefined);
    }
    audioElementsRef.current.forEach((element) => {
      element.muted = nextMuted;
      if (!nextMuted) {
        void element.play().catch(() => undefined);
      }
    });
    setIsVoiceMuted(nextMuted);
    setNotice(nextMuted ? "June’s voice is muted" : "June’s voice is on");
    if (!nextMuted) setFailure(null);
  }, [isVoiceMuted]);

  const toggleTypeInput = useCallback(async () => {
    const nextOpen = !isTypeOpen;
    setIsTypeOpen(nextOpen);
    if (nextOpen && !roomRef.current) {
      setFailure(null);
      // Keep the mic on so speech still works while the type box is open.
      await startSession(true);
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
      setNotice("Message sent — June is thinking");
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
  const activeToolActivity = toolActivities.find(
    (activity) => activity.status === "started",
  );
  const approvalActivity = toolActivities.find(
    (activity) => activity.status === "awaiting_approval",
  );
  const presenceHeading = activeToolActivity
    ? activityLabel(activeToolActivity)
    : approvalActivity
      ? "Ready when you are"
      : labels[phase];
  const presenceDescription = activeToolActivity
    ? activeToolActivity.decisionSummary || toolAction(activeToolActivity.tool)
    : approvalActivity
      ? approvalActivity.resultSummary ||
        "A consequential action is prepared and will not run without your approval."
      : phase === "idle"
        ? "Begin with your voice, or switch to typing whenever you prefer."
        : notice || "Your secure realtime session is active.";

  return (
    <>
      <div className={`breath breath-${phase}`} aria-hidden="true" />
      <div className="vignette" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <main className={`app phase-${phase}`}>
        <header className="top">
          <div className="who">
            <span className="mark" aria-hidden="true"><i /></span>
            <span className="whoName">
              June
              <small>Standing by</small>
            </span>
          </div>
          <div className="account">
            <Link className="leave homeLink" href="/">
              Home
            </Link>
            <button
              className="leave"
              type="button"
              onClick={() => setIsInboxOpen(true)}
            >
              Inbox
              {inboxUnread > 0 && (
                <span className="inboxBadge">{inboxUnread > 9 ? "9+" : inboxUnread}</span>
              )}
            </button>
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
            <button
              className="leave"
              type="button"
              onClick={() => setIsArtifactsOpen(true)}
            >
              Files
            </button>
            {phase !== "idle" && (
              <button className="leave" onClick={() => void disconnect()}>
                End call
              </button>
            )}
            <UserButton />
          </div>
        </header>

        {isInboxOpen && (
          <InboxPanel
            open={isInboxOpen}
            onClose={() => setIsInboxOpen(false)}
            onUnreadChange={setInboxUnread}
          />
        )}
        {isMemoryOpen && (
          <MemoryPanel open={isMemoryOpen} onClose={() => setIsMemoryOpen(false)} />
        )}
        {isConnectionsOpen && (
          <ConnectionsPanel
            open={isConnectionsOpen}
            onClose={() => setIsConnectionsOpen(false)}
            onActionResolved={handleActionResolved}
          />
        )}
        {isArtifactsOpen && (
          <ArtifactsPanel open={isArtifactsOpen} onClose={() => setIsArtifactsOpen(false)} />
        )}

        <div className="studioGrid">
          <aside className="presencePanel" aria-label="Voice session status">
            <div className="presenceHero">
              <p className="presenceEyebrow">
                <span className="liveDot" aria-hidden="true" />
                {activeToolActivity
                  ? "June is taking action"
                  : phase === "idle"
                    ? "June is here"
                    : "Private session active"}
              </p>
              <div
                className={`presenceOrb presenceOrb-${phase} ${
                  activeToolActivity ? "presenceOrb-toolActive" : ""
                }`}
                aria-hidden="true"
              >
                <span className="presenceAura" />
                <span className="presenceOrbit presenceOrbitA" />
                <span className="presenceOrbit presenceOrbitB" />
                <span className="presenceMembrane">
                  <i /><i />
                </span>
                <span className="presenceCore" />
                <span className="presenceWave">
                  <i /><i /><i /><i /><i />
                </span>
              </div>
              <div className="presenceCopy" aria-live="polite">
                <h1>{presenceHeading}</h1>
                <p>{presenceDescription}</p>
              </div>
            </div>

            <section className="sttPicker" aria-label="Speech recognition model">
                <div className="sttPickerHead">
                  <div>
                    <span>Voice recognition</span>
                    <strong>Choose how June hears you</strong>
                  </div>
                  <small>{phase === "idle" ? "For next call" : "In use"}</small>
                </div>
                <div className="sttChoices" role="radiogroup" aria-label="STT provider">
                  {sttOptions.map((option) => {
                    const selected = option.id === selectedSttProvider;
                    return (
                      <button
                        className={selected ? "sttChoice sttChoiceActive" : "sttChoice"}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        disabled={phase !== "idle"}
                        key={option.id}
                        onClick={() => chooseSttProvider(option.id)}
                      >
                        <span>{option.label}</span>
                        {option.realtime && <small>Live</small>}
                      </button>
                    );
                  })}
                </div>
                <p>
                  {sttOptions.find((option) => option.id === selectedSttProvider)
                    ?.description ?? "Speech recognition for this session."}
                </p>
              </section>

            <section className="activityRail" aria-label="Tool activity">
              <p className="srOnly" aria-live="polite" aria-atomic="true">
                {toolActivities[0]
                  ? `${activityLabel(toolActivities[0])}: ${toolStatusLabels[toolActivities[0].status]}. ${toolActivities[0].decisionSummary || ""}`
                  : "No tool activity yet"}
              </p>
              <div className="activityRailHead">
                <div>
                  <span>Execution trace</span>
                  <strong>Plan, decisions and outcomes</strong>
                </div>
                <span className={activeToolActivity ? "activityLive" : "activityQuiet"}>
                  <i aria-hidden="true" />
                  {activeToolActivity ? "Live" : toolActivities.length ? "Recent" : "Ready"}
                </span>
              </div>

              {workflow && workflow.plan.length > 0 && (
                <article className={`workflowCard workflowCard-${workflow.status}`}>
                  <div className="workflowCardHead">
                    <span>Execution plan</span>
                    <strong>
                      {workflow.status === "completed"
                        ? "Completed"
                        : workflow.status === "failed"
                          ? "Needs attention"
                          : `${Math.min(workflow.currentStep + 1, workflow.plan.length)} of ${workflow.plan.length}`}
                    </strong>
                  </div>
                  <p>{workflow.goal}</p>
                  <ol>
                    {workflow.plan.map((step, index) => {
                      const stepState = workflowStepState(workflow, index);
                      return (
                        <li className={`workflowStep workflowStep-${stepState}`} key={`${index}-${step}`}>
                          <span aria-hidden="true">
                            <i />
                          </span>
                          <small>{step}</small>
                        </li>
                      );
                    })}
                  </ol>
                </article>
              )}

              {activeToolActivity?.decisionSummary && (
                <article className="decisionCard">
                  <span>Why this step</span>
                  <p>{activeToolActivity.decisionSummary}</p>
                  {activeToolActivity.inputSummary && (
                    <small>{activeToolActivity.inputSummary}</small>
                  )}
                </article>
              )}

              {toolActivities.length === 0 ? (
                <div className="activityEmpty">
                  <span aria-hidden="true"><i /><i /><i /></span>
                  <p>
                    June will show her plan, safe decision summaries, tool calls,
                    approvals, and verified outcomes here.
                  </p>
                </div>
              ) : (
                <ol className="toolTimeline">
                  {toolActivities.map((activity) => (
                    <li
                      className={`toolTimelineItem toolTimelineItem-${activity.status}`}
                      key={activity.invocationId}
                    >
                      <span className="toolTimelineGlyph">
                        <ToolGlyph tool={activity.tool} />
                      </span>
                      <span className="toolTimelineCopy">
                        <strong>{activityLabel(activity)}</strong>
                        <small>
                          <i aria-hidden="true" />
                          {toolStatusLabels[activity.status]}
                        </small>
                        <p>
                          {activity.status === "started"
                            ? activity.decisionSummary || toolAction(activity.tool)
                            : activity.resultSummary || activity.decisionSummary || toolStatusLabels[activity.status]}
                        </p>
                        {activity.inputSummary && (
                          <span className="toolTimelineInput">{activity.inputSummary}</span>
                        )}
                      </span>
                      <span className="toolTimelineMeta">
                        <span>{formatToolTiming(activity, activityNow)}</span>
                        {activity.status === "awaiting_approval" && (
                          <button
                            type="button"
                            onClick={() => setIsConnectionsOpen(true)}
                          >
                            Review
                          </button>
                        )}
                        {activity.status === "completed" && activity.artifactId && (
                          <button type="button" onClick={() => setIsArtifactsOpen(true)}>
                            Open file
                          </button>
                        )}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>

            <div className="presenceTrust">
              <span aria-hidden="true"><i /></span>
              <p><strong>Transparent by design</strong>Useful rationale, never private chain-of-thought.</p>
            </div>
          </aside>

          <div className="conversationWorkspace">
            <div className="workspaceHead">
              <div>
                <span>Conversation space</span>
                <h2>{messages.length ? "You and June" : "A new thought"}</h2>
              </div>
              <span className="turnCount"><i aria-hidden="true" />{messages.length} {messages.length === 1 ? "turn" : "turns"}</span>
            </div>

            <section className="stage" aria-live="polite">
              <div
                className={`conversation ${hasConversation ? "" : "conversationEmpty"}`}
                ref={messagesRef}
              >
            {!hasConversation && (
              <div className="emptyConversation">
                <span className="emptySignal" aria-hidden="true"><i /><i /><i /><i /><i /></span>
                <p className="emptyEyebrow">June is standing by.</p>
                <h1>Go ahead, Boss.</h1>
                <p>Talk like you would to an operator who already has the brief.</p>
                {pageContext?.present && (
                  <p className="pageContextHint">
                    Page ready: {pageContext.title || "Shared article"}. Ask June to explain it.
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
                  {message.role === "user" ? "You" : "June"}
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
                  {interim.role === "user" ? "You" : "June"}
                </span>
                <p className="chatBubble" dir="ltr">{interim.text}</p>
              </article>
            )}
            {isWaitingForAuren && !interim && (
              <article className="chatMessage assistant interimMessage">
                <span className="chatSpeaker">June</span>
                <div className="chatBubble thinkingBubble" aria-label="June is thinking">
                  <span className="ellipsis">
                    <i /><i /><i />
                  </span>
                </div>
              </article>
            )}
              </div>
            </section>

            <section className="dock">
          <button
            className="mic"
            onClick={() => void toggleMicrophone()}
            disabled={phase === "connecting"}
          >
            <span className="micPresence" aria-hidden="true"><i /><i /><i /></span>
            <span className="micLabel">
              <small>{phase === "idle" ? "Tap to begin" : "Voice session"}</small>
              <strong>{labels[phase]}</strong>
            </span>
            <span className="micAction" aria-hidden="true">{phase === "idle" ? "↗" : "•••"}</span>
          </button>

          {isTypeOpen && (
            <form className="typebar" onSubmit={sendTextMessage}>
              <label className="srOnly" htmlFor="message-input">Message June</label>
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
                <span>{isVoiceMuted ? "Voice off" : "June’s voice"}</span>
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
          ) : isMicMuted && phase !== "idle" && phase !== "connecting" ? (
            <p className="failure" role="status">
              <span className="failureIcon" aria-hidden="true">!</span>
              Microphone is off — tap the mic button, then speak.
            </p>
          ) : (
            <p className="tip">{notice}</p>
          )}
            </section>
          </div>
        </div>
      </main>
    </>
  );
}
