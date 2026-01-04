import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  cancelSession,
  fetchToken,
  getResult,
  getSession,
  listSessions,
  runTask,
} from './api';
import { loadConfig } from './config';
import { connectSSE } from './sse';
import type { AppConfig, ResultResponse, SessionResponse, TerminalEvent } from './types';

const BOX_WIDTH = 62;

const STATUS_LABELS: Record<string, string> = {
  idle: 'Idle',
  running: 'Running',
  complete: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const STATUS_CLASS: Record<string, string> = {
  idle: 'status-idle',
  running: 'status-running',
  complete: 'status-complete',
  failed: 'status-failed',
  cancelled: 'status-cancelled',
};

const EMPTY_EVENTS: TerminalEvent[] = [];

function normalizeStatus(value: string): string {
  const statusValue = value.toLowerCase();
  if (statusValue === 'completed' || statusValue === 'complete') {
    return 'complete';
  }
  if (statusValue === 'failed' || statusValue === 'error') {
    return 'failed';
  }
  if (statusValue === 'cancelled' || statusValue === 'canceled') {
    return 'cancelled';
  }
  if (statusValue === 'running') {
    return 'running';
  }
  return statusValue || 'idle';
}

function padBoxLine(content: string): string {
  const maxContentWidth = BOX_WIDTH - 4;
  const trimmed = content.length > maxContentWidth
    ? `${content.slice(0, maxContentWidth - 3)}...`
    : content;
  return `│ ${trimmed.padEnd(maxContentWidth, ' ')} │`;
}

function buildBox(lines: string[]): string[] {
  const top = `┌${'─'.repeat(BOX_WIDTH - 2)}┐`;
  const mid = `├${'─'.repeat(BOX_WIDTH - 2)}┤`;
  const bottom = `└${'─'.repeat(BOX_WIDTH - 2)}┘`;
  return [top, padBoxLine(lines[0] ?? ''), mid, ...lines.slice(1).map(padBoxLine), bottom];
}

function formatDuration(durationMs?: number | null): string {
  if (!durationMs) {
    return '0.0s';
  }
  return durationMs < 1000
    ? `${durationMs}ms`
    : `${(durationMs / 1000).toFixed(1)}s`;
}

function formatCost(cost?: number | null): string {
  if (cost === null || cost === undefined) {
    return '$0.0000';
  }
  return `$${cost.toFixed(4)}`;
}

function formatTokens(result?: ResultResponse | null): { input: number; output: number; total: number } {
  const usage = result?.metrics?.usage;
  if (!usage) {
    return { input: 0, output: 0, total: 0 };
  }
  const input = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens;
  const output = usage.output_tokens;
  return { input, output, total: input + output };
}

function buildSyntheticEvents(session: SessionResponse, result?: ResultResponse | null): TerminalEvent[] {
  const now = new Date().toISOString();
  const events: TerminalEvent[] = [
    {
      type: 'agent_start',
      data: {
        session_id: session.id,
        model: session.model ?? 'unknown',
        tools: [],
        working_dir: session.working_dir ?? 'unknown',
        task: session.task ?? '',
      },
      timestamp: now,
      sequence: 1,
    },
  ];

  if (result) {
    events.push({
      type: 'output_display',
      data: {
        output: result.output,
        error: result.error,
        comments: result.comments,
        result_files: result.result_files,
        status: result.status,
      },
      timestamp: now,
      sequence: 2,
    });
    events.push({
      type: 'agent_complete',
      data: {
        status: result.status,
        num_turns: result.metrics?.num_turns ?? session.num_turns,
        duration_ms: result.metrics?.duration_ms ?? session.duration_ms ?? 0,
        total_cost_usd: result.metrics?.total_cost_usd ?? session.total_cost_usd ?? 0,
        session_id: session.id,
        usage: result.metrics?.usage ?? null,
        model: result.metrics?.model ?? session.model,
      },
      timestamp: now,
      sequence: 3,
    });
  }

  return events;
}

export default function App(): JSX.Element {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [token, setToken] = useState<string | null>(localStorage.getItem('agentum_token'));
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionResponse | null>(null);
  const [events, setEvents] = useState<TerminalEvent[]>(EMPTY_EVENTS);
  const [inputValue, setInputValue] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState({
    turns: 0,
    cost: 0,
    durationMs: 0,
    tokensIn: 0,
    tokensOut: 0,
    model: '',
  });

  const outputRef = useRef<HTMLDivElement | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const activeTurnRef = useRef(0);

  const isRunning = status === 'running';

  const statusLabel = STATUS_LABELS[status] ?? STATUS_LABELS.idle;
  const statusClass = STATUS_CLASS[status] ?? STATUS_CLASS.idle;
  const statusSymbol = status === 'idle' ? '○' : '●';

  useEffect(() => {
    loadConfig().then(setConfig).catch(() => setConfig(null));
  }, []);

  useEffect(() => {
    if (!config || token) {
      return;
    }

    fetchToken(config.api.base_url)
      .then((response) => {
        localStorage.setItem('agentum_token', response.access_token);
        setToken(response.access_token);
      })
      .catch((err) => {
        setError(`Failed to fetch token: ${err.message}`);
      });
  }, [config, token]);

  const refreshSessions = useCallback(() => {
    if (!config || !token) {
      return;
    }

    listSessions(config.api.base_url, token)
      .then((response) => setSessions(response.sessions))
      .catch((err) => setError(`Failed to load sessions: ${err.message}`));
  }, [config, token]);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  useEffect(() => {
    if (!outputRef.current || !config?.ui.auto_scroll) {
      return;
    }
    outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [events, config]);

  useEffect(() => {
    return () => {
      if (cleanupRef.current) {
        cleanupRef.current();
      }
    };
  }, []);

  const appendEvent = useCallback(
    (event: TerminalEvent) => {
      setEvents((prev) => {
        const next = [...prev, event];
        const maxLines = config?.ui.max_output_lines ?? 1000;
        if (next.length > maxLines) {
          return next.slice(-maxLines);
        }
        return next;
      });
    },
    [config]
  );

  const handleEvent = useCallback(
    (event: TerminalEvent) => {
      let enriched = event;

      if (event.type === 'conversation_turn') {
        const turnNumber = Number(event.data.turn_number ?? 0);
        activeTurnRef.current = turnNumber;
        setStats((prev) => ({
          ...prev,
          turns: turnNumber,
        }));
      }

      if (event.type === 'tool_start') {
        enriched = {
          ...event,
          meta: {
            ...(event.meta ?? {}),
            turn: activeTurnRef.current,
          },
        };
      }

      appendEvent(enriched);

      if (event.type === 'agent_start') {
        setStatus('running');
        setError(null);
        const sessionId = String(event.data.session_id ?? '');
        setCurrentSession((prev) => ({
          id: sessionId || prev?.id || 'unknown',
          status: 'running',
          task: (event.data.task as string | undefined) ?? prev?.task,
          model: (event.data.model as string | undefined) ?? prev?.model,
          working_dir: (event.data.working_dir as string | undefined) ?? prev?.working_dir,
          created_at: prev?.created_at ?? new Date().toISOString(),
          updated_at: new Date().toISOString(),
          completed_at: prev?.completed_at ?? null,
          num_turns: prev?.num_turns ?? 0,
          duration_ms: prev?.duration_ms ?? null,
          total_cost_usd: prev?.total_cost_usd ?? null,
          cancel_requested: prev?.cancel_requested ?? false,
        }));
        setStats((prev) => ({
          ...prev,
          model: String(event.data.model ?? prev.model ?? ''),
        }));
      }

      if (event.type === 'agent_complete') {
        const normalizedStatus = normalizeStatus(String(event.data.status ?? 'complete'));
        const usage = event.data.usage as {
          input_tokens?: number;
          output_tokens?: number;
          cache_creation_input_tokens?: number;
          cache_read_input_tokens?: number;
        } | undefined;
        const tokensIn = usage
          ? (usage.input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
          : undefined;
        setStats((prev) => ({
          ...prev,
          turns: Number(event.data.num_turns ?? prev.turns),
          durationMs: Number(event.data.duration_ms ?? prev.durationMs),
          cost: Number(event.data.total_cost_usd ?? prev.cost),
          tokensIn: tokensIn ?? prev.tokensIn,
          tokensOut: usage?.output_tokens ?? prev.tokensOut,
        }));
        setStatus(normalizedStatus);
        refreshSessions();
      }

      if (event.type === 'cancelled') {
        setStatus('cancelled');
        refreshSessions();
      }

      if (event.type === 'error') {
        setStatus('failed');
        setError(String(event.data.message ?? 'Unknown error'));
      }

      if (event.type === 'output_display') {
        const statusValue = String(event.data.status ?? '');
        if (statusValue) {
          setStatus(normalizeStatus(statusValue));
        }
      }
    },
    [appendEvent, refreshSessions]
  );

  const startSSE = useCallback(
    (sessionId: string) => {
      if (!config || !token) {
        return;
      }

      if (cleanupRef.current) {
        cleanupRef.current();
      }

      cleanupRef.current = connectSSE(
        config.api.base_url,
        sessionId,
        token,
        (event) => handleEvent(event),
        (err) => setError(err.message)
      );
    },
    [config, token, handleEvent]
  );

  const handleSubmit = async (): Promise<void> => {
    if (!config || !token || !inputValue.trim()) {
      return;
    }

    setError(null);
    setStatus('running');
    setEvents([]);
    activeTurnRef.current = 0;
    setStats((prev) => ({
      ...prev,
      turns: 0,
      cost: 0,
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
    }));

    try {
      const response = await runTask(config.api.base_url, token, inputValue.trim());
      const sessionId = response.session_id;
      setCurrentSession({
        id: sessionId,
        status: response.status,
        task: inputValue.trim(),
        model: null,
        working_dir: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        completed_at: null,
        num_turns: 0,
        duration_ms: null,
        total_cost_usd: null,
        cancel_requested: false,
      });
      setInputValue('');
      startSSE(sessionId);
      refreshSessions();
    } catch (err) {
      setStatus('failed');
      setError(`Failed to start task: ${(err as Error).message}`);
    }
  };

  const handleCancel = async (): Promise<void> => {
    if (!config || !token || !currentSession) {
      return;
    }

    try {
      await cancelSession(config.api.base_url, token, currentSession.id);
      setStatus('cancelled');
    } catch (err) {
      setError(`Failed to cancel: ${(err as Error).message}`);
    }
  };

  const handleSelectSession = async (sessionId: string): Promise<void> => {
    if (!config || !token) {
      return;
    }

    try {
      const session = await getSession(config.api.base_url, token, sessionId);
      setCurrentSession(session);

      let result: ResultResponse | null = null;
      if (session.status !== 'running') {
        result = await getResult(config.api.base_url, token, sessionId);
      }

      setEvents(buildSyntheticEvents(session, result));
      if (result?.metrics) {
        const tokens = formatTokens(result);
        setStats({
          turns: result.metrics.num_turns,
          cost: result.metrics.total_cost_usd ?? 0,
          durationMs: result.metrics.duration_ms ?? 0,
          tokensIn: tokens.input,
          tokensOut: tokens.output,
          model: result.metrics.model ?? session.model ?? '',
        });
      }

      setStatus(normalizeStatus(session.status));

      if (session.status === 'running') {
        startSSE(sessionId);
      }
    } catch (err) {
      setError(`Failed to load session: ${(err as Error).message}`);
    }
  };

  const handleNewSession = (): void => {
    if (cleanupRef.current) {
      cleanupRef.current();
    }
    setCurrentSession(null);
    setEvents([]);
    setStatus('idle');
    setStats({
      turns: 0,
      cost: 0,
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
      model: '',
    });
  };

  const sessionIdLabel = currentSession?.id
    ? `${currentSession.id.slice(0, 8)}...`
    : 'new';

  const renderEvent = useCallback((event: TerminalEvent, index: number) => {
    switch (event.type) {
      case 'agent_start': {
        const sessionId = String(event.data.session_id ?? 'unknown');
        const model = String(event.data.model ?? 'unknown');
        const lines = buildBox([
          '★ AGENTUM | Self-Improving Agent',
          `⚡ SESSION ${sessionId}`,
          `• Model: ${model}`,
        ]);
        return (
          <pre key={`${event.sequence}-${index}`} className="terminal-box">
            {lines.join('\n')}
          </pre>
        );
      }
      case 'tool_start': {
        const toolName = String(event.data.tool_name ?? 'Tool');
        const turn = event.meta?.turn ? ` [${event.meta.turn}]` : '';
        const input = event.data.tool_input;
        const inputLines = typeof input === 'object' && input
          ? Object.entries(input as Record<string, unknown>)
              .slice(0, 6)
              .map(([key, value]) => {
                const formatted = typeof value === 'string' ? value : JSON.stringify(value);
                const trimmed = formatted.length > 80 ? `${formatted.slice(0, 77)}...` : formatted;
                return `  │ • ${key}: ${trimmed}`;
              })
          : [];
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-block">
            <div className="terminal-line">
              <span className="event-icon">⚙</span>
              <span className="event-dim">{turn}</span>
              <span className="event-tool">{toolName}</span>
            </div>
            {inputLines.length > 0 && (
              <pre className="terminal-subline">{inputLines.join('\n')}</pre>
            )}
          </div>
        );
      }
      case 'tool_complete': {
        const toolName = String(event.data.tool_name ?? 'Tool');
        const durationMs = Number(event.data.duration_ms ?? 0);
        const isError = Boolean(event.data.is_error);
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-block">
            <div className={`terminal-line ${isError ? 'event-error' : 'event-success'}`}>
              <span className="event-icon">└─</span>
              <span className="event-tool">{toolName}</span>
              <span>{isError ? 'FAILED' : 'OK'}</span>
              <span className="event-dim">({durationMs}ms)</span>
            </div>
          </div>
        );
      }
      case 'thinking': {
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-line event-thinking">
            <span className="event-icon">❯</span>
            <span>{String(event.data.text ?? '')}</span>
          </div>
        );
      }
      case 'message': {
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-line event-message">
            <span className="event-icon">✦</span>
            <span>{String(event.data.text ?? '')}</span>
          </div>
        );
      }
      case 'profile_switch': {
        const profileName = String(event.data.profile_name ?? 'profile');
        const allowCount = Number(event.data.allow_rules_count ?? 0);
        const denyCount = Number(event.data.deny_rules_count ?? 0);
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-line event-dim">
            profile: <span className="event-highlight">{profileName}</span>
            <span className="event-dim"> [allow={allowCount}, deny={denyCount}]</span>
          </div>
        );
      }
      case 'output_display': {
        const output = String(event.data.output ?? '').trim();
        const errorText = String(event.data.error ?? '').trim();
        const comments = String(event.data.comments ?? '').trim();
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-block">
            {output && (
              <pre className="terminal-output-block">{output}</pre>
            )}
            {comments && (
              <div className="terminal-line event-comment">{comments}</div>
            )}
            {errorText && (
              <div className="terminal-line event-error">{errorText}</div>
            )}
          </div>
        );
      }
      case 'agent_complete': {
        const statusValue = String(event.data.status ?? 'COMPLETE').toUpperCase();
        const durationMs = Number(event.data.duration_ms ?? 0);
        const numTurns = Number(event.data.num_turns ?? 0);
        const cost = Number(event.data.total_cost_usd ?? 0);
        const lines = buildBox([
          `✓ ${statusValue}`,
          `Duration: ${formatDuration(durationMs)} | Turns: ${numTurns} | Cost: ${formatCost(cost)}`,
        ]);
        return (
          <pre key={`${event.sequence}-${index}`} className="terminal-box event-complete">
            {lines.join('\n')}
          </pre>
        );
      }
      case 'error': {
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-line event-error">
            ✖ {String(event.data.message ?? 'Unknown error')}
          </div>
        );
      }
      case 'cancelled': {
        return (
          <div key={`${event.sequence}-${index}`} className="terminal-line event-warning">
            ● Cancelled: {String(event.data.message ?? 'Task was cancelled')}
          </div>
        );
      }
      default: {
        return (
          <pre key={`${event.sequence}-${index}`} className="terminal-line event-dim">
            {JSON.stringify(event.data)}
          </pre>
        );
      }
    }
  }, []);

  const sessionItems = useMemo(() => {
    return sessions.map((session) => (
      <button
        key={session.id}
        className="session-item"
        onClick={() => handleSelectSession(session.id)}
        type="button"
      >
        <div className="session-item-row">
          <span className="session-id">{session.id.slice(0, 8)}...</span>
          <span className={`session-status ${session.status}`}>{session.status}</span>
        </div>
        <div className="session-task">{session.task || 'No task'}</div>
      </button>
    ));
  }, [sessions]);

  return (
    <div className="terminal-app">
      <header className="terminal-menu">
        <div className="menu-left">
          <span className="menu-logo">[::] AGENTUM</span>
          <div className="session-dropdown">
            <span className="session-current">{sessionIdLabel}</span>
            <div className="session-list">{sessionItems}</div>
          </div>
          <button className="menu-button" type="button" onClick={handleNewSession}>
            + New
          </button>
        </div>
        <div className="menu-right">
          <span className="menu-title">Agentum | Self-Improving Agent</span>
        </div>
      </header>

      <main className="terminal-body">
        <div ref={outputRef} className="terminal-output">
          {events.length === 0 ? (
            <div className="terminal-empty">Enter a task below to begin.</div>
          ) : (
            events.map(renderEvent)
          )}
        </div>

        <div className="terminal-input">
          <div className="input-header">
            <span className="input-label">❯ input</span>
            <span className="input-shortcut">Ctrl+Enter</span>
          </div>
          <div className="input-row">
            <textarea
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder="Enter task..."
              className="input-textarea"
              disabled={!config || !token || isRunning}
            />
            <div className="input-actions">
              <button
                className="execute-button"
                type="button"
                onClick={handleSubmit}
                disabled={!inputValue.trim() || !config || !token || isRunning}
              >
                {isRunning ? 'Running...' : 'Execute'}
              </button>
              {isRunning && currentSession && (
                <button className="cancel-button" type="button" onClick={handleCancel}>
                  Cancel
                </button>
              )}
            </div>
          </div>
          {error && <div className="terminal-error">{error}</div>}
        </div>
      </main>

      <footer className="terminal-status">
        <div className="status-left">
          <span className={`status-indicator ${statusClass}`}>{statusSymbol}</span>
          <span>{statusLabel}</span>
          <span className="status-metric">Turns: {stats.turns}</span>
        </div>
        <div className="status-right">
          <span className="status-metric">Tokens: {stats.tokensIn} in / {stats.tokensOut} out</span>
          <span className="status-metric">Cost: {formatCost(stats.cost)}</span>
          <span className="status-metric">{formatDuration(stats.durationMs)}</span>
        </div>
      </footer>
    </div>
  );
}
