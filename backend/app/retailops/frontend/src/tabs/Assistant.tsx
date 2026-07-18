import { useState } from "react";
import { api, type Citation, type Principal } from "../api";

export function Assistant({ principal }: { principal: Principal }) {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState("");
  const [route, setRoute] = useState("");
  const [citations, setCitations] = useState<Citation[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const ask = async () => {
    setBusy(true);
    setError("");
    setAnswer("");
    try {
      const res = await api.ask(principal, q);
      setAnswer(res.answer);
      setRoute(res.route);
      setCitations(res.citations);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <h2>Assistant</h2>
      <input
        type="text"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Ask a document question, e.g. What is the stock discrepancy escalation process?"
        onKeyDown={(e) => e.key === "Enter" && q && ask()}
      />
      <div style={{ marginTop: 8 }}>
        <button className="primary" disabled={busy || !q} onClick={ask}>
          {busy ? "Thinking…" : "Ask"}
        </button>
        {route && <span className="muted"> route: {route}</span>}
      </div>
      {error && <p className="error">{error}</p>}
      {answer && (
        <div style={{ marginTop: 12 }}>
          <p>{answer}</p>
          {citations.length > 0 ? (
            <>
              <h2>Citations</h2>
              {citations.map((c, i) => (
                <div key={i} className="citation">
                  [{c.fused_rank ?? i + 1}] {c.excerpt}
                  <div className="muted">
                    {c.filename}
                    {c.page ? ` p.${c.page}` : ""}
                    {c.vector_score != null ? ` · vec ${c.vector_score.toFixed(3)}` : ""}
                    {c.lexical_score != null ? ` · lex ${c.lexical_score.toFixed(3)}` : ""}
                  </div>
                </div>
              ))}
            </>
          ) : (
            <p className="muted">No citations — the assistant will not fabricate an answer.</p>
          )}
        </div>
      )}
    </div>
  );
}
