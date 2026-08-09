"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";

type ArtifactItem = {
  id: string;
  kind: string;
  format: string;
  title: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
};

type Props = {
  open: boolean;
  onClose: () => void;
};

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function ArtifactsPanel({ open, onClose }: Props) {
  const { getToken } = useAuth();
  const [artifacts, setArtifacts] = useState<ArtifactItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

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
      if (!token) throw new Error("Sign in to view generated files");
      const response = await fetch(`${apiBaseUrl.replace(/\/$/, "")}/v1/artifacts`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store",
      });
      if (!response.ok) throw new Error(`Could not load files (${response.status})`);
      const body = (await response.json()) as { artifacts: ArtifactItem[] };
      setArtifacts(body.artifacts);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load files");
    } finally {
      setLoading(false);
    }
  }, [getToken]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const download = useCallback(
    async (artifact: ArtifactItem) => {
      const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL;
      if (!apiBaseUrl) return;
      setDownloadingId(artifact.id);
      setError(null);
      try {
        const token = await getToken();
        if (!token) throw new Error("Sign in to download generated files");
        const response = await fetch(
          `${apiBaseUrl.replace(/\/$/, "")}/v1/artifacts/${artifact.id}/download`,
          { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
        );
        if (!response.ok) throw new Error(`Could not download file (${response.status})`);
        const url = URL.createObjectURL(await response.blob());
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = artifact.filename;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 0);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not download file");
      } finally {
        setDownloadingId(null);
      }
    },
    [getToken],
  );

  if (!open) return null;

  return (
    <aside className="memoryPanel artifactsPanel" aria-label="Generated files">
      <div className="memoryHead">
        <div>
          <p className="memoryEyebrow">Private workspace</p>
          <h2>Generated files</h2>
        </div>
        <button type="button" className="leave" onClick={onClose}>Close</button>
      </div>
      <p className="memoryLead">
        Documents Auren created for you. Downloads are authenticated and private to your account.
      </p>
      {loading && <p className="tip">Loading…</p>}
      {error && <p className="failure" role="alert">{error}</p>}
      {!loading && !error && artifacts.length === 0 && (
        <p className="tip">No files yet. Ask Auren to create a report, spreadsheet, or presentation.</p>
      )}
      <ul className="artifactList">
        {artifacts.map((artifact) => (
          <li key={artifact.id}>
            <span className="artifactFormat" aria-hidden="true">{artifact.format}</span>
            <span className="artifactCopy">
              <strong>{artifact.title}</strong>
              <small>{artifact.filename} · {formatBytes(artifact.size_bytes)}</small>
            </span>
            <button
              type="button"
              onClick={() => void download(artifact)}
              disabled={downloadingId === artifact.id}
            >
              {downloadingId === artifact.id ? "Saving…" : "Download"}
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
