import React from 'react';

interface AIConfig {
  base_model: string;
  lora_rank: number;
  learning_rate: number;
  vector_db: string;
  hnsw_preset: string;
}

interface AIConfigPanelProps {
  config: AIConfig;
  onChange: (config: AIConfig) => void;
}

const AIConfigPanel: React.FC<AIConfigPanelProps> = ({ config, onChange }) => {
  const update = (key: keyof AIConfig, value: any) => {
    onChange({ ...config, [key]: value });
  };

  return (
    <div className="grid grid-cols-2 gap-4">
      <div>
        <label className="block text-sm font-medium text-gray-700">Base Model</label>
        <select
          value={config.base_model}
          onChange={(e) => update('base_model', e.target.value)}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm"
        >
          <option>Llama-3.2-3B</option>
          <option>Llama-3.1-8B</option>
          <option>Mistral-7B</option>
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">LoRA Rank</label>
        <select
          value={config.lora_rank}
          onChange={(e) => update('lora_rank', parseInt(e.target.value))}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm"
        >
          <option value={16}>16</option>
          <option value={32}>32</option>
          <option value={64}>64</option>
          <option value={128}>128</option>
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">Learning Rate</label>
        <input
          type="number"
          step="0.00001"
          value={config.learning_rate}
          onChange={(e) => update('learning_rate', parseFloat(e.target.value))}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm"
        />
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">Vector DB</label>
        <select
          value={config.vector_db}
          onChange={(e) => update('vector_db', e.target.value)}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm"
        >
          <option>ZVec</option>
          <option>ChromaDB</option>
        </select>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700">HNSW Preset</label>
        <select
          value={config.hnsw_preset}
          onChange={(e) => update('hnsw_preset', e.target.value)}
          className="mt-1 block w-full border border-gray-300 rounded px-3 py-2 text-sm"
        >
          <option value="fast">Fast (~90% recall)</option>
          <option value="balanced">Balanced (~96% recall)</option>
          <option value="accurate">Accurate (~99% recall)</option>
        </select>
      </div>
    </div>
  );
};

export default AIConfigPanel;
