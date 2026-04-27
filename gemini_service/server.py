"""
Gemini WebSocket Server — independent service for CC-SDD.

Runs on port 9001.  The BESSER frontend connects here for all
gemini-cli interactions (discovery, spec phases, user input).

Architecture:
  - Gemini runs as a PERSISTENT interactive session via pywinpty.
  - Commands are sent to stdin, output is streamed from stdout.
  - User answers are piped directly to gemini's stdin.
  - The session stays alive between pipeline phases.
  - On the "design" phase, LangGraph agents generate the class diagram
    BEFORE the standard /kiro-spec-design command runs.
  - A "sync_diagram" action allows bidirectional traceability.
  - A "generate_diagram" action allows explicit diagram generation at any time.
  - A "set_workspace" action allows the frontend to select the working folder.

Usage:
    python -m gemini_service.server
"""

import asyncio
import json
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env file (GEMINI_API_KEY, etc.)
# ---------------------------------------------------------------------------

try:
    from dotenv import load_dotenv

    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)
        print(f"[ENV] Loaded .env from {_env_path}", flush=True)
    else:
        print(f"[ENV] No .env found at {_env_path} — using system env vars", flush=True)
except ImportError:
    print("[ENV] python-dotenv not installed — using system env vars only", flush=True)

try:
    import websockets
except ImportError:
    print("ERROR: 'websockets' is required. Install with: pip install websockets")
    sys.exit(1)

from gemini_service.bridge import GeminiBridge

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOST = "0.0.0.0"
PORT = int(os.environ.get("GEMINI_WS_PORT", "9001"))

# Default work dir — can be overridden at runtime via set_workspace action
_DEFAULT_WORK_DIR = Path(
    os.environ.get("SDD_WORK_DIR", "").strip()
    or str(Path(__file__).resolve().parent.parent / "sdd-workspace")
)


def slugify_idea(idea: str) -> str:
    """Generate a short feature name from the idea text."""
    skip = {
        "quiero", "un", "una", "el", "la", "los", "las", "de", "del",
        "que", "para", "por", "con", "mi", "mis", "sistema", "web",
        "necesito", "deseo", "me", "se", "y", "o", "en", "al",
    }
    words = re.sub(r"[^\w\s]", "", idea.lower()).split()
    meaningful = [w for w in words if w not in skip and len(w) > 2][:3]
    if not meaningful:
        meaningful = words[:3]
    return "-".join(meaningful) or "main-feature"


# ---------------------------------------------------------------------------
# Globals (mutable — workspace can change at runtime)
# ---------------------------------------------------------------------------

WORK_DIR: Path = _DEFAULT_WORK_DIR
bridge: GeminiBridge = GeminiBridge(work_dir=WORK_DIR)
_current_feature: str = ""
_current_idea: str = ""


# ---------------------------------------------------------------------------
# Workspace scaffolding
# ---------------------------------------------------------------------------

# Minimal .gemini/settings.json for a new workspace
# NOTE: Do NOT include "model" as a string — gemini-cli expects an object.
_GEMINI_SETTINGS = {}

# Minimal .kiro/steering/spec.json template
_KIRO_SPEC_TEMPLATE = {
    "language": "es",
    "framework": "generic",
    "testingFramework": "",
}


