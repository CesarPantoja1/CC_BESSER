/**
 * SDD Feature Module — Spec-Driven Development for BESSER.
 *
 * Provides the UI panel, hooks, and API client for integrating
 * cc-sdd with the BESSER Web Modeling Editor via Gemini CLI.
 */

// Components
export { SddPanel } from './components/SddPanel';

// Hooks
export { useSddPipeline } from './hooks/useSddPipeline';
export type { SddPhase, SddPipelineState, SddPipelineActions } from './hooks/useSddPipeline';

// Services
export { sddApi, SddWebSocket } from './services/sdd-api';
export type { SddStatus, SddEvent, SpecSummary, SpecFile } from './services/sdd-api';
