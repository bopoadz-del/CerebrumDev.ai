import React, { useRef, useEffect, useState } from 'react';
import { Send, User, Bot, Loader2, X, RotateCcw } from 'lucide-react';
import { useChatStream, ChatMessage } from '../../hooks/useChatStream';
import { Button } from '../ui/Button';

interface ChatSidebarProps {
  sessionId: string;
  onCommand?: (command: string, args: any) => void;
}

interface Chain {
  blocks: { id: string; params: Record<string, unknown> }[];
  connections: { from: number; to: number }[];
}

export const ChatSidebar: React.FC<ChatSidebarProps> = ({ sessionId, onCommand }) => {
  const { messages, isStreaming, error, sendMessage, cancel, setMessages } = useChatStream(sessionId);
  const [input, setInput] = useState('');
  const [currentChain, setCurrentChain] = useState<Chain | null>(null);
  const [currentRules, setCurrentRules] = useState<string[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isStreaming]);

  const handleSend = async () => {
    if (!input.trim() || isStreaming) return;
    const text = input;
    setInput('');
    await sendMessage(text, (event, data) => {
      if (event === 'chain') {
        setCurrentChain(data);
      } else if (event === 'rules') {
        setCurrentRules(data);
      } else if (event === 'command') {
        onCommand?.(data.command, data.args);
      }
    });
  };

  const clearChat = () => {
    cancel();
    setMessages([
      {
        role: 'assistant',
        content: 'Describe the workflow you want to automate, or type a command like "set domain to medical".',
      },
    ]);
    setCurrentChain(null);
    setCurrentRules([]);
  };

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
        <div>
          <h2 className="font-semibold text-gray-900">AI Assistant</h2>
          <p className="text-xs text-gray-500">{sessionId}</p>
        </div>
        <button
          onClick={clearChat}
          className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded"
          aria-label="Clear chat"
          title="Clear chat"
        >
          <RotateCcw className="w-4 h-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((m, idx) => (
          <MessageBubble key={idx} message={m} />
        ))}
        {isStreaming && (
          <div className="flex items-center space-x-2 text-gray-500 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>AI is thinking...</span>
          </div>
        )}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-md p-3 text-sm text-red-700 flex items-start gap-2">
            <X className="w-4 h-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {currentChain && (
        <div className="px-4 py-3 border-t border-gray-100 bg-gray-50">
          <p className="text-xs font-medium text-gray-500 mb-2">Proposed chain</p>
          <div className="flex flex-wrap items-center gap-1.5">
            {currentChain.blocks.map((block, idx) => (
              <React.Fragment key={idx}>
                <span className="px-2 py-1 bg-white border border-gray-200 rounded text-xs">{block.id}</span>
                {idx < currentChain.blocks.length - 1 && (
                  <span className="text-gray-400 text-xs">→</span>
                )}
              </React.Fragment>
            ))}
          </div>
          {currentRules.length > 0 && (
            <p className="text-xs text-gray-500 mt-2">Rules: {currentRules.join(', ')}</p>
          )}
        </div>
      )}

      <div className="p-3 border-t border-gray-200">
        <div className="flex space-x-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleSend()}
            placeholder="Ask or command..."
            disabled={isStreaming}
            className="flex-1 min-w-0 border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {isStreaming ? (
            <Button onClick={cancel} variant="outline" size="md">
              <X className="w-4 h-4" />
            </Button>
          ) : (
            <Button onClick={handleSend} disabled={!input.trim()} size="md">
              <Send className="w-4 h-4" />
            </Button>
          )}
        </div>
        <p className="text-xs text-gray-400 mt-2">
          Try: “set domain to medical” or “use Mistral-7B”
        </p>
      </div>
    </div>
  );
};

const MessageBubble: React.FC<{ message: ChatMessage }> = ({ message }) => {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`flex items-start gap-2 max-w-[90%] ${
          isUser ? 'flex-row-reverse' : ''
        }`}
      >
        <div
          className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 border ${
            isUser ? 'bg-gray-100 border-gray-200' : 'bg-blue-50 border-blue-200'
          }`}
        >
          {isUser ? <User className="w-3.5 h-3.5" /> : <Bot className="w-3.5 h-3.5 text-blue-600" />}
        </div>
        <div
          className={`px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${
            isUser
              ? 'bg-blue-600 text-white rounded-br-none'
              : 'bg-gray-100 text-gray-800 rounded-bl-none border border-gray-200'
          }`}
        >
          {message.content}
        </div>
      </div>
    </div>
  );
};