def _scaffold_workspace(workspace: Path) -> list[str]:
    """
    Ensure .gemini and .kiro directories exist in the workspace.
    Creates them with CC-SDD defaults if missing.
    Returns a list of actions taken.
    """
    actions = []

    # -- .gemini --
    gemini_dir = workspace / ".gemini"
    if not gemini_dir.exists():
        gemini_dir.mkdir(parents=True, exist_ok=True)
        actions.append("Creado .gemini/")

    settings_file = gemini_dir / "settings.json"
    if not settings_file.exists():
        settings_file.write_text(
            json.dumps(_GEMINI_SETTINGS, indent=2), encoding="utf-8"
        )
        actions.append("Creado .gemini/settings.json")

    # Copy skills from the default sdd-workspace if available
    skills_src = _DEFAULT_WORK_DIR / ".gemini" / "skills"
    skills_dst = gemini_dir / "skills"
    if skills_src.exists() and not skills_dst.exists():
        shutil.copytree(str(skills_src), str(skills_dst))
        actions.append("Copiadas skills CC-SDD a .gemini/skills/")

    # Copy agents from the default sdd-workspace if available
    agents_src = _DEFAULT_WORK_DIR / ".gemini" / "agents"
    agents_dst = gemini_dir / "agents"
    if agents_src.exists() and not agents_dst.exists():
        shutil.copytree(str(agents_src), str(agents_dst))
        actions.append("Copiados agents a .gemini/agents/")

    # -- .kiro --
    kiro_dir = workspace / ".kiro"
    if not kiro_dir.exists():
        kiro_dir.mkdir(parents=True, exist_ok=True)
        actions.append("Creado .kiro/")

    specs_dir = kiro_dir / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    steering_dir = kiro_dir / "steering"
    steering_dir.mkdir(parents=True, exist_ok=True)

    settings_dir = kiro_dir / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)

    # Copy steering/settings from default if available
    for sub in ["steering", "settings"]:
        src = _DEFAULT_WORK_DIR / ".kiro" / sub
        dst = kiro_dir / sub
        if src.exists():
            for item in src.iterdir():
                dst_item = dst / item.name
                if not dst_item.exists():
                    if item.is_dir():
                        shutil.copytree(str(item), str(dst_item))
                    else:
                        shutil.copy2(str(item), str(dst_item))
                    actions.append(f"Copiado .kiro/{sub}/{item.name}")

    if not actions:
        actions.append("Workspace ya inicializado — sin cambios")

    return actions


# ---------------------------------------------------------------------------
# Agent availability check
# ---------------------------------------------------------------------------

_agents_available = None  # cached result


def _check_agents_available() -> tuple[bool, str]:
    """Check if LangGraph agents can run. Returns (available, reason)."""
    global _agents_available

    if _agents_available is not None:
        return _agents_available

    # Check API key
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        _agents_available = (False, "GEMINI_API_KEY no está configurada")
        return _agents_available

    # Check dependencies
    try:
        import langgraph  # noqa: F401
        import langchain_google_genai  # noqa: F401
    except ImportError as e:
        _agents_available = (False, f"Dependencia faltante: {e}")
        return _agents_available

    _agents_available = (True, "OK")
    return _agents_available


def _run_diagram_agents(feature: str, requirements_text: str):
    """Run the LangGraph creator/reviewer/traceability pipeline.
    Returns dict with keys: spec (SystemSpec), traceability_md (markdown).
    """
    from gemini_service.agents import run_diagram_pipeline
    return run_diagram_pipeline(feature, requirements_text)


def _run_sync_agents(feature: str, old_diagram, new_diagram, requirements_text: str):
    """Run the sync/traceability pipeline. Returns updated requirements or None."""
    from gemini_service.agents import run_sync_pipeline
    return run_sync_pipeline(feature, old_diagram, new_diagram, requirements_text)


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def stream_output(ws):
    """Background task: continuously stream gemini output to the client."""
    idle_ticks = 0
    notified_waiting = False

    while True:
        try:
            output = await asyncio.to_thread(bridge.read_available, 0.4)

            if output:
                idle_ticks = 0
                notified_waiting = False
                await ws.send(json.dumps({"type": "output", "data": output}))
            else:
                idle_ticks += 1
                if idle_ticks >= 5 and not notified_waiting and bridge.is_running:
                    await ws.send(json.dumps({"type": "waiting_input"}))
                    notified_waiting = True
                if not bridge.is_running:
                    await ws.send(json.dumps({"type": "session_ended"}))
                    # AUTO-DETECT: After session ends, check if any feature
                    # has design.md but no diagram.json → auto-generate diagram
                    await _auto_generate_missing_diagrams(ws)
                    break
        except (asyncio.CancelledError, Exception):
            break

        await asyncio.sleep(0.05)


async def _auto_generate_missing_diagrams(ws):
    """
    Scan all feature specs for design.md files that have NO corresponding
    diagram.json. If found, auto-trigger diagram generation.

    This handles the case where gemini-cli runs /kiro-spec-design internally
    (from natural language), bypassing our _handle_design_phase interceptor.
    """
    specs_dir = WORK_DIR / ".kiro" / "specs"
    if not specs_dir.exists():
        return

    for feature_dir in specs_dir.iterdir():
        if not feature_dir.is_dir():
            continue

        design_path = feature_dir / "design.md"
        diagram_path = feature_dir / "diagram.json"
        req_path = feature_dir / "requirements.md"

        # Only generate if: design.md exists, diagram.json does NOT, requirements.md exists
        if design_path.exists() and not diagram_path.exists() and req_path.exists():
            feature_name = feature_dir.name
            print(f"[AUTO] Detected design.md without diagram.json for '{feature_name}'", flush=True)

            await ws.send(json.dumps({
                "type": "output",
                "data": (
                    f"\n🔎 Detectado: design.md existe para '{feature_name}' pero falta el diagrama.\n"
                    f"   Generando diagrama de clases automáticamente...\n\n"
                ),
            }))

            success = await _try_generate_diagram(ws, feature_name)
            if success:
                # Update current feature to match what was generated
                global _current_feature
                _current_feature = feature_name


