import axios from 'axios';
import { clearAuth, getLoginToken } from './auth';

export const API_KEY = import.meta.env.VITE_API_KEY || '';

// In production, the frontend calls the backend directly. In local dev, Vite can
// proxy /v1 when VITE_API_URL is not set.
export const API_BASE_URL = import.meta.env.VITE_API_URL
  ? `${import.meta.env.VITE_API_URL}/v1`
  : '/v1';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    ...(API_KEY ? { 'X-API-Key': API_KEY } : {}),
  },
});

// A logged-in user's token wins over the shared key — identity per request.
api.interceptors.request.use((config) => {
  const token = getLoginToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
    delete config.headers['X-API-Key'];
  }
  return config;
});

// Expired/revoked user token → drop it and return to the login page. Shared-key
// 401s (no user token) are surfaced to the caller as before.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error?.response?.status === 401 && getLoginToken()) {
      clearAuth();
      if (!window.location.pathname.startsWith('/login')) {
        window.location.assign('/login');
      }
    }
    // Trial over / subscription lapsed → route to the billing page (which is
    // also the only place a 402 handler must not redirect-loop).
    if (
      error?.response?.status === 402 &&
      error?.response?.data?.detail === 'trial_expired' &&
      getLoginToken() &&
      !window.location.pathname.startsWith('/billing')
    ) {
      window.location.assign('/billing');
    }
    return Promise.reject(error);
  }
);

export const uploadFiles = (sessionId: string, files: File[]) => {
  const formData = new FormData();
  files.forEach(file => formData.append('files', file));
  return api.post(`/sessions/${sessionId}/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const getUploadStatus = (sessionId: string) =>
  api.get(`/sessions/${sessionId}/upload/status`);

export const getUploadResult = (sessionId: string) =>
  api.get(`/sessions/${sessionId}/upload/result`);

export const sendChatMessage = (sessionId: string, _message: string): EventSource => {
  const url = `${API_BASE_URL}/sessions/${sessionId}/chat`;
  const es = new EventSource(url, {
    withCredentials: false,
  } as EventSourceInit);
  return es;
};

export const postChatMessage = async (
  sessionId: string,
  message: string,
  onEvent: (event: string, data: string) => void
) => {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  const token = getLoginToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  } else if (API_KEY) {
    headers['X-API-Key'] = API_KEY;
  }

  const response = await fetch(`${API_BASE_URL}/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ message }),
  });

  if (!response.body) throw new Error('No response body');

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';
    for (const block of lines) {
      const eventMatch = block.match(/^event: (.+)$/m);
      const dataMatch = block.match(/^data: (.+)$/m);
      if (eventMatch && dataMatch) {
        onEvent(eventMatch[1], JSON.parse(dataMatch[1]));
      }
    }
  }
};

export const previewChain = (sessionId: string) =>
  api.get(`/sessions/${sessionId}/chain/preview`);

export const approveChain = (sessionId: string) =>
  api.post(`/sessions/${sessionId}/chain/approve`, { approve: true });

/** Design Product mode (Factory agent architecture path) */
export const draftProductBlueprint = (
  sessionId: string,
  brief: string,
  vertical_hint?: string
) => api.post(`/sessions/${sessionId}/product/draft`, { brief, vertical_hint });

export const planProductBlueprint = (sessionId: string) =>
  api.post(`/sessions/${sessionId}/product/plan`);

export const approveProductBlueprint = (sessionId: string, approve = true) =>
  api.post(`/sessions/${sessionId}/product/approve`, { approve });

export const generateProductFromSession = (sessionId: string) =>
  api.post(`/sessions/${sessionId}/product/generate`, {});

export const getProductDesign = (sessionId: string) =>
  api.get(`/sessions/${sessionId}/product`);

export default api;
