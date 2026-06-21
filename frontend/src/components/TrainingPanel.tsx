import React, { useEffect, useState } from 'react';
import api from '../api/client';

interface TrainingPanelProps {
  sessionId: string;
  onComplete: () => void;
}

interface TrainingStatus {
  status: string;
  progress: number;
  fine_tuned_model_id: string | null;
  error: string | null;
  dataset_size: number;
}

const EXAMPLE_TEXT = `Q: What is the primary material used in this project?
A: Reinforced concrete.

Q: Who approves change orders?
A: The site engineer and project manager.`;

const TrainingPanel: React.FC<TrainingPanelProps> = ({ sessionId, onComplete }) => {
  const [rawText, setRawText] = useState(EXAMPLE_TEXT);
  const [pairs, setPairs] = useState<{ question: string; answer: string }[]>([]);
  const [status, setStatus] = useState<TrainingStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const parsePairs = (text: string): { question: string; answer: string }[] => {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) {
        return parsed
          .map((item: any) => ({
            question: String(item.question || item.q || '').trim(),
            answer: String(item.answer || item.a || '').trim(),
          }))
          .filter((p) => p.question && p.answer);
      }
    } catch {
      // Not JSON — fall through to Q: / A: parser.
    }

    const lines = text.split('\n');
    const result: { question: string; answer: string }[] = [];
    let currentQuestion = '';
    let currentAnswer = '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      if (/^[Qq]:\s*/.test(trimmed)) {
        if (currentQuestion && currentAnswer) {
          result.push({ question: currentQuestion, answer: currentAnswer });
        }
        currentQuestion = trimmed.replace(/^[Qq]:\s*/, '');
        currentAnswer = '';
      } else if (/^[Aa]:\s*/.test(trimmed)) {
        currentAnswer = trimmed.replace(/^[Aa]:\s*/, '');
      }
    }
    if (currentQuestion && currentAnswer) {
      result.push({ question: currentQuestion, answer: currentAnswer });
    }
    return result;
  };

  const handleParse = () => {
    const parsed = parsePairs(rawText);
    setPairs(parsed);
    setSaved(false);
    setError(null);
  };

  const saveData = async () => {
    setLoading(true);
    setError(null);
    try {
      await api.post(`/sessions/${sessionId}/train/data`, {
        training_data: pairs,
        training_enabled: true,
      });
      setSaved(true);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  const startTraining = async () => {
    if (pairs.length < 10) {
      setError(`You need at least 10 Q&A pairs. Found ${pairs.length}.`);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      await saveData();
      const res = await api.post(`/sessions/${sessionId}/train`);
      setStatus(res.data);
      pollStatus();
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  const pollStatus = () => {
    const interval = setInterval(async () => {
      try {
        const res = await api.get(`/sessions/${sessionId}/train/status`);
        setStatus(res.data);
        if (['succeeded', 'failed', 'idle'].includes(res.data.status)) {
          clearInterval(interval);
          if (res.data.status === 'succeeded') {
            onComplete();
          }
        }
      } catch (err: any) {
        setError(err.response?.data?.detail || err.message);
        clearInterval(interval);
      }
    }, 5000);
  };

  useEffect(() => {
    handleParse();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-4">
      <h2 className="text-xl font-semibold">Phase 4: Tinker – Fine-Tune Your Model</h2>
      <p className="text-gray-600">
        Paste question/answer pairs below (use <code className="bg-gray-100 px-1 rounded">Q:</code> and{' '}
        <code className="bg-gray-100 px-1 rounded">A:</code> prefixes), or paste a JSON array. Then start a
        Cloudflare fine-tune job.
      </p>

      <textarea
        value={rawText}
        onChange={(e) => {
          setRawText(e.target.value);
          handleParse();
        }}
        rows={10}
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm font-mono"
        placeholder="Q: ...&#10;A: ..."
      />

      <div className="flex justify-between text-sm text-gray-700">
        <span>Parsed pairs: <strong>{pairs.length}</strong></span>
        <span className={saved ? 'text-green-600' : 'text-gray-500'}>
          {saved ? 'Saved ✓' : 'Not saved yet'}
        </span>
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      <div className="flex space-x-2">
        <button
          onClick={saveData}
          disabled={loading || pairs.length === 0}
          className="flex-1 py-2 px-4 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? 'Saving...' : 'Save Q&A Pairs'}
        </button>
        <button
          onClick={startTraining}
          disabled={loading || pairs.length < 10}
          className="flex-1 py-2 px-4 bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-50"
        >
          {loading ? 'Starting...' : 'Start Fine-Tune'}
        </button>
        <button
          onClick={onComplete}
          className="flex-1 py-2 px-4 bg-gray-200 text-gray-800 rounded hover:bg-gray-300"
        >
          Skip Fine-Tuning
        </button>
      </div>

      {status && (
        <div className="space-y-2 pt-2 border-t">
          <div className="flex justify-between text-sm">
            <span>
              Status: <strong className="capitalize">{status.status}</strong>
            </span>
            <span>Progress: {Math.round((status.progress || 0) * 100)}%</span>
          </div>
          <div className="w-full bg-gray-200 rounded h-2">
            <div
              className="bg-purple-500 h-2 rounded transition-all"
              style={{ width: `${(status.progress || 0) * 100}%` }}
            />
          </div>
          {status.fine_tuned_model_id && (
            <div className="text-sm text-green-700">
              <span className="font-medium">Fine-tuned model: </span>
              <code className="bg-gray-100 px-1 rounded">{status.fine_tuned_model_id}</code>
            </div>
          )}
          {status.error && <div className="text-sm text-red-600">{status.error}</div>}
        </div>
      )}
    </div>
  );
};

export default TrainingPanel;
