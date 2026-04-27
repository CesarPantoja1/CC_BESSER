/**
 * SddPanel — CC-SDD Studio (chat-style interface).
 *
 * Design:
 *  - A chat interface that acts as an intermediary to the gemini-cli.
 *  - The user types commands or responses in a single prompt input.
 *  - Gemini output appears as "assistant" chat bubbles.
 *  - User messages appear as "user" chat bubbles.
 *  - NO step buttons — the user drives the pipeline by typing commands.
 *  - Only two action elements: the Send button and the Sync Diagram button.
 *  - Feature name is auto-derived from the first message (discovery).
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSddPipeline, type SddPhase } from '../hooks/useSddPipeline';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: Date;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Small pipeline progress indicator (non-interactive) */
function PipelineIndicator({
  completedPhases,
  currentPhase,
  isRunning,
}: {
  completedPhases: string[];
  currentPhase: string;
  isRunning: boolean;
}) {
  const phases = [
    { key: 'discovery', icon: '🔍' },
    { key: 'requirements', icon: '📝' },
    { key: 'design', icon: '🏗️' },
    { key: 'tasks', icon: '✅' },
    { key: 'implementation', icon: '⚡' },
  ];

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 border-b border-border/20">
      {phases.map((p, idx) => {
        const done = completedPhases.includes(p.key);
        const active = currentPhase === p.key;
        return (
          <React.Fragment key={p.key}>
            <span
              className={`text-[10px] ${
                done
                  ? 'opacity-100'
                  : active
                    ? `opacity-100 ${isRunning ? 'animate-pulse' : ''}`
                    : 'opacity-30'
              }`}
              title={p.key}
            >
              {p.icon}
            </span>
            {idx < phases.length - 1 && (
              <span className={`text-[8px] ${done ? 'text-emerald-500/60' : 'text-muted-foreground/15'}`}>→</span>
            )}
          </React.Fragment>
        );
      })}
      {isRunning && (
        <span className="ml-auto text-[9px] text-blue-400 animate-pulse">
          procesando...
        </span>
      )}
    </div>
  );
}

