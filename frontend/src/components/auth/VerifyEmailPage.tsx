import { useEffect, useRef, useState } from 'react';
import api from '../../api/client';

export default function VerifyEmailPage() {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading');
  const [message, setMessage] = useState('');
  const attempted = useRef(false);

  useEffect(() => {
    if (attempted.current) return;
    attempted.current = true;
    const token = new URLSearchParams(window.location.search).get('token') || '';
    if (!token) {
      setStatus('error');
      setMessage('No verification token in this link.');
      return;
    }
    api
      .post('/auth/verify-email', { token })
      .then(() => setStatus('ok'))
      .catch((err: any) => {
        setStatus('error');
        setMessage(err.response?.data?.detail || 'Verification failed');
      });
  }, []);

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 text-center shadow-sm">
        {status === 'loading' && <p className="text-sm text-gray-500">Verifying…</p>}
        {status === 'ok' && (
          <>
            <h1 className="mb-2 text-xl font-semibold text-gray-900">Email verified</h1>
            <p className="mb-4 text-sm text-gray-500">Your account is ready.</p>
            <a href="/login" className="text-blue-600 hover:underline">
              Sign in
            </a>
          </>
        )}
        {status === 'error' && (
          <>
            <h1 className="mb-2 text-xl font-semibold text-gray-900">Verification failed</h1>
            <p className="mb-4 text-sm text-red-600">{message}</p>
            <a href="/login" className="text-blue-600 hover:underline">
              Back to sign in
            </a>
          </>
        )}
      </div>
    </div>
  );
}
