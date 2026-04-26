/**
 * SddPanel — CC-SDD Studio (left side panel).
 *
 * Flow:
 *  1. User types idea → clicks "Iniciar Discovery"
 *  2. Terminal shows gemini output (questions, progress)
 *  3. Input at bottom is ALWAYS visible for answering gemini
 *  4. When done, click next step → command sent automatically
 *  5. Feature name is auto-derived from the idea (user never types it)
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useSddPipeline, type SddPhase } from '../hooks/useSddPipeline';

// ---------------------------------------------------------------------------
// Pipeline steps
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

/** Horizontal step progress bar */
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

/** Terminal output + input for answering gemini */
function Terminal({
  output,
  isRunning,
  waitingInput,
  onSendInput,
}: {
  output: string;
  isRunning: boolean;
  waitingInput: boolean;
  onSendInput: (text: string) => void;
}) {
  const termRef = useRef<HTMLPreElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [inputText, setInputText] = useState('');

  useEffect(() => {
    if (termRef.current) {
      termRef.current.scrollTop = termRef.current.scrollHeight;
    }
  }, [output]);

  // Auto-focus input when gemini is waiting
  useEffect(() => {
    if (waitingInput && inputRef.current) {
      inputRef.current.focus();
    }
  }, [waitingInput]);

  const handleSubmit = useCallback(() => {
    if (!inputText.trim()) return;
    onSendInput(inputText.trim());
    setInputText('');
  }, [inputText, onSendInput]);

  // Show input whenever there's output (session is active)
  const showInput = output.length > 0;

  return (
    <div className="flex flex-col border border-border/30 rounded-lg overflow-hidden mx-2 mb-2">
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-black/80 border-b border-border/20">
        <span className={`h-2 w-2 rounded-full ${
          waitingInput
            ? 'bg-amber-400 animate-pulse'
            : isRunning
              ? 'bg-emerald-400 animate-pulse'
              : output ? 'bg-gray-500' : 'bg-gray-700'
        }`} />
        <span className="text-[10px] font-mono text-gray-400 uppercase tracking-wider">
          Gemini CLI
          {waitingInput
            ? ' · Esperando respuesta...'
            : isRunning
              ? ' · Ejecutando...'
              : ' · Listo'
          }
        </span>
      </div>

      {/* Output */}
      <pre
        ref={termRef}
        className="bg-[#0d1117] text-[#c9d1d9] font-mono text-[11px] leading-relaxed p-3 overflow-auto whitespace-pre-wrap break-words"
        style={{ minHeight: '80px', maxHeight: '45vh' }}
      >
        {output || (
          <span className="text-gray-600 italic">
            Escribe tu idea arriba y presiona "Iniciar Discovery" para comenzar.
          </span>
        )}
      </pre>

      {/* Input — ALWAYS visible when session has output */}
      {showInput && (
        <div className={`flex items-center gap-1 px-2 py-1.5 border-t transition-colors ${
          waitingInput
            ? 'bg-amber-900/20 border-amber-500/30'
            : 'bg-[#161b22] border-border/20'
        }`}>
          <span className={`text-xs font-mono ${waitingInput ? 'text-amber-400' : 'text-emerald-500'}`}>❯</span>
          <input
            ref={inputRef}
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder={waitingInput ? '⚡ Escribe tu respuesta aquí...' : 'Escribe para responder a Gemini...'}
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

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface SddPanelProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSpecFile?: (spec: string, file: string, content: string) => void;
}

export function SddPanel({ isOpen, onClose }: SddPanelProps) {
  const [state, actions] = useSddPipeline();

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [isOpen, onClose]);

  /** Get the next step to execute */
  const getNextStep = useCallback((): string | null => {
    for (const step of STEPS) {
      if (!state.completedPhases.includes(step.key)) return step.key;
    }
    return null;
  }, [state.completedPhases]);

  /** Execute a pipeline step (auto-uses derived feature name) */
  const executeStep = useCallback(
    (step: string) => {
      if (state.isRunning) return;

      // Mark current phase as completed before starting next
      if (state.phase !== 'idle') {
        actions.markPhaseComplete();
      }

      if (step === 'discovery') {
        if (!state.ideaText.trim()) return;
        actions.startDiscovery(state.ideaText.trim());
      } else if (step === 'implementation') {
        actions.runImpl(state.currentFeature);
      } else {
        // requirements, design, tasks — auto-uses currentFeature
        actions.runSpecPhase(step, state.currentFeature, false);
      }
    },
    [state, actions],
  );

  if (!isOpen) return null;

  const nextStep = getNextStep();
  const canStartDiscovery = !state.isRunning && state.ideaText.trim().length > 0;

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
        {/* Error */}
        {state.error && (
          <div className="mx-2 my-1 p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-[11px] text-red-400">
            <span className="font-medium">Error:</span> {state.error}
          </div>
        )}

        {/* Idea input + Discovery button */}
        <div className="mx-2 my-2">
          <label className="block text-[10px] font-semibold text-muted-foreground mb-1 uppercase tracking-wider">
            💡 Idea del Sistema
          </label>
          <textarea
            value={state.ideaText}
            onChange={(e) => actions.setIdeaText(e.target.value)}
            placeholder="Describe tu sistema aquí... (ej: Sistema de reservas para restaurante)"
            className="w-full resize-none rounded-lg border border-border/50 bg-muted/30 px-2.5 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-purple-500/40 transition-all"
            rows={3}
            disabled={state.isRunning || state.completedPhases.includes('discovery')}
          />

          {/* Auto-derived feature name indicator (read-only) */}
          {state.currentFeature && (
            <div className="mt-1 px-2 py-1 rounded-md bg-muted/20 border border-border/20">
              <span className="text-[9px] text-muted-foreground">Feature: </span>
              <span className="text-[10px] font-mono text-foreground">{state.currentFeature}</span>
            </div>
          )}
        </div>

        {/* Action button — context-dependent */}
        <div className="mx-2 mb-2">
          {/* Discovery button */}
          {nextStep === 'discovery' && (
            <button
              type="button"
              onClick={() => executeStep('discovery')}
              disabled={!canStartDiscovery}
              className="w-full px-3 py-2.5 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white text-xs font-semibold rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-purple-500/20"
            >
              🔍 Iniciar Discovery
            </button>
          )}

          {/* Next step button (after discovery) */}
          {nextStep && nextStep !== 'discovery' && !state.isRunning && (
            <button
              type="button"
              onClick={() => executeStep(nextStep)}
              className="w-full px-3 py-2.5 bg-gradient-to-r from-blue-600 to-cyan-600 hover:from-blue-500 hover:to-cyan-500 text-white text-xs font-semibold rounded-lg transition-all shadow-lg shadow-blue-500/20"
            >
              {STEPS.find((s) => s.key === nextStep)?.icon} Ejecutar{' '}
              {STEPS.find((s) => s.key === nextStep)?.label}
            </button>
          )}

          {/* Running indicator */}
          {state.isRunning && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/30">
              <span className="h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
              <span className="text-[11px] text-blue-400 font-medium">
                {STEPS.find((s) => s.key === state.phase)?.icon} Ejecutando {STEPS.find((s) => s.key === state.phase)?.label}...
              </span>
            </div>
          )}
        </div>

        {/* Terminal */}
        <Terminal
          output={state.output}
          isRunning={state.isRunning}
          waitingInput={state.waitingInput}
          onSendInput={actions.sendInput}
        />

        {/* Specs generated */}
        {state.specs.length > 0 && (
          <div className="mx-2 mb-2">
            <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider px-1 mb-1">
              📁 Specs Generados
            </div>
            <div className="rounded-lg border border-border/30 bg-muted/10 overflow-hidden">
              {state.specs.map((spec) => (
                <div key={spec.name} className="px-3 py-1.5 border-b border-border/10 last:border-0">
                  <span className="text-xs font-medium text-foreground">📂 {spec.name}/</span>
                  <div className="flex gap-2 mt-0.5">
                    {spec.has_brief && <span className="text-[9px] text-emerald-400">📋 brief</span>}
                    {spec.has_requirements && <span className="text-[9px] text-emerald-400">📝 requirements</span>}
                    {spec.has_design && <span className="text-[9px] text-emerald-400">🏗️ design</span>}
                    {spec.has_tasks && <span className="text-[9px] text-emerald-400">✅ tasks</span>}
                  </div>
                </div>
              ))}
            </div>
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