async def handler(ws):
    """Handle a single WebSocket connection."""
    global _current_feature, _current_idea, WORK_DIR, bridge

    print(f"\n[WS] Client connected from {ws.remote_address}", flush=True)
    streaming_task = None

    def ensure_streaming():
        nonlocal streaming_task
        if streaming_task is None or streaming_task.done():
            streaming_task = asyncio.create_task(stream_output(ws))

    if bridge.is_running:
        ensure_streaming()

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "error", "error": "JSON invalido"}))
                continue

            action = msg.get("action")

            if action == "ping":
                await ws.send(json.dumps({
                    "type": "pong",
                    "running": bridge.is_running,
                    "feature": _current_feature,
                    "workspace": str(WORK_DIR),
                }))

            # ---- Set workspace folder ----
            elif action == "set_workspace":
                await _handle_set_workspace(ws, msg)

            elif action == "input":
                text = msg.get("text", "")
                if text:
                    await asyncio.to_thread(bridge.send_input, text)
                    ensure_streaming()

            elif action == "discovery":
                idea = msg.get("idea", "")
                if not idea:
                    await ws.send(json.dumps({"type": "error", "error": "Falta 'idea'"}))
                    continue

                _current_idea = idea
                _current_feature = slugify_idea(idea)

                await ws.send(json.dumps({
                    "type": "phase_start",
                    "phase": "discovery",
                    "idea": idea,
                    "feature": _current_feature,
                }))

                command = f"/kiro-discovery {idea}"
                await asyncio.to_thread(bridge.send_command, command, True)
                ensure_streaming()

            elif action == "spec":
                phase = msg.get("phase", "")
                feature = msg.get("feature", "") or _current_feature
                if not feature:
                    feature = "main-feature"
                _current_feature = feature

                if not phase:
                    await ws.send(json.dumps({"type": "error", "error": "Falta 'phase'"}))
                    continue

                command_map = {
                    "init": f"/kiro-spec-init {feature}",
                    "requirements": f"/kiro-spec-requirements {feature}",
                    "design": f"/kiro-spec-design {feature}",
                    "tasks": f"/kiro-spec-tasks {feature}",
                    "quick": f"/kiro-spec-quick {feature}",
                }
                cmd = command_map.get(phase)
                if not cmd:
                    await ws.send(json.dumps({"type": "error", "error": f"Fase desconocida: {phase}"}))
                    continue

                await ws.send(json.dumps({
                    "type": "phase_start",
                    "phase": phase,
                    "feature": feature,
                }))

                # DESIGN PHASE: run LangGraph agents first, then kiro-spec-design
                if phase == "design":
                    await _handle_design_phase(ws, feature, cmd, ensure_streaming)
                else:
                    await asyncio.to_thread(bridge.send_command, cmd)
                    ensure_streaming()

            elif action == "impl":
                feature = msg.get("feature", "") or _current_feature
                if not feature:
                    feature = "main-feature"

                cmd = f"/kiro-impl {feature}"
                task_ids = msg.get("task_ids")
                if task_ids:
                    cmd += " " + " ".join(task_ids)

                await ws.send(json.dumps({
                    "type": "phase_start",
                    "phase": "implementation",
                    "feature": feature,
                }))

                await asyncio.to_thread(bridge.send_command, cmd)
                ensure_streaming()

            # Explicit diagram generation (can be triggered anytime)
            elif action == "generate_diagram":
                await _handle_generate_diagram(ws, msg)

            # Export diagram to editor (read diagram.json and send to canvas)
            elif action == "export_diagram":
                await _handle_export_diagram(ws, msg)

            # Sync diagram (traceability)
            elif action == "sync_diagram":
                await _handle_sync_diagram(ws, msg)

            else:
                await ws.send(json.dumps({
                    "type": "error",
                    "error": f"Accion desconocida: {action}",
                }))

    except websockets.exceptions.ConnectionClosed:
        print("[WS] Client disconnected", flush=True)
    except Exception as exc:
        print(f"[WS] Error: {exc}", flush=True)
    finally:
        if streaming_task and not streaming_task.done():
            streaming_task.cancel()


