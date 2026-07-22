import { useEffect, useState } from 'react';
import api from '../../api/client';
import { getLoginToken } from '../../api/auth';

interface BillingSnap {
  subscription_status: string;
  trial_days_left: number | null;
  entitled: boolean;
  enforcement: boolean;
}

/**
 * Slim billing banner for the workspace header: trial countdown while the
 * trial is live, and a blocking notice once access actually stopped
 * (enforcement on + not entitled). Active subscribers see nothing.
 */
export default function TrialBanner() {
  const [snap, setSnap] = useState<BillingSnap | null>(null);

  useEffect(() => {
    if (!getLoginToken()) return; // shared-key mode: no per-user billing
    let cancelled = false;
    api
      .get('/billing/status')
      .then((res) => {
        if (!cancelled) setSnap(res.data.billing);
      })
      .catch(() => {
        // Billing status is advisory — never break the workspace over it.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!snap) return null;

  if (snap.subscription_status === 'trialing' && snap.entitled) {
    return (
      <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        Free trial: {snap.trial_days_left ?? 0} day{(snap.trial_days_left ?? 0) === 1 ? '' : 's'} left.{' '}
        <a href="/billing" className="font-medium text-blue-600 hover:underline">
          Subscribe to keep access →
        </a>
      </div>
    );
  }

  if (!snap.entitled && snap.enforcement) {
    return (
      <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
        Your trial has ended — new sessions and factory runs are paused.{' '}
        <a href="/billing" className="font-medium text-blue-600 hover:underline">
          Subscribe to continue →
        </a>
      </div>
    );
  }

  return null;
}
