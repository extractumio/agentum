import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  cancelSession,
  continueTask,
  fetchToken,
  getResult,
  getSession,
  listSessions,
  runTask,
} from './api';
import { loadConfig } from './config';
import { connectSSE } from './sse';
import type { AppConfig, ResultResponse, SessionResponse, TerminalEvent } from './types';

type ConversationItem =
  | {
      type: 'user';
      id: string;
      time: string;
      content: string;
    }
  | {
      type: 'agent_message';
      id: string;
      time: string;
      content: string;
      toolCalls: ToolCallView[];
      comments?: string;
      files?: string[];
    }
  | {
      type: 'output';
      id: string;
      time: string;
      outputParts: string[];
      commentParts: string[];
      files: string[];
      status: 'complete' | 'partial' | 'failed' | 'running';
      error?: string;
    };

type ToolCallView = {
  id: string;
  tool: string;
  time: string;
  status: 'running' | 'complete' | 'failed';
  durationMs?: number;
  input?: unknown;
  output?: string;
  thinking?: string;
  error?: string;
  suggestion?: string;
};

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

const TOOL_COLOR_CLASS: Record<string, string> = {
  Read: 'tool-read',
  Bash: 'tool-bash',
  Write: 'tool-write',
  WebFetch: 'tool-webfetch',
  Output: 'tool-output',
  Think: 'tool-think',
};

const TOOL_SYMBOL: Record<string, string> = {
  Read: '◉',
  Bash: '▶',
  Write: '✎',
  WebFetch: '⬡',
  Output: '◈',
  Think: '◇',
};

const OUTPUT_STATUS_CLASS: Record<string, string> = {
  complete: 'output-status-complete',
  partial: 'output-status-partial',
  failed: 'output-status-failed',
  running: 'output-status-running',
};

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
  if (statusValue === 'partial') {
    return 'partial';
  }
  return statusValue || 'idle';
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

function formatTimestamp(timestamp?: string): string {
  if (!timestamp) {
    return '--:--:--';
  }
  const date = new Date(timestamp);
  return date.toLocaleTimeString('en-US', { hour12: false });
}

function renderMarkdown(text: string): JSX.Element[] {
  const lines = text.split('\n');
  const elements: JSX.Element[] = [];
  let inCodeBlock = false;
  let codeLines: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    if (line.trim().startsWith('```')) {
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${i}`} className="md-code-block">
            {codeLines.join('\n')}
          </pre>
        );
        codeLines = [];
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    let element: JSX.Element;

    if (line.startsWith('### ')) {
      element = (
        <div key={i} className="md-h3">
          {renderInlineMarkdown(line.slice(4))}
        </div>
      );
    } else if (line.startsWith('## ')) {
      element = (
        <div key={i} className="md-h2">
          {renderInlineMarkdown(line.slice(3))}
        </div>
      );
    } else if (line.startsWith('# ')) {
      element = (
        <div key={i} className="md-h1">
          {renderInlineMarkdown(line.slice(2))}
        </div>
      );
    } else if (/^[-—─]{3,}$/.test(line.trim())) {
      element = <hr key={i} className="md-hr" />;
    } else if (line.trimStart().startsWith('- ') || line.trimStart().startsWith('* ')) {
      const indent = line.length - line.trimStart().length;
      element = (
        <div key={i} className="md-li" style={{ marginLeft: indent * 4 }}>
          • {renderInlineMarkdown(line.trimStart().slice(2))}
        </div>
      );
    } else if (/^\s*\d+\.\s/.test(line)) {
      const match = line.match(/^(\s*)(\d+)\.\s(.*)$/);
      if (match) {
        const [, spaces, num, content] = match;
        element = (
          <div key={i} className="md-li" style={{ marginLeft: (spaces?.length ?? 0) * 4 }}>
            {num}. {renderInlineMarkdown(content)}
          </div>
        );
      } else {
        element = <div key={i}>{renderInlineMarkdown(line)}</div>;
      }
    } else if (line.trim() === '') {
      element = <div key={i} className="md-spacer" />;
    } else {
      element = <div key={i}>{renderInlineMarkdown(line)}</div>;
    }

    elements.push(element);
  }

  if (inCodeBlock && codeLines.length > 0) {
    elements.push(
      <pre key="code-final" className="md-code-block">
        {codeLines.join('\n')}
      </pre>
    );
  }

  return elements;
}

function renderInlineMarkdown(text: string): (string | JSX.Element)[] {
  const result: (string | JSX.Element)[] = [];
  let key = 0;

  const regex = /(\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`|\[([^\]]+)\]\(([^)]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    const [fullMatch, , bold, italic, code, linkText, linkUrl] = match;

    if (bold) {
      result.push(
        <strong key={key++} className="md-bold">
          {bold}
        </strong>
      );
    } else if (italic) {
      result.push(
        <em key={key++} className="md-italic">
          {italic}
        </em>
      );
    } else if (code) {
      result.push(
        <code key={key++} className="md-code">
          {code}
        </code>
      );
    } else if (linkText && linkUrl) {
      result.push(
        <a key={key++} href={linkUrl} className="md-link" target="_blank" rel="noopener noreferrer">
          {linkText}
        </a>
      );
    }

    lastIndex = match.index + fullMatch.length;
  }

  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  return result.length > 0 ? result : [text];
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
  const events: TerminalEvent[] = [];

  if (session.task) {
    events.push({
      type: 'user_message',
      data: { text: session.task },
      timestamp: now,
      sequence: 0,
    });
  }

  events.push({
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
  });

  if (result) {
    // Create a message event with output text so output_display can attach files to it
    events.push({
      type: 'message',
      data: {
        text: result.output || 'Task completed.',
      },
      timestamp: now,
      sequence: 2,
    });
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
      sequence: 3,
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
      sequence: 4,
    });
  }

  return events;
}

