"""
Gemini WebSocket Server — independent service for CC-SDD.

Runs on port 9001.  The BESSER frontend connects here for all
gemini-cli interactions (discovery, spec phases, user input).

Architecture:
  - Gemini runs as a PERSISTENT interactive session via pywinpty.
  - Commands are sent to stdin, output is streamed from stdout.
  - User answers are piped directly to gemini's stdin.
  - The session stays alive between pipeline phases.

Usage:
    python -m gemini_service.server
"""

import asyncio
import json
import os
import re
import sys
from pathlib import Path

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
WORK_DIR = Path(
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
# Globals
# ---------------------------------------------------------------------------

bridge = GeminiBridge(work_dir=WORK_DIR)
# Track per-connection state
_current_feature: str = ""
_current_idea: str = ""


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def stream_output(ws):
    """Background task: continuously stream gemini output to the client.

    Sends ``waiting_input`` events when gemini is idle (waiting for user).
    """
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

                # After ~2 seconds of silence, tell frontend gemini is waiting
                if idle_ticks >= 5 and not notified_waiting and bridge.is_running:
                    await ws.send(json.dumps({"type": "waiting_input"}))
                    notified_waiting = True

                # If process actually exited
                if not bridge.is_running:
                    await ws.send(json.dumps({"type": "session_ended"}))
                    break

        except (asyncio.CancelledError, Exception):
            break

        await asyncio.sleep(0.05)


async def handler(ws):
    """Handle a single WebSocket connection."""
    global _current_feature, _current_idea

    print(f"\n[WS] Client connected from {ws.remote_address}", flush=True)
    streaming_task = None

    def ensure_streaming():
        nonlocal streaming_task
        if streaming_task is None or streaming_task.done():
            streaming_task = asyncio.create_task(stream_output(ws))

    # If the client reconnects while gemini is still running, resume streaming.
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
                }))

            elif action == "input":
                # User answers a question from gemini
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

                # Start session and send discovery command
                command = f"/kiro-discovery {idea}"
                await asyncio.to_thread(bridge.send_command, command, True)
                ensure_streaming()

            elif action == "spec":
                phase = msg.get("phase", "")
                # Feature is auto-derived, but allow override
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
# Main
# ---------------------------------------------------------------------------

async def main():
    print(f"\n{'='*60}", flush=True)
    print(f"  CC-SDD Gemini Service", flush=True)
    print(f"  WebSocket: ws://{HOST}:{PORT}", flush=True)
    print(f"  Work dir:  {WORK_DIR}", flush=True)
    print(f"{'='*60}\n", flush=True)

    async with websockets.serve(handler, HOST, PORT):
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[GEMINI] Service stopped", flush=True)
        bridge.close()
