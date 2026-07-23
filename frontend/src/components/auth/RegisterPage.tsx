import { FormEvent, useState } from 'react';
import { useLocation } from 'wouter';
import api from '../../api/client';
import { saveAuth } from '../../api/auth';

interface RegisterResult {
  account_id: string;
  email: string;
  login_token: string;
  verification: {
    mode: 'smtp' | 'dev_token';
    email_sent: boolean;
    dev_verification_token?: string;
    note?: string;
  };
}

export default function RegisterPage({ onRegistered }: { onRegistered: () => void }) {
  const [, navigate] = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RegisterResult | null>(null);
  const [verified, setVerified] = useState(false);
  const [verifyError, setVerifyError] = useState('');

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    setBusy(true);
    try {
      const res = await api.post('/auth/register', { email, password });
      const data = res.data as RegisterResult;
      saveAuth(data.login_token, data.email, data.account_id);
      setResult(data);
      onRegistered();
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Registration failed');
    } finally {
      setBusy(false);
    }
  }

  async function verifyNow(token: string) {
    setVerifyError('');
    try {
      await api.post('/auth/verify-email', { token });
      setVerified(true);
    } catch (err: any) {
      setVerifyError(err.response?.data?.detail || 'Verification failed');
    }
  }

  if (result) {
    const v = result.verification;
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h1 className="mb-1 text-xl font-semibold text-gray-900">Account created</h1>
          <p className="mb-4 text-sm text-gray-500">{result.email}</p>
          {v.mode === 'smtp' ? (
            <p className="mb-4 text-sm text-gray-700">
              We sent a verification link to your inbox. It expires in 24 hours.
            </p>
          ) : (
            <div className="mb-4 space-y-2">
              <p className="text-sm text-gray-700">
                Email is not configured on this deployment — verify with this token
                (honest dev mode):
              </p>
              <code className="block break-all rounded-md bg-gray-100 p-2 text-xs text-gray-800">
                {v.dev_verification_token}
              </code>
              {!verified ? (
                <button
                  type="button"
                  onClick={() => verifyNow(v.dev_verification_token || '')}
                  className="rounded-md border border-blue-600 px-3 py-1.5 text-sm text-blue-600 hover:bg-blue-50"
                >
                  Verify now
                </button>
              ) : (
                <p className="text-sm text-green-600">Email verified.</p>
              )}
              {verifyError && <p className="text-sm text-red-600">{verifyError}</p>}
            </div>
          )}
          <button
            type="button"
            onClick={() => navigate('/plans')}
            className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            Choose your plan
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Create your account</h1>
        <p className="mb-6 text-sm text-gray-500">Register to use CerebrumDev.ai</p>
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
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Password</label>
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
            <label className="mb-1 block text-sm font-medium text-gray-700">Confirm password</label>
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
            {busy ? 'Creating…' : 'Create account'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-gray-500">
          Already registered?{' '}
          <a href="/login" className="text-blue-600 hover:underline">
            Sign in
          </a>
        </p>
      </div>
    </div>
  );
}