# ---------------------------------------------------------------------------
# Set workspace handler
# ---------------------------------------------------------------------------

async def _handle_set_workspace(ws, msg: dict):
    """Change the working directory and scaffold .gemini/.kiro if needed."""
    global WORK_DIR, bridge

    workspace_path = msg.get("path", "").strip()
    if not workspace_path:
        await ws.send(json.dumps({
            "type": "error",
            "error": "Falta 'path' en set_workspace",
        }))
        return

    new_dir = Path(workspace_path)

    # Validate the path exists (or create it)
    if not new_dir.exists():
        try:
            new_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            await ws.send(json.dumps({
                "type": "error",
                "error": f"No se pudo crear la carpeta: {exc}",
            }))
            return

    if not new_dir.is_dir():
        await ws.send(json.dumps({
            "type": "error",
            "error": f"La ruta no es una carpeta: {workspace_path}",
        }))
        return

    # Scaffold .gemini and .kiro
    actions = _scaffold_workspace(new_dir)

    # Update globals
    old_dir = WORK_DIR
    WORK_DIR = new_dir

    # Recreate bridge with new work dir
    bridge.close()
    bridge = GeminiBridge(work_dir=WORK_DIR)

    await ws.send(json.dumps({
        "type": "output",
        "data": (
            f"\n📂 Workspace configurado: {new_dir}\n"
            + "".join(f"   ✔ {a}\n" for a in actions)
            + "\n"
        ),
    }))

    await ws.send(json.dumps({
        "type": "workspace_set",
        "path": str(new_dir),
        "previous": str(old_dir),
        "actions": actions,
    }))

    print(f"[WS] Workspace changed: {old_dir} → {new_dir}", flush=True)


# ---------------------------------------------------------------------------
# Design phase handler
# ---------------------------------------------------------------------------

async def _handle_design_phase(ws, feature: str, kiro_cmd: str, ensure_streaming):
    """Run LangGraph agents, send diagram to frontend, then run kiro-spec-design."""
    # Try to generate diagram with LangGraph agents
    diagram_generated = await _try_generate_diagram(ws, feature)

    # Continue with the standard kiro-spec-design command regardless
    if diagram_generated:
        await ws.send(json.dumps({
            "type": "output",
            "data": "\n📋 Continuando con la especificación de diseño...\n\n",
        }))
    await asyncio.to_thread(bridge.send_command, kiro_cmd)
    ensure_streaming()


# ---------------------------------------------------------------------------
# Diagram generation (standalone)
# ---------------------------------------------------------------------------

async def _try_generate_diagram(ws, feature: str) -> bool:
    """
    Attempt to generate a class diagram using LangGraph agents.
    Returns True if successful, False otherwise.
    """
    # Check prerequisites
    available, reason = _check_agents_available()
    if not available:
        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n⚠️ [AGENTS] No se pueden ejecutar los agentes de diagrama: {reason}\n"
                f"   Para habilitar la generación automática:\n"
                f"   1. Crea gemini_service/.env con GEMINI_API_KEY=tu_clave\n"
                f"   2. Instala: pip install langgraph langchain-google-genai\n"
                f"   3. Reinicia gemini_service\n\n"
            ),
        }))
        return False

    # Check requirements.md exists
    req_path = WORK_DIR / ".kiro" / "specs" / feature / "requirements.md"
    if not req_path.exists():
        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n⚠️ [AGENTS] requirements.md no encontrado para '{feature}'.\n"
                f"   Ejecuta primero la fase de 'requirements'.\n"
                f"   Ruta esperada: {req_path}\n\n"
            ),
        }))
        return False

    req_text = req_path.read_text(encoding="utf-8")

    await ws.send(json.dumps({
        "type": "output",
        "data": (
            "\n🤖 Iniciando generación de Diagrama de Clases (dominio)...\n"
            "   Agente Creador → Revisor Estructural → Revisor Trazabilidad\n\n"
        ),
    }))

    try:
        result = await asyncio.to_thread(
            _run_diagram_agents, feature, req_text,
        )

        diagram_spec = result["spec"]
        traceability_md = result.get("traceability_md", "")

        # Save diagram.json
        feature_dir = WORK_DIR / ".kiro" / "specs" / feature
        feature_dir.mkdir(parents=True, exist_ok=True)

        diagram_path = feature_dir / "diagram.json"
        diagram_path.write_text(
            json.dumps(diagram_spec, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Save traceability.md
        if traceability_md:
            trace_path = feature_dir / "traceability.md"
            trace_path.write_text(traceability_md, encoding="utf-8")

        class_count = len(diagram_spec.get("classes", []))
        rel_count = len(diagram_spec.get("relationships", []))

        trace_msg = ""
        if traceability_md:
            trace_msg = f"   🔗 Trazabilidad guardada en: .kiro/specs/{feature}/traceability.md\n"

        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n✅ Diagrama de dominio generado exitosamente:\n"
                f"   📊 {class_count} clases, {rel_count} relaciones\n"
                f"   💾 Guardado en: .kiro/specs/{feature}/diagram.json\n"
                f"{trace_msg}"
                f"   🖼️ Inyectando en BESSER...\n\n"
            ),
        }))

        # Send to BESSER frontend for rendering
        await ws.send(json.dumps({
            "type": "render_diagram",
            "systemSpec": diagram_spec,
        }))

        return True

    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[AGENTS] Error: {tb}", flush=True)
        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n❌ Error generando diagrama: {exc}\n"
                f"   Verifica GEMINI_API_KEY y las dependencias.\n"
                f"   Puedes reintentar escribiendo 'generar diagrama' en el chat.\n\n"
            ),
        }))
        return False


