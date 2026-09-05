"""Model Context Protocol (MCP) layer for OpenRadiology.

The server exposes the deterministic engine to any MCP client (Claude Desktop,
Cursor, Windsurf, local model front-ends) over the stdio transport. It has
**no third-party dependency**: ``openrad.mcp.protocol`` implements the small
JSON-RPC 2.0 surface MCP needs (initialize, ping, tools, resources, prompts,
logging), so the same code runs on Python 3.9 and inside restricted clinical
workstations.

Design principles
-----------------
* **Tools are the CLI.** Every tool builds an ``openrad`` argv, runs the same
  code path a human would, and parses the machine-readable output. There is one
  implementation of every algorithm; the protocol never forks behaviour.
* **No look, no claim, enforced by the protocol.** The only way to mark a page
  as reviewed is ``page_view``, which returns the PNG to the model as an image
  content block and marks the page in the same atomic step. A model cannot
  claim coverage it did not receive.
* **Immediate feedback.** Region and claim setters run the full session
  validator and return only the errors that concern the object just written,
  so the model fixes evidence gaps while the image is still in context.
* **Technical alerts, never diagnoses.** ``session_status`` surfaces geometry,
  quantitation and coverage problems (tilt, missing weight, uptake time,
  unrendered slices). It never says anything about anatomy.
* **stdout is the wire.** Tool handlers run with ``stdout`` captured; only the
  protocol writes to the real stream. Progress from the engine is forwarded as
  MCP logging notifications.
"""
from __future__ import annotations

from .protocol import PROTOCOL_VERSION, McpServer, build_server

__all__ = ["PROTOCOL_VERSION", "McpServer", "build_server"]
