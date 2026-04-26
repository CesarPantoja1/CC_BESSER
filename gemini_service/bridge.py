"""
Gemini CLI Bridge — subprocess management for gemini-cli.

Runs each command as ``gemini -p "command" --yolo --skip-trust``.
Output is streamed to a queue and printed to the terminal.
"""

import os
import subprocess
import sys
import shutil
import threading
from pathlib import Path
from queue import Queue, Empty


def find_gemini_binary() -> str:
    """Return the path to the gemini binary."""
    for candidate in ("gemini", "gemini.cmd"):
        path = shutil.which(candidate)
        if path:
            return path
    return "gemini"


def _reader_thread(pipe, queue: Queue, done: threading.Event):
    """Read subprocess stdout and put chunks into the queue."""
    try:
        while not done.is_set():
            data = pipe.read1(4096) if hasattr(pipe, "read1") else pipe.read(1)
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
    """Manages gemini-cli execution.

    Each command is a separate ``gemini -p "..." --yolo --skip-trust``
    invocation.  Output is streamed via the output queue.
    """

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self._gemini_bin = find_gemini_binary()
        self._process = None
        self._output_queue: Queue = Queue()
        self._done = threading.Event()
        self._reader = None
        self._lock = threading.Lock()

        print(f"[GEMINI] Bridge initialized", flush=True)
        print(f"[GEMINI]   binary: {self._gemini_bin}", flush=True)
        print(f"[GEMINI]   workdir: {self.work_dir}", flush=True)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def execute(self, command: str) -> None:
        """Execute a gemini command (headless, auto-approve)."""
        with self._lock:
            self._stop_current()

            env = {**os.environ}
            env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
            env["NO_COLOR"] = "1"
            env.pop("TERM", None)

            cmd = [self._gemini_bin, "-p", command, "--yolo", "--skip-trust"]

            print(f"\n{'='*60}", flush=True)
            print(f"[GEMINI] >>> {command}", flush=True)
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
            print(f"[GEMINI] pid={self._process.pid}\n", flush=True)

    def read_available(self, timeout: float = 0.3) -> str:
        """Read all currently available output."""
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
        """Send text to gemini's stdin (answer a question)."""
        if not self.is_running:
            print("[GEMINI] Cannot send — no process running", flush=True)
            return
        try:
            print(f"[GEMINI-IN] >>> {text}", flush=True)
            self._process.stdin.write((text + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            print(f"[GEMINI] Send error: {exc}", flush=True)

    def _stop_current(self) -> None:
        """Stop the current process."""
        self._done.set()
        proc = self._process
        if proc and proc.poll() is None:
            print("[GEMINI] Stopping process...", flush=True)
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        self._process = None

    def close(self) -> None:
        """Shutdown."""
        self._stop_current()
        print("[GEMINI] Bridge closed", flush=True)
