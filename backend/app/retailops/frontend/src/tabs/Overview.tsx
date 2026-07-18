import { useEffect, useState } from "react";
import { api, type Principal } from "../api";

export function Overview({ principal }: { principal: Principal }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.overview>> | null>(null);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    api.overview(principal).then(setData).catch((e) => setError(String(e)));
  }, [principal]);

  if (error) return <div className="card error">{error}</div>;
  if (!data) return <div className="card muted">Loading…</div>;

  const recent = data.recent_actions[0];
  return (
    <div>
      <div className="card">
        <h2>Overview</h2>
        <div className="row">
          <div className="stat">
            <div className="n">{data.document_count}</div>
            <div className="l">Documents</div>
          </div>
          <div className="stat">
            <div className="n">{recent ? recent.status : "—"}</div>
            <div className="l">Most recent action</div>
          </div>
          <div className="stat">
            <div className="n">{String((data.system_health as { database?: string }).database ?? "?")}</div>
            <div className="l">Database health</div>
          </div>
        </div>
      </div>
      <div className="card">
        <h2>Recent actions</h2>
        {data.recent_actions.length === 0 && <p className="muted">No actions run yet.</p>}
        {data.recent_actions.map((a, i) => (
          <div key={i} className="citation">
            <span className={`badge ${a.status}`}>{a.status}</span> {a.action_id}
            <span className="muted"> — {new Date(a.started_at).toLocaleString()}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
