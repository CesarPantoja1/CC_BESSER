"""
SDD Service — orchestrates the Spec-Driven Development pipeline.

This is the main service layer that:
  1. Manages the SDD workspace (cc-sdd installation, .kiro/ directory)
  2. Orchestrates the pipeline phases via GeminiBridge
  3. Tracks pipeline state and progress
  4. Provides spec file access via SpecParser
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from besser.utilities.web_modeling_editor.backend.services.sdd.gemini_bridge import GeminiBridge
from besser.utilities.web_modeling_editor.backend.services.sdd.spec_parser import (
    SpecParser,
    SpecSummary,
    SpecFile,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline state
# ---------------------------------------------------------------------------

class PipelinePhase(str, Enum):
    IDLE = "idle"
    DISCOVERY = "discovery"
    SPEC_INIT = "spec_init"
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    TASKS = "tasks"
    IMPLEMENTATION = "implementation"
    ERROR = "error"


class PipelineStatus(str, Enum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PipelineState:
    """Tracks the current state of the SDD pipeline."""
    phase: PipelinePhase = PipelinePhase.IDLE
    status: PipelineStatus = PipelineStatus.NOT_STARTED
    current_feature: str = ""
    progress_message: str = ""
    output_buffer: str = ""
    error: str = ""
    completed_phases: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "status": self.status.value,
            "current_feature": self.current_feature,
            "progress_message": self.progress_message,
            "error": self.error,
            "completed_phases": self.completed_phases,
        }


# ---------------------------------------------------------------------------
# SDD Service
# ---------------------------------------------------------------------------

class SddService:
    """Orchestrates the SDD pipeline.

    Parameters
    ----------
    work_dir:
        Directory containing the cc-sdd installation (.gemini/skills/, .kiro/).
        Defaults to the ``SDD_WORK_DIR`` environment variable or
        ``{project_root}/sdd-workspace``.
    """

    def __init__(self, work_dir: Optional[Path] = None) -> None:
        if work_dir is None:
            env_dir = os.environ.get("SDD_WORK_DIR")
            if env_dir:
                work_dir = Path(env_dir.strip())
            else:
                # Default: sibling of BESSER directory
                work_dir = Path(__file__).resolve().parents[7] / "sdd-workspace"

        self.work_dir = work_dir
        self._bridge: Optional[GeminiBridge] = None
        self._state = PipelineState()
        self._parser: Optional[SpecParser] = None
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

        logger.info("SDD Service initialized  work_dir=%s", self.work_dir)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_installed(self) -> bool:
        """Check if cc-sdd skills are installed in the workspace."""
        return (self.work_dir / ".gemini" / "skills").exists()

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def parser(self) -> SpecParser:
        if self._parser is None:
            self._parser = SpecParser(self.work_dir / ".kiro")
        return self._parser

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    async def install_skills(self, language: str = "es") -> Dict[str, Any]:
        """Install cc-sdd skills using npx."""
        if self.is_installed:
            return {
                "status": "already_installed",
                "message": "cc-sdd skills ya están instalados.",
                "work_dir": str(self.work_dir),
            }

        self.work_dir.mkdir(parents=True, exist_ok=True)

        npx_cmd = "npx.cmd" if sys.platform == "win32" else "npx"
        cmd = [npx_cmd, "-y", "cc-sdd@latest", "--gemini-skills", "--lang", language]

        logger.info("Installing cc-sdd: %s", " ".join(cmd))

        result = subprocess.run(
            cmd,
            cwd=str(self.work_dir),
            capture_output=True,
            text=True,
            shell=sys.platform == "win32",
            timeout=120,
        )

        if result.returncode != 0:
            logger.error("cc-sdd install failed: %s", result.stderr)
            return {
                "status": "error",
                "message": f"Instalación falló: {result.stderr[:500]}",
            }

        return {
            "status": "installed",
            "message": "cc-sdd instalado correctamente.",
            "work_dir": str(self.work_dir),
            "output": result.stdout[:1000],
        }

    # ------------------------------------------------------------------
    # Bridge management
    # ------------------------------------------------------------------

    def _get_bridge(self) -> GeminiBridge:
        if self._bridge is None:
            self._bridge = GeminiBridge(work_dir=self.work_dir)
        return self._bridge

    async def shutdown(self) -> None:
        """Shutdown the gemini bridge."""
        if self._bridge:
            await self._bridge.close()
            self._bridge = None

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    async def run_discovery(
        self, idea: str
    ) -> AsyncIterator[Dict[str, Any]]:
        """Run /kiro-discovery with the given idea.

        Yields progress events as dicts.
        """
        self._state.phase = PipelinePhase.DISCOVERY
        self._state.status = PipelineStatus.RUNNING
        self._state.current_feature = ""
        self._state.progress_message = f"Descubriendo: {idea[:100]}"
        self._state.output_buffer = ""
        self._state.error = ""

        yield {"type": "phase_start", "phase": "discovery", "idea": idea}

        bridge = self._get_bridge()
        command = f"/kiro-discovery {idea}"
        full_output = ""

        try:
            async for chunk in bridge.execute_oneshot(command):
                full_output += chunk
                yield {"type": "output", "data": chunk}

            self._state.output_buffer = full_output
            self._state.status = PipelineStatus.COMPLETED
            self._state.completed_phases.append("discovery")

            # Check for generated files
            generated_files = self._detect_generated_files()

            yield {
                "type": "phase_complete",
                "phase": "discovery",
                "files": generated_files,
                "output_length": len(full_output),
            }

        except Exception as exc:
            logger.exception("Discovery failed")
            self._state.status = PipelineStatus.FAILED
            self._state.error = str(exc)
            yield {"type": "error", "phase": "discovery", "error": str(exc)}

    async def run_spec_phase(
        self,
        phase: str,
        feature_name: str,
        auto_approve: bool = False,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Run a spec phase (init, requirements, design, tasks).

        Parameters
        ----------
        phase:
            One of: "init", "requirements", "design", "tasks", "quick"
        feature_name:
            The spec/feature name to work on.
        auto_approve:
            If True, pass ``-y`` to auto-approve the phase.
        """
        phase_map = {
            "init": PipelinePhase.SPEC_INIT,
            "requirements": PipelinePhase.REQUIREMENTS,
            "design": PipelinePhase.DESIGN,
            "tasks": PipelinePhase.TASKS,
        }

        command_map = {
            "init": f"/kiro-spec-init {feature_name}",
            "requirements": f"/kiro-spec-requirements {feature_name}",
            "design": f"/kiro-spec-design {feature_name}" + (" -y" if auto_approve else ""),
            "tasks": f"/kiro-spec-tasks {feature_name}" + (" -y" if auto_approve else ""),
            "quick": f"/kiro-spec-quick {feature_name}" + (" --auto" if auto_approve else ""),
        }

        if phase not in command_map:
            yield {"type": "error", "error": f"Fase desconocida: {phase}"}
            return

        self._state.phase = phase_map.get(phase, PipelinePhase.IDLE)
        self._state.status = PipelineStatus.RUNNING
        self._state.current_feature = feature_name
        self._state.progress_message = f"Ejecutando {phase} para {feature_name}"
        self._state.output_buffer = ""

        yield {"type": "phase_start", "phase": phase, "feature": feature_name}

        bridge = self._get_bridge()
        command = command_map[phase]
        full_output = ""

        try:
            async for chunk in bridge.execute_oneshot(command):
                full_output += chunk
                yield {"type": "output", "data": chunk}

            self._state.output_buffer = full_output
            self._state.status = PipelineStatus.COMPLETED
            if phase not in self._state.completed_phases:
                self._state.completed_phases.append(phase)

            generated_files = self._detect_generated_files(feature_name)

            yield {
                "type": "phase_complete",
                "phase": phase,
                "feature": feature_name,
                "files": generated_files,
            }

        except Exception as exc:
            logger.exception("Spec phase %s failed", phase)
            self._state.status = PipelineStatus.FAILED
            self._state.error = str(exc)
            yield {"type": "error", "phase": phase, "error": str(exc)}

    async def run_implementation(
        self, feature_name: str, task_ids: Optional[List[str]] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """Run /kiro-impl for implementation."""
        self._state.phase = PipelinePhase.IMPLEMENTATION
        self._state.status = PipelineStatus.RUNNING
        self._state.current_feature = feature_name

        command = f"/kiro-impl {feature_name}"
        if task_ids:
            command += " " + " ".join(task_ids)

        yield {"type": "phase_start", "phase": "implementation", "feature": feature_name}

        bridge = self._get_bridge()
        full_output = ""

        try:
            async for chunk in bridge.execute_oneshot(command):
                full_output += chunk
                yield {"type": "output", "data": chunk}

            self._state.status = PipelineStatus.COMPLETED
            self._state.completed_phases.append("implementation")
            yield {
                "type": "phase_complete",
                "phase": "implementation",
                "feature": feature_name,
            }

        except Exception as exc:
            logger.exception("Implementation failed")
            self._state.status = PipelineStatus.FAILED
            self._state.error = str(exc)
            yield {"type": "error", "phase": "implementation", "error": str(exc)}

    # ------------------------------------------------------------------
    # Spec access
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """Get the current pipeline status."""
        specs = self.parser.list_specs() if self.is_installed else []
        return {
            "installed": self.is_installed,
            "work_dir": str(self.work_dir),
            "pipeline": self._state.to_dict(),
            "specs": [s.to_dict() for s in specs],
            "gemini_running": False,  # Gemini runs in separate service now
        }

    def list_specs(self) -> List[Dict[str, Any]]:
        """List all specs."""
        return [s.to_dict() for s in self.parser.list_specs()]

    def get_spec_file_content(self, spec_name: str, filename: str) -> Optional[Dict[str, Any]]:
        """Get a spec file content."""
        sf = self.parser.get_spec_file(spec_name, filename)
        if sf is None:
            return None
        return {
            "name": sf.name,
            "path": sf.path,
            "content": sf.content,
            "size": sf.size,
            "last_modified": sf.last_modified,
        }

    def get_all_spec_files(self, spec_name: str) -> List[Dict[str, Any]]:
        """Get all files for a spec."""
        files = self.parser.get_all_spec_files(spec_name)
        return [
            {
                "name": f.name,
                "content": f.content,
                "size": f.size,
                "last_modified": f.last_modified,
            }
            for f in files
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _detect_generated_files(self, feature_name: Optional[str] = None) -> List[str]:
        """Detect which spec files have been generated."""
        if not self.is_installed:
            return []

        files_found: List[str] = []
        specs_dir = self.work_dir / ".kiro" / "specs"

        if feature_name:
            spec_dir = specs_dir / feature_name
            if spec_dir.exists():
                for f in spec_dir.iterdir():
                    if f.is_file():
                        files_found.append(f.name)
        else:
            # Check all specs
            if specs_dir.exists():
                for spec_dir in specs_dir.iterdir():
                    if spec_dir.is_dir():
                        for f in spec_dir.iterdir():
                            if f.is_file():
                                files_found.append(f"{spec_dir.name}/{f.name}")

            # Also check root-level files
            for root_file in ["brief.md", "roadmap.md"]:
                if (self.work_dir / ".kiro" / root_file).exists():
                    files_found.append(root_file)

        return files_found


# ---------------------------------------------------------------------------
# Singleton instance
# ---------------------------------------------------------------------------

_sdd_service: Optional[SddService] = None


def get_sdd_service() -> SddService:
    """Get or create the global SDD service instance."""
    global _sdd_service
    if _sdd_service is None:
        _sdd_service = SddService()
    return _sdd_service
