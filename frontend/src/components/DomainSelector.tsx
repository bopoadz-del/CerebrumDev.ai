import React, { useState, useEffect } from 'react';
import * as Select from '@radix-ui/react-select';
import { Check, ChevronDown } from 'lucide-react';
import api from '../api/client';

interface Domain {
  id: string;
  name: string;
  status: string;
  description: string;
}

interface DomainSelectorProps {
  selected: string;
  onChange: (domain: string) => void;
}

const DomainSelector: React.FC<DomainSelectorProps> = ({ selected, onChange }) => {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get('/domains/')
      .then(res => {
        const list = res.data?.domains || [];
        setDomains(list);
        setError(null);
      })
      .catch(err => {
        console.error(err);
        setError('Store unreachable');
        setDomains([]);
      });
  }, []);

  return (
    <div>
      <Select.Root value={selected} onValueChange={onChange}>
        <Select.Trigger className="inline-flex w-full justify-between items-center border border-gray-300 rounded px-3 py-2 bg-white text-sm">
          <Select.Value placeholder="Select a domain" />
          <Select.Icon>
            <ChevronDown className="w-4 h-4" />
          </Select.Icon>
        </Select.Trigger>

        <Select.Portal>
          <Select.Content className="overflow-hidden bg-white rounded-md shadow-lg border border-gray-200">
            <Select.Viewport className="p-1">
              {domains.map(d => (
                <Select.Item
                  key={d.id}
                  value={d.id}
                  className="flex items-center justify-between px-3 py-2 text-sm rounded hover:bg-gray-100 cursor-pointer"
                  disabled={d.status !== 'available'}
                >
                  <Select.ItemText>
                    <span>{d.name}</span>
                    {d.status !== 'available' && (
                      <span className="text-xs text-gray-400 ml-2">(Coming Soon)</span>
                    )}
                  </Select.ItemText>
                  <Select.ItemIndicator>
                    <Check className="w-4 h-4 text-blue-600" />
                  </Select.ItemIndicator>
                </Select.Item>
              ))}
            </Select.Viewport>
          </Select.Content>
        </Select.Portal>
      </Select.Root>
      {error && <p className="text-sm text-red-600 mt-1">{error}</p>}
    </div>
  );
};

export default DomainSelector;
