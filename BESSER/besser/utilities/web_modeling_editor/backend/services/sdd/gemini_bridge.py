"""
Gemini CLI Bridge — manages gemini-cli execution for SDD operations.

Uses ``subprocess.Popen`` + ``threading`` for Windows compatibility.
Each command runs as a separate ``gemini -p "command"`` invocation
because gemini-cli needs a TTY for interactive mode, which pipes
don't provide.

All gemini output is logged to the backend terminal AND forwarded
to the WebSocket client via the output queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from queue import Queue, Empty
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


def _find_gemini_binary() -> str:
    """Return the path to the ``gemini`` binary."""
    import shutil
    for candidate in ("gemini", "gemini.cmd"):
        path = shutil.which(candidate)
        if path:
            return path
    return "gemini"


def _reader_thread(pipe, queue: Queue, done: threading.Event):
    """Read pipe in background, put decoded text chunks into queue.
    Also prints to the backend terminal for debugging.
    """
    try:
        while not done.is_set():
            data = pipe.read1(4096) if hasattr(pipe, 'read1') else pipe.read(1)
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            print(text, end="", flush=True)
            queue.put(text)
    except Exception as exc:
        print(f"\n[GEMINI] Reader ended: {exc}", flush=True)
    finally:
        queue.put(None)  # EOF sentinel


class GeminiBridge:
    """Manages gemini-cli subprocess execution for SDD.

    Each command is executed as a **separate** ``gemini -p "command"``
    invocation.  This is more reliable than trying to maintain a
    long-lived interactive session via pipes (gemini-cli needs a TTY
    for interactive mode).

    The active subprocess is tracked so the WebSocket can stream its
    output and the user can see what's happening.
    """

    def __init__(self, work_dir: Path, timeout: float = 600) -> None:
        self.work_dir = work_dir
        self.timeout = timeout
        self._gemini_bin = _find_gemini_binary()
        self._process: Optional[subprocess.Popen] = None
        self._output_queue: Queue = Queue()
        self._done = threading.Event()
        self._reader: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _build_env(self) -> dict:
        """Build environment for gemini-cli."""
        env = {**os.environ}
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        env["NO_COLOR"] = "1"
        # Remove TERM to avoid "dumb terminal" warnings
        env.pop("TERM", None)
        return env

    def execute(self, command: str) -> None:
        """Execute a gemini-cli command in a subprocess.

        The command is run as ``gemini -p "command" --yolo`` so gemini
        processes it headlessly and auto-approves any actions.

        Output is streamed to ``self._output_queue`` which the
        WebSocket handler reads from.
        """
        with self._lock:
            # Kill any existing process
            self._stop_current()

            env = self._build_env()

            # Use -p for headless + --yolo for auto-approve
            cmd = [
                self._gemini_bin,
                "-p", command,
                "--yolo",
            ]

            print(f"\n{'='*60}", flush=True)
            print(f"[GEMINI] Executing: {command}", flush=True)
            print(f"[GEMINI] Working dir: {self.work_dir}", flush=True)
            print(f"{'='*60}\n", flush=True)

            self._done.clear()
            self._output_queue = Queue()

            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self.work_dir),
                env=env,
                shell=(sys.platform == "win32"),
            )

            self._reader = threading.Thread(
                target=_reader_thread,
                args=(self._process.stdout, self._output_queue, self._done),
                daemon=True,
            )
            self._reader.start()
            print(f"[GEMINI] Process started (pid={self._process.pid})\n", flush=True)

    def read_available(self, timeout: float = 0.3) -> str:
        """Read all currently available output (non-blocking)."""
        chunks = []
        try:
            first = self._output_queue.get(timeout=timeout)
            if first is None:
                return ""
            chunks.append(first)
        except Empty:
            return ""

        while True:
            try:
                data = self._output_queue.get_nowait()
                if data is None:
                    break
                chunks.append(data)
            except Empty:
                break

        return "".join(chunks)

    def send_input(self, text: str) -> None:
        """Send text to the running process's stdin.

        This is used for answering questions that gemini asks during
        execution (even in headless mode, some skills prompt for input).
        """
        if not self.is_running:
            print(f"[GEMINI] Cannot send input — no process running", flush=True)
            return

        try:
            print(f"[GEMINI-IN] >>> {text}", flush=True)
            self._process.stdin.write((text + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            print(f"[GEMINI] Input error: {exc}", flush=True)

    def _stop_current(self) -> None:
        """Stop the current process if running."""
        self._done.set()
        proc = self._process
        if proc and proc.poll() is None:
            print("[GEMINI] Stopping current process...", flush=True)
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        self._process = None

    async def close(self) -> None:
        """Shutdown everything."""
        self._stop_current()
        print("[GEMINI] Bridge closed", flush=True)

    # Alias for backward compatibility
    def get_session(self):
        """Returns self — for compatibility with the router."""
        return self
    
    @property
    def is_alive(self):
        return self.is_running
