"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

type Phase = "idle" | "connecting" | "listening" | "thinking" | "speaking";

type Message = {
  id: number;
  role: "user" | "assistant";
  text: string;
};

const labels: Record<Phase, string> = {
  idle: "Tap to begin",
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

export default function VoiceAgent() {
  const [phase, setPhase] = useState<Phase>("idle");
  const [notice, setNotice] = useState("Your microphone stays in the LiveKit session");
  const [messages, setMessages] = useState<Message[]>([]);
  const [interim, setInterim] = useState("");
  const roomRef = useRef<Room | null>(null);
  const nextId = useRef(1);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    if (!room) return;

    room.remoteParticipants.forEach((participant) => {
      participant.trackPublications.forEach((publication) => {
        publication.track?.detach().forEach((element) => element.remove());
      });
    });
    await room.disconnect();
    roomRef.current = null;
    setInterim("");
    setPhase("idle");
    setNotice("Session ended");
  }, []);

  const toggleSession = useCallback(async () => {
    if (roomRef.current) {
      await disconnect();
      return;
    }

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
        audioElement.style.display = "none";
        document.body.appendChild(audioElement);
      });

      room.on(RoomEvent.TrackUnsubscribed, (track) => {
        track.detach().forEach((element) => element.remove());
      });

      room.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
        const partial = segments
          .filter((segment) => !segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        setInterim(partial);

        const finalText = segments
          .filter((segment) => segment.final)
          .map((segment) => segment.text)
          .join(" ")
          .trim();
        if (!finalText) return;

        const role =
          participant?.identity === room.localParticipant.identity
            ? "user"
            : "assistant";
        setMessages((current) => [
          ...current,
          { id: nextId.current++, role, text: finalText },
        ]);
        setInterim("");
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
      setNotice("Connected — speak naturally");
    } catch (error) {
      roomRef.current = null;
      setPhase("idle");
      setNotice(error instanceof Error ? error.message : "Connection failed");
    }
  }, [disconnect]);

  useEffect(() => () => void disconnect(), [disconnect]);

  return (
    <main className="shell">
      <header>
        <div className="brand"><span className="brandMark" />Auren</div>
        <span className="localBadge">LOCAL UI</span>
      </header>

      <section className="hero">
        <p className="eyebrow">OPEN-SOURCE VOICE INTELLIGENCE</p>
        <h1>A natural conversation,<br />running on your stack.</h1>
        <p className="subhead">
          LiveKit transport, faster-whisper transcription, Qwen intelligence,
          and Chatterbox speech.
        </p>

        <button
          className={`orb ${phase}`}
          onClick={() => void toggleSession()}
          aria-label={roomRef.current ? "End voice session" : "Start voice session"}
        >
          <span className="orbCore" />
          <span className="orbRing ringOne" />
          <span className="orbRing ringTwo" />
        </button>
        <p className="phase">{labels[phase]}</p>
        <p className="notice">{notice}</p>
      </section>

      <section className="transcript" aria-live="polite">
        <div className="transcriptHeader">
          <h2>Conversation</h2>
          {messages.length > 0 && (
            <button onClick={() => setMessages([])}>Clear</button>
          )}
        </div>

        <div className="messages">
          {messages.length === 0 && !interim && (
            <p className="empty">Your live transcript will appear here.</p>
          )}
          {messages.map((message) => (
            <div className={`message ${message.role}`} key={message.id}>
              <span>{message.role === "user" ? "You" : "Auren"}</span>
              <p>{message.text}</p>
            </div>
          ))}
          {interim && (
            <div className="message user interim">
              <span>You</span><p>{interim}</p>
            </div>
          )}
        </div>
      </section>
    </main>
  );
}
