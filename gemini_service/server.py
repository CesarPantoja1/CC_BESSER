"""
Gemini WebSocket Server — independent service for CC-SDD.

Runs on port 9001.  The BESSER frontend connects here for all
gemini-cli interactions (discovery, spec phases, user input).

Usage:
    python -m gemini_service.server
    # or
    python gemini_service/server.py
"""

import asyncio
import json
import os
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
WORK_DIR = Path(os.environ.get("SDD_WORK_DIR", "").strip() or str(Path(__file__).resolve().parent.parent / "sdd-workspace"))

# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

bridge = GeminiBridge(work_dir=WORK_DIR)


async def stream_output(ws):
    """Background task: stream gemini output to the WebSocket client."""
    while True:
        try:
            output = await asyncio.to_thread(bridge.read_available, 0.3)
            if output:
                await ws.send(json.dumps({"type": "output", "data": output}))

            # Check if process finished
            if not bridge.is_running and not output:
                # Process ended — send completion signal
                await asyncio.sleep(0.3)  # One more drain
                final = await asyncio.to_thread(bridge.read_available, 0.2)
                if final:
                    await ws.send(json.dumps({"type": "output", "data": final}))
                await ws.send(json.dumps({"type": "process_done"}))
                break

        except Exception:
            break
        await asyncio.sleep(0.05)


async def handler(ws):
    """Handle a single WebSocket connection."""
    print(f"\n[WS] Client connected from {ws.remote_address}", flush=True)
    streaming_task = None

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "error", "error": "JSON inválido"}))
                continue

            action = msg.get("action")

            if action == "ping":
                await ws.send(json.dumps({"type": "pong"}))

            elif action == "input":
                # User answers a question from gemini
                text = msg.get("text", "")
                if text:
                    await asyncio.to_thread(bridge.send_input, text)

            elif action == "discovery":
                idea = msg.get("idea", "")
                if not idea:
                    await ws.send(json.dumps({"type": "error", "error": "Falta 'idea'"}))
                    continue

                await ws.send(json.dumps({
                    "type": "phase_start", "phase": "discovery", "idea": idea,
                }))

                command = f"/kiro-discovery {idea}"
                await asyncio.to_thread(bridge.execute, command)

                # Cancel previous streaming task if any
                if streaming_task and not streaming_task.done():
                    streaming_task.cancel()
                streaming_task = asyncio.create_task(stream_output(ws))

            elif action == "spec":
                phase = msg.get("phase", "")
                feature = msg.get("feature", "")
                auto = msg.get("auto", False)
                if not phase or not feature:
                    await ws.send(json.dumps({"type": "error", "error": "Falta 'phase' o 'feature'"}))
                    continue

                command_map = {
                    "init": f"/kiro-spec-init {feature}",
                    "requirements": f"/kiro-spec-requirements {feature}",
                    "design": f"/kiro-spec-design {feature}" + (" -y" if auto else ""),
                    "tasks": f"/kiro-spec-tasks {feature}" + (" -y" if auto else ""),
                    "quick": f"/kiro-spec-quick {feature}" + (" --auto" if auto else ""),
                }
                cmd = command_map.get(phase)
                if not cmd:
                    await ws.send(json.dumps({"type": "error", "error": f"Fase desconocida: {phase}"}))
                    continue

                await ws.send(json.dumps({
                    "type": "phase_start", "phase": phase, "feature": feature,
                }))

                await asyncio.to_thread(bridge.execute, cmd)
                if streaming_task and not streaming_task.done():
                    streaming_task.cancel()
                streaming_task = asyncio.create_task(stream_output(ws))

            elif action == "impl":
                feature = msg.get("feature", "")
                if not feature:
                    await ws.send(json.dumps({"type": "error", "error": "Falta 'feature'"}))
                    continue

                cmd = f"/kiro-impl {feature}"
                task_ids = msg.get("task_ids")
                if task_ids:
                    cmd += " " + " ".join(task_ids)

                await ws.send(json.dumps({
                    "type": "phase_start", "phase": "implementation", "feature": feature,
                }))

                await asyncio.to_thread(bridge.execute, cmd)
                if streaming_task and not streaming_task.done():
                    streaming_task.cancel()
                streaming_task = asyncio.create_task(stream_output(ws))

            else:
                await ws.send(json.dumps({
                    "type": "error",
                    "error": f"Acción desconocida: {action}",
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
