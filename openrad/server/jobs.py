"""Background jobs for long engine commands, streamed over server-sent events.

Renders and de-identification runs take seconds to minutes and must be
cancellable, so they are executed as **child processes** rather than threads:

* a wedged or crashing decoder cannot take the server down with it,
* ``terminate()`` actually stops the work instead of merely setting a flag,
* the child is the ordinary CLI, so the desktop application and the terminal
  run byte-identical commands and produce byte-identical evidence.

The child is this same executable re-entered with ``--cli``, which keeps the
frozen (PyInstaller) build working without needing a Python interpreter on the
user's machine. Progress arrives as the engine's own stderr lines -- see
``openrad.log.progress`` -- and is relayed verbatim.
"""
from __future__ import annotations

import itertools
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Optional, Sequence, Tuple

log = logging.getLogger("openrad.server.jobs")

TERMINAL = ("done", "error", "cancelled")
_counter = itertools.count(1)


def cli_command(args: Sequence[str]) -> List[str]:
    """Argv that re-enters this executable as the OpenRadiology CLI."""
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return [sys.executable, "--cli", *args]
    return [sys.executable, "-m", "openrad.server", "--cli", *args]


class Job:
    def __init__(self, kind: str, args: Sequence[str], cwd: Optional[Path] = None,
                 label: str = "", meta: Optional[Dict[str, Any]] = None) -> None:
        self.id = f"job-{next(_counter):04d}"
        self.kind = kind
        self.args = list(args)
        self.cwd = Path(cwd) if cwd else None
        self.label = label or kind
        self.meta: Dict[str, Any] = meta or {}
        self.status = "queued"
        self.created = time.time()
        self.started: Optional[float] = None
        self.finished: Optional[float] = None
        self.exit_code: Optional[int] = None
        self.error: str = ""
        self.stdout: List[str] = []
        self.events: Deque[Tuple[int, str, Dict[str, Any]]] = deque(maxlen=4000)
        self._seq = itertools.count()
        self._process: Optional[subprocess.Popen] = None
        self._condition = threading.Condition()

    # -- state -------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "created": self.created, "started": self.started, "finished": self.finished,
                "exit_code": self.exit_code, "error": self.error, "meta": self.meta,
                "command": ["openrad", *self.args]}

    def emit(self, name: str, payload: Dict[str, Any]) -> None:
        with self._condition:
            self.events.append((next(self._seq), name, payload))
            self._condition.notify_all()

    def follow(self, timeout: float = 900.0) -> Iterator[Tuple[str, Dict[str, Any]]]:
        """Replay everything recorded so far, then follow until the job ends."""
        cursor = -1
        deadline = time.time() + timeout
        while True:
            with self._condition:
                pending = [e for e in self.events if e[0] > cursor]
                if not pending:
                    if self.status in TERMINAL:
                        return
                    if time.time() > deadline:
                        return
                    self._condition.wait(timeout=1.0)
                    continue
            for seq, name, payload in pending:
                cursor = seq
                yield name, payload
            if self.status in TERMINAL and not [e for e in self.events if e[0] > cursor]:
                return

    # -- execution ---------------------------------------------------------
    def run(self) -> None:
        self.status = "running"
        self.started = time.time()
        self.emit("status", self.snapshot())
        env = dict(os.environ, PYTHONUNBUFFERED="1", OPENRAD_LOG_LEVEL="info")
        try:
            self._process = subprocess.Popen(
                cli_command(self.args), cwd=str(self.cwd) if self.cwd else None, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except OSError as e:  # pragma: no cover - depends on the platform
            self.status, self.error, self.finished = "error", f"Cannot start job: {e}", time.time()
            self.emit("error", {"message": self.error})
            self.emit("status", self.snapshot())
            return

        reader = threading.Thread(target=self._pump_stderr, name=f"{self.id}-stderr", daemon=True)
        reader.start()
        stdout, _ = self._process.communicate()
        reader.join(timeout=2)
        self.stdout = (stdout or "").splitlines()
        self.exit_code = self._process.returncode
        self.finished = time.time()
        if self.status == "cancelled":
            self.emit("status", self.snapshot())
            return
        if self.exit_code == 0:
            self.status = "done"
            self.emit("done", {"exit_code": 0, "stdout": self.stdout, "meta": self.meta})
        else:
            self.status = "error"
            self.error = self.error or f"Command failed with exit code {self.exit_code}"
            self.emit("error", {"exit_code": self.exit_code, "message": self.error})
        self.emit("status", self.snapshot())

    def _pump_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for raw in self._process.stderr:
            line = raw.rstrip("\n")
            if not line:
                continue
            if " error:" in line or line.startswith("Traceback"):
                self.error = line
            self.emit("log", {"line": line})

    def cancel(self) -> bool:
        if self.status in TERMINAL:
            return False
        self.status = "cancelled"
        self.error = "Cancelled by the user"
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
        self.emit("cancelled", {"message": self.error})
        return True


class JobManager:
    """Bounded pool of concurrently running jobs, with a retained history."""

    def __init__(self, max_parallel: int = 2, history: int = 100) -> None:
        self._jobs: Dict[str, Job] = {}
        self._order: Deque[str] = deque(maxlen=history)
        self._semaphore = threading.BoundedSemaphore(max_parallel)
        self._lock = threading.Lock()

    def submit(self, kind: str, args: Sequence[str], cwd: Optional[Path] = None,
               label: str = "", meta: Optional[Dict[str, Any]] = None) -> Job:
        job = Job(kind, args, cwd=cwd, label=label, meta=meta)
        with self._lock:
            self._jobs[job.id] = job
            if len(self._order) == self._order.maxlen:
                self._jobs.pop(self._order[0], None)
            self._order.append(job.id)

        def worker() -> None:
            with self._semaphore:
                if job.status == "cancelled":
                    return
                job.run()

        threading.Thread(target=worker, name=job.id, daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = [self._jobs[i] for i in reversed(self._order) if i in self._jobs]
        return [j.snapshot() for j in jobs]

    def shutdown(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
