"""One-command client configuration for Claude Desktop, Cursor, Windsurf, Claude Code and generic MCP clients."""
from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import InputError, UsageError

SERVER_KEY = "openradiology"
CLIENTS = ("claude-desktop", "cursor", "windsurf", "claude-code", "generic")


def server_entry(repo: Path, use_entrypoint: bool = False, lang: Optional[str] = None,
                 extra_env: Optional[Dict[str, str]] = None, bridge: bool = False) -> Dict[str, Any]:
    """The mcpServers entry. Absolute interpreter path by default: GUI clients rarely inherit the shell PATH.

    With ``bridge``, the client is pointed at the desktop bridge instead of a
    standalone server. The bridge proxies into a running OpenRadiology window
    when there is one -- sharing its session and putting ``page_view`` behind
    the display gate -- and falls back to this same in-process server when
    there is not, so the entry works either way.
    """
    if bridge:
        command, args = (sys.executable, ["--bridge"]) if getattr(sys, "frozen", False) \
            else (sys.executable, ["-m", "openrad.server", "--bridge"])
        env: Dict[str, str] = {"OPENRAD_DOCS_ROOT": str(repo)}
        if lang:
            env["OPENRAD_LANG"] = lang
        env.update(extra_env or {})
        return {"command": command, "args": args, "env": env}
    if use_entrypoint:
        command, args = "openrad", ["mcp"]
    else:
        command, args = sys.executable, ["-m", "openrad", "mcp"]
    env = {"OPENRAD_DOCS_ROOT": str(repo)}
    if lang:
        env["OPENRAD_LANG"] = lang
    env.update(extra_env or {})
    return {"command": command, "args": args + ["--repo", str(repo)], "env": env}


def client_config_path(client: str, home: Optional[Path] = None) -> Optional[Path]:
    home = home or Path.home()
    system = platform.system()
    if client == "claude-desktop":
        if system == "Darwin":
            return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        if system == "Windows":
            return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
        return home / ".config" / "Claude" / "claude_desktop_config.json"
    if client == "cursor":
        return home / ".cursor" / "mcp.json"
    if client == "windsurf":
        return home / ".codeium" / "windsurf" / "mcp_config.json"
    return None


def snippet(client: str, repo: Path, **kw: Any) -> str:
    entry = server_entry(repo, **kw)
    if client == "claude-code":
        env = " ".join(f"-e {k}={v}" for k, v in entry["env"].items())
        return f"claude mcp add {SERVER_KEY} {env} -- {entry['command']} {' '.join(entry['args'])}"
    return json.dumps({"mcpServers": {SERVER_KEY: entry}}, indent=2)


def merge_into(path: Path, repo: Path, **kw: Any) -> Path:
    """Insert/replace the server entry in a client JSON config, keeping other servers and a .bak copy."""
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            raise InputError(f"{path} is not valid JSON; fix it by hand first ({e})") from e
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    else:
        data = {}
        path.parent.mkdir(parents=True, exist_ok=True)
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise InputError(f"{path}: mcpServers is not an object")
    servers[SERVER_KEY] = server_entry(repo, **kw)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def install(client: str, repo: Path, write: bool, config_path: Optional[Path] = None, **kw: Any) -> List[str]:
    if client not in CLIENTS:
        raise UsageError(f"client must be one of {CLIENTS}")
    lines: List[str] = []
    if client in ("claude-code", "generic") or not write:
        lines.append(snippet(client, repo, **kw))
        target = config_path or client_config_path(client)
        if target and not write:
            lines.append(f"# add to {target} (or re-run with --write)")
        return lines
    target = config_path or client_config_path(client)
    if target is None:
        raise UsageError(f"no known config path for {client}; use --config-path")
    merge_into(target, repo, **kw)
    lines.append(f"wrote {SERVER_KEY} into {target} (backup: {target}.bak). Restart the client.")
    return lines
