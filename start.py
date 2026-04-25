#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║        BESSER + AI-SDD  —  Script de Inicio Rápido          ║
╚══════════════════════════════════════════════════════════════╝

Abre 3 terminales (una por servicio):
  1. Backend  (FastAPI)       → http://localhost:9000/besser_api
  2. Frontend (React/Webpack) → http://localhost:8080
  3. Modeling Agent (WS)      → ws://localhost:8765

Uso:
  python start_besser.py
"""

import os
import sys
import time
import subprocess
import platform
from pathlib import Path

# ─── Colores ─────────────────────────────────────────────────
G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; R = "\033[91m"
B = "\033[1m";  M = "\033[95m"; RESET = "\033[0m"

# ─── Rutas ───────────────────────────────────────────────────
BASE       = Path(__file__).resolve().parent
BESSER     = BASE / "BESSER"
AGENT      = BASE / "modeling-agent"
FRONTEND   = BESSER / "besser" / "utilities" / "web_modeling_editor" / "frontend"
PY_BACK    = BESSER / "venv" / "Scripts" / "python.exe"
PY_AGENT   = AGENT  / "venv" / "Scripts" / "python.exe"
NPM        = "npm.cmd" if platform.system() == "Windows" else "npm"


def ok(msg):   print(f"{G}✔  {msg}{RESET}")
def warn(msg): print(f"{Y}⚠  {msg}{RESET}")
def fail(msg): print(f"{R}✖  {msg}{RESET}"); sys.exit(1)


def check():
    """Validar que todo exista."""
    for label, path in [
        ("BESSER",          BESSER),
        ("modeling-agent",  AGENT),
        ("Frontend",        FRONTEND),
        ("Python backend",  PY_BACK),
        ("Python agent",    PY_AGENT),
    ]:
        if not path.exists():
            fail(f"{label} no encontrado: {path}\n   Ejecuta primero: python setup_besser.py")
    ok("Estructura de directorios OK")


def open_terminal(title, cmd_str, cwd):
    """Abre una nueva ventana CMD en Windows."""
    full = f'cd /d "{cwd}" && {cmd_str}'
    subprocess.Popen(
        f'start "{title}" cmd /k "{full}"',
        shell=True, cwd=str(cwd),
    )


def main():
    print(f"""
{C}{B}
╔══════════════════════════════════════════════════════════════╗
║        BESSER + AI-SDD  —  Script de Inicio Rápido          ║
╚══════════════════════════════════════════════════════════════╝
{RESET}""")

    check()

    # 1) Backend
    print(f"\n{B}▶ Abriendo Backend...{RESET}")
    open_terminal(
        "BESSER | Backend (puerto 9000)",
        f'"{PY_BACK}" -m uvicorn '
        f'besser.utilities.web_modeling_editor.backend.backend:app '
        f'--reload --port 9000',
        BESSER,
    )
    ok("Backend → http://localhost:9000/besser_api/docs")
    time.sleep(1)

    # 2) Frontend
    print(f"{B}▶ Abriendo Frontend...{RESET}")
    open_terminal(
        "BESSER | Frontend (puerto 8080)",
        f'{NPM} run dev',
        FRONTEND,
    )
    ok("Frontend → http://localhost:8080")
    time.sleep(1)

    # 3) Modeling Agent
    print(f"{B}▶ Abriendo Modeling Agent...{RESET}")
    # Buscar punto de entrada
    entry = "modeling_agent.py"
    if not (AGENT / entry).exists():
        for f in ["main.py", "agent.py", "app.py"]:
            if (AGENT / f).exists():
                entry = f; break
    open_terminal(
        "BESSER | Modeling Agent (WS 8765)",
        f'"{PY_AGENT}" {entry}',
        AGENT,
    )
    ok("Agent  → ws://localhost:8765")

    # Resumen
    print(f"""
{G}{B}
══════════════════════════════════════════════════════════════
   ✅  3 terminales abiertas — servicios arrancando
══════════════════════════════════════════════════════════════
{RESET}
  {C}Backend {RESET} → http://localhost:9000/besser_api/docs
  {M}Frontend{RESET} → http://localhost:8080
  {G}Agent   {RESET} → ws://localhost:8765

  {Y}⚡ AI-SDD endpoints:{RESET}
     GET  /besser_api/sdd/status
     POST /besser_api/sdd/config      ← {{api_key: "tu-key"}}
     POST /besser_api/sdd/generate    ← {{idea: "tu idea"}}
     POST /besser_api/sdd/vibe-modeling
     POST /besser_api/sdd/sync-traceability

  {Y}Para probar SDD desde el navegador:{RESET}
     1. Abre http://localhost:8080
     2. Click en el botón ⚡ (barra superior)
     3. Configura tu API Key de Gemini
     4. Escribe una idea y genera

  {Y}Para probar SDD desde terminal (curl):{RESET}
     curl -X POST http://localhost:9000/besser_api/sdd/config \\
       -H "Content-Type: application/json" \\
       -d '{{"api_key":"TU_GEMINI_KEY"}}'

     curl -X POST http://localhost:9000/besser_api/sdd/generate \\
       -H "Content-Type: application/json" \\
       -d '{{"idea":"Sistema de gestión de pedidos con clientes y productos"}}'

  {Y}Cierra cada ventana de terminal para detener su servicio.{RESET}
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Y}Cancelado.{RESET}")
