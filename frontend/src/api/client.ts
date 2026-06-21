import axios from 'axios';

const api = axios.create({
  baseURL: '/v1',
  headers: { 'Content-Type': 'application/json' },
});

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

export const sendChatMessage = (sessionId: string, message: string): EventSource => {
  const url = `/v1/sessions/${sessionId}/chat`;
  const es = new EventSource(url, {
    withCredentials: false,
  } as EventSourceInit);
  // EventSource only supports GET, so we send the message via a POST first?
  // FastAPI route is POST. EventSource cannot POST. We use fetch + ReadableStream instead.
  return es;
};

export const postChatMessage = async (
  sessionId: string,
  message: string,
  onEvent: (event: string, data: string) => void
) => {
  const response = await fetch(`/v1/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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

export default api;
