import React, { useState } from 'react';
import api from '../api/client';

interface DeployPanelProps {
  sessionId: string;
}

interface DeployStatus {
  status: string;
  target: string;
  progress: number;
  url: string | null;
  api_key: string | null;
  message: string | null;
}

const DeployPanel: React.FC<DeployPanelProps> = ({ sessionId }) => {
  const [target, setTarget] = useState<'cloud' | 'edge'>('cloud');
  const [status, setStatus] = useState<DeployStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  const addLog = (msg: string) => setLogs((prev) => [...prev, msg]);

  const startDeploy = async () => {
    setLoading(true);
    setError(null);
    setLogs([]);
    addLog(`Preparing ${target} deployment...`);
    try {
      const res = await api.post(`/sessions/${sessionId}/deploy?target=${target}`);
      addLog(`Server: ${res.data.message || res.data.status}`);
      if (res.data.status === 'failed') {
        setError(res.data.message || 'Deployment failed');
      }
      pollStatus();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message);
      addLog(`Error: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const pollStatus = () => {
    const interval = setInterval(async () => {
      try {
        const res = await api.get(`/sessions/${sessionId}/deploy/status`);
        setStatus(res.data);
        if (res.data.message) addLog(res.data.message);
        if (['live', 'packaged', 'failed'].includes(res.data.status)) {
          clearInterval(interval);
        }
      } catch (err: any) {
        addLog(`Status poll error: ${err.message}`);
        clearInterval(interval);
      }
    }, 4000);
  };

  const downloadPackage = (variant: 'cloud' | 'edge' = 'cloud') => {
    window.open(`/v1/sessions/${sessionId}/deploy/package?variant=${variant}`, '_blank');
  };

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-4">
      <h2 className="text-xl font-semibold">Phase 5: Ship / Deploy</h2>
      <p className="text-gray-600">
        Package this session as a deployable Cerebrum-Blocks instance.
      </p>

      <div className="flex items-center space-x-4">
        <label className="font-medium">Target:</label>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value as 'cloud' | 'edge')}
          className="border rounded px-2 py-1"
        >
          <option value="cloud">Cloud (Render)</option>
          <option value="edge">Edge (Docker)</option>
        </select>
      </div>

      <button
        onClick={startDeploy}
        disabled={loading}
        className="w-full py-2 px-4 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
      >
        {loading ? 'Preparing...' : 'Prepare Deployment'}
      </button>

      {error && <div className="text-red-600">{error}</div>}

      {status && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>Status: <strong>{status.status}</strong></span>
            <span>Progress: {Math.round(status.progress * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded h-2">
            <div
              className="bg-green-500 h-2 rounded transition-all"
              style={{ width: `${status.progress * 100}%` }}
            />
          </div>

          {status.url && (
            <div>
              <span className="font-medium">Live URL: </span>
              <a href={status.url} target="_blank" rel="noreferrer" className="text-blue-600 underline">
                {status.url}
              </a>
            </div>
          )}

          {status.api_key && (
            <div className="text-sm text-gray-700">
              <span className="font-medium">API Key: </span>
              <code className="bg-gray-100 px-1 rounded">{status.api_key}</code>
            </div>
          )}
        </div>
      )}

      {status?.status === 'failed' || status?.status === 'packaged' ? (
        <div className="flex space-x-2">
          <button
            onClick={() => downloadPackage(target)}
            className="flex-1 py-2 px-4 bg-blue-600 text-white rounded hover:bg-blue-700"
          >
            Download Package
          </button>
          {target === 'cloud' && (
            <button
              onClick={() => window.open('https://dashboard.render.com/new/web-service', '_blank')}
              className="flex-1 py-2 px-4 bg-gray-800 text-white rounded hover:bg-gray-900"
            >
              Deploy to Render
            </button>
          )}
        </div>
      ) : null}

      {logs.length > 0 && (
        <div className="bg-gray-900 text-gray-100 p-3 rounded text-sm font-mono h-40 overflow-y-auto">
          {logs.map((log, i) => (
            <div key={i}>{`> ${log}`}</div>
          ))}
        </div>
      )}
    </div>
  );
};

export default DeployPanel;
