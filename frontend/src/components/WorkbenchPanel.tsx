import React, { useCallback, useEffect, useState } from 'react';
import api from '../api/client';
import { useToast } from '../hooks/useToast';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

type QueueItem = {
  request_id: string;
  state: string;
  request?: {
    kind?: string;
    product_id?: string;
    dna_version?: string;
  };
  candidate?: {
    summary?: string;
    diff?: string;
    checksum?: string;
  };
  gate_report?: {
    passed?: boolean;
    gates?: Array<{ gate: string; passed: boolean; message?: string }>;
  };
  promotion?: {
    trust_tier?: string;
    output_dir?: string;
    product_dna_version?: string;
  };
  workbench_session?: {
    session_id?: string;
    status?: string;
  };
  audit_trail?: Array<{ kind: string; summary: string; at?: string }>;
};

/**
 * Build Mode workbench — operator tool (functional, not beautiful).
 * Queue → Run Workbench → candidate diff → gates → Promote via Packager (staging).
 */
export const WorkbenchPanel: React.FC = () => {
  const { addToast } = useToast();
  const [status, setStatus] = useState<{
    build_mode_enabled?: boolean;
    kimi_workbench_enabled?: boolean;
  } | null>(null);
  const [items, setItems] = useState<QueueItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [productRoot, setProductRoot] = useState('');
  const [busy, setBusy] = useState(false);

  const selected = items.find((i) => i.request_id === selectedId) || null;

  const refresh = useCallback(async () => {
    const st = await api.get('/workbench/status');
    setStatus(st.data);
    if (!st.data.build_mode_enabled) {
      setItems([]);
      return;
    }
    const q = await api.get('/workbench/queue');
    setItems(q.data.items || []);
  }, []);

  useEffect(() => {
    refresh().catch((err) =>
      addToast({
        type: 'error',
        title: 'Workbench',
        message: err.response?.data?.detail || err.message,
      })
    );
  }, [refresh, addToast]);

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
      await refresh();
      addToast({ type: 'success', title: label, message: 'Done.' });
    } catch (err: any) {
      addToast({
        type: 'error',
        title: label,
        message: err.response?.data?.detail || err.message,
      });
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="text-lg font-semibold text-gray-900">Build Mode workbench</h2>
        <p className="text-sm text-gray-600 mt-1">
          Operator tool: approved change-requests → sandboxed candidate → gates → packager
          staging/community. No deploy credentials. Flags default OFF.
        </p>
        <div className="mt-3 text-sm font-mono text-gray-700 space-y-1">
          <div>BUILD_MODE_ENABLED={String(status?.build_mode_enabled ?? '…')}</div>
          <div>KIMI_WORKBENCH_ENABLED={String(status?.kimi_workbench_enabled ?? '…')}</div>
        </div>
        <Button className="mt-3" variant="secondary" disabled={busy} onClick={() => run('Refresh', refresh)}>
          Refresh queue
        </Button>
      </Card>

      {!status?.build_mode_enabled && (
        <Card>
          <p className="text-sm text-amber-800">
            Build Mode is off. Set <code>BUILD_MODE_ENABLED=true</code> on the Factory backend to
            run workbench sessions.
          </p>
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <h3 className="font-medium text-gray-900">Queue</h3>
          <ul className="mt-2 divide-y divide-gray-200 max-h-96 overflow-auto">
            {items.length === 0 && (
              <li className="py-2 text-sm text-gray-500">No queue items.</li>
            )}
            {items.map((item) => (
              <li key={item.request_id}>
                <button
                  type="button"
                  className={`w-full text-left py-2 px-1 text-sm ${
                    selectedId === item.request_id ? 'bg-blue-50' : ''
                  }`}
                  onClick={() => setSelectedId(item.request_id)}
                >
                  <div className="font-mono text-xs text-gray-500">{item.request_id.slice(0, 8)}…</div>
                  <div>
                    {item.request?.kind || '?'} · {item.state}
                  </div>
                  <div className="text-xs text-gray-500">{item.request?.product_id}</div>
                </button>
              </li>
            ))}
          </ul>
        </Card>

        <Card>
          <h3 className="font-medium text-gray-900">Selected request</h3>
          {!selected && <p className="text-sm text-gray-500 mt-2">Select a queue item.</p>}
          {selected && (
            <div className="mt-2 space-y-3 text-sm">
              <div>
                <span className="font-medium">State:</span> {selected.state}
              </div>
              <div>
                <span className="font-medium">Session:</span>{' '}
                {selected.workbench_session?.status || '—'}{' '}
                {selected.workbench_session?.session_id
                  ? `(${selected.workbench_session.session_id.slice(0, 8)}…)`
                  : ''}
              </div>
              <label className="block text-xs font-medium text-gray-700">
                Product root (generated scaffold path)
                <input
                  className="mt-1 w-full border border-gray-300 rounded px-2 py-1 font-mono text-xs"
                  value={productRoot}
                  onChange={(e) => setProductRoot(e.target.value)}
                  placeholder="/path/to/generated/product"
                />
              </label>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={busy || selected.state !== 'approved'}
                  onClick={() =>
                    run('Run Workbench', async () => {
                      await api.post('/workbench/run', {
                        request_id: selected.request_id,
                        product_root: productRoot || undefined,
                        run_gates: true,
                      });
                    })
                  }
                >
                  Run Workbench
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy || selected.state !== 'candidate_ready'}
                  onClick={() =>
                    run('Re-run gates', async () => {
                      await api.post(`/workbench/gates/${selected.request_id}`);
                    })
                  }
                >
                  Re-run gates
                </Button>
                <Button
                  disabled={busy || selected.state !== 'gate_passed'}
                  onClick={() =>
                    run('Promote via Packager', async () => {
                      await api.post(`/workbench/promote/${selected.request_id}`, {
                        actor: 'owner',
                      });
                    })
                  }
                >
                  Promote via Packager (staging)
                </Button>
              </div>

              {selected.candidate && (
                <div>
                  <h4 className="font-medium">Candidate</h4>
                  <p className="text-xs text-gray-600">{selected.candidate.summary}</p>
                  <p className="text-xs font-mono text-gray-500">
                    checksum {selected.candidate.checksum?.slice(0, 16)}…
                  </p>
                  <pre className="mt-2 max-h-48 overflow-auto bg-gray-50 border p-2 text-xs whitespace-pre-wrap">
                    {selected.candidate.diff || '(empty diff)'}
                  </pre>
                </div>
              )}

              {selected.gate_report && (
                <div>
                  <h4 className="font-medium">
                    Gates — {selected.gate_report.passed ? 'PASSED' : 'FAILED'}
                  </h4>
                  <ul className="mt-1 space-y-1">
                    {(selected.gate_report.gates || []).map((g) => (
                      <li key={g.gate} className="text-xs font-mono">
                        [{g.passed ? 'ok' : 'FAIL'}] {g.gate}: {g.message}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {selected.promotion && (
                <div>
                  <h4 className="font-medium">Promotion</h4>
                  <p className="text-xs">
                    tier={selected.promotion.trust_tier} version=
                    {selected.promotion.product_dna_version}
                  </p>
                  <p className="text-xs font-mono break-all">{selected.promotion.output_dir}</p>
                </div>
              )}

              {selected.audit_trail && selected.audit_trail.length > 0 && (
                <div>
                  <h4 className="font-medium">Audit trail</h4>
                  <ul className="mt-1 max-h-40 overflow-auto space-y-1">
                    {selected.audit_trail.map((a, idx) => (
                      <li key={`${a.kind}-${idx}`} className="text-xs">
                        <span className="font-mono">{a.kind}</span> — {a.summary}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
};

export default WorkbenchPanel;
