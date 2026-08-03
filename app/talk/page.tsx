"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@clerk/nextjs";
import VoiceAgent from "../components/voice-agent";

export default function TalkPage() {
  const router = useRouter();
  const { isLoaded, isSignedIn } = useAuth();

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.replace("/sign-in");
    }
  }, [isLoaded, isSignedIn, router]);

  if (!isLoaded || !isSignedIn) {
    return (
      <div className="talkGate">
        <div className="breath" aria-hidden="true" />
        <div className="vignette" aria-hidden="true" />
        <p className="tip">Opening your studio…</p>
      </div>
    );
  }

  return (
    <div className="talkShell">
      <VoiceAgent />
    </div>
  );
}
