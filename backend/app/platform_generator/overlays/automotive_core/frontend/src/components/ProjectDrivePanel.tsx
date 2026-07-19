import React, { useState } from "react";

type DriveStatus = {
  connected: boolean;
  folder_name?: string | null;
  sync_status?: string | null;
};

const API_BASE = import.meta.env.VITE_API_URL || "";

export default function ProjectDrivePanel({ projectId }: { projectId: string }) {
  const [status, setStatus] = useState<DriveStatus>({ connected: false });
  const [error, setError] = useState<string | null>(null);

  const call = async (path: string, method = "POST") => {
    setError(null);
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      credentials: "include",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      setError(await res.text());
      return null;
    }
    return res.json();
  };

  return (
    <section className="project-drive-panel">
      <h2>Google Drive</h2>
      <p>Connect a folder to sync private project documents into client_private.</p>
      {error && <p className="error">{error}</p>}
      <p>Status: {status.connected ? "connected" : "disconnected"}</p>
      {status.folder_name && <p>Folder: {status.folder_name}</p>}
      {status.sync_status && <p>Sync: {status.sync_status}</p>}
      <div className="actions">
        <button
          onClick={async () => {
            const data = await call(`/v1/projects/${projectId}/drive/bind-folder`);
            if (data) setStatus({ connected: true, folder_name: data.folder_name || "Selected folder" });
          }}
        >
          Connect Google Drive
        </button>
        <button
          onClick={async () => {
            const data = await call(`/v1/projects/${projectId}/drive/sync`);
            if (data) setStatus((s) => ({ ...s, sync_status: data.status || "syncing" }));
          }}
        >
          Sync Folder
        </button>
        <button
          onClick={async () => {
            await call(`/v1/projects/${projectId}/drive/sync-status`, "GET");
            setStatus((s) => ({ ...s, sync_status: "idle" }));
          }}
        >
          Refresh Sync Status
        </button>
      </div>
    </section>
  );
}
