"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";

type MemoryItem = {
  id: string;
  content: string;
  created_at?: string | null;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function MemoryPanel({ open, onClose }: Props) {
  const { getToken } = useAuth();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forgettingId, setForgettingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
    if (!apiBaseUrl) {
      setError("NEXT_PUBLIC_API_URL is not configured");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      if (!token) {
        setError("Sign in to view memories");
        return;
      }
      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/memory`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(
          response.status === 404
            ? "Memory API not found. Restart the local API so it loads the Phase 1 routes."
            : `Could not load memories (${response.status}). ${detail.slice(0, 160)}`,
        );
      }
      const body = (await response.json()) as { memories: MemoryItem[] };
      setMemories(body.memories);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load memories");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    if (open) {
      void load();
    }
  }, [open, load]);

  const forget = useCallback(
    async (memoryId: string) => {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) return;
      setForgettingId(memoryId);
      try {
        const token = await getToken();
        if (!token) return;
        const response = await fetch(
          `${apiBaseUrl.replace(/\/$/, "")}/v1/memory/${memoryId}`,
          {
            method: "DELETE",
            headers: { Authorization: `Bearer ${token}` },
          },
        );
        if (!response.ok) {
          throw new Error("Could not forget that memory");
        }
        setMemories((current) => current.filter((item) => item.id !== memoryId));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not forget that memory");
      } finally {
        setForgettingId(null);
      }
    },
    [getToken],
  );

  if (!open) return null;

  return (
    <aside className="memoryPanel" aria-label="What Auren remembers">
      <div className="memoryHead">
        <div>
          <p className="memoryEyebrow">Consent & memory</p>
          <h2>What Auren remembers</h2>
        </div>
        <button type="button" className="leave" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="memoryLead">
        These facts persist across conversations. Forget anything you do not want kept.
      </p>
      {loading && <p className="tip">Loading…</p>}
      {error && (
        <p className="failure" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && memories.length === 0 && (
        <p className="tip">Nothing stored yet. Tell Auren something to remember.</p>
      )}
      <ul className="memoryList">
        {memories.map((memory) => (
          <li key={memory.id}>
            <p>{memory.content}</p>
            <button
              type="button"
              onClick={() => void forget(memory.id)}
              disabled={forgettingId === memory.id}
            >
              {forgettingId === memory.id ? "Forgetting…" : "Forget"}
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
