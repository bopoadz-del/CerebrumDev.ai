import React, { useEffect, useState } from 'react';
import api from '../api/client';
import { useToast } from '../hooks/useToast';
import { Button } from './ui/Button';
import { Card } from './ui/Card';

interface DesignProductPanelProps {
  sessionId: string;
}

type ProductState = {
  mode: string;
  brief: string;
  blueprint: Record<string, unknown> | null;
  blueprint_yaml: string | null;
  plan: Record<string, unknown> | null;
  blueprint_approved: boolean;
  generation: Record<string, unknown> | null;
  last_error: string | null;
};

export const DesignProductPanel: React.FC<DesignProductPanelProps> = ({ sessionId }) => {
  const { addToast } = useToast();
  const [brief, setBrief] = useState(
    'Generate Cerebrum-Steward private estate operations intelligence'
  );
  const [state, setState] = useState<ProductState | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    const res = await api.get(`/sessions/${sessionId}/product`);
    setState(res.data);
    if (res.data.brief) setBrief(res.data.brief);
  };

  useEffect(() => {
    refresh().catch((err) =>
      addToast({
        type: 'error',
        title: 'Product design',
        message: err.response?.data?.detail || err.message,
      })
    );
  }, [sessionId]);

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

  const strategies =
    (state?.plan as { capabilities?: Array<{ capability_id: string; strategy: string }> })
      ?.capabilities || [];

  return (
    <div className="space-y-4">
      <Card>
        <h2 className="text-lg font-semibold text-gray-900">Design product</h2>
        <p className="text-sm text-gray-600 mt-1">
          Factory agent path: brief → blueprint → plan → approve → generate. Steward briefs use the
          golden blueprint and regenerate through CerebrumDev.ai — never hand-edit the output repo.
        </p>
        <label className="block mt-4 text-sm font-medium text-gray-700">Task brief</label>
        <textarea
          className="mt-1 w-full rounded-md border border-gray-300 p-3 text-sm min-h-[96px]"
          value={brief}
          onChange={(e) => setBrief(e.target.value)}
          disabled={busy}
        />
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            disabled={busy || !brief.trim()}
            onClick={() =>
              run('Draft blueprint', async () => {
                await api.post(`/sessions/${sessionId}/product/draft`, {
                  brief,
                  vertical_hint: 'estate',
                });
              })
            }
          >
            Draft blueprint
          </Button>
          <Button
            disabled={busy || !state?.blueprint}
            variant="secondary"
            onClick={() =>
              run('Plan capabilities', async () => {
                await api.post(`/sessions/${sessionId}/product/plan`);
              })
            }
          >
            Plan
          </Button>
          <Button
            disabled={busy || !state?.blueprint}
            variant="secondary"
            onClick={() =>
              run('Approve blueprint', async () => {
                await api.post(`/sessions/${sessionId}/product/approve`, { approve: true });
              })
            }
          >
            Approve
          </Button>
          <Button
            disabled={busy || !state?.blueprint_approved}
            onClick={() =>
              run('Generate product', async () => {
                await api.post(`/sessions/${sessionId}/product/generate`, {});
              })
            }
          >
            Generate
          </Button>
        </div>
        {state?.last_error && (
          <p className="mt-3 text-sm text-red-600">{state.last_error}</p>
        )}
      </Card>

      {state?.blueprint && (
        <Card>
          <h3 className="font-medium text-gray-900">
            Blueprint{' '}
            {state.blueprint_approved ? (
              <span className="text-green-600 text-sm">(approved)</span>
            ) : (
              <span className="text-amber-600 text-sm">(draft)</span>
            )}
          </h3>
          <pre className="mt-2 text-xs bg-gray-50 p-3 rounded overflow-auto max-h-64">
            {state.blueprint_yaml || JSON.stringify(state.blueprint, null, 2)}
          </pre>
        </Card>
      )}

      {strategies.length > 0 && (
        <Card>
          <h3 className="font-medium text-gray-900">Capability plan</h3>
          <ul className="mt-2 divide-y divide-gray-100">
            {strategies.map((c) => (
              <li key={c.capability_id} className="py-2 flex justify-between text-sm">
                <span className="font-mono text-gray-800">{c.capability_id}</span>
                <span className="text-blue-700 font-medium">{c.strategy}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {state?.generation && (
        <Card>
          <h3 className="font-medium text-gray-900">Generation</h3>
          <pre className="mt-2 text-xs bg-gray-50 p-3 rounded overflow-auto">
            {JSON.stringify(state.generation, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  );
};

export default DesignProductPanel;
