import { useEffect, useState } from 'react';
import { Route, Switch, useLocation, useRoute, Redirect } from 'wouter';
import { AppShell } from './components/layout/AppShell';
import { ChatSidebar } from './components/chat/ChatSidebar';
import { ConfigCanvas } from './components/canvas/ConfigCanvas';
import DesignProductPanel from './components/DesignProductPanel';
import { ToastProvider, useToast } from './hooks/useToast';
import api from './api/client';

type WorkspaceMode = 'kit' | 'product';

function AppContent() {
  const [location, setLocation] = useLocation();
  const [match, params] = useRoute('/s/:sessionId');
  const { addToast } = useToast();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>('kit');

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

  const handleCommand = (command: string, args: any) => {
    switch (command) {
      case 'set_domain':
        addToast({
          type: 'info',
          title: 'Command received',
          message: `Domain set to ${args.domain}. Save the configuration to apply.`,
        });
        break;
      case 'set_model':
        addToast({
          type: 'info',
          title: 'Command received',
          message: `Base model set to ${args.model}. Save the configuration to apply.`,
        });
        break;
      default:
        addToast({ type: 'info', message: `Command: ${command}` });
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
    try {
      await api.post(`/sessions/${sessionId}/product/mode`, { mode });
    } catch {
      /* mode is also local UI state */
    }
  };

  return (
    <AppShell
      sidebar={<ChatSidebar sessionId={sessionId} onCommand={handleCommand} />}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((v) => !v)}
    >
      <div className="mb-4 flex gap-2">
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
      </div>
      <Switch>
        <Route path="/s/:sessionId">
          {workspaceMode === 'product' ? (
            <DesignProductPanel sessionId={sessionId} />
          ) : (
            <ConfigCanvas sessionId={sessionId} />
          )}
        </Route>
        <Route path="/">
          <Redirect to={`/s/${sessionId}`} />
        </Route>
      </Switch>
    </AppShell>
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
