import { useEffect, useState } from 'react';
import { Route, Switch, useLocation, useRoute, Redirect } from 'wouter';
import { AppShell } from './components/layout/AppShell';
import { ChatSidebar } from './components/chat/ChatSidebar';
import { ConfigCanvas } from './components/canvas/ConfigCanvas';
import { ToastProvider, useToast } from './hooks/useToast';

function generateSessionId(): string {
  return 'sess_' + Math.random().toString(36).slice(2, 11);
}

function AppContent() {
  const [location, setLocation] = useLocation();
  const [match, params] = useRoute('/s/:sessionId');
  const { addToast } = useToast();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [sessionId, setSessionId] = useState<string>(() => {
    if (match && params?.sessionId) return params.sessionId;
    const stored = localStorage.getItem('cerebrumdev:last-session-id');
    if (stored) return stored;
    return generateSessionId();
  });

  useEffect(() => {
    localStorage.setItem('cerebrumdev:last-session-id', sessionId);
  }, [sessionId]);

  useEffect(() => {
    if (match && params?.sessionId && params.sessionId !== sessionId) {
      setSessionId(params.sessionId);
    } else if (!match && location !== `/s/${sessionId}`) {
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

  return (
    <AppShell
      sidebar={<ChatSidebar sessionId={sessionId} onCommand={handleCommand} />}
      sidebarOpen={sidebarOpen}
      onToggleSidebar={() => setSidebarOpen((v) => !v)}
    >
      <Switch>
        <Route path="/s/:sessionId">
          <ConfigCanvas sessionId={sessionId} />
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
