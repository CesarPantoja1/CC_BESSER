#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║           BESSER-PEARL  –  Script de Instalación                ║
║   Instala: BESSER Core + Web Modeling Editor + Modeling Agent   ║
╚══════════════════════════════════════════════════════════════════╝

Uso:
  python setup_besser.py

El script debe ejecutarse DENTRO de la carpeta donde quieres
que vivan los proyectos. Crea las subcarpetas:
  ./BESSER/
  ./modeling-agent/
"""

import os
import sys
import shutil
import subprocess
import platform
import textwrap
from pathlib import Path

# ──────────────────────────────────────────────────────────────────
# Helpers de UI
# ──────────────────────────────────────────────────────────────────

BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"


def banner():
    print(f"""
{CYAN}{BOLD}
╔══════════════════════════════════════════════════════════════════╗
║           BESSER-PEARL  –  Script de Instalación                ║
║   Instala: BESSER Core + Web Modeling Editor + Modeling Agent   ║
╚══════════════════════════════════════════════════════════════════╝
{RESET}""")


def step(msg):
    print(f"\n{BOLD}{CYAN}▶  {msg}{RESET}")


def ok(msg):
    print(f"{GREEN}✔  {msg}{RESET}")


def warn(msg):
    print(f"{YELLOW}⚠  {msg}{RESET}")


def error(msg):
    print(f"{RED}✖  {msg}{RESET}")


def fatal(msg):
    error(msg)
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────
# Detección del SO
# ──────────────────────────────────────────────────────────────────

IS_WINDOWS = platform.system() == "Windows"
IS_MAC     = platform.system() == "Darwin"
IS_LINUX   = platform.system() == "Linux"

# En Windows npm/npx se exponen como scripts .cmd, no como .exe directos.
# subprocess.run necesita el nombre con extensión o shell=True.
NPM_CMD = "npm.cmd" if IS_WINDOWS else "npm"
NPX_CMD = "npx.cmd" if IS_WINDOWS else "npx"


def venv_python(venv_dir):
    """Ruta al intérprete Python dentro del venv."""
    if IS_WINDOWS:
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def venv_pip(venv_dir):
    """Ruta a pip dentro del venv."""
    if IS_WINDOWS:
        return venv_dir / "Scripts" / "pip.exe"
    return venv_dir / "bin" / "pip"


# ──────────────────────────────────────────────────────────────────
# Ejecución de subprocesos
# ──────────────────────────────────────────────────────────────────

def _needs_shell(cmd):
    """
    En Windows, los scripts .cmd y .bat requieren shell=True para ejecutarse
    sin indicar la ruta completa a cmd.exe.
    """
    if not IS_WINDOWS:
        return False
    first = str(cmd[0]).lower()
    return first.endswith(".cmd") or first.endswith(".bat")


def run(cmd, cwd=None, env=None, capture=False):
    """Ejecuta un comando; lanza RuntimeError si el proceso falla."""
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=merged_env,
        capture_output=capture,
        text=True,
        shell=_needs_shell(cmd),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Comando fallido: {' '.join(str(c) for c in cmd)}\n"
            f"{result.stderr or ''}"
        )
    return result


def run_pip(pip_exec, args, cwd=None):
    """Ejecuta pip con el ejecutable dado (siempre .exe dentro del venv)."""
    run([str(pip_exec)] + args, cwd=cwd)


# ──────────────────────────────────────────────────────────────────
# Verificación de requisitos del sistema
# ──────────────────────────────────────────────────────────────────

def find_python312():
    """
    Devuelve el ejecutable de Python 3.12 o detiene el script.
    Puede devolver:
      "py -3.12"   → Windows py launcher
      ruta absoluta al intérprete
    """
    # Caso especial Windows: py launcher
    if IS_WINDOWS:
        py_launcher = shutil.which("py")
        if py_launcher:
            r = subprocess.run(
                ["py", "-3.12", "--version"],
                capture_output=True, text=True
            )
            if r.returncode == 0 and "3.12" in (r.stdout + r.stderr):
                version = (r.stdout + r.stderr).strip()
                ok(f"Python 3.12 encontrado vía py launcher: {version}")
                return "py -3.12"

    # Búsqueda genérica en el PATH
    for cand in ["python3.12", "python3", "python"]:
        path = shutil.which(cand)
        if not path:
            continue
        r = subprocess.run([path, "--version"], capture_output=True, text=True)
        version_line = (r.stdout + r.stderr).strip()
        if "3.12" in version_line:
            ok(f"Python 3.12 encontrado: {path} ({version_line})")
            return path

    fatal(textwrap.dedent("""
        Python 3.12 no encontrado en el PATH.

        Por qué 3.12 y no una versión mayor:
          pydantic-core (dependencia de FastAPI) necesita Wheels pre-compilados.
          Para Python 3.13+ esos archivos aún no existen, lo que obliga a
          compilar Rust localmente → falla si no tienes Rust instalado.

        Instala Python 3.12:
          Windows : https://www.python.org/downloads/release/python-3129/
                    Marca "Add Python to PATH" durante la instalación.
          macOS   : brew install python@3.12   (o descarga el instalador)
          Linux   : sudo apt install python3.12  /  sudo dnf install python3.12

        Después de instalarlo, abre una NUEVA terminal y vuelve a ejecutar
        el script.
    """))


def check_git():
    if not shutil.which("git"):
        fatal(textwrap.dedent("""
            git no está instalado o no está en el PATH.
            Instálalo desde: https://git-scm.com/downloads
            En Windows marca "Git from the command line and also from 3rd-party software".
        """))
    r = subprocess.run(["git", "--version"], capture_output=True, text=True)
    ok(f"git encontrado: {r.stdout.strip()}")


def check_node_npm():
    """
    Verifica Node.js y npm.

    NOTA Windows: npm se instala como npm.cmd (script de cmd.exe).
    subprocess no puede ejecutarlo sin shell=True o con el nombre exacto.
    shutil.which() sí lo encuentra si está en el PATH.
    """
    node_path = shutil.which("node")

    # Intentamos ambas formas: "npm.cmd" (Windows) y "npm" (Unix)
    npm_path = shutil.which(NPM_CMD)
    if not npm_path:
        npm_path = shutil.which("npm")   # fallback por si acaso

    missing = []
    if not node_path:
        missing.append("node")
    if not npm_path:
        missing.append("npm")

    if missing:
        fatal(textwrap.dedent(f"""
            No se encontraron en el PATH: {', '.join(missing)}

            El frontend usa Node.js (recomendado >= 18 LTS).
            Instálalo desde : https://nodejs.org/en/download/
            nvm-windows     : https://github.com/coreybutler/nvm-windows/releases

            Después de instalarlo abre una NUEVA terminal y vuelve a
            ejecutar el script para que el PATH se actualice.
        """))

    # Obtener versiones — npm necesita shell=True en Windows
    r_node = subprocess.run(
        ["node", "--version"],
        capture_output=True, text=True
    )
    r_npm = subprocess.run(
        [NPM_CMD, "--version"],
        capture_output=True, text=True,
        shell=IS_WINDOWS,
    )
    ok(f"Node.js {r_node.stdout.strip()}  /  npm {r_npm.stdout.strip()}")


# ──────────────────────────────────────────────────────────────────
# Creación de venv
# ──────────────────────────────────────────────────────────────────

def create_venv(python_exec, venv_dir):
    """Crea un venv en venv_dir usando python_exec."""
    if Path(venv_dir).exists():
        warn(f"El venv ya existe en {venv_dir}. Se omite la creación.")
        return

    # "py -3.12" viene como string con espacio → partir en lista
    if isinstance(python_exec, str) and " " in python_exec:
        parts = python_exec.split() + ["-m", "venv", str(venv_dir)]
    else:
        parts = [python_exec, "-m", "venv", str(venv_dir)]

    print(f"  Creando venv en {venv_dir} ...")
    result = subprocess.run(parts, capture_output=True, text=True)
    if result.returncode != 0:
        fatal(
            f"No se pudo crear el venv:\n{result.stderr}\n"
            "Linux: sudo apt install python3.12-venv"
        )
    ok(f"venv creado: {venv_dir}")


# ──────────────────────────────────────────────────────────────────
# Clonación y configuración de BESSER
# ──────────────────────────────────────────────────────────────────

BESSER_REPO         = "https://github.com/BESSER-PEARL/BESSER.git"
MODELING_AGENT_REPO = "https://github.com/BESSER-PEARL/modeling-agent.git"


def clone_besser(base_dir):
    besser_dir = base_dir / "BESSER"
    if besser_dir.exists():
        warn("La carpeta BESSER ya existe. Se omite la clonación.")
        warn("Si quieres reinstalar, elimina la carpeta manualmente.")
        return besser_dir

    print("  Clonando BESSER (puede tardar un momento)...")
    run(["git", "clone", BESSER_REPO, str(besser_dir)])
    ok("BESSER clonado correctamente")
    return besser_dir


def init_submodules(besser_dir):
    """Inicializa el submódulo del frontend (WME)."""
    print("  Inicializando submódulos (Web Modeling Editor frontend)...")
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=besser_dir)
    ok("Submódulos inicializados")


def create_env_file(besser_dir):
    """Crea el .env del frontend a partir del .env.example."""
    webpack_dir = (
        besser_dir
        / "besser" / "utilities" / "web_modeling_editor"
        / "frontend" / "packages" / "webapp" / "webpack"
    )
    env_example = webpack_dir / ".env.example"
    env_file    = webpack_dir / ".env"

    if env_file.exists():
        warn(f".env ya existe en {webpack_dir}. No se sobreescribe.")
        return

    if env_example.exists():
        shutil.copy(str(env_example), str(env_file))
        ok(f".env creado copiando .env.example en {webpack_dir}")
    else:
        env_content = textwrap.dedent("""\
            # === Deployment URLs (Change for local/prod) ===
            # LOCAL:
            DEPLOYMENT_URL=http://localhost:8080
            BACKEND_URL=http://localhost:9000/besser_api
            UML_BOT_WS_URL=ws://localhost:8765

            # PRODUCTION (descomenta y ajusta):
            # DEPLOYMENT_URL=https://editor.besser-pearl.org
            # BACKEND_URL=https://editor.besser-pearl.org/besser_api
            # UML_BOT_WS_URL=wss://editor.besser-pearl.org/agent

            # GitHub OAuth CLIENT ID ONLY (sin secret)
            GITHUB_CLIENT_ID=your_github_client_id_here
        """)
        webpack_dir.mkdir(parents=True, exist_ok=True)
        env_file.write_text(env_content, encoding="utf-8")
        ok(f".env creado con valores por defecto en {webpack_dir}")

    print(f"\n  {YELLOW}⚠  RECUERDA editar el .env en:{RESET}")
    print(f"     {env_file}")
    print(f"  {YELLOW}   Rellena GITHUB_CLIENT_ID con tu OAuth App de GitHub.{RESET}")


def install_besser_requirements(besser_dir, pip_exec):
    req = besser_dir / "requirements.txt"
    if not req.exists():
        warn("requirements.txt del core BESSER no encontrado. Se omite.")
        return
    print("  Instalando dependencias del BESSER core...")
    run_pip(pip_exec, ["install", "-r", str(req)], cwd=besser_dir)
    ok("Dependencias del BESSER core instaladas")


def install_backend_requirements(besser_dir, pip_exec):
    backend_dir = (
        besser_dir / "besser" / "utilities" / "web_modeling_editor" / "backend"
    )
    req = backend_dir / "requirements.txt"
    if not req.exists():
        warn("requirements.txt del backend no encontrado. Se omite.")
        return
    print("  Instalando dependencias del backend...")
    run_pip(pip_exec, ["install", "-r", str(req)], cwd=backend_dir)
    ok("Dependencias del backend instaladas")


def install_frontend_dependencies(besser_dir):
    frontend_dir = (
        besser_dir / "besser" / "utilities" / "web_modeling_editor" / "frontend"
    )
    if not frontend_dir.exists():
        warn(f"No se encontró el directorio del frontend: {frontend_dir}")
        warn("¿Se inicializaron los submódulos correctamente?")
        return

    print("  Instalando dependencias npm del frontend (puede tardar varios minutos)...")
    run([NPM_CMD, "install"], cwd=frontend_dir)

    print("  Ejecutando npm audit fix (no fatal si hay advertencias)...")
    subprocess.run(
        [NPM_CMD, "audit", "fix", "--force"],
        cwd=str(frontend_dir),
        env=os.environ,
        shell=IS_WINDOWS,
    )
    ok("Dependencias npm del frontend instaladas")


# ──────────────────────────────────────────────────────────────────
# Modeling Agent
# ──────────────────────────────────────────────────────────────────

def clone_modeling_agent(base_dir):
    agent_dir = base_dir / "modeling-agent"
    if agent_dir.exists():
        warn("La carpeta modeling-agent ya existe. Se omite la clonación.")
        return agent_dir

    print("  Clonando modeling-agent...")
    run(["git", "clone", MODELING_AGENT_REPO, str(agent_dir)])
    ok("modeling-agent clonado correctamente")
    return agent_dir


def setup_modeling_agent(agent_dir, python_exec):
    venv_dir = agent_dir / "venv"
    create_venv(python_exec, venv_dir)
    pip_exec = venv_pip(venv_dir)

    req = agent_dir / "requirements.txt"
    if req.exists():
        print("  Instalando dependencias del modeling-agent...")
        try:
            run_pip(pip_exec, ["install", "-r", str(req)], cwd=agent_dir)
        except RuntimeError:
            warn("Fallo con caché. Reintentando con --no-cache-dir...")
            run_pip(
                pip_exec,
                ["install", "--no-cache-dir", "-r", str(req)],
                cwd=agent_dir,
            )
        ok("Dependencias del modeling-agent instaladas")
    else:
        warn("requirements.txt del modeling-agent no encontrado.")

    config_example = agent_dir / "config_example.yaml"
    config_file    = agent_dir / "config.yaml"
    if not config_file.exists():
        if config_example.exists():
            shutil.copy(str(config_example), str(config_file))
            ok("config.yaml creado a partir de config_example.yaml")
        else:
            warn("config_example.yaml no encontrado. Crea config.yaml manualmente.")
    else:
        warn("config.yaml ya existe. No se sobreescribe.")

    if config_file.exists():
        print(f"\n  {YELLOW}⚠  RECUERDA editar config.yaml del modeling-agent:{RESET}")
        print(f"     {config_file}")
        print(f"  {YELLOW}   Rellena las claves de API (OpenAI, Anthropic, etc.).{RESET}")


# ──────────────────────────────────────────────────────────────────
# Resumen final
# ──────────────────────────────────────────────────────────────────

def print_summary(base_dir):
    besser_dir   = base_dir / "BESSER"
    agent_dir    = base_dir / "modeling-agent"
    frontend_dir = (
        besser_dir / "besser" / "utilities" / "web_modeling_editor" / "frontend"
    )
    webpack_dir  = frontend_dir / "packages" / "webapp" / "webpack"

    sep = "\\" if IS_WINDOWS else "/"
    print(f"""
{BOLD}{GREEN}
══════════════════════════════════════════════════════════════════
   ✅  Instalación completada exitosamente
