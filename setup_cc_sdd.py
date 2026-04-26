#!/usr/bin/env python3
"""
Diagnóstico rápido para CC-SDD en Windows.

Valida:
- Presencia de Gemini CLI
- Estructura de carpetas críticas
- Permisos de escritura sobre sdd-workspace
- ACL actual de la carpeta de trabajo (icacls)

Uso:
    py .\\setup_cc_sdd.py
    py .\\setup_cc_sdd.py --root F:\\PRESENTABLE
    py .\\setup_cc_sdd.py --fix-acl
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str


def run_command(command: List[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        shell=False,
    )


def check_python() -> CheckResult:
    version = sys.version.split()[0]
    return CheckResult("Python", "PASS", f"Python {version}")


def check_node() -> CheckResult:
    node_bin = shutil.which("node")
    npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
    if not node_bin or not npm_bin:
        return CheckResult("Node/NPM", "WARN", "Node o npm no están en PATH")

    node_v = run_command([node_bin, "--version"])
    npm_v = run_command([npm_bin, "--version"])
    if node_v.returncode == 0 and npm_v.returncode == 0:
        return CheckResult("Node/NPM", "PASS", f"node={node_v.stdout.strip()} npm={npm_v.stdout.strip()}")

    return CheckResult("Node/NPM", "WARN", "No se pudo leer versión de node/npm")


def check_gemini() -> CheckResult:
    gemini_bin = shutil.which("gemini") or shutil.which("gemini.cmd")
    if not gemini_bin:
        return CheckResult("Gemini CLI", "FAIL", "No se encontró `gemini` en PATH")

    result = run_command([gemini_bin, "--help"])
    if result.returncode != 0:
        return CheckResult("Gemini CLI", "FAIL", "`gemini --help` falló")

    first_line = result.stdout.splitlines()[0].strip() if result.stdout else "gemini --help OK"
    return CheckResult("Gemini CLI", "PASS", first_line)


def check_paths(root: Path) -> List[CheckResult]:
    paths = {
        "Raíz proyecto": root,
        "BESSER": root / "BESSER",
        "modeling-agent": root / "modeling-agent",
        "sdd-workspace": root / "sdd-workspace",
        "gemini_service": root / "gemini_service",
    }

    results: List[CheckResult] = []
    for name, path in paths.items():
        if path.exists():
            results.append(CheckResult(name, "PASS", str(path)))
        else:
            results.append(CheckResult(name, "FAIL", f"No existe: {path}"))
    return results


def check_workspace_writable(work_dir: Path) -> CheckResult:
    if not work_dir.exists():
        return CheckResult("Escritura sdd-workspace", "FAIL", f"No existe: {work_dir}")

    test_file = work_dir / "write_test_cc_sdd.tmp"
    try:
        test_file.write_text("ok", encoding="utf-8")
        exists = test_file.exists()
        if exists:
            test_file.unlink(missing_ok=True)
            return CheckResult("Escritura sdd-workspace", "PASS", "Se pudo crear y borrar archivo de prueba")
        return CheckResult("Escritura sdd-workspace", "FAIL", "No se logró verificar escritura")
    except Exception as exc:
        return CheckResult("Escritura sdd-workspace", "FAIL", f"Error de escritura: {exc}")


def check_icacls(work_dir: Path) -> CheckResult:
    if platform.system().lower() != "windows":
        return CheckResult("ACL carpeta", "WARN", "icacls aplica solo en Windows")

    result = run_command(["icacls", str(work_dir)])
    if result.returncode == 0:
        first = result.stdout.splitlines()[0].strip() if result.stdout else "icacls OK"
        return CheckResult("ACL carpeta", "PASS", first)

    return CheckResult("ACL carpeta", "WARN", "No se pudo leer ACL con icacls")


def fix_acl(work_dir: Path) -> CheckResult:
    if platform.system().lower() != "windows":
        return CheckResult("Fix ACL", "WARN", "Solo soportado en Windows")

    username = os.environ.get("USERNAME", "")
    if not username:
        return CheckResult("Fix ACL", "FAIL", "No se encontró USERNAME en entorno")

    grant = f"{username}:(OI)(CI)M"
    result = run_command(["icacls", str(work_dir), "/grant", grant, "/T"])
    if result.returncode == 0:
        return CheckResult("Fix ACL", "PASS", f"Permisos de modificación aplicados a {username}")

    detail = result.stderr.strip() or result.stdout.strip() or "Error desconocido"
    return CheckResult("Fix ACL", "FAIL", detail)


def print_result(item: CheckResult) -> None:
    prefix = {
        "PASS": "[OK]",
        "WARN": "[WARN]",
        "FAIL": "[FAIL]",
    }.get(item.status, "[INFO]")
    print(f"{prefix} {item.name}: {item.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico de configuración para CC-SDD")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Ruta raíz del proyecto (por defecto: carpeta del script)",
    )
    parser.add_argument(
        "--fix-acl",
        action="store_true",
        help="Intenta otorgar permisos de modificación al usuario actual sobre sdd-workspace",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    work_dir = root / "sdd-workspace"

    print("=" * 62)
    print("  Diagnóstico CC-SDD")
    print(f"  Root: {root}")
    print(f"  Work dir: {work_dir}")
    print("=" * 62)

    results: List[CheckResult] = []
    results.append(check_python())
    results.append(check_node())
    results.append(check_gemini())
    results.extend(check_paths(root))
    results.append(check_workspace_writable(work_dir))
    results.append(check_icacls(work_dir))

    if args.fix_acl:
        results.append(fix_acl(work_dir))
        results.append(check_workspace_writable(work_dir))

    for item in results:
        print_result(item)

    fail_count = sum(1 for item in results if item.status == "FAIL")
    warn_count = sum(1 for item in results if item.status == "WARN")

    print("-" * 62)
    print(f"Resumen: FAIL={fail_count} WARN={warn_count} TOTAL={len(results)}")

    if fail_count:
        print("Acción recomendada: corrige los FAIL y vuelve a ejecutar el script.")
        return 1

    print("Estado general: OK para ejecutar CC-SDD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
