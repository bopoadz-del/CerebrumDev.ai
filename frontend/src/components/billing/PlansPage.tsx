import { useEffect, useState } from 'react';
import { useLocation } from 'wouter';
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

type PlanCta = 'free' | 'pro' | 'team';

interface Plan {
  name: string;
  tagline: string;
  priceNote: string;
  cta: PlanCta;
  features: string[];
}

// Capability rows trace to docs/COMMERCIAL_TIERS.md (ratified gold, 2026-07-22).
// External names only — internal program names never reach the UI.
const PLANS: Plan[] = [
  {
    name: 'Cerebrum Free',
    tagline: 'Domain intelligence for one user',
    priceNote: 'Free — included with every registered account',
    cta: 'free',
    features: [
      'Registered account — 1 user',
      'Domain-specific platform',
      'Standard Reasoning Layer',
      'Preloaded domain knowledge (RAG Layer 1)',
      'Domain calculations and rules',
      'Launch trial: up to 3 document uploads',
      'Launch trial: 7-day private index',
      '1 project · limited exports',
    ],
  },
  {
    name: 'Cerebrum Pro',
    tagline: 'Domain + private organisation intelligence for one user',
    priceNote: 'Subscription',
    cta: 'pro',
    features: [
      'Everything in Free, without the trial limits',
      'Full personal document upload',
      'Private second RAG (Layer 2) — persistent',
      'Several projects / workspaces',
      'Full XLSX/PDF exports',
      'Personal confirmation approvals',
      'Basic activity history',
      'Higher usage limits',
    ],
  },
  {
    name: 'Cerebrum Team',
    tagline: 'Organisation intelligence + people, roles, approvals, governance',
    priceNote: 'Seats / organisation',
    cta: 'team',
    features: [
      'Everything in Pro',
      'Multiple users — organisation-wide workspaces',
      'Multi-user profiles and RBAC',
      'Full role-based approval workflows',
      'Organisation audit trail and evidence',
      'Admin and user management',
      'Connectors',
      'Contract-based usage limits',
    ],
  },
];

function statusLine(snap: BillingSnap): string | null {
  switch (snap.subscription_status) {
    case 'active':
      return 'Active subscription — thank you.';
    case 'trialing':
      return snap.entitled
        ? `Free trial running — ${snap.trial_days_left ?? 0} of ${snap.trial_days} days left.`
        : 'Your free trial has ended.';
    case 'past_due':
      return 'Payment failed — update your card from the billing page.';
    case 'canceled':
      return 'Subscription canceled.';
    default:
      return null;
  }
}

export default function PlansPage() {
  const [, navigate] = useLocation();
  const [snap, setSnap] = useState<BillingSnap | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const email = getAccountEmail();

  useEffect(() => {
    if (!getLoginToken()) return; // shared-key mode: no per-user billing
    let cancelled = false;
    api
      .get('/billing/status')
      .then((res) => {
        if (!cancelled) setSnap(res.data.billing);
      })
      .catch(() => {
        // The comparison still renders — live status is advisory here.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function startCheckout() {
    setBusy(true);
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
      setBusy(false);
    }
  }

  function renderCta(plan: Plan) {
    if (plan.cta === 'free') {
      return (
        <div>
          <p className="mb-2 text-sm text-green-700">Included with your account</p>
          <button
            type="button"
            onClick={() => navigate('/')}
            className="w-full rounded-md border border-blue-600 px-3 py-2 text-sm font-medium text-blue-600 hover:bg-blue-50"
          >
            Enter the factory
          </button>
        </div>
      );
    }
    if (plan.cta === 'pro') {
      if (snap?.subscription_status === 'active') {
        return (
          <div>
            <p className="mb-2 text-sm text-green-700">Current plan</p>
            <a
              href="/billing"
              className="block w-full rounded-md border border-gray-300 px-3 py-2 text-center text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Manage subscription
            </a>
          </div>
        );
      }
      if (snap && !snap.checkout_available) {
        return (
          <p className="text-sm text-gray-500">
            Online checkout is not configured yet — contact support to subscribe.
          </p>
        );
      }
      return (
        <button
          type="button"
          onClick={startCheckout}
          disabled={busy}
          className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? 'Opening checkout…' : 'Subscribe to Pro'}
        </button>
      );
    }
    return (
      <p className="text-sm text-gray-500">
        Not on sale yet — multi-user, RBAC and organisation approvals launch later. Pro covers one
        user today.
      </p>
    );
  }

  if (!getLoginToken()) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4">
        <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
          <h1 className="mb-2 text-xl font-semibold text-gray-900">Plans</h1>
          <p className="text-sm text-gray-500">
            This deployment runs in shared-key mode — there are no individual accounts, so there is
            nothing to choose.{' '}
            <a href="/" className="text-blue-600 hover:underline">
              Back to the factory
            </a>
          </p>
        </div>
      </div>
    );
  }

  const status = snap ? statusLine(snap) : null;

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-10">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-2xl font-semibold text-gray-900">Choose your plan</h1>
        <p className="mt-1 text-sm text-gray-500">
          {email} — Free understands the domain. Paid understands your organisation.
        </p>

        {status && (
          <p className="mt-4 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-800">
            {status}
          </p>
        )}
        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {PLANS.map((plan) => (
            <div
              key={plan.cta}
              className="flex flex-col rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
            >
              <h2 className="text-lg font-semibold text-gray-900">{plan.name}</h2>
              <p className="mt-1 text-sm text-gray-500">{plan.tagline}</p>
              <p className="mt-2 text-sm font-medium text-gray-700">{plan.priceNote}</p>
              <ul className="mt-4 flex-1 space-y-1.5 text-sm text-gray-600">
                {plan.features.map((feature) => (
                  <li key={feature} className="flex gap-2">
                    <span className="text-blue-600">•</span>
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-5">{renderCta(plan)}</div>
            </div>
          ))}
        </div>

        <p className="mt-6 text-xs text-gray-400">
          Launch trial honesty: the free upload trial (up to three documents, seven-day private
          index) expires with a visible countdown — no silent persistence, no trial data becoming
          organisation memory.
        </p>

        <div className="mt-4 text-sm">
          <a href="/" className="text-blue-600 hover:underline">
            ← Back to the factory
          </a>
          {' · '}
          <a href="/billing" className="text-blue-600 hover:underline">
            Billing
          </a>
        </div>
      </div>
    </div>
  );
}
