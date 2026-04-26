/**
 * useSddPipeline — React hook for managing the SDD pipeline.
 *
 * **Persistence:** All important state survives panel close/reopen via localStorage.
 * **Interactive:** Supports sending text input to gemini for answering questions.
 * **Auto-advance:** Feature name is auto-derived from idea by the server.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  SddWebSocket,
  sddApi,
  type SddEvent,
  type SddStatus,
  type SpecSummary,
} from '../services/sdd-api';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SddPhase =
  | 'idle'
  | 'discovery'
  | 'spec_init'
  | 'requirements'
  | 'design'
  | 'tasks'
  | 'implementation';

export interface SddPipelineState {
  isInstalled: boolean;
  isRunning: boolean;
  /** True when gemini is idle and waiting for user input */
  waitingInput: boolean;
  phase: SddPhase;
  currentFeature: string;
  output: string;
  completedPhases: string[];
  specs: SpecSummary[];
  error: string | null;
  isConnected: boolean;
  lastGeneratedFiles: string[];
  ideaText: string;
  /** Cached spec files: key = "specName/filename", value = content */
  fileCache: Record<string, string>;
  /** File currently open for viewing */
  openFile: { spec: string; file: string } | null;
}

export interface SddPipelineActions {
  install: (language?: string) => Promise<void>;
  startDiscovery: (idea: string) => void;
  runSpecPhase: (phase: string, feature: string, autoApprove?: boolean) => void;
  runImpl: (feature: string, taskIds?: string[]) => void;
  /** Send text input to gemini (response to a question) */
  sendInput: (text: string) => void;
  clearOutput: () => void;
  refreshStatus: () => Promise<void>;
  reset: () => void;
  setIdeaText: (text: string) => void;
  /** Mark the current phase as completed and advance */
  markPhaseComplete: () => void;
  /** Open a spec file for viewing */
  openSpecFile: (spec: string, file: string) => void;
  /** Close the spec file viewer */
  closeSpecFile: () => void;
}

// ---------------------------------------------------------------------------
// localStorage persistence
// ---------------------------------------------------------------------------

const STORAGE_KEY = 'besser_sdd_pipeline_state';
const FILES_STORAGE_KEY = 'besser_sdd_file_cache';

interface PersistedState {
  isInstalled: boolean;
  completedPhases: string[];
  currentFeature: string;
  ideaText: string;
  output: string;
  lastGeneratedFiles: string[];
}

function loadPersistedState(): Partial<PersistedState> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // Ignore
  }
  return {};
}

function savePersistedState(state: PersistedState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Ignore quota
  }
}

