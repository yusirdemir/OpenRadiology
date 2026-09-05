"""Sidecar lifecycle: bind loopback, publish the handshake, shut down cleanly.

The server never chooses its own port. It binds port 0, lets the operating
system pick, and announces the result as a single JSON line on stdout:

    {"port": 51734, "token": "...", "pid": 4711, "version": "0.4.0"}

The Tauri shell reads that line and injects it into the web view. Nothing else
is printed on stdout, so the handshake is unambiguous. The same values go into
the handshake file so an MCP client can find a running application.

A sidecar that outlives its window is a bug users notice, so the process also
watches its parent: when the shell disappears, the server exits rather than
lingering with several hundred megabytes of decoded volume resident.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import signal
import sys
import threading
from typing import Any, Dict, Optional, Tuple

from .. import __version__
from ..config import load_settings
from ..log import configure
from . import handshake
from .api import AppState, build_router
from .attest import DwellPolicy
from .httpd import Server

log = logging.getLogger("openrad.server")


def create_app(budget_bytes: Optional[int] = None, policy: Optional[DwellPolicy] = None,
               token: Optional[str] = None, host: str = "127.0.0.1", port: int = 0) -> Tuple[AppState, Server]:
    state = AppState(load_settings(), budget_bytes=budget_bytes, policy=policy)
    server = Server(build_router(state), token or secrets.token_urlsafe(32), host=host, port=port)
    return state, server


def _watch_parent(pid: int, stop: threading.Event, publish_handshake: bool) -> None:
    """Exit when the shell that started us goes away.

    This is a hard exit rather than a graceful shutdown: the window is already
    gone, and a sidecar holding several hundred megabytes of decoded volume
    should not linger while threads unwind. The handshake is withdrawn first,
    because a file left behind advertises a port that is about to close and
    would send the next MCP bridge to a dead socket.
    """
    while not stop.wait(1.5):
        try:
            os.kill(pid, 0)
        except OSError:
            log.warning("parent process %s exited; shutting the sidecar down", pid)
            if publish_handshake:
                try:
                    handshake.clear()
                except OSError:  # pragma: no cover - unwritable state directory
                    pass
            os._exit(0)


def serve(host: str = "127.0.0.1", port: int = 0, token: Optional[str] = None,
          budget_bytes: Optional[int] = None, policy: Optional[DwellPolicy] = None,
          parent_pid: Optional[int] = None, publish_handshake: bool = True,
          announce: bool = True, log_level: str = "info") -> int:
    configure(log_level)
    state, server = create_app(budget_bytes=budget_bytes, policy=policy, token=token, host=host, port=port)
    payload: Dict[str, Any] = {"port": server.port, "token": server.token, "pid": os.getpid(),
                               "version": __version__, "url": f"http://{server.host}:{server.port}"}
    if publish_handshake:
        try:
            handshake.write(server.port, server.token, {"version": __version__})
        except OSError as e:  # pragma: no cover - unwritable state directory
            log.warning("could not publish the bridge handshake: %s", e)
    if announce:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    stop = threading.Event()
    if parent_pid:
        threading.Thread(target=_watch_parent, args=(parent_pid, stop, publish_handshake),
                         name="parent-watch", daemon=True).start()

    def shutdown(signum: int, _frame: Any) -> None:
        log.info("signal %s received; stopping", signum)
        stop.set()
        threading.Thread(target=server.stop, daemon=True).start()

    for name in ("SIGINT", "SIGTERM"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, shutdown)
            except ValueError:  # pragma: no cover - not on the main thread
                pass

    log.info("openrad-server %s listening on %s:%s", __version__, server.host, server.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        stop.set()
        state.shutdown()
        if publish_handshake:
            current = handshake.read()
            if current is None or int(current.get("pid", 0)) == os.getpid():
                handshake.clear()
        log.info("openrad-server stopped")
    return 0
