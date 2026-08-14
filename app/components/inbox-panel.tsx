"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";

type NotificationItem = {
  id: string;
  kind: string;
  title: string;
  body: string;
  status: string;
  created_at?: string | null;
};

type DeliverySettings = {
  timezone_name: string;
  quiet_hours_start: string | null;
  quiet_hours_end: string | null;
  max_interruptions_per_day: number;
  spoken_delivery_enabled: boolean;
  pod_wake_enabled: boolean;
  pod_wake_available: boolean;
  morning_brief_enabled: boolean;
  morning_brief_hour: number;
};

type Props = {
  open: boolean;
  onClose: () => void;
  onUnreadChange?: (count: number) => void;
};

function kindLabel(kind: string): string {
  if (kind === "reminder") return "Reminder";
  if (kind === "follow_up") return "Follow-up";
  if (kind === "email_reply_check") return "Email check";
  if (kind === "morning_brief") return "Morning brief";
  return kind.split("_").join(" ");
}

export default function InboxPanel({ open, onClose, onUnreadChange }: Props) {
  const { getToken } = useAuth();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [settings, setSettings] = useState<DeliverySettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const apiBase = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

  const load = useCallback(async () => {
    if (!apiBase) {
      setError("NEXT_PUBLIC_API_URL is not configured");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      if (!token) {
        setError("Sign in to view your inbox");
        return;
      }
      const headers = { Authorization: `Bearer ${token}` };
      const [inboxRes, settingsRes] = await Promise.all([
        fetch(`${apiBase}/v1/notifications`, { headers, cache: "no-store" }),
        fetch(`${apiBase}/v1/settings`, { headers, cache: "no-store" }),
      ]);
      if (!inboxRes.ok) {
        throw new Error(`Could not load inbox (${inboxRes.status})`);
      }
      const body = (await inboxRes.json()) as {
        notifications: NotificationItem[];
        unread_count: number;
      };
      setItems(body.notifications);
      setUnreadCount(body.unread_count);
      onUnreadChange?.(body.unread_count);
      if (settingsRes.ok) {
        setSettings((await settingsRes.json()) as DeliverySettings);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load inbox");
    } finally {
      setLoading(false);
    }
  }, [apiBase, getToken, onUnreadChange]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const act = useCallback(
    async (path: string) => {
      if (!apiBase) return;
      const token = await getToken();
      if (!token) return;
      const response = await fetch(`${apiBase}${path}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) throw new Error("Could not update that item");
      await load();
    },
    [apiBase, getToken, load],
  );

  const saveSettings = useCallback(
    async (patch: Partial<DeliverySettings>) => {
      if (!apiBase || !settings) return;
      setSaving(true);
      try {
        const token = await getToken();
        if (!token) return;
        const response = await fetch(`${apiBase}/v1/settings`, {
          method: "PUT",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(patch),
        });
        if (!response.ok) {
          const detail = await response.text();
          throw new Error(detail.slice(0, 180) || "Could not save settings");
        }
        setSettings((await response.json()) as DeliverySettings);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not save settings");
      } finally {
        setSaving(false);
      }
    },
    [apiBase, getToken, settings],
  );

  if (!open) return null;

  return (
    <aside className="memoryPanel" aria-label="June inbox">
      <div className="memoryHead">
        <div>
          <p className="memoryEyebrow">Proactive delivery</p>
          <h2>Inbox</h2>
        </div>
        <button type="button" className="leave" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="memoryLead">
        Reminders and follow-ups land here even when the voice worker is idle.
        {unreadCount > 0 ? ` ${unreadCount} unread.` : ""}
      </p>
      {loading && <p className="tip">Loading…</p>}
      {error && (
        <p className="failure" role="alert">
          {error}
        </p>
      )}
      {!loading && items.length === 0 && (
        <p className="tip">Nothing waiting. Ask June to remind you in a minute to test this.</p>
      )}
      <ul className="memoryList">
        {items.map((item) => (
          <li key={item.id}>
            <div>
              <p className="inboxKind">{kindLabel(item.kind)}</p>
              <p>
                <strong>{item.title}</strong>
              </p>
              <p>{item.body}</p>
            </div>
            <div className="approvalRow">
              {item.status === "unread" && (
                <button type="button" onClick={() => void act(`/v1/notifications/${item.id}/read`)}>
                  Read
                </button>
              )}
              {item.status !== "dismissed" && (
                <button
                  type="button"
                  onClick={() => void act(`/v1/notifications/${item.id}/dismiss`)}
                >
                  Dismiss
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {unreadCount > 0 && (
        <button
          type="button"
          className="leave"
          onClick={() => void act("/v1/notifications/read-all")}
        >
          Mark all read
        </button>
      )}

      {settings && (
        <div className="connectionBlock">
          <h3>When June may interrupt</h3>
          <p>
            Inbox is always written. Spoken delivery and pod wake stay inside quiet hours
            and the daily cap.
          </p>
          <label className="settingsRow">
            <span>Timezone</span>
            <input
              value={settings.timezone_name}
              onChange={(event) =>
                setSettings({ ...settings, timezone_name: event.target.value })
              }
              onBlur={() => void saveSettings({ timezone_name: settings.timezone_name })}
            />
          </label>
          <label className="settingsRow">
            <span>Quiet hours</span>
            <span className="settingsPair">
              <input
                placeholder="22:00"
                value={settings.quiet_hours_start || ""}
                onChange={(event) =>
                  setSettings({ ...settings, quiet_hours_start: event.target.value || null })
                }
                onBlur={() =>
                  void saveSettings({ quiet_hours_start: settings.quiet_hours_start })
                }
              />
              <input
                placeholder="07:00"
                value={settings.quiet_hours_end || ""}
                onChange={(event) =>
                  setSettings({ ...settings, quiet_hours_end: event.target.value || null })
                }
                onBlur={() =>
                  void saveSettings({ quiet_hours_end: settings.quiet_hours_end })
                }
              />
            </span>
          </label>
          <label className="settingsRow">
            <span>Daily spoken cap</span>
            <input
              type="number"
              min={0}
              max={50}
              value={settings.max_interruptions_per_day}
              onChange={(event) =>
                setSettings({
                  ...settings,
                  max_interruptions_per_day: Number(event.target.value),
                })
              }
              onBlur={() =>
                void saveSettings({
                  max_interruptions_per_day: settings.max_interruptions_per_day,
                })
              }
            />
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.spoken_delivery_enabled}
              onChange={(event) =>
                void saveSettings({ spoken_delivery_enabled: event.target.checked })
              }
              disabled={saving}
            />
            Speak due items at the start of the next call
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.pod_wake_enabled}
              onChange={(event) =>
                void saveSettings({ pod_wake_enabled: event.target.checked })
              }
              disabled={saving || !settings.pod_wake_available}
            />
            Wake the RunPod worker for spoken delivery
            {!settings.pod_wake_available && " (set RUNPOD_API_KEY and RUNPOD_POD_ID on the API)"}
          </label>
          <label className="settingsCheck">
            <input
              type="checkbox"
              checked={settings.morning_brief_enabled}
              onChange={(event) =>
                void saveSettings({ morning_brief_enabled: event.target.checked })
              }
              disabled={saving}
            />
            Morning brief at {String(settings.morning_brief_hour).padStart(2, "0")}:00
          </label>
          <label className="settingsRow">
            <span>Brief hour</span>
            <input
              type="number"
              min={0}
              max={23}
              value={settings.morning_brief_hour}
              onChange={(event) =>
                setSettings({
                  ...settings,
                  morning_brief_hour: Number(event.target.value),
                })
              }
              onBlur={() =>
                void saveSettings({ morning_brief_hour: settings.morning_brief_hour })
              }
            />
          </label>
        </div>
      )}
    </aside>
  );
}
