import { useState, useEffect, useCallback, useRef } from 'react';
import api from '../api/client';
import {
  SessionState,
  SessionConfig,
  defaultSessionState,
} from '../types/session';

const storageKey = (id: string) => `cerebrumdev:session:${id}`;

function loadPersistedState(sessionId: string): SessionState | null {
  try {
    const raw = localStorage.getItem(storageKey(sessionId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.session_id === sessionId) return parsed as SessionState;
  } catch {
    // ignore corrupt storage
  }
  return null;
}

function persistState(state: SessionState) {
  try {
    localStorage.setItem(storageKey(state.session_id), JSON.stringify(state));
  } catch {
    // ignore storage errors
  }
}

export function useSession(sessionId: string) {
  const [state, setState] = useState<SessionState>(() => {
    const persisted = loadPersistedState(sessionId);
    return persisted || defaultSessionState(sessionId);
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const createdRef = useRef(false);

  // Persist state on every change
  useEffect(() => {
    persistState(state);
  }, [state]);

  // Ensure backend session exists and sync state
  useEffect(() => {
    let cancelled = false;

    async function init() {
      setLoading(true);
      setError(null);
      try {
        const res = await api.get(`/sessions/${sessionId}`);
        if (!cancelled) {
          setState((prev) => ({
            ...prev,
            ...res.data,
            // Preserve local UI defaults if backend returns empty/defaults
            config: res.data.config || prev.config,
            upload: res.data.upload || prev.upload,
            deployment: res.data.deployment || prev.deployment,
            training_job: res.data.training_job || prev.training_job,
          }));
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err.response?.data?.detail || err.message || 'Failed to load session');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    init();
    createdRef.current = true;

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const updateConfig = useCallback(async (config: SessionConfig) => {
    setState((prev) => ({ ...prev, config }));
    try {
      await api.post(`/sessions/${sessionId}/config`, config);
      return true;
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to save config');
      return false;
    }
  }, [sessionId]);

  const refreshState = useCallback(async () => {
    try {
      const res = await api.get(`/sessions/${sessionId}`);
      setState((prev) => ({
        ...prev,
        ...res.data,
        config: res.data.config || prev.config,
        upload: res.data.upload || prev.upload,
        deployment: res.data.deployment || prev.deployment,
        training_job: res.data.training_job || prev.training_job,
      }));
      return res.data as SessionState;
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to refresh session');
      return null;
    }
  }, [sessionId]);

  const setUploadComplete = useCallback(() => {
    setState((prev) => ({
      ...prev,
      upload: { ...prev.upload, status: 'completed', progress: 1 },
    }));
  }, []);

  const approveChain = useCallback(() => {
    setState((prev) => ({ ...prev, chain_approved: true, phase: 4 }));
  }, []);

  const setTrainingComplete = useCallback(() => {
    setState((prev) => ({
      ...prev,
      training_job: { ...prev.training_job, status: 'completed', progress: 1 },
    }));
  }, []);

  const patchState = useCallback((patch: Partial<SessionState>) => {
    setState((prev) => ({ ...prev, ...patch }));
  }, []);

  return {
    state,
    loading,
    error,
    setError,
    updateConfig,
    refreshState,
    setUploadComplete,
    approveChain,
    setTrainingComplete,
    patchState,
  };
}