# ---------------------------------------------------------------------------
# Explicit generate_diagram handler
# ---------------------------------------------------------------------------

async def _handle_generate_diagram(ws, msg: dict):
    """Handle explicit diagram generation request."""
    global _current_feature

    feature = msg.get("feature", "") or _current_feature

    # Auto-discover feature if not set or doesn't exist
    if not feature or not (WORK_DIR / ".kiro" / "specs" / feature / "requirements.md").exists():
        specs_dir = WORK_DIR / ".kiro" / "specs"
        if specs_dir.exists():
            for d in specs_dir.iterdir():
                if d.is_dir() and (d / "requirements.md").exists():
                    feature = d.name
                    _current_feature = feature
                    break

    if not feature:
        await ws.send(json.dumps({
            "type": "output",
            "data": "\n⚠️ No hay feature con requirements.md. Ejecuta Discovery y Requirements primero.\n",
        }))
        return

    success = await _try_generate_diagram(ws, feature)
    if not success:
        # Check if diagram.json already exists (maybe from a previous run)
        diagram_path = WORK_DIR / ".kiro" / "specs" / feature / "diagram.json"
        if diagram_path.exists():
            try:
                diagram_spec = json.loads(diagram_path.read_text(encoding="utf-8"))
                await ws.send(json.dumps({
                    "type": "output",
                    "data": "\n📂 Se encontró un diagram.json existente. Inyectando...\n",
                }))
                await ws.send(json.dumps({
                    "type": "render_diagram",
                    "systemSpec": diagram_spec,
                }))
            except Exception:
                pass

# ---------------------------------------------------------------------------
# Export diagram to editor handler (NEW)
# ---------------------------------------------------------------------------

async def _handle_export_diagram(ws, msg: dict):
    """Read diagram.json from disk and send it to the frontend canvas."""
    global _current_feature

    feature = msg.get("feature", "") or _current_feature
    if not feature:
        # Auto-discover
        specs_dir = WORK_DIR / ".kiro" / "specs"
        if specs_dir.exists():
            for d in specs_dir.iterdir():
                if d.is_dir() and (d / "diagram.json").exists():
                    feature = d.name
                    _current_feature = feature
                    break

    if not feature:
        await ws.send(json.dumps({
            "type": "output",
            "data": "\n⚠️ No hay feature con diagram.json. Genera el diagrama primero.\n",
        }))
        return

    diagram_path = WORK_DIR / ".kiro" / "specs" / feature / "diagram.json"
    if not diagram_path.exists():
        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n⚠️ No se encontró diagram.json para '{feature}'.\n"
                f"   Ejecuta primero la fase de diseño o escribe 'generar diagrama'.\n"
            ),
        }))
        return

    try:
        diagram_spec = json.loads(diagram_path.read_text(encoding="utf-8"))
        class_count = len(diagram_spec.get("classes", []))
        rel_count = len(diagram_spec.get("relationships", []))

        await ws.send(json.dumps({
            "type": "output",
            "data": (
                f"\n📤 Exportando diagrama al editor BESSER...\n"
                f"   📊 {class_count} clases, {rel_count} relaciones\n\n"
            ),
        }))

        await ws.send(json.dumps({
            "type": "render_diagram",
            "systemSpec": diagram_spec,
        }))

    except Exception as exc:
        await ws.send(json.dumps({
            "type": "output",
            "data": f"\n❌ Error leyendo diagram.json: {exc}\n",
        }))


