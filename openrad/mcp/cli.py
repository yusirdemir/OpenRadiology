"""``openrad mcp``: run the MCP server or configure a client.

    openrad mcp                              # serve over stdio (what clients launch)
    openrad mcp --repo /path/to/workspace    # session cache and reports live here
    openrad mcp --print [--client cursor]    # show the client configuration snippet
    openrad mcp --install claude-desktop --write   # merge into the client's config file
    openrad mcp --list-tools [--json]        # catalogue for documentation or debugging
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .install import CLIENTS, install
from .protocol import build_server, serve_stdio


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openrad mcp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=None, help="review workspace root (default: current directory)")
    ap.add_argument("--print", action="store_true", help="print the client configuration snippet and exit")
    ap.add_argument("--install", choices=CLIENTS, help="target client for the snippet / --write")
    ap.add_argument("--client", choices=CLIENTS, default="claude-desktop", help="client for --print (default claude-desktop)")
    ap.add_argument("--write", action="store_true", help="merge the entry into the client's config file (creates a .bak)")
    ap.add_argument("--config-path", type=Path, help="explicit client config file for --write")
    ap.add_argument("--entrypoint", action="store_true", help="use the 'openrad' executable instead of the absolute Python interpreter")
    ap.add_argument("--lang", help="OPENRAD_LANG to embed in the client entry")
    ap.add_argument("--list-tools", action="store_true", help="print the tool catalogue")
    ap.add_argument("--json", action="store_true")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    repo = (a.repo or Path.cwd()).resolve()
    if a.list_tools:
        tools = build_server(repo).catalogue.list()
        if a.json:
            print(json.dumps(tools, indent=2))
        else:
            for t in tools:
                print(f"{t['name']:<22} {t['description'].splitlines()[0]}")
        return 0
    if a.print or a.install:
        client = a.install or a.client
        for line in install(client, repo, a.write, a.config_path, use_entrypoint=a.entrypoint, lang=a.lang):
            print(line)
        return 0
    return serve_stdio(repo)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
