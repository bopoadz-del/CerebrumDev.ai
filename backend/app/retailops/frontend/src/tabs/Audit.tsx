import { useEffect, useState } from "react";
import { api, type Principal } from "../api";

export function Audit({ principal }: { principal: Principal }) {
  const [runs, setRuns] = useState<Awaited<ReturnType<typeof api.audit>>["runs"]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    api.audit(principal).then((d) => setRuns(d.runs)).catch((e) => setError(String(e)));
  }, [principal]);

  return (
    <div className="card">
      <h2>Audit trail</h2>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Action</th><th>User</th><th>Status</th><th>When</th>
            <th>Duration</th><th>Evidence</th><th>Request ID</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.request_id}>
              <td>{r.action_id}</td>
              <td>{r.user_id}</td>
              <td><span className={`badge ${r.status}`}>{r.status}</span></td>
              <td>{new Date(r.started_at).toLocaleString()}</td>
              <td>{r.duration_ms} ms</td>
              <td>{r.evidence_count}</td>
              <td className="muted">{r.request_id.slice(0, 12)}…</td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs.length === 0 && <p className="muted">No audit records yet.</p>}
    </div>
  );
}
