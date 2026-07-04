import { useState, useRef, useCallback } from 'react';
import { API_KEY } from '../api/client';

export type ChatMessageRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  role: ChatMessageRole;
  content: string;
}

export interface ChatEvent {
  event: string;
  data: any;
}

const MAX_RETRIES = 2;
const RETRY_DELAY_MS = 1000;

export function useChatStream(sessionId: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      content: 'Describe the workflow you want to automate, or type a command like "set domain to medical".',
    },
  ]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const sendMessage = useCallback(async (
    text: string,
    onEvent?: (event: string, data: any) => void
  ) => {
    if (!text.trim() || isStreaming) return;

    const userMsg = text.trim();
    setMessages((prev) => [...prev, { role: 'user', content: userMsg }]);
    setIsStreaming(true);
    setError(null);

    let assistantText = '';

    const attempt = async (retriesLeft: number): Promise<void> => {
      abortRef.current = new AbortController();
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
      };
      if (API_KEY) headers['X-API-Key'] = API_KEY;

      try {
        const response = await fetch(`/v1/sessions/${sessionId}/chat`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ message: userMsg }),
          signal: abortRef.current.signal,
        });

        if (!response.ok) {
          const body = await response.text();
          throw new Error(`HTTP ${response.status}: ${body || response.statusText}`);
        }

        if (!response.body) throw new Error('No response body');

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split('\n\n');
          buffer = blocks.pop() || '';

          for (const block of blocks) {
            const eventMatch = block.match(/^event: (.+)$/m);
            const dataMatch = block.match(/^data: (.+)$/m);
            if (!eventMatch || !dataMatch) continue;

            const event = eventMatch[1];
            let data: any = dataMatch[1];
            try {
              data = JSON.parse(data);
            } catch {
              // leave as string
            }

            if (event === 'delta') {
              assistantText += data;
              setMessages((prev) => {
                const last = prev[prev.length - 1];
                if (last && last.role === 'assistant') {
                  return [...prev.slice(0, -1), { ...last, content: assistantText }];
                }
                return [...prev, { role: 'assistant', content: assistantText }];
              });
            }

            onEvent?.(event, data);
          }
        }
      } catch (err: any) {
        if (err.name === 'AbortError') {
          setIsStreaming(false);
          return;
        }
        if (retriesLeft > 0) {
          await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
          return attempt(retriesLeft - 1);
        }
        throw err;
      } finally {
        abortRef.current = null;
      }
    };

    try {
      await attempt(MAX_RETRIES);
    } catch (err: any) {
      const msg = err.message || 'Failed to get response from AI.';
      setError(msg);
      setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
    } finally {
      setIsStreaming(false);
    }
  }, [sessionId, isStreaming]);

  return { messages, isStreaming, error, sendMessage, cancel, setMessages };
}
