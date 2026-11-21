import React, { useState, useEffect, useRef } from 'react';

// API Base URL from environment variable with fallback
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

// Generate a random short UUID session ID
function generateSessionId() {
  const rand = Math.random().toString(36).substring(2, 10);
  const time = Date.now().toString(36).slice(-4);
  return `s-${rand}-${time}`;
}

export default function App() {
  const [sessionId, setSessionId] = useState(generateSessionId);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [isSystemPromptOpen, setIsSystemPromptOpen] = useState(false);
  const [simulateFailure, setSimulateFailure] = useState('');
  const [isDevMenuOpen, setIsDevMenuOpen] = useState(false);

  const [inputMessage, setInputMessage] = useState('');
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [activeError, setActiveError] = useState(null);
  const [healthStatus, setHealthStatus] = useState(null);

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  // Auto-scroll to bottom of messages
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading, activeError]);

  // Check health on mount
  useEffect(() => {
    async function checkHealth() {
      try {
        const res = await fetch(`${API_BASE_URL}/health`);
        if (res.ok) {
          const data = await res.json();
          setHealthStatus(data);
        } else {
          setHealthStatus({ status: 'error' });
        }
      } catch (e) {
        setHealthStatus({ status: 'disconnected' });
      }
    }
    checkHealth();
  }, []);

  // Reset to a new session
  const handleNewSession = () => {
    setSessionId(generateSessionId());
    setMessages([]);
    setActiveError(null);
    setInputMessage('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  // Adjust textarea height automatically
  const handleTextareaChange = (e) => {
    setInputMessage(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 180)}px`;
  };

  // Handle message submission
  const handleSendMessage = async (e) => {
    if (e) e.preventDefault();
    const trimmed = inputMessage.trim();
    if (!trimmed || isLoading) return;

    // Immediately clear input and reset textarea height
    setInputMessage('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    setActiveError(null);

    // Optimistically append user message to UI
    const userMsg = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };
    setMessages((prev) => [...prev, userMsg]);
    setIsLoading(true);

    // Prepare payload (only send session_id, message, and optional system_prompt)
    const payload = {
      session_id: sessionId,
      message: trimmed,
    };
    if (systemPrompt.trim()) {
      payload.system_prompt = systemPrompt.trim();
    }

    // Prepare request headers
    const headers = {
      'Content-Type': 'application/json',
    };
    if (simulateFailure.trim()) {
      headers['X-Simulate-Failure'] = simulateFailure.trim();
    }

    try {
      const response = await fetch(`${API_BASE_URL}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        let errDetail = 'Request failed';
        try {
          const errData = await response.json();
          if (errData.detail) {
            if (typeof errData.detail === 'string') {
              errDetail = errData.detail;
            } else if (Array.isArray(errData.detail)) {
              errDetail = errData.detail.map((d) => d.msg || JSON.stringify(d)).join(', ');
            } else {
              errDetail = JSON.stringify(errData.detail);
            }
          }
        } catch {
          errDetail = `HTTP ${response.status} ${response.statusText}`;
        }

        setActiveError({
          status: response.status,
          message: errDetail,
        });
        setIsLoading(false);
        return;
      }

      const data = await response.json();
      const assistantMsg = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: data.reply,
        metadata: data.metadata,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, assistantMsg]);
    } catch (err) {
      setActiveError({
        status: 'network_error',
        message: 'Unable to reach the server. Please verify the backend is running.',
      });
    } finally {
      setIsLoading(false);
    }
  };

  // Enter to send, Shift+Enter for newline
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="app-container">
      {/* Top Header */}
      <header className="app-header">
        <div className="header-brand">
          <div className="brand-logo">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
            </svg>
          </div>
          <div>
            <div className="brand-title">ResilioChat</div>
            <div className="brand-subtitle">Resilient High-Availability LLM Gateway</div>
          </div>
        </div>

        <div className="header-actions">
          {/* Health Badge */}
          <div className="status-indicator">
            <span
              className={`status-dot ${
                healthStatus?.status === 'ok' ? 'status-online' : 'status-offline'
              }`}
            />
            <span className="status-text">
              {healthStatus?.status === 'ok'
                ? `Online (${healthStatus.keys_configured} ${
                    healthStatus.keys_configured === 1 ? 'key' : 'keys'
                  })`
                : 'Offline'}
            </span>
          </div>

          {/* Session Pill */}
          <div className="session-tag" title="Active Session ID">
            <span className="session-label">Session:</span>
            <code className="session-id">{sessionId}</code>
          </div>

          {/* System Prompt Toggle Button */}
          <button
            type="button"
            className={`btn-secondary ${isSystemPromptOpen ? 'btn-active' : ''}`}
            onClick={() => setIsSystemPromptOpen(!isSystemPromptOpen)}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M12 20h9M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
            </svg>
            System Prompt
            {systemPrompt.trim() && <span className="pill-dot" />}
          </button>

          {/* Failover Simulator Button */}
          <button
            type="button"
            className={`btn-secondary ${isDevMenuOpen ? 'btn-active' : ''}`}
            onClick={() => setIsDevMenuOpen(!isDevMenuOpen)}
            title="Simulate upstream failure headers"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
            </svg>
            Failover Sim
            {simulateFailure.trim() && <span className="pill-dot warning" />}
          </button>

          {/* New Session Button */}
          <button type="button" className="btn-secondary" onClick={handleNewSession} title="Start new session">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
              <path d="M21 3v5h-5" />
              <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
              <path d="M8 16H3v5" />
            </svg>
            New Session
          </button>
        </div>
      </header>

      {/* Collapsible System Prompt Drawer */}
      {isSystemPromptOpen && (
        <div className="drawer-panel">
          <div className="drawer-inner">
            <div className="drawer-header">
              <label htmlFor="system-prompt-input" className="drawer-label">
                Custom Session System Prompt (Optional)
              </label>
              <button
                type="button"
                className="btn-text-close"
                onClick={() => setIsSystemPromptOpen(false)}
              >
                Close
              </button>
            </div>
            <textarea
              id="system-prompt-input"
              data-testid="system-prompt-input"
              className="drawer-textarea"
              placeholder="e.g. You are a senior DevOps architect providing concise commands and best practices..."
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={2}
            />
            <div className="drawer-hint">
              Overrides the default system prompt for all turns in this session.
            </div>
          </div>
        </div>
      )}

      {/* Developer Failure Simulation Drawer */}
      {isDevMenuOpen && (
        <div className="drawer-panel dev-panel">
          <div className="drawer-inner">
            <div className="drawer-header">
              <span className="drawer-label">Upstream Failure Simulator (`X-Simulate-Failure`)</span>
              <button
                type="button"
                className="btn-text-close"
                onClick={() => setIsDevMenuOpen(false)}
              >
                Close
              </button>
            </div>
            <div className="dev-inputs">
              <input
                type="text"
                className="drawer-input"
                placeholder="e.g. key_1=429, key_1=500, or *=429"
                value={simulateFailure}
                onChange={(e) => setSimulateFailure(e.target.value)}
              />
              <div className="dev-presets">
                <button
                  type="button"
                  className="preset-chip"
                  onClick={() => setSimulateFailure('key_1=429')}
                >
                  key_1=429
                </button>
                <button
                  type="button"
                  className="preset-chip"
                  onClick={() => setSimulateFailure('key_1=500')}
                >
                  key_1=500
                </button>
                <button
                  type="button"
                  className="preset-chip"
                  onClick={() => setSimulateFailure('*=429')}
                >
                  *=429 (All Fail)
                </button>
                <button
                  type="button"
                  className="preset-chip clear"
                  onClick={() => setSimulateFailure('')}
                >
                  Clear
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Main Conversation Stream */}
      <main className="conversation-container">
        {messages.length === 0 && !activeError && (
          <div className="empty-state">
            <div className="empty-state-badge">Zero-Downtime Architecture</div>
            <h2 className="empty-state-title">Resilient Chat Gateway</h2>
            <p className="empty-state-desc">
              Multi-key failover with instant LiteLLM response caching and SQLite session history.
            </p>
            <div className="quick-suggestions">
              <button
                type="button"
                className="suggestion-card"
                onClick={() => {
                  setInputMessage('Explain how LiteLLM handles transparent failover across multiple API keys.');
                }}
              >
                <div className="suggestion-title">Explain Key Failover</div>
                <div className="suggestion-snippet">How automatic fallback occurs on HTTP 429 quota limits.</div>
              </button>
              <button
                type="button"
                className="suggestion-card"
                onClick={() => {
                  setInputMessage('What is semantic vs exact prompt caching in LLM gateways?');
                }}
              >
                <div className="suggestion-title">Cache Efficiency</div>
                <div className="suggestion-snippet">Benchmark latency reduction from caching duplicate messages.</div>
              </button>
            </div>
          </div>
        )}

        <div className="messages-list">
          {messages.map((msg) => (
            <div key={msg.id} className={`message-row ${msg.role}-row`}>
              {msg.role === 'user' ? (
                <div className="user-message-bubble">
                  <div className="message-content">{msg.content}</div>
                  <div className="message-time">{msg.timestamp}</div>
                </div>
              ) : (
                <div className="assistant-message-container" data-testid="assistant-message">
                  <div className="assistant-header">
                    <div className="assistant-avatar">
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                      </svg>
                    </div>
                    <span className="assistant-name">Assistant</span>
                    <span className="message-time">{msg.timestamp}</span>
                  </div>

                  <div className="message-content assistant-content">{msg.content}</div>

                  {/* Metadata telemetry card */}
                  {msg.metadata && (
                    <div className="metadata-bar">
                      {/* Resolution Source */}
                      <span
                        className={`meta-chip source-chip ${
                          msg.metadata.resolution_source === 'LLM_CACHE' ? 'cache-hit' : 'live-hit'
                        }`}
                        data-testid="meta-source"
                      >
                        {msg.metadata.resolution_source}
                      </span>

                      {/* Model */}
                      <span className="meta-chip model-chip" data-testid="meta-model" title="Inference Model">
                        {msg.metadata.model}
                      </span>

                      {/* Latency */}
                      <span className="meta-chip latency-chip" data-testid="meta-latency" title="Server Latency">
                        {`${msg.metadata.latency_ms} ms`}
                      </span>

                      {/* Input Tokens */}
                      <span className="meta-chip token-chip" title="Prompt Tokens">
                        In: <strong data-testid="meta-input-tokens">{msg.metadata.input_tokens}</strong>
                      </span>

                      {/* Output Tokens */}
                      <span className="meta-chip token-chip" title="Completion Tokens">
                        Out: <strong data-testid="meta-output-tokens">{msg.metadata.output_tokens}</strong>
                      </span>

                      {/* Key ID if available */}
                      {msg.metadata.key_id && (
                        <span className="meta-chip key-chip" title="Active Key ID">
                          Key: {msg.metadata.key_id}
                        </span>
                      )}

                      {/* Fallback attempts if any */}
                      {msg.metadata.fallback_attempts > 0 && (
                        <span className="meta-chip warning-chip" title="Fallback Retries Prior to Success">
                          Retries: {msg.metadata.fallback_attempts}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}

          {/* Loading Indicator */}
          {isLoading && (
            <div className="message-row assistant-row">
              <div className="assistant-message-container loading-container">
                <div className="assistant-header">
                  <div className="assistant-avatar pulse">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                    </svg>
                  </div>
                  <span className="assistant-name">Generating response...</span>
                </div>
                <div className="typing-dots">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                </div>
              </div>
            </div>
          )}

          {/* Error Message Container (Inside Chat Area) */}
          {activeError && (
            <div className="error-message-banner" role="alert">
              <div className="error-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
              </div>
              <div className="error-details">
                <div className="error-title">
                  {activeError.status === 503
                    ? 'All Providers Unavailable (503)'
                    : activeError.status === 422
                    ? 'Invalid Request (422)'
                    : 'Request Error'}
                </div>
                <div className="error-body">{activeError.message}</div>
              </div>
              <button
                type="button"
                className="error-dismiss"
                onClick={() => setActiveError(null)}
                title="Dismiss error"
              >
                ×
              </button>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </main>

      {/* Input Composer */}
      <footer className="composer-container">
        <form className="composer-form" onSubmit={handleSendMessage}>
          <div className="composer-wrapper">
            <textarea
              ref={textareaRef}
              data-testid="chat-input"
              className="chat-textarea"
              placeholder="Send a message... (Shift+Enter for new line)"
              rows={1}
              value={inputMessage}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              disabled={isLoading}
            />

            <div className="composer-footer">
              <div className="composer-hint">
                <span>Enter</span> to send &bull; <span>Shift+Enter</span> for new line
              </div>

              <button
                type="submit"
                data-testid="send-button"
                className="send-button"
                disabled={isLoading || !inputMessage.trim()}
                title="Send message"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <line x1="22" y1="2" x2="11" y2="13" />
                  <polygon points="22 2 15 22 11 13 2 9 22 2" />
                </svg>
              </button>
            </div>
          </div>
        </form>
      </footer>
    </div>
  );
}
