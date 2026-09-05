"""``openrad-server`` entry point.

Two modes, one executable:

``openrad-server``
    Run the desktop sidecar.
``openrad-server --cli <command> ...``
    Behave exactly like the ``openrad`` command line. Background jobs re-enter
    the binary this way, so a packaged build needs no Python interpreter on the
    user's machine and the desktop application runs the very same commands a
    terminal user would.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional, Sequence

from .. import __version__
from .attest import DwellPolicy


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openrad-server",
                                     description="Local sidecar for the OpenRadiology desktop application.")
    parser.add_argument("--version", action="version", version=f"openrad-server {__version__}")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (loopback only by design)")
    parser.add_argument("--port", type=int, default=0, help="0 lets the operating system choose")
    parser.add_argument("--token", default=None, help="bearer token; a fresh random one by default")
    parser.add_argument("--parent-pid", type=int, default=None, help="exit when this process disappears")
    parser.add_argument("--budget-mb", type=int, default=None, help="volume cache budget in MB")
    parser.add_argument("--dwell-ms", type=float, default=DwellPolicy.min_dwell_ms,
                        help="milliseconds an image must stay on screen to count as looked at")
    parser.add_argument("--min-scale", type=float, default=DwellPolicy.min_scale,
                        help="minimum screen pixels per image pixel for an attestation")
    parser.add_argument("--no-handshake", action="store_true", help="do not publish the MCP bridge handshake file")
    parser.add_argument("--quiet", action="store_true", help="do not print the handshake line on stdout")
    parser.add_argument("--log-level", default=os.environ.get("OPENRAD_LOG_LEVEL", "info"))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args: List[str] = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        from ..cli import main as cli_main

        return cli_main(args[1:])
    if args and args[0] == "--bridge":
        from .bridge import main as bridge_main

        return bridge_main(args[1:])
    parsed = build_parser().parse_args(args)
    from .app import serve

    return serve(
        host=parsed.host,
        port=parsed.port,
        token=parsed.token,
        budget_bytes=parsed.budget_mb * 1024 ** 2 if parsed.budget_mb else None,
        policy=DwellPolicy(min_dwell_ms=parsed.dwell_ms, min_scale=parsed.min_scale),
        parent_pid=parsed.parent_pid,
        publish_handshake=not parsed.no_handshake,
        announce=not parsed.quiet,
        log_level=parsed.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
