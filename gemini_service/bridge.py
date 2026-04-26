"""
Gemini CLI Bridge — headless execution inside a PTY wrapper.

Uses `gemini-cli`'s built-in `--resume latest` functionality 
to maintain conversational context.
Crucially, it executes the headless command INSIDE `pywinpty`. 
This is because gemini-cli uses `node-pty` internally, which crashes 
with "AttachConsole failed" on Windows if it is run without a real console buffer.
"""

import os
import re
import sys
import shutil
import threading
import time
from pathlib import Path
from queue import Queue, Empty

try:
    from winpty import PtyProcess
except ImportError:
    print("[GEMINI] WARNING: pywinpty not installed. Install with: pip install pywinpty")
    PtyProcess = None

# ---------------------------------------------------------------------------
# ANSI stripping
# ---------------------------------------------------------------------------

ANSI_RE = re.compile(
    r"\x1b"                    
    r"("
    r"\[[0-9;?]*[a-zA-Z]"     
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)"  
    r"|\([0-9A-Za-z]"         
    r"|[=>]"                  
    r"|[\[\]>]\?[0-9;]*[a-z]" 
    r")"
    r"|\x07"                   
    r"|\x08"                   
    , re.IGNORECASE
)

def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes."""
    text = ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "")


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class GeminiBridge:
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self._output_queue: Queue = Queue()
        self._current_pty = None
        self._is_running = False
        self._lock = threading.Lock()
        self._has_session = False

        print(f"[GEMINI] Bridge initialized (Headless + PTY wrapper)", flush=True)

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _enqueue_output(self, pty_proc) -> None:
        """Continuously read from the PTY and put cleaned text into the queue."""
        while True:
            if not pty_proc.isalive():
                break
            try:
                raw = pty_proc.read(4096)
                if raw:
                    clean = strip_ansi(raw)
                    if clean.strip():
                        self._output_queue.put(clean)
            except EOFError:
                break
            except Exception:
                pass
            time.sleep(0.05)
            
        # Try one last read after it dies
        try:
            raw = pty_proc.read(4096)
            if raw:
                clean = strip_ansi(raw)
                if clean.strip():
                    self._output_queue.put(clean)
        except:
            pass

        self._is_running = False
        # Do NOT set self._current_pty = None here. 
        # Leave the reference so _stop_current can explicitly call .close() on it.

    def execute(self, prompt: str, start_new_session: bool = False) -> None:
        """Run a command or input in headless mode wrapped in a PTY."""
        with self._lock:
            self._stop_current()
            
            env = {**os.environ}
            env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
            env["PYTHONIOENCODING"] = "utf-8"

            # Construct the command line
            cmd = "gemini --skip-trust --yolo"
            
            if not start_new_session and self._has_session:
                cmd += " --resume latest"
            
            # Escape quotes and completely remove newlines which break Windows PTY command lines
            safe_prompt = prompt.replace('"', '\\"').replace('\r', ' ').replace('\n', ' ')
            cmd += f' -p "{safe_prompt}"'

            print(f"\n{'='*60}", flush=True)
            print(f"[GEMINI] >>> {prompt[:100]}...", flush=True)
            print(f"[GEMINI] New Session: {start_new_session}", flush=True)
            print(f"{'='*60}\n", flush=True)

            self._has_session = True
            self._is_running = True
            
            if PtyProcess is None:
                print("[GEMINI] ERROR: pywinpty is required on Windows.", flush=True)
                return

            try:
                self._current_pty = PtyProcess.spawn(
                    cmd,
                    cwd=str(self.work_dir),
                    env=env,
                    dimensions=(50, 120),
                )

                threading.Thread(
                    target=self._enqueue_output,
                    args=(self._current_pty,),
                    daemon=True,
                ).start()
            except Exception as e:
                print(f"[GEMINI] ERROR spawning PTY: {e}", flush=True)
                self._is_running = False
                self._current_pty = None

    def send_command(self, command: str, new_session: bool = False) -> None:
        self.execute(command, start_new_session=new_session)

    def send_input(self, text: str) -> None:
        self.execute(text, start_new_session=False)

    def read_available(self, timeout: float = 0.3) -> str:
        chunks = []
        try:
            chunks.append(self._output_queue.get(timeout=timeout))
        except Empty:
            return ""

        while True:
            try:
                chunks.append(self._output_queue.get_nowait())
            except Empty:
                break

        result = "".join(chunks)
        if result.strip():
            print(result, end="", flush=True)
        return result

    def _stop_current(self) -> None:
        if self._current_pty:
            if self._current_pty.isalive():
                print("[GEMINI] Terminating previous PTY process...", flush=True)
                try:
                    self._current_pty.terminate()
                except Exception:
                    pass
            try:
                # Explicitly close the PTY handle to prevent winpty-agent.exe leaks
                # and silent hangs on subsequent spawns.
                self._current_pty.close()
            except AttributeError:
                # Pywinpty might use del or implicit cleanup if close() doesn't exist
                pass
            except Exception:
                pass
            
            self._is_running = False
            self._current_pty = None

    def close(self) -> None:
        self._stop_current()
        print("[GEMINI] Bridge closed", flush=True)
