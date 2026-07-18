import { useState } from "react";
import type { Principal } from "./api";
import { Overview } from "./tabs/Overview";
import { Documents } from "./tabs/Documents";
import { Assistant } from "./tabs/Assistant";
import { Actions } from "./tabs/Actions";
import { Audit } from "./tabs/Audit";

const TABS = ["Overview", "Documents", "Assistant", "Actions", "Audit"] as const;
type Tab = (typeof TABS)[number];

export function App() {
  const [tab, setTab] = useState<Tab>("Overview");
  const [principal, setPrincipal] = useState<Principal>({
    userId: "u_ops_manager",
    tenantId: "northstar",
    projectId: "",
  });

  const set = (k: keyof Principal) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setPrincipal((p) => ({ ...p, [k]: e.target.value }));

  return (
    <div className="app">
      <header className="top">
        <div className="brand">CEREBRUM RetailOps</div>
        <div className="ctx">
          <input value={principal.userId} onChange={set("userId")} placeholder="user id" />
          <input value={principal.tenantId} onChange={set("tenantId")} placeholder="tenant id" />
          <input value={principal.projectId} onChange={set("projectId")} placeholder="project id" />
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "active" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </nav>

      {!principal.projectId && (
        <div className="card">
          <p className="muted">
            Enter a <strong>project id</strong> above to begin. Load the Northstar fixture
            pack via the backend, then paste a generated project id here.
          </p>
        </div>
      )}

      {principal.projectId && tab === "Overview" && <Overview principal={principal} />}
      {principal.projectId && tab === "Documents" && <Documents principal={principal} />}
      {principal.projectId && tab === "Assistant" && <Assistant principal={principal} />}
      {principal.projectId && tab === "Actions" && <Actions principal={principal} />}
      {principal.projectId && tab === "Audit" && <Audit principal={principal} />}
    </div>
  );
}