# ---------------------------------------------------------------------------
# Sync diagram handler (traceability)
# ---------------------------------------------------------------------------

async def _handle_sync_diagram(ws, msg: dict):
    """
    Receive the current canvas model from BESSER, compare against the
    stored diagram.json, and update requirements.md if changes exist.
    """
    global _current_feature

    feature = msg.get("feature", "") or _current_feature
    if not feature:
        await ws.send(json.dumps({
            "type": "sync_result",
            "status": "error",
            "message": "No hay feature activo. Ejecuta Discovery primero.",
        }))
        return

    # Check agent availability
    available, reason = _check_agents_available()
    if not available:
        await ws.send(json.dumps({
            "type": "sync_result",
            "status": "error",
            "message": f"Agentes no disponibles: {reason}",
        }))
        return

    new_diagram = msg.get("systemSpec")
    if not new_diagram or not isinstance(new_diagram, dict):
        await ws.send(json.dumps({
            "type": "sync_result",
            "status": "error",
            "message": "No se recibió el diagrama del canvas.",
        }))
        return

    diagram_path = WORK_DIR / ".kiro" / "specs" / feature / "diagram.json"
    req_path = WORK_DIR / ".kiro" / "specs" / feature / "requirements.md"

    old_diagram = {}
    if diagram_path.exists():
        try:
            old_diagram = json.loads(diagram_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    req_text = ""
    if req_path.exists():
        req_text = req_path.read_text(encoding="utf-8")

    await ws.send(json.dumps({
        "type": "output",
        "data": "\n🔄 Analizando cambios en el diagrama...\n",
    }))

    try:
        updated_req = await asyncio.to_thread(
            _run_sync_agents, feature, old_diagram, new_diagram, req_text,
        )

        if updated_req is None:
            await ws.send(json.dumps({
                "type": "output",
                "data": "✅ No se detectaron cambios en el diagrama.\n",
            }))
            await ws.send(json.dumps({
                "type": "sync_result",
                "status": "no_changes",
                "message": "El diagrama no ha cambiado respecto al guardado.",
            }))
        else:
            req_path.write_text(updated_req, encoding="utf-8")
            diagram_path.write_text(
                json.dumps(new_diagram, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            await ws.send(json.dumps({
                "type": "output",
                "data": (
                    "✅ Trazabilidad actualizada:\n"
                    "   → requirements.md actualizado\n"
                    "   → diagram.json actualizado\n"
                ),
            }))
            await ws.send(json.dumps({
                "type": "sync_result",
                "status": "updated",
                "message": "Requisitos actualizados exitosamente.",
            }))

    except Exception as exc:
        tb = traceback.format_exc()
        print(f"[SYNC] Error: {tb}", flush=True)
        await ws.send(json.dumps({
            "type": "output",
            "data": f"\n❌ Error durante sincronización: {exc}\n",
        }))
        await ws.send(json.dumps({
            "type": "sync_result",
            "status": "error",
            "message": str(exc),
        }))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # Print agent status at startup
    available, reason = _check_agents_available()
    status_icon = "✅" if available else "⚠️"

    print(f"\n{'='*60}", flush=True)
    print(f"  CC-SDD Gemini Service", flush=True)
    print(f"  WebSocket: ws://{HOST}:{PORT}", flush=True)
    print(f"  Work dir:  {WORK_DIR}", flush=True)
    print(f"  Agents:    {status_icon} {reason}", flush=True)
    print(f"{'='*60}\n", flush=True)

    if not available:
        print(
            f"  ⚠️  Los agentes LangGraph para diagramas NO están disponibles.\n"
            f"     Razón: {reason}\n"
            f"     El servicio funcionará pero sin generación automática de diagramas.\n"
            f"     Para habilitarlos:\n"
            f"       1. Crea gemini_service/.env con GEMINI_API_KEY=tu_clave\n"
            f"       2. pip install langgraph langchain-google-genai\n",
            flush=True,
        )

    async with websockets.serve(handler, HOST, PORT):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[GEMINI] Service stopped", flush=True)
        bridge.close()
