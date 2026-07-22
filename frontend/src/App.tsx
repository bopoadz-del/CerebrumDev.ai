import { useEffect, useState } from 'react';
import { Route, Switch, useLocation, useRoute, Redirect } from 'wouter';
import { AppShell } from './components/layout/AppShell';
import { ChatSidebar } from './components/chat/ChatSidebar';
import { ConfigCanvas } from './components/canvas/ConfigCanvas';
import DesignProductPanel from './components/DesignProductPanel';
import WorkbenchPanel from './components/WorkbenchPanel';
import LoginPage from './components/auth/LoginPage';
import RegisterPage from './components/auth/RegisterPage';
import VerifyEmailPage from './components/auth/VerifyEmailPage';
import ForgotPasswordPage from './components/auth/ForgotPasswordPage';
import ResetPasswordPage from './components/auth/ResetPasswordPage';
import BillingPage from './components/billing/BillingPage';
import TrialBanner from './components/billing/TrialBanner';
import { ToastProvider, useToast } from './hooks/useToast';
import api, { API_KEY } from './api/client';
import { clearAuth, getAccountEmail, getLoginToken } from './api/auth';

type WorkspaceMode = 'kit' | 'product' | 'workbench';

function Workspace({ onLogout }: { onLogout: () => void }) {
  const [location, setLocation] = useLocation();
  const [match, params] = useRoute('/s/:sessionId');
  const { addToast } = useToast();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('kit');
  const accountEmail = getAccountEmail();

  const [sessionId, setSessionId] = useState<string | null>(() => {
    if (match && params?.sessionId) return params.sessionId;
    return localStorage.getItem('cerebrumdev:last-session-id');
  });

  useEffect(() => {
    let cancelled = false;

    async function initSession() {
      if (sessionId) return;
      try {
        const res = await api.post('/sessions/');
        if (!cancelled) {
          setSessionId(res.data.session_id);
        }
      } catch (err: any) {
        if (!cancelled) {
          addToast({
            type: 'error',
            title: 'Session error',
            message: err.response?.data?.detail || err.message || 'Failed to create session',
          });
        }
      }
    }

    initSession();

    return () => {
      cancelled = true;
    };
  }, [sessionId, addToast]);

  useEffect(() => {
    if (sessionId) {
      localStorage.setItem('cerebrumdev:last-session-id', sessionId);
    }
  }, [sessionId]);

  useEffect(() => {
    if (match && params?.sessionId && params.sessionId !== sessionId) {
      setSessionId(params.sessionId);
    } else if (sessionId && !match && location !== `/s/${sessionId}`) {
      setLocation(`/s/${sessionId}`, { replace: true });
    }
  }, [match, params, location, sessionId, setLocation]);

  // Bumped after chat config mutations so ConfigCanvas reloads localConfig
  // and a later "Save Configuration" cannot overwrite chat-applied values.
  const [configEpoch, setConfigEpoch] = useState(0);

  const handleCommand = async (command: string, args: any) => {
    const configMutators = new Set([
      'set_domain',
      'set_model',
      'set_lora_rank',
      'set_learning_rate',
      'set_vector_db',
      'set_hnsw_preset',
    ]);
    try {
      if (command === 'set_domain' && args?.domain && sessionId) {
        const cur = await api.get(`/sessions/${sessionId}`);
        const config = {
          ...(cur.data.config || {}),
          domain: args.domain,
        };
        await api.post(`/sessions/${sessionId}/config`, config);
        setConfigEpoch((n) => n + 1);
        addToast({
          type: 'success',
          title: 'Domain updated',
          message: `Domain set to ${args.domain} and saved.`,
        });
        return;
      }
      if (command === 'set_model' && args?.model && sessionId) {
        const cur = await api.get(`/sessions/${sessionId}`);
        const prev = cur.data.config || {};
        const config = {
          ...prev,
          ai_config: { ...(prev.ai_config || {}), base_model: args.model },
        };
        await api.post(`/sessions/${sessionId}/config`, config);
        setConfigEpoch((n) => n + 1);
        addToast({
          type: 'success',
          title: 'Model updated',
          message: `Base model set to ${args.model} and saved.`,
        });
        return;
      }
      if (configMutators.has(command) && sessionId) {
        // Backend already applied these via the chat SSE path; refresh canvas only.
        setConfigEpoch((n) => n + 1);
      }
      addToast({ type: 'info', message: `Command: ${command}` });
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Command failed',
        message: err.response?.data?.detail || err.message || 'Could not apply command',
      });
    }
  };

  if (!sessionId) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
      </div>
    );
  }

  const switchMode = async (mode: WorkspaceMode) => {
    setWorkspaceMode(mode);
    if (mode === 'workbench') return;
    try {
      await api.post(`/sessions/${sessionId}/product/mode`, { mode });
    } catch (err: any) {
      // Local UI mode still switches; surface sync failures so auth/path bugs are visible.
      addToast({
        type: 'error',
        title: 'Mode sync',
        message: err.response?.data?.detail || err.message || 'Failed to sync mode',
      });
    }
  };

  return (
    <AppShell
      sidebar={<ChatSidebar sessionId={sessionId} onCommand={handleCommand} />}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((v) => !v)}
    >
      <TrialBanner />
      <div className="mb-4 flex items-center gap-2">
        <button
          type="button"
          onClick={() => switchMode('kit')}
          className={`px-3 py-1.5 text-sm rounded-md border ${
            workspaceMode === 'kit'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-700 border-gray-300'
          }`}
        >
          Kit configurator
        </button>
        <button
          type="button"
          onClick={() => switchMode('product')}
          className={`px-3 py-1.5 text-sm rounded-md border ${
            workspaceMode === 'product'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-700 border-gray-300'
          }`}
        >
          Design product
        </button>
        <button
          type="button"
          onClick={() => switchMode('workbench')}
          className={`px-3 py-1.5 text-sm rounded-md border ${
            workspaceMode === 'workbench'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-700 border-gray-300'
          }`}
        >
          Build mode
        </button>
        <span className="ml-auto text-sm text-gray-500">
          {accountEmail || 'shared key mode'}
        </span>
        {accountEmail && (
          <a
            href="/billing"
            className="px-3 py-1.5 text-sm rounded-md border bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
          >
            Billing
          </a>
        )}
        {accountEmail && (
          <button
            type="button"
            onClick={onLogout}
            className="px-3 py-1.5 text-sm rounded-md border bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
          >
            Sign out
          </button>
        )}
      </div>
      <Switch>
        <Route path="/s/:sessionId">
          {workspaceMode === 'workbench' ? (
            <WorkbenchPanel />
          ) : workspaceMode === 'product' ? (
            <DesignProductPanel sessionId={sessionId} />
          ) : (
            <ConfigCanvas sessionId={sessionId} configEpoch={configEpoch} />
          )}
        </Route>
        <Route path="/">
          <Redirect to={`/s/${sessionId}`} />
        </Route>
      </Switch>
    </AppShell>
  );
}

