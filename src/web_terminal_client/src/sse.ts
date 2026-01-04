import type { SSEEvent } from './types';

export function connectSSE(
  baseUrl: string,
  sessionId: string,
  token: string,
  onEvent: (event: SSEEvent) => void,
  onError: (error: Error) => void
): () => void {
  const url = `${baseUrl}/api/v1/sessions/${sessionId}/events?token=${encodeURIComponent(token)}`;
  const source = new EventSource(url);

  source.onmessage = (event) => {
    try {
      const parsed = JSON.parse(event.data) as SSEEvent;
      onEvent(parsed);
    } catch (error) {
      onError(new Error('Failed to parse SSE payload'));
    }
  };

  source.onerror = () => {
    onError(new Error('SSE connection error'));
    source.close();
  };

  return () => source.close();
}
