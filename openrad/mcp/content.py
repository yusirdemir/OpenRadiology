"""MCP content-block helpers and the tool execution context."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Block = Dict[str, Any]
Notifier = Callable[[str, str, Optional[Dict[str, Any]]], None]

MAX_INLINE_IMAGES = 6
MAX_INLINE_BYTES = 6 * 1024 * 1024  # total base64 payload per tool result


def text(value: str) -> Block:
    return {"type": "text", "text": value}


def json_block(value: Any) -> Block:
    return {"type": "text", "text": json.dumps(value, indent=2, ensure_ascii=False, default=str)}


def image_png(path: Path) -> Block:
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return {"type": "image", "data": data, "mimeType": "image/png"}


def resource_link(uri: str, name: str, description: str = "", mime: str = "image/png") -> Block:
    block: Block = {"type": "resource_link", "uri": uri, "name": name, "mimeType": mime}
    if description:
        block["description"] = description
    return block


def inline_images(paths: Sequence[Path], limit: int) -> Tuple[List[Block], List[Path]]:
    """Return image blocks for up to ``limit`` files within the byte budget, plus the files left out."""
    blocks: List[Block] = []
    budget = MAX_INLINE_BYTES
    shown: List[Path] = []
    for p in paths[:max(0, min(limit, MAX_INLINE_IMAGES))]:
        size = Path(p).stat().st_size * 4 // 3
        if size > budget:
            break
        blocks.append(image_png(Path(p)))
        budget -= size
        shown.append(Path(p))
    rest = [p for p in paths if Path(p) not in shown]
    return blocks, rest


@dataclass
class ToolResult:
    content: List[Block] = field(default_factory=list)
    structured: Optional[Dict[str, Any]] = None
    is_error: bool = False

    def to_wire(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"content": self.content}
        if self.structured is not None:
            out["structuredContent"] = self.structured
        if self.is_error:
            out["isError"] = True
        return out


def error_result(message: str, **extra: Any) -> ToolResult:
    return ToolResult([text(message)], {"error": message, **extra}, True)


@dataclass
class ToolContext:
    """What a tool handler may use besides its arguments."""

    notify: Notifier
    cwd: Path

    def run_cli(self, argv: Sequence[str]) -> Tuple[int, str, str]:
        """Run an ``openrad`` command in-process with stdout/stderr captured (stdout is the MCP wire)."""
        from ..cli import main as cli_main

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli_main(list(argv))
            except SystemExit as e:  # argparse
                rc = int(e.code or 0) if isinstance(e.code, int) or e.code is None else 2
        if err.getvalue().strip():
            self.notify("info" if rc == 0 else "error", err.getvalue().strip()[-4000:], {"argv": list(argv)})
        return rc, out.getvalue(), err.getvalue()

    def run_json(self, argv: Sequence[str]) -> Tuple[int, Any, str]:
        rc, out, err = self.run_cli(argv)
        try:
            payload = json.loads(out) if out.strip() else None
        except json.JSONDecodeError:
            payload = None
        return rc, payload, err


def real_stdout() -> Any:
    """The process stdout captured before any redirection (the transport)."""
    return sys.__stdout__
