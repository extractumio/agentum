import type {
  ResultResponse,
  SessionListResponse,
  SessionResponse,
  TaskStartedResponse,
  TokenResponse,
} from './types';

async function apiRequest<T>(
  baseUrl: string,
  path: string,
  options: RequestInit = {},
  token?: string
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function fetchToken(baseUrl: string): Promise<TokenResponse> {
  return apiRequest<TokenResponse>(baseUrl, '/api/v1/auth/token', { method: 'POST' });
}

export async function listSessions(
  baseUrl: string,
  token: string
): Promise<SessionListResponse> {
  return apiRequest<SessionListResponse>(baseUrl, '/api/v1/sessions', {}, token);
}

export async function getSession(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<SessionResponse> {
  return apiRequest<SessionResponse>(baseUrl, `/api/v1/sessions/${sessionId}`, {}, token);
}

export async function runTask(
  baseUrl: string,
  token: string,
  task: string
): Promise<TaskStartedResponse> {
  return apiRequest<TaskStartedResponse>(
    baseUrl,
    '/api/v1/sessions/run',
    {
      method: 'POST',
      body: JSON.stringify({
        task,
        config: {},
      }),
    },
    token
  );
}

export async function cancelSession(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<void> {
  await apiRequest<{ status: string }>(
    baseUrl,
    `/api/v1/sessions/${sessionId}/cancel`,
    { method: 'POST' },
    token
  );
}

export async function getResult(
  baseUrl: string,
  token: string,
  sessionId: string
): Promise<ResultResponse> {
  return apiRequest<ResultResponse>(baseUrl, `/api/v1/sessions/${sessionId}/result`, {}, token);
}
