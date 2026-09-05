"""Environment self-check for humans and agents.

    openrad doctor [--json]

Reports interpreter, dependency versions, available pixel-data decoders
(compressed transfer syntaxes need a plugin), the effective configuration and
its sources, and writable cache/output locations. Exit 0 when the core stack
works; exit 1 when a required dependency is missing.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .config import load_settings

REQUIRED = ("pydicom", "numpy", "PIL")
OPTIONAL = ("pylibjpeg", "openjpeg", "gdcm", "tomli", "tomllib", "pytest", "ruff")


def _version(module: str) -> Optional[str]:
    try:
        m = importlib.import_module(module)
    except Exception:
        return None
    return str(getattr(m, "__version__", getattr(m, "version", "present")))


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return True
    except OSError:
        return False


def collect() -> Dict[str, Any]:
    st = load_settings()
    deps = {m: _version(m) for m in REQUIRED}
    opt = {m: _version(m) for m in OPTIONAL}
    handlers: List[str] = []
    try:
        from pydicom import config as pdconf
        handlers = [getattr(h, "__name__", str(h)) for h in pdconf.pixel_data_handlers if h.is_available()]
    except Exception:
        pass
    cwd = Path.cwd()
    cache = st.cache_dir or cwd / ".cache" / "create-report"
    out = st.output_dir or cwd / "reports"
    return {
        "openrad": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "dependencies": deps,
        "optional": {k: v for k, v in opt.items() if v},
        "pixel_handlers": handlers,
        "compressed_syntax_support": any(h for h in handlers if "jpeg" in h.lower() or "gdcm" in h.lower() or "rle" in h.lower()),
        "config_files": [str(p) for p in st.files],
        "settings": st.as_dict(),
        "sources": st.sources,
        "env": {k: v for k, v in os.environ.items() if k.startswith("OPENRAD_") and "SALT" not in k},
        "salt_configured": bool(os.environ.get("OPENRAD_PSEUDONYM_SALT") or st.pseudonym_salt),
        "paths": {"cwd": str(cwd), "cache_dir": str(cache), "cache_writable": _writable(Path(cache)),
                  "output_dir": str(out), "output_writable": _writable(Path(out))},
        "ok": all(deps.values()),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="openrad doctor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    info = collect()
    if a.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        print(f"openrad {info['openrad']}  python {info['python']}  {info['platform']}")
        for k, v in info["dependencies"].items():
            print(f"  {k:<10} {v or 'MISSING'}")
        print(f"  optional   {', '.join(f'{k} {v}' for k, v in info['optional'].items()) or '(none)'}")
        print(f"  decoders   {', '.join(info['pixel_handlers']) or '(none)'}  compressed syntaxes: "
              f"{'yes' if info['compressed_syntax_support'] else 'no (pip install openradiology[codecs])'}")
        print(f"  config     {', '.join(info['config_files']) or '(defaults)'}")
        print(f"  lang={info['settings']['general']['lang']} grid={info['settings']['render']['grid']} "
              f"max_side={info['settings']['render']['effective_max_side']} convention={info['settings']['measure']['convention']}")
        print(f"  cache      {info['paths']['cache_dir']} ({'writable' if info['paths']['cache_writable'] else 'NOT writable'})")
        print(f"  output     {info['paths']['output_dir']} ({'writable' if info['paths']['output_writable'] else 'NOT writable'})")
        print(f"  salt       {'configured' if info['salt_configured'] else 'not set (needed only for anonymize)'}")
        print("OK" if info["ok"] else "MISSING DEPENDENCIES")
    return 0 if info["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
