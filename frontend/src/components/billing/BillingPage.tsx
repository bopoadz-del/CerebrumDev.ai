import { useEffect, useState } from 'react';
import api from '../../api/client';
import { getAccountEmail, getLoginToken } from '../../api/auth';

interface BillingSnap {
  subscription_status: string;
  trial_ends_at: string | null;
  trial_days_left: number | null;
  trial_days: number;
  entitled: boolean;
  enforcement: boolean;
  checkout_available: boolean;
}

type BusyAction = 'checkout' | 'portal' | 'refresh' | '';

function statusLine(snap: BillingSnap): { title: string; tone: 'ok' | 'warn' | 'bad' } {
  switch (snap.subscription_status) {
    case 'active':
      return { title: 'Active subscription', tone: 'ok' };
    case 'trialing':
      return snap.entitled
        ? { title: `Free trial — ${snap.trial_days_left ?? 0} of ${snap.trial_days} days left`, tone: 'warn' }
        : { title: 'Your free trial has ended', tone: 'bad' };
    case 'past_due':
      return { title: 'Payment failed — update your card to keep access', tone: 'bad' };
    case 'canceled':
      return { title: 'Subscription canceled', tone: 'bad' };
    default:
      return { title: 'No active subscription', tone: 'bad' };
  }
}

export default function BillingPage() {
  const [snap, setSnap] = useState<BillingSnap | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<BusyAction>('');
  const [notice, setNotice] = useState('');
  const email = getAccountEmail();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('checkout') === 'success') {
      setNotice('Payment received — your subscription is being activated. Refresh in a few seconds.');
    } else if (params.get('checkout') === 'cancel') {
      setNotice('Checkout canceled — nothing was charged.');
    }
  }, []);

  function load() {
    setBusy('refresh');
    api
      .get('/billing/status')
      .then((res) => setSnap(res.data.billing))
      .catch((err: any) =>
        setError(err.response?.data?.detail || err.message || 'Could not load billing status')
      )
      .finally(() => setBusy(''));
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, []);

  async function startCheckout() {
    setBusy('checkout');
    setError('');
    try {
      const res = await api.post('/billing/checkout');
      window.location.assign(res.data.checkout_url);
    } catch (err: any) {
      setError(
        err.response?.data?.detail === 'stripe_not_configured'
          ? 'Online checkout is not configured yet — contact support.'
          : err.response?.data?.detail || err.message || 'Could not start checkout'
      );
      setBusy('');
    }
  }

  async function openPortal() {
    setBusy('portal');
    setError('');
    try {
      const res = await api.post('/billing/portal');
      window.location.assign(res.data.portal_url);
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Could not open the customer portal');
      setBusy('');
    }
  }

  if (!getLoginToken()) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h1 className="mb-2 text-xl font-semibold text-gray-900">Billing</h1>
          <p className="text-sm text-gray-500">
            This deployment runs in shared-key mode — there are no individual accounts, so there is
            nothing to bill. <a href="/" className="text-blue-600 hover:underline">Back to the factory</a>
          </p>
        </div>
      </div>
    );
  }

  const line = snap ? statusLine(snap) : null;
  const toneClasses = {
    ok: 'border-green-200 bg-green-50 text-green-800',
    warn: 'border-amber-200 bg-amber-50 text-amber-800',
    bad: 'border-red-200 bg-red-50 text-red-800',
  } as const;

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold text-gray-900">Billing</h1>
        <p className="mb-6 text-sm text-gray-500">{email}</p>

        {notice && (
          <p className="mb-4 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
            {notice}
          </p>
        )}

        {!snap && !error && <p className="text-sm text-gray-500">Loading…</p>}

        {snap && line && (
          <div className="space-y-4">
            <p className={`rounded-md border px-3 py-2 text-sm ${toneClasses[line.tone]}`}>
              {line.title}
            </p>

            {snap.subscription_status === 'trialing' && snap.trial_ends_at && (
              <p className="text-sm text-gray-500">
                Trial ends {new Date(snap.trial_ends_at).toLocaleDateString()}.
                {snap.enforcement
                  ? ' After that, creating sessions and factory runs is paused until you subscribe.'
                  : ''}
              </p>
            )}

            <div className="flex flex-col gap-2">
              {snap.subscription_status !== 'active' && snap.checkout_available && (
                <button
                  type="button"
                  onClick={startCheckout}
                  disabled={busy !== ''}
                  className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {busy === 'checkout' ? 'Opening checkout…' : 'Subscribe now'}
                </button>
              )}
              {(snap.subscription_status === 'active' || snap.subscription_status === 'past_due') && (
                <button
                  type="button"
                  onClick={openPortal}
                  disabled={busy !== ''}
                  className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {busy === 'portal'
                    ? 'Opening…'
                    : snap.subscription_status === 'past_due'
                      ? 'Update payment method'
                      : 'Manage subscription'}
                </button>
              )}
              {!snap.checkout_available && snap.subscription_status !== 'active' && (
                <p className="text-sm text-gray-500">
                  Online checkout is not configured yet — contact support to subscribe.
                </p>
              )}
            </div>
          </div>
        )}

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        <div className="mt-6 flex items-center justify-between text-sm">
          <a href="/" className="text-blue-600 hover:underline">
            ← Back to the factory
          </a>
          <button
            type="button"
            onClick={load}
            disabled={busy !== ''}
            className="text-gray-500 hover:underline disabled:opacity-50"
          >
            Refresh status
          </button>
        </div>
      </div>
    </div>
  );
}
