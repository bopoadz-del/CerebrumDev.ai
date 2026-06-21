import { useState } from 'react';
import PhaseWizard from './components/PhaseWizard';

function App() {
  const [sessionId] = useState(() => 'sess_' + Math.random().toString(36).substr(2, 9));

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <header className="max-w-4xl mx-auto mb-8">
        <h1 className="text-3xl font-bold text-gray-800">CerebrumDev.ai</h1>
        <p className="text-gray-600">Configure and deploy your sovereign AI instance</p>
      </header>
      <div className="max-w-4xl mx-auto">
        <PhaseWizard sessionId={sessionId} />
      </div>
    </div>
  );
}

export default App;
