"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";

type MemoryItem = {
  id: string;
  content: string;
  created_at?: string | null;
  source?: string;
};

type MemorySettings = {
  autonomous_memory_enabled: boolean;
  semantic_memory_enabled: boolean;
  procedural_memory_enabled: boolean;
  prospective_memory_enabled: boolean;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

export default function MemoryPanel({ open, onClose }: Props) {
  const { getToken } = useAuth();
  const [memories, setMemories] = useState<MemoryItem[]>([]);
  const [settings, setSettings] = useState<MemorySettings | null>(null);
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
      const [memoryRes, settingsRes] = await Promise.all([
        fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/memory`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/settings`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ]);
      if (!memoryRes.ok) {
        const detail = await memoryRes.text();
        throw new Error(
          memoryRes.status === 404
            ? "Memory API not found. Restart the local API so it loads the Phase 1 routes."
            : `Could not load memories (${memoryRes.status}). ${detail.slice(0, 160)}`,
        );
      }
      const body = (await memoryRes.json()) as { memories: MemoryItem[] };
      setMemories(body.memories);
      if (settingsRes.ok) {
        setSettings((await settingsRes.json()) as MemorySettings);
      }
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

  const saveSettings = useCallback(
    async (patch: Partial<MemorySettings>) => {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) return;
      try {
        const token = await getToken();
        if (!token) return;
        const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/settings`, {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(patch),
        });
        if (!response.ok) throw new Error("Could not save memory settings");
        setSettings((await response.json()) as MemorySettings);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not save memory settings");
      }
    },
    [getToken],
  );

  if (!open) return null;

  return (
    <aside className="memoryPanel" aria-label="What June remembers">
      <div className="memoryHead">
        <div>
          <p className="memoryEyebrow">Consent & memory</p>
          <h2>What June remembers</h2>
        </div>
        <button type="button" className="leave" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="memoryLead">
        These facts persist across conversations. Forget anything you do not want kept.
        Autonomous extraction stays off until you opt in.
      </p>
      {settings && (
        <div className="connectionBlock">
          <h3>What June may store on her own</h3>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.autonomous_memory_enabled}
              onChange={(event) =>
                void saveSettings({ autonomous_memory_enabled: event.target.checked })
              }
            />
            Remember useful facts without me saying “remember this”
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.semantic_memory_enabled}
              onChange={(event) =>
                void saveSettings({ semantic_memory_enabled: event.target.checked })
              }
            />
            Facts and preferences
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.procedural_memory_enabled}
              onChange={(event) =>
                void saveSettings({ procedural_memory_enabled: event.target.checked })
              }
            />
            Recurring routines (kept as candidates until confirmed)
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.prospective_memory_enabled}
              onChange={(event) =>
                void saveSettings({ prospective_memory_enabled: event.target.checked })
              }
            />
            Turn “I’ll follow up” into background jobs
          </label>
        </div>
      )}
      {loading && <p className="tip">Loading…</p>}
      {error && (
        <p className="failure" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && memories.length === 0 && (
        <p className="tip">Nothing stored yet. Tell June something to remember.</p>
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
