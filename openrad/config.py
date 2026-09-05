"""Layered configuration for OpenRadiology (12-factor style).

Precedence, highest first:

1. Command-line flags (each command sets only the keys it exposes)
2. Environment variables ``OPENRAD_<KEY>`` (upper-case key, e.g. ``OPENRAD_LANG``)
3. Project config file: ``$OPENRAD_CONFIG`` if set, else ``./.openrad.toml``,
   else ``[tool.openrad]`` in ``./pyproject.toml`` (searched upwards from cwd)
4. User config file: ``$XDG_CONFIG_HOME/openrad/config.toml`` (``~/.config/openrad/config.toml``)
5. Built-in defaults below

Every setting records where its value came from so ``openrad config`` can
explain the effective configuration. Booleans accept ``1/0``, ``true/false``,
``yes/no``. Paths are expanded (``~``) but not resolved; commands resolve
relative paths against the repository root they operate on.

Why these settings exist
------------------------
* ``general.lang``: report language. English is the neutral default for a
  global tool; institutions switch once in ``.openrad.toml``.
* ``paths.*``: where studies live, where scratch evidence goes and where the
  two final documents are written. Institutions keep archives, caches and
  reports on different volumes with different retention rules.
* ``render.*``: the vision-model budget. ``max_side`` is the longest sheet
  edge the target model ingests without down-sampling (see ``VISION_PROFILES``),
  ``grid`` is the default tile layout, ``mip_mm``/``step_mm`` are the
  systematic-read defaults recommended by the literature.
* ``measure.*``: which diameter convention is *reported* by default
  (Fleischner average vs RECIST long axis); the raw axes are always printed.
* ``pet.*``: detection threshold and display scale; EANM uptake window used for
  warnings.
* ``privacy.*``: redaction of identifiers from inventories and logs, and the
  pseudonymisation salt for ``openrad anonymize`` (prefer the environment
  variable, never commit it).
* ``compute.threads``: caps BLAS/OpenMP threads so a review on a shared
  workstation does not starve the PACS client next to it.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from .errors import UsageError
from .locales import DEFAULT_LANGUAGE, available_languages

try:  # Python 3.11+
    import tomllib as _toml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - depends on interpreter
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ModuleNotFoundError:  # pragma: no cover
        _toml = None  # type: ignore[assignment]

# Longest image edge (px) that common vision models accept without resampling.
# Values are the vendor-documented limits at the time of writing; override with
# render.max_side when your deployment differs.
VISION_PROFILES: Dict[str, int] = {"claude": 1568, "gpt": 2048, "gemini": 3072, "local": 1024}

Coercer = Callable[[Any], Any]


def _bool(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    s = str(x).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"not a boolean: {x!r}")


def _path(x: Any) -> Optional[Path]:
    if x in (None, ""):
        return None
    return Path(str(x)).expanduser()


def _str_choice(*choices: str) -> Coercer:
    def coerce(x: Any) -> str:
        s = str(x).strip().lower()
        if s not in choices:
            raise ValueError(f"{x!r} not in {choices}")
        return s
    return coerce


def _int_range(lo: int, hi: int) -> Coercer:
    def coerce(x: Any) -> int:
        v = int(x)
        if not lo <= v <= hi:
            raise ValueError(f"{v} outside [{lo}, {hi}]")
        return v
    return coerce


def _float_pos(x: Any) -> float:
    v = float(x)
    if v <= 0:
        raise ValueError("must be > 0")
    return v


def _pair(x: Any) -> Tuple[float, float]:
    if isinstance(x, str):
        parts = [p for p in x.replace(";", ",").split(",") if p.strip()]
    else:
        parts = list(x)
    if len(parts) != 2:
        raise ValueError("expected two numbers")
    a, b = float(parts[0]), float(parts[1])
    if a >= b:
        raise ValueError("first value must be smaller")
    return a, b


def _windows(x: Any) -> Dict[str, Tuple[float, float]]:
    """TOML table {name = [center, width]} or env 'name:center/width,name2:c/w'."""
    out: Dict[str, Tuple[float, float]] = {}
    if isinstance(x, Mapping):
        items = [(k, v) for k, v in x.items()]
    else:
        items = []
        for chunk in str(x).split(","):
            if not chunk.strip():
                continue
            name, cw = chunk.split(":")
            items.append((name.strip(), cw.split("/")))
    for name, cw in items:
        c, w = float(cw[0]), float(cw[1])
        if w <= 0:
            raise ValueError(f"window {name}: width must be > 0")
        out[str(name).lower()] = (c, w)
    return out


@dataclass(frozen=True)
class Setting:
    key: str          # flat key, also used for env var OPENRAD_<KEY>
    section: str      # TOML table
    default: Any
    coerce: Coercer
    help: str

    @property
    def env(self) -> str:
        return "OPENRAD_" + self.key.upper()


SETTINGS: List[Setting] = [
    Setting("lang", "general", DEFAULT_LANGUAGE, _str_choice(*available_languages()), f"Report language ({', '.join(available_languages())})"),
    Setting("log_level", "general", "info", _str_choice("debug", "info", "warning", "error"), "Diagnostic verbosity on stderr"),
    Setting("studies_root", "paths", None, _path, "Directory holding study folders (default <repo>/DCIM)"),
    Setting("cache_dir", "paths", None, _path, "Scratch/evidence directory (default <repo>/.cache/create-report)"),
    Setting("output_dir", "paths", None, _path, "Where the two final documents go (default <repo>/reports)"),
    Setting("identity_index", "paths", "PATIENT_INDEX.md", str, "File inside studies_root that documents same-patient mapping for anonymized exports"),
    Setting("vision_profile", "render", "claude", _str_choice(*VISION_PROFILES, "custom"), "Target vision model; sets max_side unless overridden"),
    Setting("max_side", "render", None, _int_range(512, 8192), "Longest sheet edge in px (default from vision_profile)"),
    Setting("grid", "render", "2x2", str, "Default tile grid COLSxROWS or 'auto'"),
    Setting("mip_mm", "render", 10.0, float, "Default sliding-slab MIP thickness for lung window (0 = off)"),
    Setting("step_mm", "render", 1.0, _float_pos, "Default axial sampling step in mm"),
    Setting("windows", "render", {}, _windows, "Extra window presets name = [center, width]"),
    Setting("convention", "measure", "fleischner", _str_choice("fleischner", "recist"), "Reported diameter: fleischner (average <10 mm) or recist (long axis)"),
    Setting("region_radius_mm", "measure", 40.0, _float_pos, "Default region-growing radius"),
    Setting("suv_threshold", "pet", 2.5, _float_pos, "Hotspot detection threshold (SUVbw)"),
    Setting("suv_display_max", "pet", 8.0, _float_pos, "SUV scale maximum for MIP/fusion display"),
    Setting("uptake_window_min", "pet", (45.0, 90.0), _pair, "Acceptable injection-to-scan interval (min) for warnings"),
    Setting("redact_identifiers", "privacy", True, _bool, "Drop PatientID/Name/Accession from inventories and logs"),
    Setting("pseudonym_salt", "privacy", None, lambda x: None if x in (None, "") else str(x), "Secret salt for deterministic UID/ID pseudonymisation"),
    Setting("date_shift_days", "privacy", None, lambda x: None if x in (None, "") else int(x), "Fixed date shift for anonymize (default derived from salt)"),
    Setting("threads", "compute", 0, _int_range(0, 256), "BLAS/OpenMP thread cap (0 = library default)"),
]
BY_KEY: Dict[str, Setting] = {s.key: s for s in SETTINGS}


@dataclass
class Settings:
    values: Dict[str, Any] = field(default_factory=dict)
    sources: Dict[str, str] = field(default_factory=dict)
    files: List[Path] = field(default_factory=list)

    def __getattr__(self, name: str) -> Any:
        try:
            return self.values[name]
        except KeyError as e:
            raise AttributeError(name) from e

    @property
    def effective_max_side(self) -> int:
        if self.values.get("max_side"):
            return int(self.values["max_side"])
        return VISION_PROFILES.get(self.values.get("vision_profile", "claude"), 1568)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for s in SETTINGS:
            v = self.values.get(s.key)
            out.setdefault(s.section, {})[s.key] = str(v) if isinstance(v, Path) else v
        out["render"]["effective_max_side"] = self.effective_max_side
        return out

    def explain(self) -> List[Tuple[str, str, Any, str]]:
        return [(s.section, s.key, self.values.get(s.key), self.sources.get(s.key, "default")) for s in SETTINGS]


def _read_toml(path: Path) -> Dict[str, Any]:
    if _toml is None:
        raise UsageError("Reading TOML needs Python 3.11+ or the 'tomli' package (pip install tomli)")
    with path.open("rb") as stream:
        return _toml.load(stream)


def _find_upwards(start: Path, name: str) -> Optional[Path]:
    for d in [start, *start.parents]:
        candidate = d / name
        if candidate.is_file():
            return candidate
    return None


def discover_files(cwd: Optional[Path] = None) -> List[Tuple[Path, str]]:
    """Return (path, kind) pairs in ascending precedence: user file first, project file last."""
    cwd = (cwd or Path.cwd()).resolve()
    found: List[Tuple[Path, str]] = []
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    user = xdg / "openrad" / "config.toml"
    if user.is_file():
        found.append((user, "toml"))
    explicit = os.environ.get("OPENRAD_CONFIG")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise UsageError(f"OPENRAD_CONFIG points to a missing file: {p}")
        found.append((p, "toml"))
        return found
    project = _find_upwards(cwd, ".openrad.toml")
    if project:
        found.append((project, "toml"))
        return found
    pyproject = _find_upwards(cwd, "pyproject.toml")
    if pyproject:
        found.append((pyproject, "pyproject"))
    return found


def _flatten(table: Mapping[str, Any]) -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for section, body in table.items():
        if isinstance(body, Mapping) and section in {s.section for s in SETTINGS}:
            for k, v in body.items():
                flat[k] = v
        elif section in BY_KEY:
            flat[section] = body
    return flat


def load_settings(overrides: Optional[Mapping[str, Any]] = None, cwd: Optional[Path] = None,
                  environ: Optional[Mapping[str, str]] = None) -> Settings:
    """Merge defaults < user file < project file < environment < overrides."""
    env = os.environ if environ is None else environ
    st = Settings()
    for s in SETTINGS:
        st.values[s.key] = s.default
        st.sources[s.key] = "default"
    for path, kind in discover_files(cwd):
        if _toml is None and kind == "pyproject":
            continue  # no parser on this interpreter; pyproject is optional, an explicit file is not
        table = _read_toml(path)
        if kind == "pyproject":
            table = table.get("tool", {}).get("openrad", {})
            if not table:
                continue
        st.files.append(path)
        for k, v in _flatten(table).items():
            if k not in BY_KEY:
                raise UsageError(f"{path}: unknown setting '{k}'")
            _assign(st, k, v, str(path))
    for s in SETTINGS:
        if s.env in env:
            _assign(st, s.key, env[s.env], s.env)
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if k not in BY_KEY:
            raise UsageError(f"unknown setting '{k}'")
        _assign(st, k, v, "cli")
    if st.values.get("max_side") is None and st.values.get("vision_profile") == "custom":
        raise UsageError("render.vision_profile = custom requires render.max_side")
    return st


def _assign(st: Settings, key: str, raw: Any, source: str) -> None:
    s = BY_KEY[key]
    try:
        st.values[key] = s.coerce(raw)
    except (TypeError, ValueError) as e:
        raise UsageError(f"invalid value for {key} from {source}: {e}") from e
    st.sources[key] = source


def apply_compute_settings(st: Settings, environ: Optional[Dict[str, str]] = None) -> None:
    """Cap BLAS/OpenMP threads. Must run before numpy is imported to take effect."""
    env = os.environ if environ is None else environ
    n = int(st.values.get("threads") or 0)
    if n > 0:
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            env.setdefault(var, str(n))


TEMPLATE = '''# OpenRadiology configuration (.openrad.toml). Precedence: CLI > OPENRAD_* env > this file > defaults.
[general]
lang = "en"            # any locale in openrad/locales (en, tr, ...)
log_level = "info"

[paths]
# studies_root = "DCIM"
# cache_dir = ".cache/create-report"
# output_dir = "reports"
# identity_index = "PATIENT_INDEX.md"

[render]
vision_profile = "claude"   # claude (1568) | gpt (2048) | gemini (3072) | local (1024) | custom
# max_side = 1568
grid = "2x2"                # COLSxROWS or auto
mip_mm = 10.0
step_mm = 1.0
# [render.windows]
# pe = [100, 700]

[measure]
convention = "fleischner"   # fleischner | recist
region_radius_mm = 40.0

[pet]
suv_threshold = 2.5
suv_display_max = 8.0
uptake_window_min = [45, 90]

[privacy]
redact_identifiers = true
# pseudonym_salt: set OPENRAD_PSEUDONYM_SALT in the environment instead of committing it
# date_shift_days = 123

[compute]
threads = 0
'''


def write_template(path: Path, force: bool = False) -> Path:
    if path.exists() and not force:
        raise UsageError(f"{path} exists; use --force to overwrite")
    path.write_text(TEMPLATE, encoding="utf-8")
    return path


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="openrad config", description="Show or initialise the effective configuration.")
    ap.add_argument("--json", action="store_true", help="print the effective settings as JSON")
    ap.add_argument("--init", type=Path, nargs="?", const=Path(".openrad.toml"), help="write a commented template (default .openrad.toml)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing template")
    a = ap.parse_args(argv)
    if a.init is not None:
        print(write_template(a.init, a.force))
        return 0
    st = load_settings()
    if a.json:
        print(json.dumps({"settings": st.as_dict(), "sources": st.sources, "files": [str(p) for p in st.files]}, indent=2))
        return 0
    print(f"config files: {', '.join(str(p) for p in st.files) or '(none)'}")
    print(f"{'section':<9} {'key':<20} {'value':<32} source")
    for section, key, value, source in st.explain():
        shown = "" if value is None else (str(value) if not isinstance(value, dict) else json.dumps(value))
        print(f"{section:<9} {key:<20} {shown[:32]:<32} {source}")
    print(f"render    effective_max_side   {st.effective_max_side:<32} {'max_side' if st.values.get('max_side') else 'vision_profile'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
