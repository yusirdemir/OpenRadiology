"""Agent bridge: the display gate and the live transcript.

An MCP client connected through the bridge is driving the same session the
person is looking at, which makes one thing possible that the terminal
workflow cannot do.

``skills/openrad/SKILL.md`` says of the terminal path:

    "Marking a page ``reviewed: true`` without opening it is falsification;
    ``openrad check`` cannot detect it, so you must not do it."

Over the bridge it does not have to be a matter of discipline. ``page_view``
is intercepted here: the request is handed to the application window, which
draws the page and reports back, and only once an attestation covering that
page exists in the ledger is the underlying tool allowed to run and return the
image. If the window is hidden, or nobody is there, the tool fails with an
instruction to ask the person to bring the application forward. An agent
cannot claim to have read a page the person's screen never showed.

The transcript exists for the same reason in the other direction: every tool
call the agent makes is recorded with enough detail for the panel to reproduce
the exact view, so the person can check the agent's work rather than trust it.
"""
from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

DEFAULT_DISPLAY_TIMEOUT = 180.0
_ids = itertools.count(1)


class DisplayRequest:
    """One page the agent wants to look at, waiting for the window to show it."""

    def __init__(self, page_path: str, session_path: str, purpose: str = "") -> None:
        self.id = f"disp-{next(_ids):04d}"
        self.page_path = page_path
        self.session_path = session_path
        self.purpose = purpose
        self.created = time.time()
        self.shown_at: Optional[float] = None
        self.declined: str = ""
        self.event = threading.Event()

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "page_path": self.page_path, "session_path": self.session_path,
                "purpose": self.purpose, "created": self.created, "shown_at": self.shown_at,
                "declined": self.declined}


class DisplayGate:
    """Bridges an agent's request to look at a page and the window that draws it."""

    def __init__(self, timeout: float = DEFAULT_DISPLAY_TIMEOUT) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._open: Dict[str, DisplayRequest] = {}
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    # -- subscription (the application window) -----------------------------
    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def cancel() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return cancel

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._subscribers)

    def pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [r.as_dict() for r in self._open.values() if r.shown_at is None and not r.declined]

    # -- the agent's side --------------------------------------------------
    def require_display(self, page_path: str, session_path: str, purpose: str = "",
                        timeout: Optional[float] = None) -> DisplayRequest:
        """Ask the window to show a page and block until it has, or time out."""
        request = DisplayRequest(page_path, session_path, purpose)
        with self._lock:
            self._open[request.id] = request
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(request.as_dict())
            except Exception:  # pragma: no cover - a dead subscriber must not block the agent
                pass
        request.event.wait(timeout if timeout is not None else self.timeout)
        with self._lock:
            self._open.pop(request.id, None)
        return request

    # -- the window's side -------------------------------------------------
    def shown(self, request_id: str) -> bool:
        with self._lock:
            request = self._open.get(request_id)
        if request is None:
            return False
        request.shown_at = time.time()
        request.event.set()
        return True

    def decline(self, request_id: str, reason: str) -> bool:
        with self._lock:
            request = self._open.get(request_id)
        if request is None:
            return False
        request.declined = reason or "declined"
        request.event.set()
        return True


class Transcript:
    """Everything the agent did, in order, replayable from the panel."""

    def __init__(self, limit: int = 500) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._limit = limit
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[Dict[str, Any]], None]] = []

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def cancel() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return cancel

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
        entry = {"id": f"ev-{next(_ids):05d}", "ts": time.time(), "kind": kind, **fields}
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._limit:
                del self._entries[: len(self._entries) - self._limit]
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(entry)
            except Exception:  # pragma: no cover
                pass
        return entry

    def entries(self, after: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            if after is None:
                return list(self._entries)
            for index, entry in enumerate(self._entries):
                if entry["id"] == after:
                    return list(self._entries[index + 1:])
            return list(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def summarise_call(name: str, arguments: Dict[str, Any]) -> str:
    """One readable line per tool call, for the transcript panel."""
    def short(value: Any, width: int = 42) -> str:
        text = str(value)
        return text if len(text) <= width else "…" + text[-(width - 1):]

    if name == "page_view":
        return f"sayfa açıldı: {Path(str(arguments.get('page', ''))).name}"
    if name.startswith("render_"):
        return f"{name[7:]} render: seri {arguments.get('series', '?')}"
    if name == "measure":
        target = next((f"{k} {arguments[k]}" for k in ("roi", "points", "auto3d", "extent") if arguments.get(k)), "")
        return f"ölçüm: seri {arguments.get('series', '?')} {target}".strip()
    if name.startswith("session_"):
        return f"{name}: {short(arguments.get('session', ''))}"
    return name
