import { FormEvent, useState } from 'react';
import { useLocation } from 'wouter';
import api from '../../api/client';

export default function ResetPasswordPage() {
  const [, navigate] = useLocation();
  const [token, setToken] = useState(
    () => new URLSearchParams(window.location.search).get('token') || ''
  );
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      await api.post('/auth/reset-password', { token: token.trim(), new_password: password });
      setDone(true);
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Reset failed');
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 text-center shadow-sm">
          <h1 className="mb-2 text-xl font-semibold text-gray-900">Password updated</h1>
          <p className="mb-4 text-sm text-gray-500">
            All previous sessions were signed out. Use your new password.
          </p>
          <button
            type="button"
            onClick={() => navigate('/login')}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Sign in
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Choose a new password</h1>
        <p className="mb-6 text-sm text-gray-500">Reset links expire after 1 hour.</p>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Reset token</label>
            <input
              type="text"
              required
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">New password</label>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              autoComplete="new-password"
            />
            <p className="mt-1 text-xs text-gray-400">Minimum 8 characters</p>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Confirm new password</label>
            <input
              type="password"
              required
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
              autoComplete="new-password"
            />
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {busy ? 'Updating…' : 'Update password'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-gray-500">
          <a href="/login" className="text-blue-600 hover:underline">
            Back to sign in
          </a>
        </p>
      </div>
    </div>
  );
}
