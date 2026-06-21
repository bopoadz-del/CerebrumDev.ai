import React, { useState, useEffect } from 'react';
import api from '../api/client';
import DomainSelector from './DomainSelector';
import AIConfigPanel from './AIConfigPanel';
import DataUploader from './DataUploader';
import ChatChainGenerator from './ChatChainGenerator';
import DeployPanel from './DeployPanel';
import TrainingPanel from './TrainingPanel';

interface PhaseWizardProps {
  sessionId: string;
}

interface ConfigData {
  domain: string;
  ai_config: {
    base_model: string;
    lora_rank: number;
    learning_rate: number;
    vector_db: string;
    hnsw_preset: string;
  };
}

const PhaseWizard: React.FC<PhaseWizardProps> = ({ sessionId }) => {
  useEffect(() => {
    api.post('/sessions/', null, { headers: { 'X-Session-ID': sessionId } }).catch(console.error);
  }, [sessionId]);

  const [config, setConfig] = useState<ConfigData>({
    domain: 'construction',
    ai_config: {
      base_model: 'Llama-3.2-3B',
      lora_rank: 32,
      learning_rate: 2e-4,
      vector_db: 'ZVec',
      hnsw_preset: 'balanced',
    },
  });
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);
  const [uploadComplete, setUploadComplete] = useState(false);
  const [chainApproved, setChainApproved] = useState(false);
  const [trainingComplete, setTrainingComplete] = useState(false);

  const handleSave = async () => {
    setLoading(true);
    try {
      await api.post(`/sessions/${sessionId}/config`, config);
      setSaved(true);
    } catch (err) {
      console.error(err);
      alert('Failed to save config');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-lg shadow p-6 space-y-6">
        <h2 className="text-xl font-semibold">Phase 1: Configure Your Instance</h2>

        <div>
          <label className="block text-sm font-medium text-gray-700">Select Domain</label>
          <DomainSelector selected={config.domain} onChange={(domain) => setConfig({ ...config, domain })} />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700">AI Configuration</label>
          <AIConfigPanel config={config.ai_config} onChange={(ai_config) => setConfig({ ...config, ai_config })} />
        </div>

        <button
          onClick={handleSave}
          disabled={loading}
          className="w-full py-2 px-4 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? 'Saving...' : saved ? 'Saved ✓' : 'Save Configuration'}
        </button>
      </div>

      {saved && (
        <DataUploader sessionId={sessionId} onComplete={() => setUploadComplete(true)} />
      )}

      {saved && uploadComplete && (
        <ChatChainGenerator sessionId={sessionId} onApproved={() => setChainApproved(true)} />
      )}

      {chainApproved && !trainingComplete && (
        <TrainingPanel sessionId={sessionId} onComplete={() => setTrainingComplete(true)} />
      )}

      {chainApproved && trainingComplete && (
        <DeployPanel sessionId={sessionId} />
      )}
    </div>
  );
};

export default PhaseWizard;