function formatToolInput(input: unknown): string {
  let obj: unknown = input;
  
  // If input is a string, try to parse it as JSON
  if (typeof input === 'string') {
    try {
      obj = JSON.parse(input);
    } catch {
      // Not valid JSON, return the string as-is
      return input;
    }
  }
  
  // If it's an object, format it with custom replacer to unescape newlines in display
  if (typeof obj === 'object' && obj !== null) {
    const formatted = JSON.stringify(obj, null, 2);
    // Replace escaped newlines with actual newlines for display (but preserve JSON structure)
    return formatted
      .replace(/\\n/g, '\n')
      .replace(/\\t/g, '\t');
  }
  
  return String(input);
}

function formatToolName(name: string): string {
  if (name.startsWith('mcp_agentum_')) {
    const suffix = name.slice('mcp_agentum_'.length);
    const capitalized = suffix
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join('');
    return `Agentum${capitalized}`;
  }
  return name;
}

function ToolTag({ type, count, showSymbol = true }: { type: string; count?: number; showSymbol?: boolean }): JSX.Element {
  const colorClass = TOOL_COLOR_CLASS[type] ?? 'tool-read';
  const symbol = TOOL_SYMBOL[type] ?? TOOL_SYMBOL.Read;
  const displayName = formatToolName(type);

  return (
    <span className={`tool-tag ${colorClass}`}>
      {showSymbol && <span className="tool-symbol">{symbol}</span>}
      <span className="tool-name">{displayName}</span>
      {count !== undefined && (
        <span className="tool-count">×{count}</span>
      )}
    </span>
  );
}

const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

function AgentSpinner(): JSX.Element {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setFrame((prev) => (prev + 1) % SPINNER_FRAMES.length);
    }, 80);
    return () => clearInterval(interval);
  }, []);

  return (
    <span className="agent-spinner">
      <span className="agent-spinner-char">{SPINNER_FRAMES[frame]}</span>
      <span className="agent-spinner-label">processing...</span>
    </span>
  );
}

function MessageBlock({ sender, time, content }: { sender: string; time: string; content: string }): JSX.Element {
  return (
    <div className="message-block user-message">
      <div className="message-header">
        <span className="message-icon">⟩</span>
        <span className="message-sender">{sender}</span>
        <span className="message-time">@ {time}</span>
      </div>
      <div className="message-content">{content}</div>
    </div>
  );
}