function AppContent() {
  const [location] = useLocation();
  // Shared-key deployments (VITE_API_KEY set) keep the old no-login flow.
  // User deployments: a login token is required.
  const [authed, setAuthed] = useState<boolean>(
    () => Boolean(getLoginToken()) || Boolean(API_KEY)
  );

  if (location === '/login') {
    return <LoginPage onAuthed={() => setAuthed(true)} />;
  }
  if (location === '/register') {
    return <RegisterPage onRegistered={() => setAuthed(true)} />;
  }
  if (location.startsWith('/verify-email')) {
    return <VerifyEmailPage />;
  }
  if (location === '/forgot-password') {
    return <ForgotPasswordPage />;
  }
  if (location.startsWith('/reset-password')) {
    return <ResetPasswordPage />;
  }
  if (!authed) {
    return <Redirect to="/login" />;
  }
  // Billing is a signed-in page and must render without a workspace session —
  // an expired trial blocks session creation (402), so this route comes first.
  if (location.startsWith('/billing')) {
    return <BillingPage />;
  }
  return (
    <Workspace
      onLogout={() => {
        clearAuth();
        setAuthed(false);
      }}
    />
  );
}

function App() {
  return (
    <ToastProvider>
      <AppContent />
    </ToastProvider>
  );
}

export default App;
