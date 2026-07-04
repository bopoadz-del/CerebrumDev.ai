import React from 'react';
import { PanelLeft, Bot } from 'lucide-react';

interface AppShellProps {
  sidebar: React.ReactNode;
  children: React.ReactNode;
  sidebarOpen: boolean;
  onToggleSidebar: () => void;
}

export const AppShell: React.FC<AppShellProps> = ({
  sidebar,
  children,
  sidebarOpen,
  onToggleSidebar,
}) => {
  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <header className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center space-x-3">
          <button
            onClick={onToggleSidebar}
            className="p-2 rounded-md hover:bg-gray-100 lg:hidden"
            aria-label="Toggle chat sidebar"
          >
            <PanelLeft className="w-5 h-5 text-gray-600" />
          </button>
          <div className="flex items-center space-x-2">
            <Bot className="w-6 h-6 text-blue-600" />
            <div>
              <h1 className="text-xl font-bold text-gray-900 leading-none">CerebrumDev.ai</h1>
              <p className="text-xs text-gray-500 mt-0.5">Configure and deploy your sovereign AI instance</p>
            </div>
          </div>
        </div>
      </header>

      <div className="flex-1 flex overflow-hidden">
        <aside
          className={`
            fixed inset-y-0 left-0 z-20 w-80 bg-white border-r border-gray-200 transform transition-transform
            lg:static lg:translate-x-0 lg:block
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full'}
          `}
          style={{ top: '57px' }}
        >
          {sidebar}
        </aside>

        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-black/20 z-10 lg:hidden"
            onClick={onToggleSidebar}
            aria-hidden="true"
          />
        )}

        <main className="flex-1 overflow-y-auto p-4 lg:p-8">
          <div className="max-w-5xl mx-auto">{children}</div>
        </main>
      </div>
    </div>
  );
};