function ToolCallBlock({
  tool,
  expanded,
  onToggle,
  isLast,
}: {
  tool: ToolCallView;
  expanded: boolean;
  onToggle: () => void;
  isLast: boolean;
}): JSX.Element {
  const hasContent = Boolean(tool.thinking || tool.input || tool.output || tool.error);
  const treeChar = isLast ? '└──' : '├──';
  const previewSource = tool.output ?? tool.input;
  const previewText = previewSource
    ? String(typeof previewSource === 'string' ? previewSource : JSON.stringify(previewSource))
    : '';

  return (
    <div className="tool-call">
      <div className="tool-call-header" onClick={hasContent ? onToggle : undefined} role="button">
        <span className="tool-tree">{treeChar}</span>
        {hasContent && <span className="tool-toggle">{expanded ? '▼' : '▶'}</span>}
        <ToolTag type={tool.tool} showSymbol={false} />
        <span className="tool-time">@ {tool.time}</span>
        {!expanded && previewText && (
          <span className="tool-preview">
            — {previewText.slice(0, 60)}
            {previewText.length > 60 ? '...' : ''}
          </span>
        )}
      </div>
      {expanded && hasContent && (
        <div className="tool-call-body">
          {tool.thinking && (
            <div className="tool-thinking">💭 {tool.thinking}</div>
          )}
          {tool.input !== undefined && tool.input !== null && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ command ─────────</div>
              <pre className="tool-section-body">{formatToolInput(tool.input)}</pre>
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {tool.output && (
            <div className="tool-section">
              <div className="tool-section-title">┌─ output ──────────</div>
              <pre className="tool-section-body tool-output">{tool.output}</pre>
              <div className="tool-section-title">└────────────────────</div>
            </div>
          )}
          {tool.error && (
            <div className="tool-error">
              <div className="tool-error-title">⚠ ERROR: {tool.error}</div>
              {tool.suggestion && <div className="tool-suggestion">→ {tool.suggestion}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AgentMessageBlock({
  time,
  content,
  toolCalls,
  toolExpanded,
  onToggleTool,
  status,
  comments,
  commentsExpanded,
  onToggleComments,
  files,
  filesExpanded,
  onToggleFiles,
}: {
  time: string;
  content: string;
  toolCalls: ToolCallView[];
  toolExpanded: Set<string>;
  onToggleTool: (id: string) => void;
  status?: string;
  comments?: string;
  commentsExpanded?: boolean;
  onToggleComments?: () => void;
  files?: string[];
  filesExpanded?: boolean;
  onToggleFiles?: () => void;
}): JSX.Element {
  const statusClass = status ? `agent-status-${status}` : '';
  const hasResult = Boolean(comments) || (files && files.length > 0);

  return (
    <div className={`message-block agent-message ${statusClass}`}>
      <div className="message-header">
        <span className="message-icon">◆</span>
        <span className="message-sender">AGENT</span>
        <span className="message-time">@ {time}</span>
      </div>
      <div className="message-content md-container">
        {content ? renderMarkdown(content) : <AgentSpinner />}
      </div>
      {hasResult && (
        <div className="result-section">
          <div className="result-title">Result</div>
          {comments && (
            <div className="result-item">
              <div className="result-item-header" onClick={onToggleComments} role="button">
                <span className="result-tree">└──</span>
                <span className="result-toggle">{commentsExpanded ? '▼' : '▶'}</span>
                <span className="result-label">Comments</span>
                <span className="result-count">({comments.length})</span>
              </div>
              {commentsExpanded && (
                <div className="result-item-body md-container">
                  {renderMarkdown(comments)}
                </div>
              )}
            </div>
          )}
          {files && files.length > 0 && (
            <div className="result-item">
              <div className="result-item-header" onClick={onToggleFiles} role="button">
                <span className="result-tree">{comments ? '└──' : '└──'}</span>
                <span className="result-toggle">{filesExpanded ? '▼' : '▶'}</span>
                <span className="result-label">Files</span>
                <span className="result-count">({files.length})</span>
              </div>
              {filesExpanded && (
                <div className="result-item-body result-files-list">
                  {files.map((file, index) => (
                    <div key={index} className="result-file-item">
                      <span className="result-file-icon">📄</span>
                      <span className="result-file-name">{file}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
      {toolCalls.length > 0 && (
        <div className="tool-call-section">
          <div className="tool-call-title">Tool Calls ({toolCalls.length})</div>
          {toolCalls.map((tool, index) => (
            <ToolCallBlock
              key={tool.id}
              tool={tool}
              expanded={toolExpanded.has(tool.id)}
              onToggle={() => onToggleTool(tool.id)}
              isLast={index === toolCalls.length - 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function OutputBlock({
  time,
  outputParts,
  commentParts,
  commentsExpanded,
  onToggleComments,
  files,
  status,
  error,
  onFileAction,
}: {
  time: string;
  outputParts: string[];
  commentParts: string[];
  commentsExpanded: boolean;
  onToggleComments: () => void;
  files: string[];
  status: 'complete' | 'partial' | 'failed' | 'running';
  error?: string;
  onFileAction: (filePath: string, mode: 'view' | 'download') => void;
}): JSX.Element {
  const statusClass = OUTPUT_STATUS_CLASS[status] ?? '';

  return (
    <div className={`message-block output-block ${statusClass}`}>
      <div className="message-header">
        <span className="message-icon">◆</span>
        <span className="message-sender">OUTPUT</span>
        <span className="message-time">@ {time}</span>
      </div>
      <div className="message-content md-container">
        {outputParts.length > 0
          ? outputParts.map((part, index) => (
              <div key={`output-${index}`} className="output-part">
                {renderMarkdown(part)}
              </div>
            ))
          : 'No output yet.'}
      </div>
      {commentParts.length > 0 && (
        <div className="output-comments">
          <button className="comment-toggle" type="button" onClick={onToggleComments}>
            {commentsExpanded ? '▼' : '▶'} comments
          </button>
          {commentsExpanded && (
            <div className="comment-body md-container">
              {commentParts.map((part, index) => (
                <div key={`comment-${index}`} className="output-part">
                  {renderMarkdown(part)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {files.length > 0 && (
        <div className="output-files">
          {files.map((file) => (
            <div key={file} className="output-file-row">
              <span className="output-file-name">{file}</span>
              <button type="button" className="output-file-action" onClick={() => onFileAction(file, 'view')}>
                view
              </button>
              <button type="button" className="output-file-action" onClick={() => onFileAction(file, 'download')}>
                download
              </button>
            </div>
          ))}
        </div>
      )}
      {error && <div className="output-error">{error}</div>}
    </div>
  );
}

function InputField({
  value,
  onChange,
  onSubmit,
  onCancel,
  isRunning,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isRunning: boolean;
}): JSX.Element {
  return (
    <div className="input-shell">
      <div className="input-row">
        <span className="input-prompt">⟩</span>
        <textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              if (!isRunning) {
                onSubmit();
              }
            }
          }}
          placeholder="Enter your request..."
          className="input-textarea"
          disabled={isRunning}
          rows={1}
        />
        {isRunning ? (
          <button className="input-button cancel" type="button" onClick={onCancel}>
            ■ Cancel
          </button>
        ) : (
          <button className="input-button" type="button" onClick={onSubmit}>
            Send ↵
          </button>
        )}
      </div>
      <div className="input-footer">
        <span className="input-footer-item">📎 Attach</span>
        <span className="input-footer-item">📁 Files</span>
        <span className="input-divider">│</span>
        <span className="input-footer-item">[skills]</span>
        <span className="input-footer-item">[model ▼]</span>
      </div>
    </div>
  );
}

function StatusFooter({
  isRunning,
  statusLabel,
  statusClass,
  stats,
  connected,
}: {
  isRunning: boolean;
  statusLabel: string;
  statusClass: string;
  stats: {
    turns: number;
    tokensIn: number;
    tokensOut: number;
    cost: number;
    durationMs: number;
  };
  connected: boolean;
}): JSX.Element {
  return (
    <div className="terminal-status">
      <div className="status-left">
        <span className={`status-connection ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? '🟢 Connected' : '🔴 Disconnected'}
        </span>
        <span className="status-divider">│</span>
        <span className={`status-state ${statusClass}`}>
          {isRunning ? (
            <>
              <span className="status-spinner">◐</span> Running...
            </>
          ) : (
            <>{statusLabel === 'Idle' ? '● Idle' : statusLabel}</>
          )}
        </span>
      </div>
      <div className="status-right">
        <span className="status-metric">Turns: <strong>{stats.turns}</strong></span>
        <span className="status-metric">Tokens: <strong>{stats.tokensIn}</strong> in / <strong>{stats.tokensOut}</strong> out</span>
        <span className="status-metric cost">${stats.cost.toFixed(4)}</span>
        <span className="status-metric">{formatDuration(stats.durationMs)}</span>
      </div>
    </div>
  );
}

export default function App(): JSX.Element {
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [token, setToken] = useState<string | null>(localStorage.getItem('agentum_token'));
  const [userId, setUserId] = useState<string>('');
  const [sessions, setSessions] = useState<SessionResponse[]>([]);
  const [currentSession, setCurrentSession] = useState<SessionResponse | null>(null);
  const [events, setEvents] = useState<TerminalEvent[]>(EMPTY_EVENTS);
  const [inputValue, setInputValue] = useState('');
  const [status, setStatus] = useState('idle');
  const [error, setError] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [filter, setFilter] = useState<'all' | 'complete' | 'partial' | 'failed'>('all');
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedComments, setExpandedComments] = useState<Set<string>>(new Set());
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
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
        setUserId(response.user_id);
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
        const eventSessionId = String(event.data.session_id ?? '');
        setCurrentSession((prev) => ({
          id: prev?.id || eventSessionId || 'unknown',
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
        const newTokensIn = usage
          ? (usage.input_tokens ?? 0) + (usage.cache_creation_input_tokens ?? 0) + (usage.cache_read_input_tokens ?? 0)
          : 0;
        const newTokensOut = usage?.output_tokens ?? 0;

        setStats((prev) => ({
          ...prev,
          turns: prev.turns + Number(event.data.num_turns ?? 0),
          durationMs: prev.durationMs + Number(event.data.duration_ms ?? 0),
          cost: prev.cost + Number(event.data.total_cost_usd ?? 0),
          tokensIn: prev.tokensIn + newTokensIn,
          tokensOut: prev.tokensOut + newTokensOut,
        }));
        setStatus(normalizedStatus);

        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: normalizedStatus,
                completed_at: new Date().toISOString(),
                num_turns: prev.num_turns + Number(event.data.num_turns ?? 0),
              }
            : null
        );

        refreshSessions();
      }

      if (event.type === 'cancelled') {
        setStatus('cancelled');
        setCurrentSession((prev) =>
          prev
            ? {
                ...prev,
                status: 'cancelled',
                completed_at: new Date().toISOString(),
              }
            : null
        );
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
        (event) => {
          setReconnecting(false);
          handleEvent(event);
        },
        (err) => {
          setReconnecting(false);
          setError(err.message);
        },
        (attempt) => {
          setReconnecting(true);
          setError(`Connection lost. Reconnecting (attempt ${attempt})...`);
        }
      );
    },
    [config, token, handleEvent]
  );

  const handleSubmit = async (): Promise<void> => {
    if (!config || !token || !inputValue.trim()) {
      return;
    }

    const taskText = inputValue.trim();
    setError(null);
    setStatus('running');
    activeTurnRef.current = 0;
    const userEvent: TerminalEvent = {
      type: 'user_message',
      data: { text: taskText },
      timestamp: new Date().toISOString(),
      sequence: Date.now(),
    };

    const shouldContinue = currentSession && currentSession.status !== 'running';

    if (shouldContinue) {
      appendEvent(userEvent);
      try {
        const response = await continueTask(
          config.api.base_url,
          token,
          currentSession.id,
          taskText
        );

        setCurrentSession((prev) => ({
          ...prev!,
          status: response.status,
          updated_at: new Date().toISOString(),
        }));

        setInputValue('');
        startSSE(currentSession.id);
        refreshSessions();
      } catch (err) {
        setStatus('failed');
        setError(`Failed to continue task: ${(err as Error).message}`);
      }
    } else {
      setEvents([userEvent]);
      setExpandedTools(new Set());
      setExpandedComments(new Set());
      setExpandedFiles(new Set());
      setStats({
        turns: 0,
        cost: 0,
        durationMs: 0,
        tokensIn: 0,
        tokensOut: 0,
        model: '',
      });

      try {
        const response = await runTask(config.api.base_url, token, taskText);
        const sessionId = response.session_id;
        setCurrentSession({
          id: sessionId,
          status: response.status,
          task: taskText,
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
    setExpandedTools(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
    setStats({
      turns: 0,
      cost: 0,
      durationMs: 0,
      tokensIn: 0,
      tokensOut: 0,
      model: '',
    });
  };

  const conversation = useMemo<ConversationItem[]>(() => {
    const items: ConversationItem[] = [];
    let pendingTools: ToolCallView[] = [];
    // Capture result data from mcp__agentum__WriteOutput tool calls
    // Using separate variables to avoid TypeScript's overly strict control flow narrowing
    let capturedComments: string | undefined;
    let capturedFiles: string[] | undefined;
    let capturedResultAttached = false;

    const findOpenTool = (toolName: string): ToolCallView | undefined => {
      for (let i = pendingTools.length - 1; i >= 0; i -= 1) {
        const tool = pendingTools[i];
        if (tool.tool === toolName && tool.status === 'running') {
          return tool;
        }
      }
      return undefined;
    };

    const flushPendingTools = (timestamp?: string) => {
      if (pendingTools.length > 0) {
        items.push({
          type: 'agent_message',
          id: `agent-auto-${items.length}`,
          time: formatTimestamp(timestamp),
          content: '',
          toolCalls: pendingTools,
        });
        pendingTools = [];
      }
    };

    // Parse result_files from WriteOutput - can be JSON string or array
    const parseResultFiles = (files: unknown): string[] => {
      if (Array.isArray(files)) {
        return files.map(String);
      }
      if (typeof files === 'string') {
        try {
          const parsed = JSON.parse(files);
          if (Array.isArray(parsed)) {
            return parsed.map(String);
          }
        } catch {
          // Not valid JSON, treat as single file
          if (files.trim()) {
            return [files.trim()];
          }
        }
      }
      return [];
    };

    let toolIdCounter = 0;

    events.forEach((event) => {
      switch (event.type) {
        case 'user_message': {
          const content = String(event.data.text ?? '');
          items.push({
            type: 'user',
            id: `user-${items.length}`,
            time: formatTimestamp(event.timestamp),
            content,
          });
          break;
        }
        case 'thinking': {
          pendingTools.push({
            id: `think-${toolIdCounter++}`,
            tool: 'Think',
            time: formatTimestamp(event.timestamp),
            status: 'complete',
            thinking: String(event.data.text ?? ''),
          });
          break;
        }
        case 'tool_start': {
          const toolName = String(event.data.tool_name ?? 'Tool');
          const toolInput = event.data.tool_input as Record<string, unknown> | undefined;
          
          pendingTools.push({
            id: `tool-${toolIdCounter++}`,
            tool: toolName,
            time: formatTimestamp(event.timestamp),
            status: 'running',
            input: toolInput ?? '',
          });
          
          // Extract result data from WriteOutput tool calls
          // These contain status, output, comments, result_files
          if (toolName.includes('WriteOutput') || toolName.includes('write_output')) {
            if (toolInput) {
              const comments = String(toolInput.comments ?? '').trim();
              const files = parseResultFiles(toolInput.result_files);
              
              if (comments) {
                capturedComments = comments;
              }
              if (files.length > 0) {
                capturedFiles = files;
              }
              capturedResultAttached = false;
            }
          }
          break;
        }
        case 'tool_complete': {
          const toolName = String(event.data.tool_name ?? 'Tool');
          const durationMs = Number(event.data.duration_ms ?? 0);
          const isError = Boolean(event.data.is_error);
          const result = event.data.result;
          const tool = findOpenTool(toolName);
          if (tool) {
            tool.status = isError ? 'failed' : 'complete';
            tool.durationMs = durationMs;
            if (result !== undefined && result !== null) {
              tool.output = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
            }
            if (isError) {
              tool.error = String(event.data.error ?? 'Tool failed');
            }
          }
          break;
        }
        case 'message': {
          const text = String(event.data.text ?? '').trim();
          const agentMessage: ConversationItem = {
            type: 'agent_message',
            id: `agent-${items.length}`,
            time: formatTimestamp(event.timestamp),
            content: text,
            toolCalls: pendingTools,
          };
          
          // Attach pending result data from WriteOutput tool
          if (!capturedResultAttached && (capturedComments || capturedFiles)) {
            if (capturedComments) {
              (agentMessage as { comments?: string }).comments = capturedComments;
            }
            if (capturedFiles && capturedFiles.length > 0) {
              (agentMessage as { files?: string[] }).files = capturedFiles;
            }
            capturedResultAttached = true;
          }
          
          items.push(agentMessage);
          pendingTools = [];
          break;
        }
        case 'output_display': {
          if (pendingTools.length > 0) {
            flushPendingTools(event.timestamp);
          }
          const output = String(event.data.output ?? '').trim();
          const comments = String(event.data.comments ?? '').trim();
          const errorText = String(event.data.error ?? '').trim();
          const files = Array.isArray(event.data.result_files)
            ? (event.data.result_files as string[])
            : [];
          const statusValue = normalizeStatus(String(event.data.status ?? 'complete')) as
            | 'complete'
            | 'partial'
            | 'failed'
            | 'running';


          // Find the last agent message WITH CONTENT to attach comments and files
          // Skip empty auto-flushed messages (created by flushPendingTools)
          let foundAgentMessage = false;
          
          if (comments || files.length > 0) {
            for (let i = items.length - 1; i >= 0; i--) {
              const item = items[i];
              if (item.type === 'agent_message') {
                const hasContent = item.content && item.content.trim() !== '';
                const hasWriteOutputTool = item.toolCalls.some(
                  (t) => t.tool.includes('WriteOutput') || t.tool.includes('Output')
                );
                
                if (hasContent || hasWriteOutputTool) {
                  if (comments) {
                    (item as { comments?: string }).comments = comments;
                  }
                  if (files.length > 0) {
                    (item as { files?: string[] }).files = files;
                  }
                  foundAgentMessage = true;
                  break;
                }
              }
            }
          }

          // If no suitable agent message found, try any agent_message as fallback
          if (!foundAgentMessage && (comments || files.length > 0)) {
            for (let i = items.length - 1; i >= 0; i--) {
              if (items[i].type === 'agent_message') {
                if (comments) {
                  (items[i] as { comments?: string }).comments = comments;
                }
                if (files.length > 0) {
                  (items[i] as { files?: string[] }).files = files;
                }
                foundAgentMessage = true;
                break;
              }
            }
          }

          // If still no agent message found, create one
          if (!foundAgentMessage && (comments || files.length > 0)) {
            items.push({
              type: 'agent_message',
              id: `agent-output-${items.length}`,
              time: formatTimestamp(event.timestamp),
              content: output || 'Task completed.',
              toolCalls: [],
              comments: comments || undefined,
              files: files.length > 0 ? files : undefined,
            });
          }

          items.push({
            type: 'output',
            id: `output-${items.length}`,
            time: formatTimestamp(event.timestamp),
            outputParts: output ? [output] : [],
            commentParts: comments ? [comments] : [],
            files,
            status: statusValue,
            error: errorText || undefined,
          });
          break;
        }
        default:
          break;
      }
    });

    if (pendingTools.length > 0) {
      flushPendingTools();
    }

    // Attach any remaining pending result to the last agent message if not yet attached
    if (!capturedResultAttached && (capturedComments || capturedFiles)) {
      for (let i = items.length - 1; i >= 0; i--) {
        const item = items[i];
        if (item.type === 'agent_message') {
          if (capturedComments) {
            item.comments = capturedComments;
          }
          if (capturedFiles && capturedFiles.length > 0) {
            item.files = capturedFiles;
          }
          break;
        }
      }
    }

    return items;
  }, [events]);

  const toolStats = useMemo(() => {
    const statsMap: Record<string, number> = {};
    conversation.forEach((item) => {
      if (item.type === 'agent_message') {
        item.toolCalls.forEach((tool) => {
          const toolName = tool.tool;
          statsMap[toolName] = (statsMap[toolName] ?? 0) + 1;
        });
      }
    });
    return statsMap;
  }, [conversation]);

  const totalToolCalls = Object.values(toolStats).reduce((sum, count) => sum + count, 0);

  const headerStats = useMemo(() => {
    const counts = { complete: 0, partial: 0, failed: 0 };
    conversation.forEach((item) => {
      if (item.type === 'output') {
        if (item.status === 'complete') {
          counts.complete += 1;
        } else if (item.status === 'partial') {
          counts.partial += 1;
        } else if (item.status === 'failed') {
          counts.failed += 1;
        }
      }
    });
    return counts;
  }, [conversation]);

  const outputItems = useMemo(() => conversation.filter((item) => item.type === 'output'), [conversation]);

  const sessionDuration = formatDuration(stats.durationMs);
  const sessionIdLabel = currentSession?.id ?? 'new';

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

  const handleFileAction = async (filePath: string, mode: 'view' | 'download') => {
    if (!config || !token || !currentSession) {
      return;
    }

    try {
      const response = await fetch(
        `${config.api.base_url}/api/v1/sessions/${currentSession.id}/files?path=${encodeURIComponent(filePath)}`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (!response.ok) {
        throw new Error(`Failed to fetch file: ${response.status}`);
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const filename = filePath.split('/').pop() || 'result-file';

      if (mode === 'view') {
        window.open(url, '_blank', 'noopener,noreferrer');
      } else {
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.click();
      }

      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(`Failed to load file: ${(err as Error).message}`);
    }
  };

  const toggleTool = (id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleComments = (id: string) => {
    setExpandedComments((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const toggleFiles = (id: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const expandAllSections = () => {
    const allToolIds = conversation.flatMap((item) =>
      item.type === 'agent_message' ? item.toolCalls.map((tool) => tool.id) : []
    );
    const allOutputIds = outputItems.map((item) => item.id);
    const allAgentMessageIds = conversation
      .filter((item) => item.type === 'agent_message')
      .map((item) => item.id);
    setExpandedTools(new Set(allToolIds));
    setExpandedComments(new Set([...allOutputIds, ...allAgentMessageIds]));
    setExpandedFiles(new Set(allAgentMessageIds));
  };

  const collapseAllSections = () => {
    setExpandedTools(new Set());
    setExpandedComments(new Set());
    setExpandedFiles(new Set());
  };

  const toggleAllSections = () => {
    if (expandedTools.size > 0 || expandedComments.size > 0 || expandedFiles.size > 0) {
      collapseAllSections();
    } else {
      expandAllSections();
    }
  };

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isRunning) {
        handleCancel();
      }
      if (event.key === '/' && event.ctrlKey) {
        event.preventDefault();
        toggleAllSections();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [handleCancel, isRunning, toggleAllSections]);

  return (
    <div className="terminal-app">
      <header className="terminal-header">
        <div className="header-top">
          <div className="header-title">
            <span className="header-icon">◆</span>
            <span className="header-label">AGENT SESSION</span>
            <span className="header-divider">│</span>
            <span className="header-meta">{sessionIdLabel}</span>
            <span className="header-divider">│</span>
            <span className="header-meta">user: {userId || 'unknown'}</span>
          </div>
          <div className="session-dropdown">
            <span className="session-current">session list</span>
            <div className="session-list">
              {sessionItems}
              <button className="session-item session-new" type="button" onClick={handleNewSession}>
                + New Session
              </button>
            </div>
          </div>
        </div>
        <div className="header-stats">
          <span>Messages: <strong>{conversation.length}</strong></span>
          <span>Duration: <strong>{sessionDuration}</strong></span>
          <span>Tools: <strong>{totalToolCalls}</strong></span>
          <span className="header-status">
            <span className="status-complete">✓ {headerStats.complete}</span>
            <span className="status-partial">◐ {headerStats.partial}</span>
            <span className="status-failed">✗ {headerStats.failed}</span>
          </span>
        </div>
        <div className="header-filters">
          <span className="filter-label">Filter:</span>
          {(['all', 'complete', 'partial', 'failed'] as const).map((item) => (
            <button
              key={item}
              className={`filter-button ${filter === item ? 'active' : ''}`}
              type="button"
              onClick={() => setFilter(item)}
            >
              [{item}]
            </button>
          ))}
          <div className="filter-actions">
            <button className="filter-button" type="button" onClick={expandAllSections}>
              [expand all]
            </button>
            <button className="filter-button" type="button" onClick={collapseAllSections}>
              [collapse all]
            </button>
          </div>
        </div>
      </header>

      <main className="terminal-body">
        <div ref={outputRef} className="terminal-output">
          {conversation.length === 0 ? (
            <div className="terminal-empty">Enter a task below to begin.</div>
          ) : (
            conversation.map((item, index) => {
              if (item.type === 'user') {
                return (
                  <MessageBlock
                    key={item.id}
                    sender="USER"
                    time={item.time}
                    content={item.content}
                  />
                );
              }
              if (item.type === 'agent_message') {
                const isLastAgentMessage = conversation
                  .slice(index + 1)
                  .every((i) => i.type !== 'agent_message');
                const messageStatus = isLastAgentMessage && status !== 'running' ? status : undefined;
                return (
                  <AgentMessageBlock
                    key={item.id}
                    time={item.time}
                    content={item.content}
                    toolCalls={item.toolCalls}
                    toolExpanded={expandedTools}
                    onToggleTool={toggleTool}
                    status={messageStatus}
                    comments={item.comments}
                    commentsExpanded={expandedComments.has(item.id)}
                    onToggleComments={() => toggleComments(item.id)}
                    files={item.files}
                    filesExpanded={expandedFiles.has(item.id)}
                    onToggleFiles={() => toggleFiles(item.id)}
                  />
                );
              }
              if (item.type === 'output') {
                if (filter !== 'all' && item.status !== filter) {
                  return null;
                }
                return (
                  <OutputBlock
                    key={item.id}
                    time={item.time}
                    outputParts={item.outputParts}
                    commentParts={item.commentParts}
                    commentsExpanded={expandedComments.has(item.id)}
                    onToggleComments={() => toggleComments(item.id)}
                    files={item.files}
                    status={item.status}
                    error={item.error}
                    onFileAction={handleFileAction}
                  />
                );
              }
              return null;
            })
          )}
        </div>
      </main>

      <div className="terminal-footer">
        <div className="tool-usage-bar">
          <span className="tool-usage-label">Tool Usage ({totalToolCalls} calls):</span>
          {Object.keys(toolStats).map((tool) => (
            <ToolTag key={tool} type={tool} count={toolStats[tool]} />
          ))}
        </div>
        <div className="input-wrapper">
          <InputField
            value={inputValue}
            onChange={setInputValue}
            onSubmit={handleSubmit}
            onCancel={handleCancel}
            isRunning={isRunning}
          />
          {error && <div className={reconnecting ? 'terminal-warning' : 'terminal-error'}>{error}</div>}
        </div>
        <StatusFooter
          isRunning={isRunning}
          statusLabel={statusLabel}
          statusClass={statusClass}
          stats={stats}
          connected={Boolean(token) && !reconnecting}
        />
      </div>
    </div>
  );
}