function loadFileCache(): Record<string, string> {
  try {
    const raw = localStorage.getItem(FILES_STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    // Ignore
  }
  return {};
}

function saveFileCache(cache: Record<string, string>): void {
  try {
    localStorage.setItem(FILES_STORAGE_KEY, JSON.stringify(cache));
  } catch {
    // Ignore quota
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSddPipeline(): [SddPipelineState, SddPipelineActions] {
  const persisted = useRef(loadPersistedState());
  const cachedFiles = useRef(loadFileCache());

  const [state, setState] = useState<SddPipelineState>(() => ({
    isInstalled: persisted.current.isInstalled ?? true, // Default to true
    isRunning: false,
    waitingInput: false,
    phase: 'idle',
    currentFeature: persisted.current.currentFeature ?? '',
    output: persisted.current.output ?? '',
    completedPhases: persisted.current.completedPhases ?? [],
    specs: [],
    error: null,
    isConnected: false,
    lastGeneratedFiles: persisted.current.lastGeneratedFiles ?? [],
    ideaText: persisted.current.ideaText ?? '',
    fileCache: cachedFiles.current,
    openFile: null,
  }));

  const wsRef = useRef<SddWebSocket | null>(null);
  const outputRef = useRef(persisted.current.output ?? '');

  // ------------------------------------------------------------------
  // Persist state
  // ------------------------------------------------------------------

  useEffect(() => {
    savePersistedState({
      isInstalled: state.isInstalled,
      completedPhases: state.completedPhases,
      currentFeature: state.currentFeature,
      ideaText: state.ideaText,
      output: state.output.slice(-15000),
      lastGeneratedFiles: state.lastGeneratedFiles,
    });
  }, [
    state.isInstalled,
    state.completedPhases,
    state.currentFeature,
    state.ideaText,
    state.output,
    state.lastGeneratedFiles,
  ]);

  // Persist file cache separately
  useEffect(() => {
    saveFileCache(state.fileCache);
  }, [state.fileCache]);

  // ------------------------------------------------------------------
  // WebSocket connection
  // ------------------------------------------------------------------

  useEffect(() => {
    const ws = new SddWebSocket();
    wsRef.current = ws;

    const unsub = ws.onEvent((event: SddEvent) => {
      handleEvent(event);
    });

    ws.connect();

    const checkConnection = setInterval(() => {
      setState((prev) => ({
        ...prev,
        isConnected: ws.isConnected,
      }));
    }, 2000);

    return () => {
      clearInterval(checkConnection);
      unsub();
      ws.disconnect();
    };
  }, []);

  // ------------------------------------------------------------------
  // Event handler
  // ------------------------------------------------------------------

  const handleEvent = useCallback((event: SddEvent) => {
    switch (event.type) {
      case 'phase_start':
        outputRef.current = '';
        setState((prev) => ({
          ...prev,
          isRunning: true,
          waitingInput: false,
          phase: (event.phase as SddPhase) || prev.phase,
          currentFeature: (event.feature as string) || prev.currentFeature,
          output: '',
          error: null,
          lastGeneratedFiles: [],
        }));
        break;

      case 'output':
        outputRef.current += event.data || '';
        setState((prev) => ({
          ...prev,
          output: outputRef.current,
          waitingInput: false, // Got output → not waiting anymore
        }));
        break;

      case 'waiting_input':
        // Gemini is idle and waiting for user response
        setState((prev) => ({
          ...prev,
          waitingInput: true,
        }));
        break;

      case 'phase_complete':
        setState((prev) => ({
          ...prev,
          isRunning: false,
          waitingInput: false,
          phase: 'idle',
          completedPhases: event.phase
            ? [...new Set([...prev.completedPhases, event.phase])]
            : prev.completedPhases,
          lastGeneratedFiles: (event.files as string[]) || [],
        }));
        // Refresh specs
        sddApi.listSpecs().then(({ specs }) => {
          setState((prev) => ({ ...prev, specs }));
        }).catch(console.error);
        break;

      case 'process_done':
        // Gemini process finished this invocation.
        // DON'T auto-complete the phase — user may need to respond to
        // questions. Keep the input visible via waitingInput: true.
        setState((prev) => ({
          ...prev,
          isRunning: false,
          waitingInput: true, // Keep input visible for follow-up
        }));
        // Refresh specs in case files were generated
        sddApi.listSpecs().then(({ specs }) => {
          setState((prev) => ({ ...prev, specs }));
        }).catch(console.error);
        break;

      case 'session_ended':
        // The gemini session exited completely
        setState((prev) => ({
          ...prev,
          isRunning: false,
          waitingInput: false,
          completedPhases: prev.phase !== 'idle'
            ? [...new Set([...prev.completedPhases, prev.phase])]
            : prev.completedPhases,
          phase: 'idle',
        }));
        sddApi.listSpecs().then(({ specs }) => {
          setState((prev) => ({ ...prev, specs }));
        }).catch(console.error);
        break;

      case 'error':
        setState((prev) => ({
          ...prev,
          isRunning: false,
          waitingInput: false,
          error: event.error || 'Error desconocido',
        }));
        break;

      case 'status': {
        const status = event as unknown as SddStatus;
        setState((prev) => ({
          ...prev,
          isInstalled: status.installed ?? prev.isInstalled,
          specs: status.specs ?? prev.specs,
          phase: (status.pipeline?.phase as SddPhase) ?? prev.phase,
          isRunning: status.pipeline?.status === 'running',
          currentFeature: status.pipeline?.current_feature || prev.currentFeature,
          completedPhases: status.pipeline?.completed_phases?.length
            ? status.pipeline.completed_phases
            : prev.completedPhases,
          error: status.pipeline?.error || null,
        }));
        break;
      }

      case 'specs': {
        const specsEvent = event as unknown as { specs: SpecSummary[] };
        setState((prev) => ({
          ...prev,
          specs: specsEvent.specs ?? prev.specs,
        }));
        break;
      }

      case 'pong':
        break;
    }
  }, []);

  // ------------------------------------------------------------------
  // Actions
  // ------------------------------------------------------------------

  const install = useCallback(async (language = 'es') => {
    try {
      setState((prev) => ({ ...prev, isRunning: true, error: null }));
      const result = await sddApi.install(language);
      setState((prev) => ({
        ...prev,
        isRunning: false,
        isInstalled: result.status !== 'error',
        error: result.status === 'error' ? result.message : null,
      }));
    } catch (err) {
      setState((prev) => ({
        ...prev,
        isRunning: false,
        error: err instanceof Error ? err.message : 'Error de instalación',
      }));
    }
  }, []);

  const startDiscovery = useCallback((idea: string) => {
    wsRef.current?.discovery(idea);
  }, []);

  const runSpecPhase = useCallback(
    (phase: string, feature: string, autoApprove = false) => {
      wsRef.current?.specPhase(phase, feature, autoApprove);
    },
    [],
  );

  const runImpl = useCallback((feature: string, taskIds?: string[]) => {
    wsRef.current?.impl(feature, taskIds);
  }, []);

  const sendInput = useCallback((text: string) => {
    wsRef.current?.sendInput(text);
  }, []);

  const clearOutput = useCallback(() => {
    outputRef.current = '';
    setState((prev) => ({ ...prev, output: '' }));
  }, []);

  const refreshStatus = useCallback(async () => {
    try {
      const status = await sddApi.getStatus();
      setState((prev) => ({
        ...prev,
        isInstalled: status.installed,
        specs: status.specs,
        phase: (status.pipeline.phase as SddPhase) || prev.phase,
        isRunning: status.pipeline.status === 'running',
        currentFeature: status.pipeline.current_feature || prev.currentFeature,
        completedPhases: status.pipeline.completed_phases?.length
          ? status.pipeline.completed_phases
          : prev.completedPhases,
        error: status.pipeline.error || null,
      }));
    } catch {
      console.warn('[SDD] Status refresh failed — using cached state');
    }
  }, []);

  const reset = useCallback(() => {
    outputRef.current = '';
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(FILES_STORAGE_KEY);
    setState((prev) => ({
      isInstalled: prev.isInstalled,
      isRunning: false,
      waitingInput: false,
      phase: 'idle',
      currentFeature: '',
      output: '',
      completedPhases: [],
      specs: [],
      error: null,
      isConnected: prev.isConnected,
      lastGeneratedFiles: [],
      ideaText: '',
      fileCache: {},
      openFile: null,
    }));
  }, []);

  const setIdeaText = useCallback((text: string) => {
    setState((prev) => ({ ...prev, ideaText: text }));
  }, []);

  const openSpecFile = useCallback((spec: string, file: string) => {
    setState((prev) => ({ ...prev, openFile: { spec, file } }));
  }, []);

  const closeSpecFile = useCallback(() => {
    setState((prev) => ({ ...prev, openFile: null }));
  }, []);

  const markPhaseComplete = useCallback(() => {
    setState((prev) => ({
      ...prev,
      isRunning: false,
      waitingInput: false,
      completedPhases: prev.phase !== 'idle'
        ? [...new Set([...prev.completedPhases, prev.phase])]
        : prev.completedPhases,
      phase: 'idle',
    }));
    sddApi.listSpecs().then(({ specs }) => {
      setState((prev) => ({ ...prev, specs }));
    }).catch(console.error);
  }, []);

  // Load initial status
  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  return [
    state,
    {
      install,
      startDiscovery,
      runSpecPhase,
      runImpl,
      sendInput,
      clearOutput,
      refreshStatus,
      reset,
      setIdeaText,
      markPhaseComplete,
      openSpecFile,
      closeSpecFile,
    },
  ];
}