══════════════════════════════════════════════════════════════════
{RESET}
{BOLD}Pasos siguientes (manuales):{RESET}

  1  Edita el archivo .env del frontend:
       {webpack_dir / ".env"}
       -> Rellena GITHUB_CLIENT_ID con tu GitHub OAuth App Client ID
          (Settings -> Developer settings -> OAuth Apps -> New OAuth App)
          Homepage URL  : http://localhost:8080
          Callback URL  : http://localhost:9000/besser_api/github/auth/callback

  2  Edita config.yaml del modeling-agent:
       {agent_dir / "config.yaml"}
       -> Rellena las claves de API del LLM que uses (OpenAI, Anthropic, etc.)

  3  Una vez configurados los archivos, ejecuta el proyecto con:
       python run_besser.py

{BOLD}Estructura creada:{RESET}
  {base_dir}
  |-- BESSER{sep}                   Core BESSER + Backend FastAPI + Frontend
  |   |-- venv{sep}                 Python 3.12 environment para back
  |-- modeling-agent{sep}           Modeling Agent (WebSocket)
  |   |-- venv{sep}                 Python 3.12 environment para el agente
  |-- setup_besser.py           (este script)
  |-- run_besser.py             (script para levantar los servidores)
""")


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main():
    banner()

    base_dir = Path(__file__).resolve().parent
    print(f"  Directorio base de trabajo: {base_dir}\n")

    step("Verificando herramientas del sistema")
    check_git()
    check_node_npm()
    python_exec = find_python312()

    step("Clonando BESSER Core + Web Modeling Editor")
    besser_dir = clone_besser(base_dir)

    step("Inicializando submódulos del frontend")
    init_submodules(besser_dir)

    step("Creando entorno virtual Python para BESSER/Backend")
    besser_venv = besser_dir / "venv"
    create_venv(python_exec, besser_venv)
    pip_exec = venv_pip(besser_venv)

    step("Instalando dependencias Python del BESSER core")
    install_besser_requirements(besser_dir, pip_exec)

    step("Instalando dependencias Python del Backend")
    install_backend_requirements(besser_dir, pip_exec)

    step("Configurando .env del frontend")
    create_env_file(besser_dir)

    step("Instalando dependencias Node.js del frontend")
    install_frontend_dependencies(besser_dir)

    step("Clonando y configurando el Modeling Agent")
    agent_dir = clone_modeling_agent(base_dir)
    setup_modeling_agent(agent_dir, python_exec)

    print_summary(base_dir)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Instalación cancelada por el usuario.{RESET}")
        sys.exit(0)
    except RuntimeError as exc:
        fatal(str(exc))
