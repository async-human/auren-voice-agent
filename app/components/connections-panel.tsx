"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useState } from "react";

type GoogleConnection = {
  connected: boolean;
  account_email?: string | null;
};

type PendingAction = {
  id: string;
  tool: string;
  preview: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
  onActionResolved?: (id: string, decision: "confirm" | "reject") => void;
};

export default function ConnectionsPanel({
  open,
  onClose,
  onActionResolved,
}: Props) {
  const { getToken } = useAuth();
  const [google, setGoogle] = useState<GoogleConnection | null>(null);
  const [pending, setPending] = useState<PendingAction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apiBase = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

  const refresh = useCallback(async () => {
    if (!apiBase) {
      setError("NEXT_PUBLIC_API_URL is not configured");
      return;
    }
    try {
      const token = await getToken();
      if (!token) return;
      const headers = { Authorization: `Bearer ${token}` };
      const [connectionsRes, pendingRes] = await Promise.all([
        fetch(`${apiBase}/v1/connections`, { headers }),
        fetch(`${apiBase}/v1/actions/pending`, { headers }),
      ]);
      if (connectionsRes.ok) {
        const body = await connectionsRes.json();
        setGoogle(body.google);
      }
      if (pendingRes.ok) {
        const body = await pendingRes.json();
        setPending(body.actions || []);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load connections");
    }
  }, [apiBase, getToken]);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  if (!open) return null;

  async function connectGoogle() {
    setBusy(true);
    try {
      const token = await getToken();
      const response = await fetch(`${apiBase}/v1/connections/google/start`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const body = await response.json();
      if (!response.ok || !body.authorize_url) {
        throw new Error(body.detail || "Could not start Google connect");
      }
      window.location.href = body.authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Connect failed");
      setBusy(false);
    }
  }

  async function disconnectGoogle() {
    setBusy(true);
    try {
      const token = await getToken();
      await fetch(`${apiBase}/v1/connections/google`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Disconnect failed");
    } finally {
      setBusy(false);
    }
  }

  async function resolveAction(id: string, decision: "confirm" | "reject") {
    setBusy(true);
    try {
      const token = await getToken();
      const response = await fetch(`${apiBase}/v1/actions/${id}/${decision}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Action could not be resolved");
      }
      onActionResolved?.(id, decision);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="memoryPanel" role="dialog" aria-label="Connections">
      <div className="memoryHead">
        <div>
          <p className="memoryEyebrow">Connections</p>
          <h2>Accounts & approvals</h2>
        </div>
        <button type="button" className="leave" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="memoryLead">
        Connect Google so Auren can schedule meetings and send email after your
        confirmation — not just tell you how.
      </p>

      {error && <p className="failure">{error}</p>}

      <section className="connectionBlock">
        <h3>Google</h3>
        {google?.connected ? (
          <>
            <p>Connected as {google.account_email || "Google account"}</p>
            <button type="button" disabled={busy} onClick={() => void disconnectGoogle()}>
              Disconnect
            </button>
          </>
        ) : (
          <button type="button" disabled={busy} onClick={() => void connectGoogle()}>
            Connect Google Calendar & Gmail
          </button>
        )}
      </section>

      <section className="connectionBlock">
        <h3>Pending approvals</h3>
        {pending.length === 0 ? (
          <p>No actions waiting.</p>
        ) : (
          <ul className="memoryList">
            {pending.map((action) => (
              <li key={action.id}>
                <p>
                  <strong>{action.tool}</strong>
                  <br />
                  {action.preview}
                </p>
                <div className="approvalRow">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void resolveAction(action.id, "confirm")}
                  >
                    Confirm
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void resolveAction(action.id, "reject")}
                  >
                    Reject
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
