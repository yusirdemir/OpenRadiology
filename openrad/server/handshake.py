"""Discovery file that lets an MCP client find a running desktop application.

MCP clients speak stdio, so the desktop application cannot be an MCP server
directly. Instead a thin stdio shim (``openrad.server.bridge``) is launched by
the client, reads this file, and proxies JSON-RPC to the running application.
When no application is running the shim falls back to the ordinary in-process
server, so a configured client never breaks.

The file holds the loopback port and the session token, so it is a capability:
written with owner-only permissions, refreshed on every launch, and removed on
exit. A stale file (dead pid) is ignored and overwritten.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

HANDSHAKE_NAME = "bridge.json"


def state_dir() -> Path:
    """Per-user application directory, following each platform's convention."""
    override = os.environ.get("OPENRAD_STATE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OpenRadiology"
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "OpenRadiology"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "openradiology"


def handshake_path() -> Path:
    return state_dir() / HANDSHAKE_NAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write(port: int, token: str, extra: Optional[Dict[str, Any]] = None) -> Path:
    """Publish the handshake atomically with owner-only permissions."""
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "pid": os.getpid(), "port": int(port), "token": token,
               "url": f"http://127.0.0.1:{int(port)}"}
    payload.update(extra or {})
    target = handshake_path()
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".bridge-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def read() -> Optional[Dict[str, Any]]:
    """Return a live handshake, or ``None`` when absent, malformed or stale."""
    path = handshake_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("port") or not payload.get("token"):
        return None
    if not _pid_alive(int(payload.get("pid", 0) or 0)):
        return None
    return payload


def clear() -> None:
    handshake_path().unlink(missing_ok=True)
