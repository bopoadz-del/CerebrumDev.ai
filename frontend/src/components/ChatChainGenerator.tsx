import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Check, Loader2, Package, Layers } from 'lucide-react';
import { useToast } from '../hooks/useToast';
import { postChatMessage, approveChain } from '../api/client';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface ChainBlock {
  id: string;
  params: Record<string, any>;
}

interface ChainConnection {
  from: number;
  to: number;
}

interface Chain {
  blocks: ChainBlock[];
  connections: ChainConnection[];
}

interface BlueprintCapability {
  id: string;
  block_ids?: string[];
}

interface BlueprintPayload {
  ok: boolean;
  source?: string;
  summary: string;
  blueprint?: {
    product_id?: string;
    product_name?: string;
    vertical?: string;
    capabilities?: BlueprintCapability[];
  };
}

interface GenerationPayload {
  ok: boolean;
  summary: string;
  generation?: {
    product_id?: string;
    output_dir?: string;
    inputs_hash?: string;
  };
}

interface ChatChainGeneratorProps {
  sessionId: string;
  onApproved?: () => void;
}

const ChatChainGenerator: React.FC<ChatChainGeneratorProps> = ({ sessionId, onApproved }) => {
  const { addToast } = useToast();
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content:
        'Describe the workflow you want to automate and I will suggest a chain of blocks. ' +
        'Or say "new platform: <what it should do>" and I will draft a product blueprint for you to approve.',
    },
  ]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentChain, setCurrentChain] = useState<Chain | null>(null);
  const [currentQuality, setCurrentQuality] = useState<any | null>(null);
  const [currentRules, setCurrentRules] = useState<string[]>([]);
  const [approved, setApproved] = useState(false);
  const [blueprint, setBlueprint] = useState<BlueprintPayload | null>(null);
  const [generation, setGeneration] = useState<GenerationPayload | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const sendMessage = async (userMsg: string) => {
    if (!userMsg.trim() || isStreaming) return;
    setMessages(prev => [...prev, { role: 'user', content: userMsg }]);
    setIsStreaming(true);

    let assistantText = '';
    try {
      await postChatMessage(sessionId, userMsg, (event, data) => {
        if (event === 'delta') {
          assistantText += data;
          setMessages(prev => {
            const last = prev[prev.length - 1];
            if (last && last.role === 'assistant' && !approved) {
              return [...prev.slice(0, -1), { ...last, content: assistantText }];
            }
            return [...prev, { role: 'assistant', content: assistantText }];
          });
        } else if (event === 'chain') {
          const parsed = JSON.parse(data);
          if (parsed && parsed.chain) {
            setCurrentChain(parsed.chain);
            setCurrentQuality(parsed.quality ?? null);
          } else {
            setCurrentChain(parsed);
            setCurrentQuality(null);
          }
        } else if (event === 'rules') {
          setCurrentRules(JSON.parse(data));
        } else if (event === 'blueprint') {
          // Platform-creation flow: a draft blueprint is parked on the session,
          // waiting for the user to approve (chat message "approve").
          setGeneration(null);
          setBlueprint(JSON.parse(data));
        } else if (event === 'generation') {
          // Blueprint approved and the product was generated.
          setBlueprint(null);
          setGeneration(JSON.parse(data));
        } else if (event === 'error') {
          setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${data}` }]);
        }
      });
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { role: 'assistant', content: 'Failed to get response from AI.' }]);
    } finally {
      setIsStreaming(false);
    }
  };

  const handleSend = async () => {
    const userMsg = input.trim();
    if (!userMsg) return;
    setInput('');
    await sendMessage(userMsg);
  };

  const handleApproveChain = async () => {
    try {
      await approveChain(sessionId);
      setApproved(true);
      onApproved?.();
    } catch (err) {
      console.error(err);
      addToast({ type: 'error', title: 'Approval failed', message: 'Failed to approve chain.' });
    }
  };

  const handleApproveBlueprint = async () => {
    // The backend intercepts "approve" when a blueprint is pending and runs
    // plan + generate, streaming back a `generation` event.
    await sendMessage('approve');
  };

  const capabilities = blueprint?.blueprint?.capabilities ?? [];
  const blockCount = new Set(
    capabilities.flatMap(c => c.block_ids ?? []),
  ).size;

  return (
    <div className="bg-white rounded-lg shadow p-6 space-y-4">
      <h2 className="text-xl font-semibold">Phase 3: Design Workflow with AI</h2>

      <div className="h-80 overflow-y-auto border rounded p-4 space-y-3 bg-gray-50">
        {messages.map((m, idx) => (
          <div key={idx} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`flex items-start space-x-2 max-w-[80%] ${m.role === 'user' ? 'flex-row-reverse space-x-reverse' : ''}`}>
              <div className="w-8 h-8 rounded-full flex items-center justify-center bg-white border">
                {m.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>
              <div className={`px-4 py-2 rounded-lg text-sm ${m.role === 'user' ? 'bg-blue-600 text-white' : 'bg-white border'}`}>
                {m.content}
              </div>
            </div>
          </div>
        ))}
        {isStreaming && (
          <div className="flex items-center space-x-2 text-gray-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>AI is thinking...</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="flex space-x-2">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSend()}
          placeholder="Describe your workflow, or: new platform: <idea>"
          disabled={isStreaming || approved}
          className="flex-1 border rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
        <button
          onClick={handleSend}
          disabled={isStreaming || approved || !input.trim()}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>

      {blueprint && !generation && (
        <div className="border rounded p-4 bg-indigo-50 border-indigo-200">
          <div className="flex items-center space-x-2 mb-2">
            <Layers className="w-4 h-4 text-indigo-600" />
            <h3 className="text-sm font-semibold text-indigo-900">
              Draft Blueprint: {blueprint.blueprint?.product_name ?? 'Untitled product'}
            </h3>
          </div>
          <div className="text-sm text-indigo-900 space-y-1">
            <div>
              <span className="font-medium">Vertical:</span> {blueprint.blueprint?.vertical ?? '—'}
              {blueprint.source === 'golden_steward' && (
                <span className="ml-2 px-2 py-0.5 bg-indigo-600 text-white rounded text-xs">golden blueprint</span>
              )}
            </div>
            <div>
              <span className="font-medium">Capabilities:</span> {capabilities.length}
              {' · '}
              <span className="font-medium">Blocks:</span> {blockCount}
            </div>
            {capabilities.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-1">
                {capabilities.map((c, idx) => (
                  <span key={idx} className="px-2 py-0.5 bg-white border border-indigo-200 rounded text-xs">
                    {c.id}
                  </span>
                ))}
              </div>
            )}
          </div>
          <button
            onClick={handleApproveBlueprint}
            disabled={isStreaming}
            className="mt-3 flex items-center space-x-1 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
          >
            <Check className="w-4 h-4" />
            <span>Approve & Generate Product</span>
          </button>
          <div className="mt-1 text-xs text-indigo-700">
            Or refine your brief in the chat — a new draft replaces this one.
          </div>
        </div>
      )}

      {generation && (
        <div className="border rounded p-4 bg-green-50 border-green-200">
          <div className="flex items-center space-x-2 mb-2">
            <Package className="w-4 h-4 text-green-600" />
            <h3 className="text-sm font-semibold text-green-900">
              Product Generated: {generation.generation?.product_id ?? 'unknown'}
            </h3>
          </div>
          <div className="text-sm text-green-900 space-y-1">
            <div>
              <span className="font-medium">Output:</span>{' '}
              <code className="text-xs bg-white border border-green-200 rounded px-1">
                {generation.generation?.output_dir ?? '—'}
              </code>
            </div>
            {generation.generation?.inputs_hash && (
              <div>
                <span className="font-medium">Inputs hash:</span>{' '}
                <code className="text-xs bg-white border border-green-200 rounded px-1">
                  {generation.generation.inputs_hash.slice(0, 12)}…
                </code>
              </div>
            )}
            <div className="text-xs text-green-700">
              Open the Deploy panel (Phase 5) to package and ship this product.
            </div>
          </div>
        </div>
      )}

      {currentChain && !approved && (
        <div className="border rounded p-4 bg-gray-50">
          <h3 className="text-sm font-semibold mb-2">Proposed Chain</h3>
          <div className="flex flex-wrap items-center gap-2">
            {currentChain.blocks.map((block, idx) => (
              <React.Fragment key={idx}>
                <div className="px-3 py-1 bg-white border rounded text-sm">{block.id}</div>
                {idx < currentChain.blocks.length - 1 && <span className="text-gray-400">→</span>}
              </React.Fragment>
            ))}
          </div>
          {currentQuality?.status === 'needs_review' && (
            <div className="mt-3 p-3 bg-yellow-50 border border-yellow-200 rounded text-sm text-yellow-800">
              <div className="font-semibold mb-1">Chain quality warning</div>
              {currentQuality.warnings?.map((warning: any, idx: number) => (
                <div key={idx} className="space-y-1">
                  <div>{warning.message}</div>
                  {warning.suggested_block && (
                    <div>Suggested block: {warning.suggested_block}</div>
                  )}
                </div>
              ))}
            </div>
          )}
          {currentRules.length > 0 && (
            <div className="mt-2 text-sm text-gray-600">
              Rules: {currentRules.join(', ')}
            </div>
          )}
          <button
            onClick={handleApproveChain}
            className="mt-3 flex items-center space-x-1 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
          >
            <Check className="w-4 h-4" />
            <span>Approve Chain</span>
          </button>
        </div>
      )}

      {approved && (
        <div className="flex items-center space-x-2 text-green-600 text-sm font-medium">
          <Check className="w-4 h-4" />
          <span>Chain approved. Moving to Phase 4.</span>
        </div>
      )}
    </div>
  );
};

export default ChatChainGenerator;
