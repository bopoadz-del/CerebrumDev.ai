import React, { useState, useEffect } from 'react';
import { Save, Upload, Workflow, Dumbbell, Rocket } from 'lucide-react';
import api from '../../api/client';
import { useSession } from '../../hooks/useSession';
import { useToast } from '../../hooks/useToast';
import { Button } from '../ui/Button';
import { Card } from '../ui/Card';
import AIConfigPanel from '../AIConfigPanel';
import DomainSelector from '../DomainSelector';
import DataUploader from '../DataUploader';
import TrainingPanel from '../TrainingPanel';
import DeployPanel from '../DeployPanel';

interface ConfigCanvasProps {
  sessionId: string;
}

const StepIcon: React.FC<{ icon: React.ReactNode; active: boolean; done: boolean }> = ({
  icon,
  active,
  done,
}) => (
  <div
    className={`w-8 h-8 rounded-full flex items-center justify-center border-2 ${
      done
        ? 'bg-green-600 border-green-600 text-white'
        : active
        ? 'bg-blue-600 border-blue-600 text-white'
        : 'bg-white border-gray-300 text-gray-400'
    }`}
  >
    {icon}
  </div>
);

export const ConfigCanvas: React.FC<ConfigCanvasProps> = ({ sessionId }) => {
  const { state, loading, error, updateConfig, setUploadComplete, approveChain, setTrainingComplete } =
    useSession(sessionId);
  const { addToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [localConfig, setLocalConfig] = useState(state.config);

  useEffect(() => {
    setLocalConfig(state.config);
  }, [state.config]);

  useEffect(() => {
    if (error) {
      addToast({ type: 'error', title: 'Session error', message: error });
    }
  }, [error, addToast]);

  const handleSaveConfig = async () => {
    setSaving(true);
    const ok = await updateConfig(localConfig);
    setSaving(false);
    if (ok) {
      addToast({ type: 'success', title: 'Saved', message: 'Configuration saved.' });
    } else {
      addToast({ type: 'error', title: 'Save failed', message: 'Could not save configuration.' });
    }
  };

  const phase = state.phase;
  const uploadDone = state.upload.status === 'completed' || state.upload.status === 'completed_with_warnings';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between overflow-x-auto pb-2">
        <div className="flex items-center space-x-2 min-w-max">
          <StepIcon icon={<Save className="w-4 h-4" />} active={phase === 1} done={phase > 1 || state.chain_approved} />
          <span className="text-sm text-gray-500">Configure</span>
        </div>
        <div className="w-8 h-px bg-gray-200 mx-2" />
        <div className="flex items-center space-x-2 min-w-max">
          <StepIcon icon={<Upload className="w-4 h-4" />} active={phase === 2 || (phase === 1 && uploadDone)} done={uploadDone || state.chain_approved} />
          <span className="text-sm text-gray-500">Upload</span>
        </div>
        <div className="w-8 h-px bg-gray-200 mx-2" />
        <div className="flex items-center space-x-2 min-w-max">
          <StepIcon icon={<Workflow className="w-4 h-4" />} active={state.proposed_chain !== null && !state.chain_approved} done={state.chain_approved} />
          <span className="text-sm text-gray-500">Chain</span>
        </div>
        <div className="w-8 h-px bg-gray-200 mx-2" />
        <div className="flex items-center space-x-2 min-w-max">
          <StepIcon icon={<Dumbbell className="w-4 h-4" />} active={phase === 4} done={state.training_job.status === 'completed'} />
          <span className="text-sm text-gray-500">Train</span>
        </div>
        <div className="w-8 h-px bg-gray-200 mx-2" />
        <div className="flex items-center space-x-2 min-w-max">
          <StepIcon icon={<Rocket className="w-4 h-4" />} active={phase === 5 || state.deployment.status === 'deploying'} done={state.deployment.status === 'live' || state.deployment.status === 'packaged'} />
          <span className="text-sm text-gray-500">Deploy</span>
        </div>
      </div>

      <Card title="Configuration" description="Set the domain and AI parameters for your instance.">
        <div className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Domain</label>
            <DomainSelector
              selected={localConfig.domain}
              onChange={(domain) => setLocalConfig({ ...localConfig, domain })}
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">AI Configuration</label>
            <AIConfigPanel
              config={localConfig.ai_config}
              onChange={(ai_config) => setLocalConfig({ ...localConfig, ai_config })}
            />
          </div>

          <Button onClick={handleSaveConfig} loading={saving || loading} disabled={loading}>
            Save Configuration
          </Button>
        </div>
      </Card>

      <DataUploader sessionId={sessionId} onComplete={setUploadComplete} />

      {uploadDone && (
        <ChainApprovalPanel sessionId={sessionId} onApproved={approveChain} />
      )}

      {state.chain_approved && (
        <TrainingPanel sessionId={sessionId} onComplete={setTrainingComplete} />
      )}

      {state.chain_approved && state.training_job.status === 'completed' && (
        <DeployPanel sessionId={sessionId} />
      )}
    </div>
  );
};

const ChainApprovalPanel: React.FC<{ sessionId: string; onApproved: () => void }> = ({
  sessionId,
  onApproved,
}) => {
  const { addToast } = useToast();
  const [approving, setApproving] = useState(false);
  const [chain, setChain] = useState<{ blocks: { id: string }[] } | null>(null);

  useEffect(() => {
    api
      .get(`/sessions/${sessionId}/chain/preview`)
      .then((res) => setChain(res.data.chain))
      .catch(() => setChain(null));
  }, [sessionId]);

  const handleApprove = async () => {
    setApproving(true);
    try {
      await api.post(`/sessions/${sessionId}/chain/approve`, { approve: true });
      onApproved();
      addToast({ type: 'success', title: 'Chain approved', message: 'Moving to training phase.' });
    } catch (err: any) {
      addToast({
        type: 'error',
        title: 'Approval failed',
        message: err.response?.data?.detail || err.message,
      });
    } finally {
      setApproving(false);
    }
  };

  return (
    <Card title="Proposed Chain" description="Review the AI-suggested block chain before training.">
      <div className="space-y-4">
        {chain ? (
          <div className="flex flex-wrap items-center gap-2">
            {chain.blocks.map((block, idx) => (
              <React.Fragment key={idx}>
                <div className="px-3 py-1.5 bg-white border border-gray-200 rounded-md text-sm font-medium">
                  {block.id}
                </div>
                {idx < chain.blocks.length - 1 && <span className="text-gray-400">→</span>}
              </React.Fragment>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            No chain proposed yet. Ask the assistant to suggest a workflow.
          </p>
        )}
        <Button onClick={handleApprove} loading={approving} disabled={!chain}>
          Approve Chain
        </Button>
      </div>
    </Card>
  );
};
