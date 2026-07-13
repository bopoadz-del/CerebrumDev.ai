import { useCallback, useEffect, useState } from "react";
import { api, type Principal } from "../api";

export function Documents({ principal }: { principal: Principal }) {
  const [docs, setDocs] = useState<Awaited<ReturnType<typeof api.documents>>["documents"]>([]);
  const [error, setError] = useState("");
  const [filename, setFilename] = useState("note.txt");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    api.documents(principal).then((d) => setDocs(d.documents)).catch((e) => setError(String(e)));
  }, [principal]);

  useEffect(refresh, [refresh]);

  const upload = async () => {
    setBusy(true);
    setError("");
    try {
      await api.ingest(principal, filename, text);
      setText("");
      refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="card">
        <h2>Upload a document</h2>
        <label>Filename</label>
        <input type="text" value={filename} onChange={(e) => setFilename(e.target.value)} />
        <label>Text content</label>
        <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Paste text to ingest…" />
        <div style={{ marginTop: 8 }}>
          <button className="primary" disabled={busy || !text} onClick={upload}>
            {busy ? "Ingesting…" : "Ingest"}
          </button>
        </div>
      </div>
      <div className="card">
        <h2>Sources</h2>
        {error && <p className="error">{error}</p>}
        <table>
          <thead>
            <tr><th>File</th><th>Status</th><th>Chunks</th><th>Role</th></tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.document_id}>
                <td>{d.filename}</td>
                <td><span className={`badge ${d.status}`}>{d.status}</span>{d.error ? ` ${d.error}` : ""}</td>
                <td>{d.chunk_count}</td>
                <td className="muted">{d.doc_role ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {docs.length === 0 && <p className="muted">No documents ingested yet.</p>}
      </div>
    </div>
  );
}
