import React from 'react';
import * as Checkbox from '@radix-ui/react-checkbox';
import { Check } from 'lucide-react';

const FEATURES = [
  { id: 'chat', label: 'Chat UI', category: 'AI' },
  { id: 'ocr', label: 'OCR', category: 'Vision' },
  { id: 'pdf', label: 'PDF Extractor', category: 'Document' },
  { id: 'google_drive', label: 'Google Drive', category: 'Storage' },
  { id: 'local_drive', label: 'Local Drive', category: 'Storage' },
  { id: 'one_drive', label: 'OneDrive', category: 'Storage' },
  { id: 'vector_search', label: 'Vector Search', category: 'AI' },
  { id: 'zvec', label: 'ZVec Embedding', category: 'AI' },
  { id: 'web_scraping', label: 'Web Scraping', category: 'Infra' },
  { id: 'email', label: 'Email', category: 'Infra' },
  { id: 'webhook', label: 'Webhook', category: 'Infra' },
  { id: 'code_execution', label: 'Code Execution', category: 'Infra' },
];

interface FeaturePickerProps {
  selected: string[];
  onChange: (features: string[]) => void;
}

const FeaturePicker: React.FC<FeaturePickerProps> = ({ selected, onChange }) => {
  const toggle = (id: string) => {
    if (selected.includes(id)) {
      onChange(selected.filter(f => f !== id));
    } else {
      onChange([...selected, id]);
    }
  };

  const categories = ['AI', 'Vision', 'Document', 'Storage', 'Infra'];

  return (
    <div className="grid grid-cols-2 gap-4">
      {categories.map(cat => (
        <div key={cat} className="space-y-1">
          <div className="text-sm font-medium text-gray-500">{cat}</div>
          {FEATURES.filter(f => f.category === cat).map(f => (
            <label key={f.id} className="flex items-center space-x-2">
              <Checkbox.Root
                checked={selected.includes(f.id)}
                onCheckedChange={() => toggle(f.id)}
                className="w-4 h-4 border border-gray-300 rounded flex items-center justify-center data-[state=checked]:bg-blue-600 data-[state=checked]:border-blue-600"
              >
                <Checkbox.Indicator>
                  <Check className="w-3 h-3 text-white" />
                </Checkbox.Indicator>
              </Checkbox.Root>
              <span className="text-sm">{f.label}</span>
            </label>
          ))}
        </div>
      ))}
    </div>
  );
};

export default FeaturePicker;
