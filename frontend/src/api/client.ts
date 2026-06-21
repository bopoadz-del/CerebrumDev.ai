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

export default api;