/** Single chat bubble */
function ChatBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';

  if (isSystem) {
    return (
      <div className="flex justify-center my-1">
        <span className="text-[9px] text-muted-foreground/60 bg-muted/20 px-2 py-0.5 rounded-full">
          {message.content}
        </span>
      </div>
    );
  }

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-2`}>
      <div
        className={`max-w-[85%] rounded-xl px-3 py-2 text-[11px] leading-relaxed ${
          isUser
            ? 'bg-blue-600/90 text-white rounded-br-sm'
            : 'bg-muted/40 text-foreground border border-border/20 rounded-bl-sm'
        }`}
      >
        {!isUser && (
          <div className="flex items-center gap-1 mb-1">
            <span className="text-[9px] font-semibold text-purple-400">🤖 Gemini</span>
          </div>
        )}
        <div className="whitespace-pre-wrap break-words font-mono text-[10.5px]">
          {message.content}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface SddPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSpecFile?: (spec: string, file: string, content: string) => void;
}

let messageIdCounter = 0;
function nextId(): string {
  return `msg-${Date.now()}-${++messageIdCounter}`;
}

export function SddPanel({ isOpen, onClose }: SddPanelProps) {
  const [state, actions] = useSddPipeline();
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [inputText, setInputText] = useState('');
  const [showWorkspaceInput, setShowWorkspaceInput] = useState(false);
  const [workspacePath, setWorkspacePath] = useState(state.workspace || '');
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastOutputLenRef = useRef(0);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [isOpen, onClose]);

  // Auto-scroll chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatMessages]);

  // Convert gemini output stream into chat messages
  useEffect(() => {
    if (!state.output) return;

    const newContent = state.output.slice(lastOutputLenRef.current);
    lastOutputLenRef.current = state.output.length;

    if (!newContent.trim()) return;

    setChatMessages((prev) => {
      // If the last message is from assistant, append to it
      const last = prev[prev.length - 1];
      if (last && last.role === 'assistant') {
        const updated = [...prev];
        updated[updated.length - 1] = {
          ...last,
          content: last.content + newContent,
        };
        return updated;
      }
      // Otherwise create a new assistant message
      return [...prev, {
        id: nextId(),
        role: 'assistant',
        content: newContent,
        timestamp: new Date(),
      }];
    });
  }, [state.output]);

  // Focus input when waiting for input
  useEffect(() => {
    if (state.waitingInput && inputRef.current) {
      inputRef.current.focus();
    }
  }, [state.waitingInput]);

  /** Send message — handles both commands and user input */
  const handleSend = useCallback(() => {
    const text = inputText.trim();
    if (!text) return;

    // Add user message to chat
    setChatMessages((prev) => [...prev, {
      id: nextId(),
      role: 'user',
      content: text,
      timestamp: new Date(),
    }]);
    setInputText('');

    // Reset output tracking so next assistant response starts fresh
    lastOutputLenRef.current = state.output.length;

    // If there's no active session yet, treat this as discovery
    if (!state.currentFeature && !state.isRunning) {
      actions.setIdeaText(text);
      actions.startDiscovery(text);
      return;
    }

    // If gemini is running/waiting for input, send as direct input
    if (state.isRunning || state.waitingInput) {
      actions.sendInput(text);
      return;
    }

    // Check if user typed a CC-SDD command
    const lowerText = text.toLowerCase();

    if (lowerText.startsWith('/kiro-') || lowerText.startsWith('/discovery') || lowerText.startsWith('/impl')) {
      // Direct CLI command — forward as input
      actions.sendInput(text);
      return;
    }

    // Explicit diagram export command
    if (
      lowerText.includes('exportar diagrama') ||
      lowerText.includes('export diagram') ||
      lowerText.includes('exportar al editor') ||
      lowerText === 'exportar'
    ) {
      handleExportDiagram();
      return;
    }

    // Explicit diagram generation command
    if (
      lowerText.includes('generar diagrama') ||
      lowerText.includes('generate diagram') ||
      lowerText.includes('crear diagrama') ||
      lowerText === 'diagrama'
    ) {
      actions.generateDiagram();
      return;
    }

    // Workspace folder command: "workspace C:\path\to\folder"
    if (lowerText.startsWith('workspace ') || lowerText.startsWith('carpeta ')) {
      const folderPath = text.replace(/^(workspace|carpeta)\s+/i, '').trim();
      if (folderPath) {
        actions.setWorkspace(folderPath);
        setWorkspacePath(folderPath);
        setChatMessages((prev) => [...prev, {
          id: nextId(),
          role: 'system',
          content: `📂 Configurando workspace: ${folderPath}`,
          timestamp: new Date(),
        }]);
        return;
      }
    }

    // Check for phase keywords
    if (lowerText.includes('requisitos') || lowerText.includes('requirements') || lowerText === 'requirements') {
      if (state.phase !== 'idle') actions.markPhaseComplete();
      actions.runSpecPhase('requirements', state.currentFeature, false);
      return;
    }
    if (lowerText.includes('diseño') || lowerText.includes('design') || lowerText === 'design') {
      if (state.phase !== 'idle') actions.markPhaseComplete();
      actions.runSpecPhase('design', state.currentFeature, false);
      return;
    }
    if (lowerText.includes('tareas') || lowerText.includes('tasks') || lowerText === 'tasks') {
      if (state.phase !== 'idle') actions.markPhaseComplete();
      actions.runSpecPhase('tasks', state.currentFeature, false);
      return;
    }
    if (lowerText.includes('implementa') || lowerText.includes('impl') || lowerText === 'implementation') {
      if (state.phase !== 'idle') actions.markPhaseComplete();
      actions.runImpl(state.currentFeature);
      return;
    }
    if (lowerText.includes('siguiente') || lowerText === 'next' || lowerText === 'siguiente fase') {
      // Auto-advance to next step
      const steps = ['discovery', 'requirements', 'design', 'tasks', 'implementation'];
      const nextStep = steps.find((s) => !state.completedPhases.includes(s));
      if (nextStep) {
        if (state.phase !== 'idle') actions.markPhaseComplete();
        if (nextStep === 'implementation') {
          actions.runImpl(state.currentFeature);
        } else {
          actions.runSpecPhase(nextStep, state.currentFeature, false);
        }
      } else {
        setChatMessages((prev) => [...prev, {
          id: nextId(),
          role: 'system',
          content: 'Todas las fases han sido completadas.',
          timestamp: new Date(),
        }]);
      }
      return;
    }

    // Default: send as input to gemini
    actions.sendInput(text);
  }, [inputText, state, actions]);

  /** Sync diagram handler */
  const handleSyncDiagram = useCallback(() => {
    if (state.isSyncing || state.isRunning) return;

    setChatMessages((prev) => [...prev, {
      id: nextId(),
      role: 'system',
      content: '🔄 Sincronizando diagrama con requisitos...',
      timestamp: new Date(),
    }]);

    const onModelResponse = (e: Event) => {
      window.removeEventListener('wme:sdd-model-response', onModelResponse);
      const detail = (e as CustomEvent).detail;
      if (detail?.model) {
        actions.syncDiagram(detail.model);
      }
    };
    window.addEventListener('wme:sdd-model-response', onModelResponse);
    window.dispatchEvent(new CustomEvent('wme:sdd-request-model'));

    setTimeout(() => {
      window.removeEventListener('wme:sdd-model-response', onModelResponse);
    }, 3000);
  }, [state.isSyncing, state.isRunning, actions]);

  /** Export diagram to editor */
  const handleExportDiagram = useCallback(() => {
    if (state.isRunning) return;

    setChatMessages((prev) => [...prev, {
      id: nextId(),
      role: 'system',
      content: '📤 Exportando diagrama al editor BESSER...',
      timestamp: new Date(),
    }]);

    actions.exportDiagramToEditor();
  }, [state.isRunning, actions]);

  // Show sync result as system message
  useEffect(() => {
    if (state.syncMessage) {
      setChatMessages((prev) => [...prev, {
        id: nextId(),
        role: 'system',
        content: state.syncMessage || '',
        timestamp: new Date(),
      }]);
    }
  }, [state.syncMessage]);

  if (!isOpen) return null;

  return (
    <div
      className="relative flex h-full w-full max-w-sm flex-col overflow-hidden bg-background border-r border-border/50 shadow-xl animate-in slide-in-from-left-4 duration-300 z-30"
      id="sdd-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50 bg-gradient-to-r from-purple-500/10 via-blue-500/10 to-cyan-500/10">
        <div className="flex items-center gap-2">
          <span className="text-base">🧠</span>
          <div>
            <h2 className="text-xs font-bold text-foreground tracking-tight">CC-SDD Studio</h2>
            <p className="text-[9px] text-muted-foreground">
              {state.currentFeature
                ? `Feature: ${state.currentFeature}`
                : 'Escribe tu idea para comenzar'
              }
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${state.isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          <button type="button" onClick={onClose} className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors text-xs" aria-label="Cerrar">✕</button>
        </div>
      </div>

      {/* Pipeline progress (compact, non-interactive) */}
      <PipelineIndicator
        completedPhases={state.completedPhases}
        currentPhase={state.phase}
        isRunning={state.isRunning}
      />

      {/* Workspace folder selector */}
      <div className="px-2 py-1.5 border-b border-border/20">
        <button
          type="button"
          onClick={() => setShowWorkspaceInput((p) => !p)}
          className="w-full flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
          title="Seleccionar carpeta de trabajo"
        >
          <span>📂</span>
          <span className="truncate flex-1 text-left">
            {state.workspace || workspacePath || 'Seleccionar carpeta de trabajo...'}
          </span>
          <span className="text-[8px]">{showWorkspaceInput ? '▲' : '▼'}</span>
        </button>
        {showWorkspaceInput && (
          <div className="mt-1.5 flex items-center gap-1">
            <input
              type="text"
              value={workspacePath}
              onChange={(e) => setWorkspacePath(e.target.value)}
              placeholder="C:\ruta\a\tu\carpeta"
              className="flex-1 bg-muted/30 rounded border border-border/40 px-2 py-1 text-[10px] text-foreground placeholder:text-muted-foreground/40 focus:outline-none focus:ring-1 focus:ring-purple-500/40"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && workspacePath.trim()) {
                  e.preventDefault();
                  actions.setWorkspace(workspacePath.trim());
                  setShowWorkspaceInput(false);
                  setChatMessages((prev) => [...prev, {
                    id: nextId(),
                    role: 'system',
                    content: `📂 Workspace: ${workspacePath.trim()}`,
                    timestamp: new Date(),
                  }]);
                }
              }}
            />
            <button
              type="button"
              onClick={() => {
                if (workspacePath.trim()) {
                  actions.setWorkspace(workspacePath.trim());
                  setShowWorkspaceInput(false);
                  setChatMessages((prev) => [...prev, {
                    id: nextId(),
                    role: 'system',
                    content: `📂 Workspace: ${workspacePath.trim()}`,
                    timestamp: new Date(),
                  }]);
                }
              }}
              disabled={!workspacePath.trim()}
              className="px-2 py-1 bg-purple-600 hover:bg-purple-500 text-white text-[9px] font-semibold rounded transition-all disabled:opacity-30 disabled:cursor-not-allowed"
            >
              ✓
            </button>
          </div>
        )}
      </div>

      {/* Error */}
      {state.error && (
        <div className="mx-2 my-1 p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-[11px] text-red-400">
          <span className="font-medium">Error:</span> {state.error}
        </div>
      )}

      {/* Chat area */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {chatMessages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center opacity-50">
            <span className="text-3xl mb-2">🧠</span>
            <p className="text-xs text-muted-foreground">
              Selecciona tu carpeta de trabajo y describe tu sistema.
            </p>
            <p className="text-[9px] text-muted-foreground mt-1">
              Comandos: "requirements", "design", "tasks", "next", "generar diagrama", "exportar"
            </p>
            <p className="text-[9px] text-muted-foreground">
              Carpeta: "workspace C:\ruta" o usa el selector 📂
            </p>
          </div>
        )}
        {chatMessages.map((msg) => (
          <ChatBubble key={msg.id} message={msg} />
        ))}
        <div ref={chatEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t border-border/30 bg-muted/10">
        {/* Action buttons */}
        <div className="px-2 py-1.5 border-b border-border/20 flex gap-1.5">
          {/* Export to editor button */}
          <button
            type="button"
            onClick={handleExportDiagram}
            disabled={state.isRunning || !state.currentFeature}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-blue-600/80 to-indigo-600/80 hover:from-blue-500 hover:to-indigo-500 text-white text-[10px] font-semibold rounded-md transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
            title="Cargar diagram.json en el editor BESSER"
            id="sdd-export-diagram-btn"
          >
            📤 Exportar al Editor
          </button>

          {/* Sync button */}
          <button
            type="button"
            onClick={handleSyncDiagram}
            disabled={state.isSyncing || state.isRunning || !state.currentFeature}
            className="flex-1 flex items-center justify-center gap-1.5 px-3 py-1.5 bg-gradient-to-r from-amber-600/80 to-orange-600/80 hover:from-amber-500 hover:to-orange-500 text-white text-[10px] font-semibold rounded-md transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-sm"
            title="Enviar el diagrama actual al servicio para sincronizar requisitos"
            id="sdd-sync-diagram-btn"
          >
            {state.isSyncing ? (
              <>
                <span className="h-2 w-2 rounded-full bg-white animate-pulse" />
                Sincronizando...
              </>
            ) : (
              '🔄 Sincronizar'
            )}
          </button>
        </div>

        {/* Chat input */}
        <div className="flex items-center gap-1 px-2 py-2">
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={
              state.waitingInput
                ? '⚡ Gemini espera tu respuesta...'
                : state.isRunning
                  ? 'Procesando...'
                  : !state.currentFeature
                    ? '💡 Describe tu sistema...'
                    : 'Escribe un comando o respuesta...'
            }
            className="flex-1 bg-muted/30 rounded-lg border border-border/40 px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-purple-500/40 transition-all"
            id="sdd-chat-input"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!inputText.trim()}
            className="px-3 py-2 bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold rounded-lg transition-all disabled:opacity-30 disabled:cursor-not-allowed shadow-sm"
            id="sdd-send-btn"
          >
            ↵
          </button>
        </div>
      </div>
    </div>
  );
}
