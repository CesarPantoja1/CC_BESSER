/**
 * SDD API Client — communicates with the FastAPI SDD endpoints.
 *
 * Provides both REST calls (for status/spec queries) and a WebSocket
 * connection (for real-time streaming of gemini-cli output).
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SddStatus {
  installed: boolean;
  work_dir: string;
  pipeline: PipelineState;
  specs: SpecSummary[];
  gemini_running: boolean;
}

export interface PipelineState {
  phase: string;
  status: string;
  current_feature: string;
  progress_message: string;
  error: string;
  completed_phases: string[];
}

export interface SpecSummary {
  name: string;
  path: string;
  has_brief: boolean;
  has_requirements: boolean;
  has_design: boolean;
  has_tasks: boolean;
  requirement_count: number;
  task_count: number;
  tasks_completed: number;
  language: string;
}

export interface SpecFile {
  name: string;
  content: string;
  size: number;
  last_modified: number;
}

export interface SddEvent {
  type: 'phase_start' | 'output' | 'phase_complete' | 'error' | 'status' | 'specs' | 'process_done' | 'pong' | 'waiting_input' | 'session_ended';
  phase?: string;
  data?: string;
  files?: string[];
  error?: string;
  feature?: string;
  idea?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const API_BASE = '/besser_api/sdd';

/**
 * Build the WebSocket URL for the Gemini service.
 *
 * The gemini service runs as an independent WebSocket server on port 9001,
 * separate from the BESSER backend (port 9000).
 */
function getWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const geminiHost = window.location.hostname + ':9001';
  return `${protocol}//${geminiHost}`;
}

// ---------------------------------------------------------------------------
// REST API
// ---------------------------------------------------------------------------

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SDD API error ${res.status}: ${body}`);
  }
  return res.json();
}

export const sddApi = {
  /** Get pipeline status */
  getStatus(): Promise<SddStatus> {
    return fetchJson(`${API_BASE}/status`);
  },

  /** Install cc-sdd skills */
  install(language = 'es'): Promise<{ status: string; message: string }> {
    return fetchJson(`${API_BASE}/install`, {
      method: 'POST',
      body: JSON.stringify({ language }),
    });
  },

  /** List all specs */
  listSpecs(): Promise<{ specs: SpecSummary[] }> {
    return fetchJson(`${API_BASE}/specs`);
  },

  /** Get all files for a spec */
  getSpec(specName: string): Promise<{ spec_name: string; files: SpecFile[] }> {
    return fetchJson(`${API_BASE}/specs/${encodeURIComponent(specName)}`);
  },

  /** Get a single spec file */
  getSpecFile(specName: string, filename: string): Promise<SpecFile> {
    return fetchJson(
      `${API_BASE}/specs/${encodeURIComponent(specName)}/${encodeURIComponent(filename)}`,
    );
  },

  /** Run discovery (non-streaming) */
  runDiscovery(idea: string): Promise<{ status: string; output: string; files: string[] }> {
    return fetchJson(`${API_BASE}/discovery`, {
      method: 'POST',
      body: JSON.stringify({ idea }),
    });
  },

  /** Run a spec phase (non-streaming) */
  runSpecPhase(
    phase: string,
    feature: string,
    autoApprove = false,
  ): Promise<{ status: string; output: string; files: string[] }> {
    return fetchJson(`${API_BASE}/spec/${phase}`, {
      method: 'POST',
      body: JSON.stringify({ feature, auto_approve: autoApprove }),
    });
  },

  /** Run implementation (non-streaming) */
  runImpl(
    feature: string,
    taskIds?: string[],
  ): Promise<{ status: string; output: string }> {
    return fetchJson(`${API_BASE}/impl`, {
      method: 'POST',
      body: JSON.stringify({ feature, task_ids: taskIds }),
    });
  },
};

// ---------------------------------------------------------------------------
// WebSocket connection manager
// ---------------------------------------------------------------------------

export type SddEventHandler = (event: SddEvent) => void;

export class SddWebSocket {
  private ws: WebSocket | null = null;
  private handlers: Set<SddEventHandler> = new Set();
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private _isConnected = false;
  private _reconnectAttempts = 0;
  private _maxReconnectAttempts = 5;
  private _disposed = false;

  get isConnected(): boolean {
    return this._isConnected;
  }

  connect(): void {
    if (this._disposed) return;
    if (this.ws?.readyState === WebSocket.OPEN) return;

    const url = getWsUrl();
    try {
      this.ws = new WebSocket(url);
    } catch {
      console.warn('[SDD-WS] Failed to create WebSocket');
      return;
    }

    this.ws.onopen = () => {
      this._isConnected = true;
      this._reconnectAttempts = 0;
      console.log('[SDD-WS] Connected');
    };

    this.ws.onmessage = (e) => {
      try {
        const event: SddEvent = JSON.parse(e.data);
        this.handlers.forEach((h) => h(event));
      } catch (err) {
        console.error('[SDD-WS] Parse error:', err);
      }
    };

    this.ws.onclose = () => {
      this._isConnected = false;
      if (this._disposed) return;
      if (this._reconnectAttempts < this._maxReconnectAttempts) {
        this._reconnectAttempts++;
        const delay = Math.min(3000 * Math.pow(1.5, this._reconnectAttempts - 1), 15000);
        console.log(`[SDD-WS] Disconnected — reconnecting in ${Math.round(delay / 1000)}s (attempt ${this._reconnectAttempts}/${this._maxReconnectAttempts})`);
        this.reconnectTimeout = setTimeout(() => this.connect(), delay);
      } else {
        console.warn('[SDD-WS] Max reconnect attempts reached. Use refreshStatus() for polling.');
      }
    };

    this.ws.onerror = () => {
      // Suppress noisy error logs — onclose handles reconnection
    };
  }

  disconnect(): void {
    this._disposed = true;
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    this.ws?.close();
    this.ws = null;
    this._isConnected = false;
  }

  /** Subscribe to incoming events */
  onEvent(handler: SddEventHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Send a command to the SDD service */
  send(msg: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('[SDD-WS] Not connected');
      return;
    }
    this.ws.send(JSON.stringify(msg));
  }

  /** Send discovery command */
  discovery(idea: string): void {
    this.send({ action: 'discovery', idea });
  }

  /** Send spec phase command */
  specPhase(phase: string, feature: string, auto = false): void {
    this.send({ action: 'spec', phase, feature, auto });
  }

  /** Send impl command */
  impl(feature: string, taskIds?: string[]): void {
    this.send({ action: 'impl', feature, task_ids: taskIds });
  }

  /** Send text input to gemini (user response to a question) */
  sendInput(text: string): void {
    this.send({ action: 'input', text });
  }

  /** Request status */
  requestStatus(): void {
    this.send({ action: 'status' });
  }

  /** Request specs list */
  requestSpecs(): void {
    this.send({ action: 'specs' });
  }
}
