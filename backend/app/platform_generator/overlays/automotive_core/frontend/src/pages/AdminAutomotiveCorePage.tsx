import React, { useEffect, useState } from "react";

type PackStatus = {
  pack_id?: string;
  pack_version?: string | null;
  status?: string;
  harvest_timestamp?: string;
  build_timestamp?: string;
  source_families?: string[];
  record_count?: number;
  chunk_count?: number;
  embedding_identity?: Record<string, unknown>;
  vector_namespace?: string;
  evaluation_result?: { passed?: boolean; metrics?: Record<string, number> } | null;
  activation_status?: string;
};

const API_BASE = import.meta.env.VITE_API_URL || "";

async function adminFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

export default function AdminAutomotiveCorePage() {
  const [status, setStatus] = useState<PackStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = async () => {
    setError(null);
    const data = await adminFetch("/v1/admin/automotive-core/status");
    setStatus(data);
  };

  useEffect(() => {
    refresh().catch((err) => setError(String(err)));
  }, []);

  const run = async (action: string, path: string, body?: unknown) => {
    setBusy(action);
    setError(null);
    try {
      await adminFetch(path, {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
      });
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(null);
    }
  };

  const confirmRebuild = async () => {
    const typed = window.prompt('Type "REBUILD" to confirm destructive rebuild');
    if (typed !== "REBUILD") return;
    await run("build", "/v1/admin/automotive-core/build", { records: [], dry_run: false });
  };

  return (
    <main className="automotive-admin">
      <h1>Automotive Core Knowledge</h1>
      <p>Foundation pack status, evaluation, and activation controls.</p>
      {error && <p className="error">{error}</p>}
      {status && (
        <section>
          <dl>
            <dt>Pack</dt>
            <dd>{status.pack_id}</dd>
            <dt>Version</dt>
            <dd>{status.pack_version || "—"}</dd>
            <dt>Status</dt>
            <dd>{status.activation_status || status.status}</dd>
            <dt>Harvest</dt>
            <dd>{status.harvest_timestamp || "—"}</dd>
            <dt>Build</dt>
            <dd>{status.build_timestamp || "—"}</dd>
            <dt>Families</dt>
            <dd>{(status.source_families || []).join(", ") || "—"}</dd>
            <dt>Records / Chunks</dt>
            <dd>
              {status.record_count ?? 0} / {status.chunk_count ?? 0}
            </dd>
            <dt>Embedding</dt>
            <dd>{JSON.stringify(status.embedding_identity || {})}</dd>
            <dt>Namespace</dt>
            <dd>{status.vector_namespace}</dd>
            <dt>Evaluation</dt>
            <dd>
              {status.evaluation_result
                ? status.evaluation_result.passed
                  ? "passed"
                  : "failed"
                : "not run"}
            </dd>
          </dl>
        </section>
      )}
      <div className="actions">
        <button disabled={!!busy} onClick={confirmRebuild}>
          Build / Resume Pack
        </button>
        <button
          disabled={!!busy}
          onClick={() => run("verify", "/v1/admin/automotive-core/verify")}
        >
          Verify Pack
        </button>
        <button
          disabled={!!busy}
          onClick={() => run("evaluate", "/v1/admin/automotive-core/evaluate", {})}
        >
          Run Evaluation
        </button>
        <button
          disabled={!!busy}
          onClick={() => run("activate", "/v1/admin/automotive-core/activate", {})}
        >
          Activate Validated Version
        </button>
        <button
          disabled={!!busy}
          onClick={() => run("rollback", "/v1/admin/automotive-core/rollback")}
        >
          Rollback to Prior Version
        </button>
      </div>
      {busy && <p>Running: {busy}…</p>}
    </main>
  );
}
