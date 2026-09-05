"""Session registry, progress dashboard, technical alerts and the reading plan.

A session is the ``session.json`` created by ``openrad prepare``. The MCP layer
addresses it by its run-directory name (``run-ab12cd``) or by path. Everything
here is bookkeeping on that JSON; interpretation never happens in this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..create_report import REGIONS, load_session, validate, write_json
from ..errors import InputError

SessionDict = Dict[str, Any]


class SessionStore:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def resolve(self, session: str) -> Path:
        p = Path(session).expanduser()
        if p.is_file() and p.name == "session.json":
            return p.resolve()
        if p.is_dir() and (p / "session.json").is_file():
            return (p / "session.json").resolve()
        if self.cache_dir.is_dir():
            for run in sorted(self.cache_dir.iterdir()):
                if run.name == session or run.name.endswith(session):
                    candidate = run / "session.json"
                    if candidate.is_file():
                        return candidate.resolve()
        raise InputError(f"Session '{session}' not found (give the run id, the run directory or the session.json path)")

    def list(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.cache_dir.is_dir():
            return out
        for run in sorted(self.cache_dir.iterdir()):
            p = run / "session.json"
            if p.is_file():
                try:
                    s = load_session(p)
                except Exception:
                    continue
                out.append({"id": run.name, "path": str(p), "created_utc": s.get("created_utc"), "mode": s.get("mode"),
                            "language": s.get("language"), "studies": [st["folder"] for st in s.get("studies", [])],
                            "finalized": bool(s.get("finalized_utc"))})
        return out

    @staticmethod
    def session_id(path: Path) -> str:
        return path.parent.name

    @staticmethod
    def load(path: Path) -> SessionDict:
        return load_session(path)

    @staticmethod
    def save(path: Path, s: SessionDict) -> None:
        write_json(path, s)


# --------------------------------------------------------------------- status
def coverage(s: SessionDict) -> Dict[str, Any]:
    pages = s.get("pages", [])
    reviewed = [p for p in pages if p.get("reviewed") is True]
    per_series: List[Dict[str, Any]] = []
    for study in s["studies"]:
        for se in study["series"]:
            if se.get("disposition") != "read":
                continue
            for purpose in se.get("required_passes", []):
                covered = {src["sop_uid"] for p in reviewed for src in p.get("sources", [])
                           if src.get("study_uid") == study["uid"] and src.get("series_uid") == se["uid"] and src.get("purpose") == purpose}
                rendered = {src["sop_uid"] for p in pages for src in p.get("sources", [])
                            if src.get("study_uid") == study["uid"] and src.get("series_uid") == se["uid"] and src.get("purpose") == purpose}
                total = len(se["sops"])
                per_series.append({"study": study["folder"], "series": se["number"], "pass": purpose, "slices": total,
                                   "rendered": len(rendered & set(se["sops"])), "reviewed": len(covered & set(se["sops"]))})
    regions = [(study["folder"], name, r.get("status")) for study in s["studies"] for name, r in study["regions"].items()]
    pending = [f"{f}:{n}" for f, n, st in regions if st == "pending"]
    return {"pages_total": len(pages), "pages_reviewed": len(reviewed),
            "pages_unreviewed": [p["path"] for p in pages if p.get("reviewed") is not True],
            "passes": per_series, "regions_total": len(regions), "regions_pending": pending,
            "claims": len(s.get("claims", [])), "reading_complete": bool(s.get("reading_complete")),
            "finalized": bool(s.get("finalized_utc"))}


def technical_alerts(s: SessionDict) -> List[Dict[str, str]]:
    """Geometry, quantitation and coverage problems. Never anatomy."""
    alerts: List[Dict[str, str]] = []
    work = Path(s.get("work_dir", ""))
    for study in s["studies"]:
        mods = study.get("modalities", [])
        if "PT" in mods and "CT" not in mods:
            alerts.append({"level": "warning", "code": "pet_without_ct", "text": f"{study['folder']}: PET without a CT series in the same study; fusion impossible"})
        for se in study["series"]:
            if se.get("disposition") == "unsupported":
                alerts.append({"level": "warning", "code": "series_unsupported", "text": f"{study['folder']} S{se['number']}: {se.get('reason') or 'unsupported geometry'}"})
            if se.get("disposition") == "read" and not se.get("required_passes"):
                alerts.append({"level": "error", "code": "passes_missing", "text": f"{study['folder']} S{se['number']}: read series without declared passes"})
    for info_path in sorted(work.rglob("suv_info.json")) if work.is_dir() else []:
        try:
            info = json.loads(info_path.read_text())
        except Exception:
            continue
        for w in info.get("warnings", []):
            alerts.append({"level": "warning", "code": "suv_warning", "text": f"{info_path.parent.name}: {w}"})
        if info.get("error"):
            alerts.append({"level": "error", "code": "suv_unavailable", "text": f"{info_path.parent.name}: {info['error']}"})
        if str(info.get("weight_source", "")).startswith("argument"):
            alerts.append({"level": "info", "code": "weight_external", "text": f"{info_path.parent.name}: SUV uses an externally documented weight; state the source in technique"})
    cov = coverage(s)
    for p in cov["passes"]:
        if p["rendered"] < p["slices"]:
            alerts.append({"level": "error", "code": "unrendered_slices", "text": f"{p['study']} S{p['series']} {p['pass']}: {p['slices'] - p['rendered']} slices not rendered"})
        elif p["reviewed"] < p["slices"]:
            alerts.append({"level": "warning", "code": "unreviewed_slices", "text": f"{p['study']} S{p['series']} {p['pass']}: {p['slices'] - p['reviewed']} slices rendered but not yet viewed"})
    if s.get("mode") == "comparison" and s.get("comparison", {}).get("status") == "pending":
        alerts.append({"level": "info", "code": "comparison_pending", "text": "comparison verdict and plain-language explanation not yet recorded"})
    return alerts


def status(s: SessionDict, session_id: str) -> Dict[str, Any]:
    errors = validate(s, verify_files=False)
    return {"session": session_id, "mode": s.get("mode"), "language": s.get("language"),
            "studies": [{"folder": st["folder"], "date": st["date"], "modalities": st["modalities"],
                         "series": [{"number": se["number"], "modality": se["modality"], "disposition": se["disposition"],
                                     "slices": len(se["sops"]), "required_passes": se.get("required_passes", [])} for se in st["series"]]}
                        for st in s["studies"]],
            "coverage": coverage(s), "alerts": technical_alerts(s),
            "validation_errors": len(errors), "validation_sample": errors[:12]}


# ----------------------------------------------------------------------- plan
def plan(s: SessionDict, session_id: str) -> List[Dict[str, Any]]:
    """Ordered, concrete tool calls for this session's modalities (the SKILL routing as data)."""
    steps: List[Dict[str, Any]] = []
    steps.append({"step": 1, "tool": "study_inventory", "why": "select diagnostic series, note tilt/multiframe/PET timing",
                  "calls": [{"study_dir": st["path"]} for st in s["studies"]]})
    calls: List[Dict[str, Any]] = []
    for st in s["studies"]:
        mods = set(st["modalities"])
        if "CT" in mods and "PT" not in mods:
            calls.append({"tool": "render_ct", "args": {"session": session_id, "study_dir": st["path"], "series": "<thin axial CT>",
                                                        "windows": "lung", "mip": 10, "mpr": True, "grid": "3x3"},
                          "required_passes": ["lung:native", "lung:mip"]})
            calls.append({"tool": "render_ct", "args": {"session": session_id, "study_dir": st["path"], "series": "<soft-tissue CT>",
                                                        "windows": "soft,bone", "mpr": True, "grid": "2x2"},
                          "required_passes": ["soft:native", "bone:native"]})
        if "PT" in mods:
            calls.append({"tool": "render_pet", "args": {"session": session_id, "study_dir": st["path"], "pt": "<PET BQML series>",
                                                         "ct": "<CT series>", "weight": "<kg if header lacks it>"},
                          "required_passes": {"PT": ["pet:native", "pet:fused"], "CT": ["soft:native"]}})
        if "MR" in mods:
            calls.append({"tool": "render_mr", "args": {"session": session_id, "study_dir": st["path"], "step": 1},
                          "required_passes": ["mr:native"]})
    steps.append({"step": 2, "tool": "render_*", "why": "every native slice on a page; declare required_passes exactly as rendered", "calls": calls})
    steps.append({"step": 3, "tool": "session_set_series", "why": "disposition read/excluded/unsupported for every series"})
    steps.append({"step": 4, "tool": "session_register + page_view", "why": "register renders, then view each page; viewing is what marks it reviewed"})
    steps.append({"step": 5, "tool": "zoom + measure", "why": "resolve every candidate on consecutive slices and a second plane; numbers only from measure"})
    steps.append({"step": 6, "tool": "session_set_region / session_add_claim / session_set_meta",
                  "why": "one entry per region with refs and pages; claims with patient explanations; limitations; reading_complete"})
    if s.get("mode") == "comparison":
        steps.append({"step": 6.5, "tool": "session_set_meta", "why": "comparison status/reason/explanation; each claim needs a timeline for every study"})
    steps.append({"step": 7, "tool": "session_check then session_finish", "why": "exit only when the ledger is complete; read both documents end to end"})
    return steps


def region_keys(s: SessionDict) -> Dict[str, List[str]]:
    return {mod: REGIONS[mod] for mod in sorted({m for st in s["studies"] for m in st["modalities"]} & set(REGIONS))}


def errors_for(errors: List[str], *needles: str) -> List[str]:
    keys = [n for n in needles if n]
    return [e for e in errors if any(k in e for k in keys)] if keys else errors


def find_series(s: SessionDict, study_uid: Optional[str], series: str) -> Dict[str, Any]:
    for st in s["studies"]:
        if study_uid and st["uid"] != study_uid:
            continue
        for se in st["series"]:
            if se["uid"] == series or se["number"] == str(series):
                return se
    raise InputError(f"Series '{series}' not found in session" + (f" study {study_uid}" if study_uid else ""))
