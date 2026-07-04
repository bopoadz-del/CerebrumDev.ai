import React, { useState, useEffect, useCallback } from 'react';
import * as RadixSelect from '@radix-ui/react-select';
import { Check, ChevronDown, RefreshCw, AlertCircle } from 'lucide-react';
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

const FALLBACK_DOMAINS = ['construction', 'medical', 'legal', 'finance', 'general'];

const DomainSelector: React.FC<DomainSelectorProps> = ({ selected, onChange }) => {
  const [domains, setDomains] = useState<Domain[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [manualMode, setManualMode] = useState(false);

  const fetchDomains = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get('/domains/');
      const list = res.data?.domains || [];
      setDomains(list);
      if (list.length > 0 && !list.find((d: Domain) => d.id === selected)) {
        const firstAvailable = list.find((d: Domain) => d.status === 'available');
        if (firstAvailable) onChange(firstAvailable.id);
      }
      setManualMode(false);
    } catch (err: any) {
      console.error(err);
      setError(err.response?.data?.detail || err.message || 'Domain store unreachable');
      setDomains([]);
      setManualMode(true);
    } finally {
      setLoading(false);
    }
  }, [selected, onChange]);

  useEffect(() => {
    fetchDomains();
  }, [fetchDomains]);

  if (manualMode || error) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <select
            value={selected}
            onChange={(e) => onChange(e.target.value)}
            className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {FALLBACK_DOMAINS.map((d) => (
              <option key={d} value={d}>
                {d.charAt(0).toUpperCase() + d.slice(1)}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={fetchDomains}
            disabled={loading}
            className="p-2 border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50"
            aria-label="Retry loading domains"
            title="Retry"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {error && (
          <div className="flex items-start gap-2 text-sm text-red-600">
            <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <p>{error}</p>
              <p className="text-xs text-gray-500 mt-1">
                Using manual fallback. You can retry or select a domain above.
              </p>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <RadixSelect.Root value={selected} onValueChange={onChange} disabled={loading}>
      <RadixSelect.Trigger className="inline-flex w-full justify-between items-center border border-gray-300 rounded px-3 py-2 bg-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
        <RadixSelect.Value placeholder="Select a domain" />
        <RadixSelect.Icon>
          <ChevronDown className="w-4 h-4" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>

      <RadixSelect.Portal>
        <RadixSelect.Content className="overflow-hidden bg-white rounded-md shadow-lg border border-gray-200">
          <RadixSelect.Viewport className="p-1">
            {domains.map((d) => (
              <RadixSelect.Item
                key={d.id}
                value={d.id}
                className="flex items-center justify-between px-3 py-2 text-sm rounded hover:bg-gray-100 cursor-pointer focus:outline-none"
                disabled={d.status !== 'available'}
              >
                <RadixSelect.ItemText>
                  <span>{d.name}</span>
                  {d.status !== 'available' && (
                    <span className="text-xs text-gray-400 ml-2">(Coming Soon)</span>
                  )}
                </RadixSelect.ItemText>
                <RadixSelect.ItemIndicator>
                  <Check className="w-4 h-4 text-blue-600" />
                </RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
};

export default DomainSelector;
