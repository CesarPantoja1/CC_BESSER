/**
 * SddPanel — CC-SDD Studio (left side panel).
 *
 * Flow:
 *  1. User types idea → clicks "Iniciar Discovery" button
 *  2. Terminal shows gemini output in real time
 *  3. Input field at bottom is ONLY for answering gemini's questions
 *  4. When done, next pipeline step becomes available
 *  5. Click next step → pre-defined command runs automatically
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSddPipeline, type SddPhase } from '../hooks/useSddPipeline';
import { sddApi } from '../services/sdd-api';

// ---------------------------------------------------------------------------
// Pipeline steps (pre-defined, user just clicks)
// ---------------------------------------------------------------------------

const STEPS = [
  { key: 'discovery', label: 'Discovery', icon: '🔍' },
  { key: 'requirements', label: 'Requirements', icon: '📝' },
  { key: 'design', label: 'Design', icon: '🏗️' },
  { key: 'tasks', label: 'Tasks', icon: '✅' },
  { key: 'implementation', label: 'Impl', icon: '⚡' },
];

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Horizontal step-by-step progress bar */
function PipelineStepper({
  completedPhases,
  currentPhase,
  isRunning,
}: {
  completedPhases: string[];
  currentPhase: string;
  isRunning: boolean;
}) {
  return (
    <div className="flex items-center gap-0.5 px-2 py-2 overflow-x-auto border-b border-border/30">
      {STEPS.map((step, idx) => {
        const done = completedPhases.includes(step.key);
        const active = currentPhase === step.key;

        return (
          <React.Fragment key={step.key}>
            <div
              className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium whitespace-nowrap transition-all ${
                done
                  ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                  : active
                    ? `bg-blue-500/15 text-blue-400 border border-blue-500/30 ${isRunning ? 'animate-pulse' : ''}`
                    : 'bg-muted/20 text-muted-foreground/40 border border-transparent'
              }`}
            >
              <span className="text-[10px]">{step.icon}</span>
              <span>{step.label}</span>
              {done && <span className="text-[9px]">✓</span>}
            </div>
            {idx < STEPS.length - 1 && (
              <span className={`text-[10px] ${done ? 'text-emerald-500/60' : 'text-muted-foreground/15'}`}>→</span>
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

/** Interactive terminal — shows gemini output + input for answering questions */
function Terminal({
  output,
  isRunning,
  onSendInput,
}: {
  output: string;
  isRunning: boolean;
  onSendInput: (text: string) => void;
}) {
  const termRef = useRef<HTMLPreElement>(null);
  const [inputText, setInputText] = useState('');

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight;
    }
  }, [output]);

  const handleSubmit = useCallback(() => {
    if (!inputText.trim()) return;
    onSendInput(inputText.trim());
    setInputText('');
  }, [inputText, onSendInput]);

  return (
    <div className="flex flex-col border border-border/30 rounded-lg overflow-hidden mx-2 mb-2">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-black/80 border-b border-border/20">
        <span className={`h-2 w-2 rounded-full ${isRunning ? 'bg-emerald-400 animate-pulse' : output ? 'bg-gray-500' : 'bg-gray-700'}`} />
        <span className="text-[10px] font-mono text-gray-400 uppercase tracking-wider">
          Gemini CLI {isRunning ? '· Ejecutando...' : '· Listo'}
        </span>
      </div>

      {/* Output */}
      <pre
        ref={termRef}
        className="bg-[#0d1117] text-[#c9d1d9] font-mono text-[11px] leading-relaxed p-3 overflow-auto whitespace-pre-wrap break-words"
        style={{ minHeight: '100px', maxHeight: '45vh' }}
      >
        {output || (
          <span className="text-gray-600 italic">
            Escribe tu idea arriba y presiona "Iniciar Discovery" para comenzar.
          </span>
        )}
      </pre>

      {/* Input — ONLY for answering gemini's questions */}
      {isRunning && (
        <div className="flex items-center gap-1 px-2 py-1.5 bg-[#161b22] border-t border-border/20">
          <span className="text-emerald-500 text-xs font-mono">❯</span>
          <input
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="Responde a la pregunta de Gemini..."
            className="flex-1 bg-transparent text-[11px] font-mono text-[#c9d1d9] placeholder:text-gray-600 focus:outline-none"
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!inputText.trim()}
            className="px-2 py-0.5 text-[10px] text-emerald-400 hover:bg-emerald-500/20 rounded transition-colors disabled:opacity-30"
          >
            Enviar ↵
          </button>
        </div>
      )}
    </div>
  );
}

/** File tree showing generated spec files */
function FileTree({
  specs,
  fileCache,
  onFileClick,
  openFile,
}: {
  specs: Array<{ name: string; has_brief: boolean; has_requirements: boolean; has_design: boolean; has_tasks: boolean }>;
  fileCache: Record<string, string>;
  onFileClick: (spec: string, file: string) => void;
  openFile: { spec: string; file: string } | null;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (specs.length > 0 && expanded.size === 0) {
      setExpanded(new Set([specs[0].name]));
    }
  }, [specs]);

  if (specs.length === 0) return null;

  const FILES = [
    { key: 'brief.md', check: 'has_brief', icon: '📋' },
    { key: 'requirements.md', check: 'has_requirements', icon: '📝' },
    { key: 'design.md', check: 'has_design', icon: '🏗️' },
    { key: 'tasks.md', check: 'has_tasks', icon: '✅' },
  ] as const;

  return (
    <div className="mx-2 mb-2">
      <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1 mb-1">
        📁 Archivos del Proyecto
      </div>
      <div className="rounded-lg border border-border/30 bg-muted/10 overflow-hidden">
        <div className="px-2 py-1 text-[10px] font-mono text-muted-foreground border-b border-border/20 bg-muted/20">
          .kiro/specs/
        </div>
        {specs.map((spec) => {
          const isExpanded = expanded.has(spec.name);
          return (
            <div key={spec.name}>
              <button
                type="button"
                onClick={() => {
                  setExpanded((prev) => {
                    const next = new Set(prev);
                    if (next.has(spec.name)) next.delete(spec.name);
                    else next.add(spec.name);
                    return next;
                  });
                }}
                className="flex items-center gap-1.5 w-full px-3 py-1.5 text-left hover:bg-muted/30 transition-colors"
              >
                <span className="text-[10px] text-muted-foreground">{isExpanded ? '▼' : '▶'}</span>
                <span className="text-[10px]">📂</span>
                <span className="text-xs font-medium text-foreground">{spec.name}/</span>
              </button>
              {isExpanded && (
                <div className="pl-6 pb-1">
                  {FILES.map(({ key, check, icon }) => {
                    if (!spec[check as keyof typeof spec]) return null;
                    const isOpen = openFile?.spec === spec.name && openFile?.file === key;
                    return (
                      <button
                        key={key}
                        type="button"
                        onClick={() => onFileClick(spec.name, key)}
                        className={`flex items-center gap-1.5 w-full px-2 py-1 text-left rounded transition-colors text-[11px] ${
                          isOpen
                            ? 'bg-blue-500/15 text-blue-400'
                            : 'text-muted-foreground hover:bg-muted/30 hover:text-foreground'
                        }`}
                      >
                        <span className="text-[10px]">{icon}</span>
                        <span className="font-mono">{key}</span>
                        {!!fileCache[`${spec.name}/${key}`] && !isOpen && (
                          <span className="text-[8px] text-emerald-500 ml-auto">●</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
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

export function SddPanel({ isOpen, onClose, onOpenSpecFile }: SddPanelProps) {
  const [state, actions] = useSddPipeline();

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [isOpen, onClose]);

  /** Determine the next step the user should execute */
  const getNextStep = useCallback((): string | null => {
    for (const step of STEPS) {
      if (!state.completedPhases.includes(step.key)) return step.key;
    }
    return null;
  }, [state.completedPhases]);

  /** Execute the next pipeline step */
  const executeStep = useCallback(
    (step: string) => {
      if (state.isRunning) return;
      const feature = state.featureText.trim() || state.currentFeature;

      if (step === 'discovery') {
        if (!state.ideaText.trim()) return;
        actions.startDiscovery(state.ideaText.trim());
      } else if (step === 'implementation') {
        if (feature) actions.runImpl(feature);
      } else {
        if (feature) actions.runSpecPhase(step, feature, false);
      }
    },
    [state, actions],
  );

  const handleFileClick = useCallback(
    (spec: string, file: string) => {
      actions.openSpecFile(spec, file);
      const cached = state.fileCache[`${spec}/${file}`];
      if (cached && onOpenSpecFile) {
        onOpenSpecFile(spec, file, cached);
      } else {
        sddApi.getSpecFile(spec, file).then((f) => {
          onOpenSpecFile?.(spec, file, f.content);
        }).catch(console.error);
      }
    },
    [state.fileCache, actions, onOpenSpecFile],
  );

  if (!isOpen) return null;

  const nextStep = getNextStep();
  const canStartDiscovery = state.isInstalled && !state.isRunning && state.ideaText.trim().length > 0;
  const needsFeature = nextStep && nextStep !== 'discovery' && !(state.featureText.trim() || state.currentFeature);

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
            <p className="text-[9px] text-muted-foreground">Spec-Driven Development</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${state.isConnected ? 'bg-emerald-400' : 'bg-red-400'}`} />
          <button type="button" onClick={onClose} className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors text-xs" aria-label="Cerrar">✕</button>
        </div>
      </div>

      {/* Pipeline Progress */}
      <PipelineStepper
        completedPhases={state.completedPhases}
        currentPhase={state.phase}
        isRunning={state.isRunning}
      />

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {/* Install prompt */}
        {!state.isInstalled && (
          <div className="mx-2 my-2 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs">
            <p className="text-amber-300 font-medium mb-2">⚠️ CC-SDD no instalado</p>
            <button
              type="button"
              onClick={() => actions.install()}
              disabled={state.isRunning}
              className="px-3 py-1.5 bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 text-xs font-medium rounded-lg border border-amber-500/30 transition-colors disabled:opacity-50"
            >
              {state.isRunning ? '⏳ Instalando...' : '📦 Instalar CC-SDD'}
            </button>
          </div>
        )}

        {/* Error */}
        {state.error && (
          <div className="mx-2 my-1 p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-[11px] text-red-400">
            <span className="font-medium">Error:</span> {state.error}
          </div>
        )}

        {/* Idea input + Discovery button */}
        {state.isInstalled && (
          <div className="mx-2 my-2">
            <label className="block text-[10px] font-semibold text-muted-foreground mb-1 uppercase tracking-wider">
              💡 Idea del Sistema
            </label>
            <textarea
              value={state.ideaText}
              onChange={(e) => actions.setIdeaText(e.target.value)}
              placeholder="Describe tu sistema aquí... (ej: Sistema de reservas para restaurante)"
              className="w-full resize-none rounded-lg border border-border/50 bg-muted/30 px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-purple-500/40 transition-all"
              rows={2}
              disabled={state.isRunning || state.completedPhases.includes('discovery')}
            />

            {/* Discovery button — only before discovery is done */}
            {!state.completedPhases.includes('discovery') && (
              <button
                type="button"
                onClick={() => executeStep('discovery')}
                disabled={!canStartDiscovery}
                className="mt-1.5 w-full px-3 py-2 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white text-xs font-semibold rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-purple-500/20"
              >
                🔍 Iniciar Discovery
              </button>
            )}
          </div>
        )}

        {/* Feature input (after discovery) */}
        {state.isInstalled && state.completedPhases.includes('discovery') && (
          <div className="mx-2 mb-2">
            <label className="block text-[10px] font-semibold text-muted-foreground mb-1 uppercase tracking-wider">
              📋 Feature Name
            </label>
            <input
              type="text"
              value={state.featureText}
              onChange={(e) => actions.setFeatureText(e.target.value)}
              placeholder={state.currentFeature || 'nombre-del-feature'}
              className="w-full rounded-lg border border-border/50 bg-muted/30 px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-purple-500/40 transition-all"
              disabled={state.isRunning}
            />

            {/* Next step button */}
            {nextStep && nextStep !== 'discovery' && !state.isRunning && (
              <button
                type="button"
                onClick={() => executeStep(nextStep)}
                disabled={!!needsFeature}
                className="mt-1.5 w-full px-3 py-2 bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white text-xs font-semibold rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-blue-500/20"
              >
                {STEPS.find((s) => s.key === nextStep)?.icon} Ejecutar{' '}
                {STEPS.find((s) => s.key === nextStep)?.label}
              </button>
            )}
          </div>
        )}

        {/* Terminal */}
        <Terminal
          output={state.output}
          isRunning={state.isRunning}
          onSendInput={actions.sendInput}
        />

        {/* File Tree */}
        <FileTree
          specs={state.specs}
          fileCache={state.fileCache}
          onFileClick={handleFileClick}
          openFile={state.openFile}
        />

        {/* Last generated files */}
        {state.lastGeneratedFiles.length > 0 && (
          <div className="mx-2 mb-2 p-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30">
            <p className="text-[10px] font-medium text-emerald-400 mb-1">✅ Archivos generados:</p>
            <ul className="text-[9px] font-mono text-emerald-300 space-y-0.5">
              {state.lastGeneratedFiles.map((f) => (
                <li key={f}>📄 {f}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="flex items-center justify-between px-2 py-1.5 border-t border-border/30 bg-muted/20">
        <span className="text-[9px] text-muted-foreground">CC-SDD Studio</span>
        <div className="flex gap-1">
          <button type="button" onClick={() => actions.clearOutput()} className="px-1.5 py-0.5 text-[9px] text-muted-foreground hover:text-foreground rounded border border-border/30 hover:bg-muted/50 transition-colors" title="Limpiar terminal">🗑</button>
          <button type="button" onClick={() => actions.refreshStatus()} className="px-1.5 py-0.5 text-[9px] text-muted-foreground hover:text-foreground rounded border border-border/30 hover:bg-muted/50 transition-colors" title="Actualizar estado">🔄</button>
          <button type="button" onClick={() => actions.reset()} className="px-1.5 py-0.5 text-[9px] text-muted-foreground hover:text-foreground rounded border border-border/30 hover:bg-muted/50 transition-colors" title="Reset pipeline">⟳</button>
        </div>
      </div>
    </div>
  );
}
