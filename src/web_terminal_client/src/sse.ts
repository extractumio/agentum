import type { SSEEvent } from './types';

const MAX_RECONNECT_ATTEMPTS = 5;
const INITIAL_RECONNECT_DELAY_MS = 1000;

export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void,
  onReconnecting?: (attempt: number) => void
): () => void {
  let source: EventSource | null = null;
  let reconnectAttempts = 0;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let isClosed = false;
  let lastEventId: string | null = null;

  function connect() {
    if (isClosed) return;

    const url = `${baseUrl}/api/v1/sessions/${sessionId}/events?token=${encodeURIComponent(token)}`;
    source = new EventSource(url);

    source.onopen = () => {
      reconnectAttempts = 0;
    };

    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as SSEEvent;
        lastEventId = event.lastEventId;
        onEvent(parsed);
        
        // Stop reconnecting on terminal events
        if (parsed.type === 'agent_complete' || parsed.type === 'error' || parsed.type === 'cancelled') {
          isClosed = true;
        }
      } catch (error) {
        onError(new Error('Failed to parse SSE payload'));
      }
    };

    source.onerror = () => {
      if (isClosed) {
        source?.close();
        return;
      }

      source?.close();
      reconnectAttempts++;

      if (reconnectAttempts <= MAX_RECONNECT_ATTEMPTS) {
        const delay = INITIAL_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttempts - 1);
        onReconnecting?.(reconnectAttempts);
        reconnectTimeout = setTimeout(connect, delay);
      } else {
        onError(new Error('SSE connection failed after multiple attempts'));
      }
    };
  }

  connect();

  return () => {
    isClosed = true;
    if (reconnectTimeout) {
      clearTimeout(reconnectTimeout);
    }
    source?.close();
  };
}
