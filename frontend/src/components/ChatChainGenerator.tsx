import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Check, Loader2 } from 'lucide-react';
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

interface ChatChainGeneratorProps {
  sessionId: string;
  onApproved?: () => void;
}

const ChatChainGenerator: React.FC<ChatChainGeneratorProps> = ({ sessionId, onApproved }) => {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: 'Describe the workflow you want to automate. I will suggest a chain of blocks and extract any rules.',
    },
  ]);
  const [input, setInput] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentChain, setCurrentChain] = useState<Chain | null>(null);
  const [currentRules, setCurrentRules] = useState<string[]>([]);
  const [approved, setApproved] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    const userMsg = input.trim();
    setInput('');
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
          setCurrentChain(JSON.parse(data));
        } else if (event === 'rules') {
          setCurrentRules(JSON.parse(data));
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

  const handleApprove = async () => {
    try {
      await approveChain(sessionId);
      setApproved(true);
      onApproved?.();
    } catch (err) {
      console.error(err);
      alert('Failed to approve chain');
    }
  };

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
          placeholder="Describe your workflow..."
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
          {currentRules.length > 0 && (
            <div className="mt-2 text-sm text-gray-600">
              Rules: {currentRules.join(', ')}
            </div>
          )}
          <button
            onClick={handleApprove}
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
