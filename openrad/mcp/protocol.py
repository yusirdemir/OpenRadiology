"""Dependency-free MCP server core (JSON-RPC 2.0 over newline-delimited stdio).

Implements the subset of the Model Context Protocol that tool-using clients
need: ``initialize`` / ``notifications/initialized``, ``ping``, ``tools/list``,
``tools/call``, ``resources/list``, ``resources/templates/list``,
``resources/read``, ``prompts/list``, ``prompts/get``, ``logging/setLevel`` and
outbound ``notifications/message``. Messages are processed one per line; the
transport writes only to the real stdout captured at start, so tool handlers
may print freely while their stdout is redirected.

The class is transport-agnostic: :meth:`handle` takes and returns plain dicts,
which is how the synthetic test suite drives it.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

from .. import __version__
from ..config import load_settings
from ..errors import OpenRadError
from .content import ToolContext, real_stdout
from .prompts import get_prompt, list_prompts
from .resources import Resources
from .sessions import SessionStore
from .tools import Catalogue, server_instructions

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LEVELS = ("debug", "info", "notice", "warning", "error", "critical", "alert", "emergency")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603

Message = Dict[str, Any]


class McpServer:
    def __init__(self, store: SessionStore, repo_default: Path, writer: Optional[Callable[[Message], None]] = None) -> None:
        self.store = store
        self.catalogue = Catalogue(store, repo_default)
        self.resources = Resources(store)
        self.initialized = False
        self.client: Dict[str, Any] = {}
        self.log_level = "info"
        self._writer = writer or (lambda m: None)
        self.notifications: List[Message] = []
        self.context = ToolContext(notify=self.notify, cwd=repo_default)

    # ------------------------------------------------------------ outbound
    def notify(self, level: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
        if LEVELS.index(level if level in LEVELS else "info") < LEVELS.index(self.log_level):
            return
        note = {"jsonrpc": "2.0", "method": "notifications/message",
                "params": {"level": level, "logger": "openrad", "data": {"message": message, **(data or {})}}}
        self.notifications.append(note)
        self._writer(note)

    # ------------------------------------------------------------- inbound
    def handle(self, msg: Message) -> Optional[Message]:
        """Process one JSON-RPC message; return the response (None for notifications)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, INVALID_REQUEST, "invalid JSON-RPC request")
        method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        is_notification = "id" not in msg
        try:
            if method == "initialize":
                result = self._initialize(params)
            elif method == "notifications/initialized":
                self.initialized = True
                return None
            elif method.startswith("notifications/"):
                return None
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.catalogue.list()}
            elif method == "tools/call":
                result = self._call_tool(params)
            elif method == "resources/list":
                result = {"resources": self.resources.list()}
            elif method == "resources/templates/list":
                result = {"resourceTemplates": self.resources.templates()}
            elif method == "resources/read":
                if not params.get("uri"):
                    return _error(rid, INVALID_PARAMS, "uri is required")
                result = self.resources.read(params["uri"])
            elif method == "prompts/list":
                result = {"prompts": list_prompts()}
            elif method == "prompts/get":
                if not params.get("name"):
                    return _error(rid, INVALID_PARAMS, "name is required")
                result = get_prompt(params["name"], params.get("arguments") or {})
            elif method == "logging/setLevel":
                level = params.get("level")
                if level not in LEVELS:
                    return _error(rid, INVALID_PARAMS, f"level must be one of {LEVELS}")
                self.log_level = level
                result = {}
            else:
                return None if is_notification else _error(rid, METHOD_NOT_FOUND, f"method not found: {method}")
        except OpenRadError as e:
            return _error(rid, INVALID_PARAMS, e.message, {"exit_code": e.exit_code})
        except KeyError as e:
            return _error(rid, INVALID_PARAMS, f"unknown item: {e}")
        except Exception as e:  # pragma: no cover - defensive
            return _error(rid, INTERNAL_ERROR, f"{e.__class__.__name__}: {e}")
        return None if is_notification else {"jsonrpc": "2.0", "id": rid, "result": result}

    def _initialize(self, params: Dict[str, Any]) -> Dict[str, Any]:
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        self.client = params.get("clientInfo") or {}
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return {"protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False},
                                 "prompts": {"listChanged": False}, "logging": {}},
                "serverInfo": {"name": "openradiology", "title": "OpenRadiology", "version": __version__},
                "instructions": server_instructions()}

    def _call_tool(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        if not name:
            raise KeyError("tool name")
        result = self.catalogue.call(name, params.get("arguments") or {}, self.context)
        return result.to_wire()

    # ----------------------------------------------------------- transport
    def serve(self, reader: TextIO, writer: TextIO) -> int:
        """Newline-delimited JSON-RPC loop. Returns when the reader is exhausted."""
        def write(m: Message) -> None:
            writer.write(json.dumps(m, ensure_ascii=False, default=str) + "\n")
            writer.flush()
        self._writer = write
        handler = _NotificationHandler(self)
        logging.getLogger("openrad").addHandler(handler)
        try:
            for line in reader:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    write(_error(None, PARSE_ERROR, "parse error"))
                    continue
                messages = msg if isinstance(msg, list) else [msg]
                for m in messages:
                    response = self.handle(m)
                    if response is not None:
                        write(response)
        finally:
            logging.getLogger("openrad").removeHandler(handler)
        return 0


class _NotificationHandler(logging.Handler):
    """Forward engine progress (openrad logger) to the client as MCP logging notifications."""

    def __init__(self, server: McpServer) -> None:
        super().__init__(logging.DEBUG)
        self.server = server

    def emit(self, record: logging.LogRecord) -> None:
        level = {"DEBUG": "debug", "INFO": "info", "WARNING": "warning", "ERROR": "error", "CRITICAL": "critical"}.get(record.levelname, "info")
        try:
            self.server.notify(level, record.getMessage())
        except Exception:  # pragma: no cover
            pass


def _error(rid: Any, code: int, message: str, data: Optional[Dict[str, Any]] = None) -> Message:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def build_server(repo: Optional[Path] = None, cache_dir: Optional[Path] = None) -> McpServer:
    repo = (repo or Path.cwd()).resolve()
    cfg = load_settings()
    cache = cache_dir or cfg.cache_dir or (repo / ".cache" / "create-report")
    cache = cache if cache.is_absolute() else repo / cache
    return McpServer(SessionStore(cache.resolve()), repo)


def serve_stdio(repo: Optional[Path] = None) -> int:
    server = build_server(repo)
    return server.serve(sys.stdin, real_stdout())
