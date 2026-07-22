import { FormEvent, useState } from 'react';
import api from '../../api/client';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [devToken, setDevToken] = useState('');
  const [error, setError] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setMessage('');
    setDevToken('');
    setBusy(true);
    try {
      const res = await api.post('/auth/forgot-password', { email });
      setMessage(res.data.message || 'If the email is registered, a reset link follows.');
      if (res.data.dev_reset_token) {
        setDevToken(res.data.dev_reset_token);
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Request failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Reset your password</h1>
        <p className="mb-6 text-sm text-gray-500">
          Enter your account email and we will send a reset link.
        </p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Email</label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              autoComplete="email"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? 'Sending…' : 'Send reset link'}
          </button>
        </form>
        {message && <p className="mt-4 text-sm text-gray-700">{message}</p>}
        {devToken && (
          <div className="mt-3 space-y-2">
            <p className="text-xs text-gray-500">
              Email is not configured on this deployment — use this token (honest dev mode):
            </p>
            <code className="block break-all rounded-md bg-gray-100 p-2 text-xs text-gray-800">
              {devToken}
            </code>
            <a
              href={`/reset-password?token=${encodeURIComponent(devToken)}`}
              className="inline-block text-sm text-blue-600 hover:underline"
            >
              Continue to reset
            </a>
          </div>
        )}
        <p className="mt-4 text-center text-sm text-gray-500">
          <a href="/login" className="text-blue-600 hover:underline">
            Back to sign in
          </a>
        </p>
      </div>
    </div>
  );
}
