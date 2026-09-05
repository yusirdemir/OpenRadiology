"""Stdio shim that connects an MCP client to a running desktop application.

MCP clients speak stdio, so the application -- which is an HTTP server on
loopback -- cannot be an MCP server directly. The client launches this shim
instead. It reads the handshake file the application publishes, proxies each
JSON-RPC message to ``POST /mcp``, and writes the responses back on stdout.

Two properties make the detour worth it:

* the agent shares the application's decoded volumes and its open session, so
  nothing is read twice and every tool call appears in the window as it happens;
* ``page_view`` passes through the display gate, so an agent cannot mark a page
  reviewed unless the person's screen actually showed it.

When no application is running the shim falls back to the ordinary in-process
server, so a configured client never breaks -- it simply loses the gate, which
is exactly the terminal behaviour it would have had anyway.

    openrad-server --bridge            # what an MCP client is configured to run
    openrad mcp                        # the plain in-process server
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, TextIO

from . import handshake

CONNECT_TIMEOUT = 5.0
CALL_TIMEOUT = 600.0  # a render or a display gate can legitimately take minutes


class BridgeUnavailable(Exception):
    """No application is listening, or it stopped answering."""


class Bridge:
    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    @classmethod
    def discover(cls) -> Optional[Bridge]:
        payload = handshake.read()
        if not payload:
            return None
        bridge = cls(str(payload.get("url") or f"http://127.0.0.1:{payload['port']}"), str(payload["token"]))
        try:
            bridge.health()
        except BridgeUnavailable:
            return None
        return bridge

    def health(self) -> Dict[str, Any]:
        request = urllib.request.Request(f"{self.url}/health")
        try:
            with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as response:
                return json.loads(response.read() or b"{}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise BridgeUnavailable(str(e)) from e

    def call(self, message: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(f"{self.url}/mcp", data=body, method="POST")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Content-Type", "application/json")
        request.add_header("Origin", "http://127.0.0.1")
        try:
            with urllib.request.urlopen(request, timeout=CALL_TIMEOUT) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise BridgeUnavailable(f"{e.code} {detail}") from e
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise BridgeUnavailable(str(e)) from e


def _pump(bridge: Bridge, reader: TextIO, writer: TextIO) -> int:
    """Newline-delimited JSON-RPC in, the application's answers out."""

    def emit(message: Dict[str, Any]) -> None:
        writer.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        writer.flush()

    for line in reader:
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        for message in parsed if isinstance(parsed, list) else [parsed]:
            try:
                envelope = bridge.call(message)
            except BridgeUnavailable as e:
                # Losing the window mid-session must not look like a protocol
                # failure: say what happened, in the model's own channel.
                if "id" in message:
                    emit({"jsonrpc": "2.0", "id": message.get("id"),
                          "error": {"code": -32000, "message": f"OpenRadiology window unavailable: {e}"}})
                continue
            for notification in envelope.get("notifications") or []:
                emit(notification)
            response = envelope.get("response")
            if response is not None:
                emit(response)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="openrad-server --bridge",
        description="Connect an MCP client to a running OpenRadiology window, "
                    "falling back to the in-process server when none is running.",
    )
    parser.add_argument("--no-fallback", action="store_true",
                        help="fail instead of starting an in-process server when no window is running")
    parser.add_argument("--status", action="store_true", help="report what the bridge can see and exit")
    args = parser.parse_args(list(argv or []))

    bridge = Bridge.discover()
    if args.status:
        payload = handshake.read()
        print(json.dumps({"window": bool(bridge), "handshake": bool(payload),
                          "handshake_path": str(handshake.handshake_path()),
                          "url": bridge.url if bridge else None}, indent=2))
        return 0

    if bridge is not None:
        print("[bridge] connected to the OpenRadiology window", file=sys.stderr)
        return _pump(bridge, sys.stdin, sys.stdout)

    if args.no_fallback:
        print("[bridge] no OpenRadiology window is running", file=sys.stderr)
        return 3

    print("[bridge] no window running; serving in-process (no display gate)", file=sys.stderr)
    from ..mcp.protocol import serve_stdio

    return serve_stdio()


def install_snippet() -> List[str]:
    """The command an MCP client should be configured with."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--bridge"]
    return [sys.executable, "-m", "openrad.server", "--bridge"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
