"""Evidence ledger: prepare, register, check and finish a review session. Does not interpret images.

    openrad prepare  FOLDER [FOLDER ...] [--repo PATH] [--studies-root DIR] [--identity-source FILE] [--lang en|tr]
    openrad register SESSION --directory DIR      # register rendered PNGs, then actually view them
    openrad check    SESSION [--json]             # structural validation, exit 5 on failure
    openrad finish   SESSION [--lang en|tr] [--output-dir DIR]

The session JSON is the single source of truth. A finding is accepted only if
it cites a registered, reviewed page and a DICOM triple
(StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID) with native row/col
inside the slice matrix. Measurements must point to a write-once tool output
whose SHA-256 still matches. Structural checks do not establish diagnostic
truth; they establish that nothing was asserted without looking.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import Settings, load_settings
from .dcmlib import group_series, read_headers, sha256_file
from .errors import InputError, IntegrityError, UsageError, ValidationError
from .locales import DEFAULT_LANGUAGE, available_languages, load_locale
from .log import progress

SCHEMA_VERSION = 2
LANGUAGES = available_languages()

REGIONS: Dict[str, List[str]] = {
    "CT": ["technique", "airways_stump", "right_lung_all_lobes", "left_lung_all_lobes",
           "right_pleura_fissures_costophrenic", "left_pleura_fissures_costophrenic",
           "mediastinum_hila_nodes", "heart_pericardium_vessels", "chest_wall_retroareolar",
           "bones", "liver_spleen", "right_adrenal", "left_adrenal", "right_kidney",
           "left_kidney", "gastroesophageal_junction", "remaining_upper_abdomen", "lower_neck"],
    "MR": ["technique_sequences_contrast", "supratentorial", "posterior_fossa", "ventricles",
           "dura_leptomeninges", "sella_skull_base", "calvarium_marrow", "orbits_sinuses",
           "vessels", "diffusion_adc", "susceptibility"],
    "PT": ["technique_suv_validity", "head_neck", "thorax_stump_pleura_nodes", "lungs_ct",
           "liver_reference", "blood_pool_reference", "abdomen_pelvis", "skeleton_soft_tissue",
           "physiologic_uptake_and_artifacts", "coverage_outside_ct"],
}

REGION_STATUSES = ("finding", "no_finding", "limited", "not_covered")
CLAIM_PRIORITIES = ("important", "routine", "incidental")
CONFIDENCES = ("low", "moderate", "high")
MEASUREMENT_UNITS = ("mm", "HU", "SUVbw", "mL", "mm2", "mm3")
COMPARISON_STATUSES = ("new", "increased", "decreased", "stable", "resolved", "indeterminate", "not_comparable")
CHANGE_STATUSES = ("new", "increased", "decreased", "stable", "resolved")
TIMEPOINT_STATUSES = ("present", "not_seen", "not_covered", "indeterminate")

digest = sha256_file


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def write_json(path: Path | str, data: Any, exclusive: bool = False) -> None:
    with Path(path).open("x" if exclusive else "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def load_session(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise InputError(f"Session file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ prepare
def resolve_study(studies_root: Path, value: str) -> Path:
    root = studies_root.resolve()
    raw = Path(value)
    path = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    if not path.is_dir():
        # allow "DCIM/<folder>" style relative to the parent of the studies root
        alt = (root.parent / raw).resolve()
        if alt.is_dir():
            path = alt
    if path == root or root not in path.parents or not path.is_dir():
        raise InputError(f"Give an existing study folder inside the studies root {root}")
    return path


def _resolve(repo: Path, value: Optional[Path], default: Path) -> Path:
    if value is None:
        return default.resolve()
    return (value if value.is_absolute() else repo / value).resolve()


def prepare(repo: Path, folders: Sequence[str], identity_source: Optional[Path] = None,
            studies_root: Optional[Path] = None, lang: Optional[str] = None, cache_dir: Optional[Path] = None,
            cfg: Optional[Settings] = None) -> Path:
    cfg = cfg or load_settings()
    repo = repo.resolve()
    lang = lang or cfg.lang
    if lang not in LANGUAGES:
        raise UsageError(f"--lang must be one of {LANGUAGES}")
    if not folders:
        raise UsageError("At least one study folder is required")
    studies_root = _resolve(repo, studies_root or cfg.studies_root, repo / "DCIM")
    paths = [resolve_study(studies_root, f) for f in folders]
    if len(set(paths)) != len(paths):
        raise InputError("The same study was supplied twice")
    studies: List[Dict[str, Any]] = []
    identities = []
    for path in paths:
        headers = read_headers(path)
        if not headers:
            raise InputError(f"No DICOM headers in {path.name}")
        uids = {str(d.get("StudyInstanceUID", "")) for _, d in headers}
        if len(uids) != 1 or "" in uids:
            raise InputError("One StudyInstanceUID per folder is required")
        ids = {(str(d.get("PatientID", "")), str(d.get("PatientName", "")), str(d.get("PatientBirthDate", ""))) for _, d in headers}
        if len(ids) != 1 or not any(next(iter(ids))):
            raise InputError("Missing or inconsistent patient identity; resolve before review")
        identities.append(next(iter(ids)))
        dates = {str(d.get("StudyDate", "")) for _, d in headers}
        if len(dates) != 1:
            raise InputError("Inconsistent study dates")
        date = next(iter(dates))
        try:
            dt.datetime.strptime(date, "%Y%m%d")
        except ValueError as e:
            raise InputError("Valid StudyDate is required; do not infer from folder name") from e
        series: List[Dict[str, Any]] = []
        source_files: List[Dict[str, str]] = []
        for uid, items in group_series(headers).items():
            d = items[0][1]
            mod = str(d.get("Modality", ""))
            sop: Dict[str, Dict[str, Any]] = {}
            for p, ds in items:
                key = str(ds.get("SOPInstanceUID", ""))
                if not key or key in sop:
                    raise InputError("Missing/duplicate SOPInstanceUID")
                sop[key] = {"instance": str(ds.get("InstanceNumber", "")),
                            "rows": int(ds.get("Rows", 0)), "cols": int(ds.get("Columns", 0))}
                source_files.append({"path": str(p), "sha256": digest(p)})
            series.append({"uid": uid, "number": str(d.get("SeriesNumber", "")), "modality": mod,
                           "description": str(d.get("SeriesDescription", "")), "sops": sop,
                           "disposition": "pending", "reason": "", "geometry_checked": False,
                           "required_passes": []})
        mods = sorted({s["modality"] for s in series})
        regions = {f"{mod}:{key}": {"status": "pending", "text": "", "explanation": "", "refs": [], "pages": []}
                   for mod in mods for key in REGIONS.get(mod, [])}
        if not regions:
            raise InputError("Supported workflow: CT, PET/CT, brain MR. Other anatomy needs a dedicated protocol")
        studies.append({"folder": path.name, "path": str(path), "uid": next(iter(uids)),
                        "date": date, "time": str(headers[0][1].get("StudyTime", "")),
                        "modalities": mods, "series": series, "regions": regions, "source_files": source_files})
    identity_evidence = None
    if len(set(identities)) != 1:
        if identity_source is None:
            default_index = studies_root / cfg.identity_index
            if default_index.is_file():
                identity_source = default_index
            else:
                raise InputError("Patient identifiers differ (possibly anonymized exports). "
                                 "Supply a documented --identity-source only after confirming same patient")
        identity_source = identity_source.resolve()
        if not identity_source.is_file():
            raise InputError("Identity evidence file missing")
        identity_text = identity_source.read_text(encoding="utf-8")
        if not all(p.name in identity_text for p in paths):
            raise InputError("Identity evidence must explicitly identify all supplied study folders")
        identity_evidence = {"path": str(identity_source), "sha256": digest(identity_source),
                             "basis": "Documented index confirmed same-patient mapping; differing header identifiers retained as limitation"}
    if len({s["uid"] for s in studies}) != len(studies):
        raise InputError("Two exports of the same study cannot be a longitudinal comparison")
    studies.sort(key=lambda s: (s["date"], s["time"], s["uid"]))
    cache = _resolve(repo, cache_dir or cfg.cache_dir, repo / ".cache" / "create-report")
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="run-", dir=cache))
    package = Path(__file__).resolve().parent
    files = sorted(p for p in package.rglob("*.py") if p.is_file())
    checklists = package.parent / "checklists"
    if checklists.is_dir():
        files += sorted(p for p in checklists.rglob("*.md") if p.is_file())
    session = {"schema": SCHEMA_VERSION, "created_utc": now(), "repo": str(repo), "work_dir": str(work),
               "studies_root": str(studies_root), "language": lang,
               "mode": "comparison" if len(studies) > 1 else "single", "studies": studies,
               "toolchain": [{"path": str(p), "sha256": digest(p)} for p in files],
               "reader": "", "context_disclosure": "", "blind_status": "not_claimed",
               "clinical_context": "", "limitations": [], "recommendations": [], "pages": [], "claims": [],
               "identity_evidence": identity_evidence,
               "comparison": {"status": "pending" if len(studies) > 1 else "not_applicable", "reason": "", "explanation": ""},
               "patient_context": "", "patient_limitations": "", "glossary": [],
               "reading_complete": False,
               "validation_note": "Structural evidence checks do not validate image interpretation or diagnostic sensitivity."}
    write_json(work / "session.json", session, True)
    progress(f"session created for {len(studies)} study(ies), language {lang}, cache {cache}")
    print(work / "session.json")
    return work / "session.json"


# ----------------------------------------------------------------- register
def register(path: Path, directory: Path) -> int:
    session = load_session(path)
    work = Path(session["work_dir"]).resolve()
    directory = directory.resolve()
    if directory != work and work not in directory.parents:
        raise InputError("Render into this run's work directory")
    old = {p["path"]: p for p in session["pages"]}
    for p in sorted(directory.rglob("*.png")):
        name = str(p.resolve())
        h = digest(p)
        index_path = p.parent / "render_index.json"
        entry = json.loads(index_path.read_text()).get(p.name, {}) if index_path.exists() else {}
        sources = entry.get("sources", [])
        if entry and entry.get("sha256") != h:
            raise IntegrityError(f"Render index hash mismatch for {p.name}")
        if name in old and old[name]["sha256"] != h:
            raise IntegrityError("Registered page changed; use a new filename and inspect it")
        first = sources[0] if sources else {}
        old.setdefault(name, {"path": name, "sha256": h, "reviewed": False,
                              "study_uid": first.get("study_uid", ""), "series_uid": first.get("series_uid", ""),
                              "purpose": first.get("purpose", ""), "sources": sources})
    session["pages"] = list(old.values())
    write_json(path, session)
    progress(f"{len(old)} pages registered; none automatically marked reviewed")
    return len(old)


# ----------------------------------------------------------------- validate
def validate(s: Dict[str, Any], verify_files: bool = True) -> List[str]:
    errors: List[str] = []

    def need(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)

    need(s.get("reading_complete") is True, "Reading not complete")
    need(bool(s.get("reader")), "Record model/reader")
    need(bool(s.get("context_disclosure")), "Disclose prior context/leakage, even when none")
    need(s.get("blind_status") in ("not_claimed", "partial", "blinded"), "Invalid blind status")
    need(bool(s.get("limitations")), "Record actual coverage and sensitivity limitations")
    need(bool(s.get("patient_context")), "Explain examination scope in plain language (patient_context)")
    need(bool(s.get("patient_limitations")), "Explain limitations in plain language (patient_limitations)")
    need(s.get("language", DEFAULT_LANGUAGE) in LANGUAGES, "Unsupported report language")
    page_map = {p["path"]: p for p in s["pages"]}
    need(bool(page_map), "No rendered pages registered")
    study_map = {study["uid"]: study for study in s["studies"]}
    series_map = {(study["uid"], se["uid"]): se for study in s["studies"] for se in study["series"]}
    for page in s["pages"]:
        need(page.get("reviewed") is True, f"Unreviewed page: {page['path']}")
        need((page.get("study_uid"), page.get("series_uid")) in series_map, "Page needs valid study and series UID")
        need(bool(page.get("purpose")), "Page purpose missing")

    def refs_ok(refs: List[Dict[str, Any]], owner: str) -> None:
        need(bool(refs), f"{owner}: source references missing")
        for ref in refs:
            series = series_map.get((ref.get("study_uid"), ref.get("series_uid")))
            sop = series.get("sops", {}).get(ref.get("sop_uid")) if series else None
            need(sop is not None, f"{owner}: unknown source SOP/series/study")
            if series:
                need(series.get("disposition") == "read", f"{owner}: referenced series has not been read")
            if sop and "row" in ref and "col" in ref:
                need(isinstance(ref["row"], (int, float)) and isinstance(ref["col"], (int, float)) and
                     0 <= ref["row"] < sop["rows"] and 0 <= ref["col"] < sop["cols"], f"{owner}: coordinates out of range")

    def pages_ok(pages: List[str], owner: str, study_uid: Optional[str] = None) -> None:
        need(bool(pages), f"{owner}: inspected image pages missing")
        for p in pages:
            need(p in page_map, f"{owner}: unregistered page")
            if study_uid and p in page_map:
                need(page_map[p].get("study_uid") == study_uid, f"{owner}: page belongs to another study")

    for study in s["studies"]:
        for se in study["series"]:
            status = se.get("disposition")
            need(status in ("read", "excluded", "unsupported"), f"Series {se['number']}: disposition pending")
            if status == "read":
                need(se.get("geometry_checked") is True, f"Series {se['number']}: read series geometry not checked")
                need(any(p.get("series_uid") == se["uid"] and p.get("study_uid") == study["uid"] for p in s["pages"]),
                     f"Series {se['number']}: read series has no pages")
                if se["modality"] in ("CT", "MR", "PT"):
                    need(bool(se.get("required_passes")), f"Series {se['number']}: read image series needs declared exhaustive passes")
                for purpose in se.get("required_passes", []):
                    covered = {src["sop_uid"] for p in s["pages"] if p.get("reviewed") is True
                               for src in p.get("sources", [])
                               if src.get("study_uid") == study["uid"] and src.get("series_uid") == se["uid"] and src.get("purpose") == purpose}
                    missing = set(se["sops"]) - covered
                    need(not missing, f"Series {se['number']} {purpose}: {len(missing)} unread/unrendered slices")
            else:
                need(bool(se.get("reason")), f"Series {se['number']}: excluded/unsupported series needs reason")
        for name, region in study["regions"].items():
            status = region.get("status")
            owner = f"{study['folder']} {name}"
            need(status in REGION_STATUSES, f"{owner}: unresolved region")
            need(bool(region.get("text")), f"{owner}: explanation missing")
            need(bool(region.get("explanation")), f"{owner}: patient-friendly explanation missing")
            if status in ("finding", "no_finding"):
                refs_ok(region.get("refs", []), owner)
                need(all(r.get("study_uid") == study["uid"] for r in region.get("refs", [])), f"{owner}: wrong study reference")
                pages_ok(region.get("pages", []), owner, study["uid"])
    seen = set()
    for claim in s["claims"]:
        cid = claim.get("id")
        need(bool(cid) and cid not in seen, "Claim IDs must be unique")
        seen.add(cid)
        need(bool(claim.get("text")), f"{cid}: text missing")
        for field in ("meaning", "importance", "uncertainty", "discuss_with_doctor"):
            need(bool(claim.get("patient", {}).get(field)), f"{cid}: patient explanation {field} missing")
        need(claim.get("priority") in CLAIM_PRIORITIES, f"{cid}: priority missing")
        need(claim.get("confidence") in CONFIDENCES, f"{cid}: confidence missing")
        refs_ok(claim.get("refs", []), str(cid))
        pages_ok(claim.get("pages", []), str(cid))
        for measurement in claim.get("measurements", []):
            measured_ref = measurement.get("ref", {})
            refs_ok([measured_ref], f"{cid} measurement")
            need("row" in measured_ref and "col" in measured_ref, f"{cid}: measurement needs native row/column coordinates")
            need(measurement.get("unit") in MEASUREMENT_UNITS, f"{cid}: invalid measurement unit")
            value = measurement.get("value")
            need(isinstance(value, (int, float)) and math.isfinite(value), f"{cid}: invalid measured value")
            need(bool(measurement.get("method")), f"{cid}: measurement method missing")
            log = measurement.get("evidence_file")
            need(bool(log) and Path(log).is_file(), f"{cid}: measurement tool output missing")
            need(bool(measurement.get("sha256")), f"{cid}: measurement output hash missing")
            if verify_files and log and Path(log).is_file():
                need(digest(log) == measurement.get("sha256"), f"{cid}: measurement output changed")
        if s["mode"] == "comparison":
            cmp = claim.get("comparison", {})
            need(cmp.get("status") in COMPARISON_STATUSES, f"{cid}: comparison status missing")
            need(bool(cmp.get("reason")), f"{cid}: comparison rationale missing")
            timeline = claim.get("timeline", [])
            need(len(timeline) == len(study_map) and {t.get("study_uid") for t in timeline} == set(study_map),
                 f"{cid}: timeline must cover every supplied study")
            for point in timeline:
                need(point.get("status") in TIMEPOINT_STATUSES, f"{cid}: invalid timepoint")
                need(bool(point.get("text")) and bool(point.get("explanation")),
                     f"{cid}: timepoint needs professional and plain-language descriptions")
                if point.get("status") in ("present", "not_seen"):
                    refs_ok(point.get("refs", []), f"{cid} timepoint")
                    need(all(r.get("study_uid") == point.get("study_uid") for r in point.get("refs", [])),
                         f"{cid}: timepoint source belongs to a different study")
            if cmp.get("status") in CHANGE_STATUSES:
                from_uid, to_uid = cmp.get("from_study_uid"), cmp.get("to_study_uid")
                need(from_uid in study_map and to_uid in study_map and from_uid != to_uid,
                     f"{cid}: change claim needs explicit distinct comparison endpoints")
                if from_uid in study_map and to_uid in study_map:
                    need(list(study_map).index(from_uid) < list(study_map).index(to_uid), f"{cid}: comparison endpoints reversed")
                need({from_uid, to_uid} <= {r.get("study_uid") for r in claim.get("refs", [])},
                     f"{cid}: change claim needs both endpoint studies' evidence")
    if s["mode"] == "comparison":
        need(s.get("comparison", {}).get("status") in ("compared", "limited", "not_comparable"), "Comparison not completed")
        need(bool(s.get("comparison", {}).get("reason")), "Comparison limitations/method missing")
        need(bool(s.get("comparison", {}).get("explanation")), "Plain-language comparison explanation missing")
    if verify_files:
        tracked = s["toolchain"] + s["pages"] + [f for study in s["studies"] for f in study["source_files"]]
        if s.get("identity_evidence"):
            tracked.append(s["identity_evidence"])
        for item in tracked:
            p = Path(item["path"])
            need(p.is_file() and digest(p) == item["sha256"], f"Missing/changed input: {p.name}")
    return errors


# ------------------------------------------------------------------- finish
def source_text(ref: Dict[str, Any]) -> str:
    return (f"study={ref['study_uid']}; series={ref['series_uid']}; SOP={ref['sop_uid']}"
            + (f"; r={ref['row']}, c={ref['col']}" if "row" in ref and "col" in ref else ""))


def _texts(lang: str) -> Dict[str, Any]:
    return load_locale(lang)["text"]


def _region_names(lang: str) -> Dict[str, str]:
    return load_locale(lang)["regions"]


def date_text(date: str, lang: str = DEFAULT_LANGUAGE) -> str:
    """Format a DICOM DA value with the locale's date format."""
    try:
        return dt.datetime.strptime(date[:8], "%Y%m%d").strftime(_texts(lang)["date_format"])
    except ValueError:
        return date


def render_documents(s: Dict[str, Any], lang: str, report_name: str, guide_name: str, stamp: str) -> tuple[str, str]:
    t = _texts(lang)
    names = _region_names(lang)
    marker = "<!-- generated-by: openrad finish; AI preliminary review -->"
    title = t["title_comparison"] if s["mode"] == "comparison" else t["title_single"]
    lines = [marker, "# " + title, "", "> " + t["banner"],
             f"> {t['review_date']}: {stamp} · {t['reader']}: {s['reader']}",
             f"> {t['context']}: {s['blind_status']} — {s['context_disclosure']}", "",
             f"## {t['h_studies']}", ""]
    for study in s["studies"]:
        lines.append(f"- **{date_text(study['date'], lang)}** {study['time']} — {', '.join(study['modalities'])}; `{study['folder']}`")
    lines.extend(["", s.get("clinical_context") or t["no_clinical"], "", f"## {t['h_technique']}", ""])
    for study in s["studies"]:
        for name, region in study["regions"].items():
            if name.split(":")[-1].startswith("technique"):
                lines.append(f"- {date_text(study['date'], lang)}: {region['text']}")
    lines.append("")
    lines.append(s["comparison"]["reason"] if s["mode"] == "comparison" else t["no_prior"])
    lines.extend(["", f"## {t['h_findings']}", ""])
    for study in s["studies"]:
        lines.extend([f"### {date_text(study['date'], lang)} — {', '.join(study['modalities'])}", ""])
        for name, region in study["regions"].items():
            if name.split(":")[-1].startswith("technique"):
                continue
            label_ = names.get(name.split(":")[-1], name)
            emphasis = "**" if region["status"] in ("finding", "limited") else ""
            lines.append(f"- {label_}: {emphasis}{region['text']}{emphasis}")
        lines.append("")
    lines.extend([f"## {t['h_impression']}", ""])
    for i, claim in enumerate(s["claims"], 1):
        text = claim["text"]
        if claim["priority"] == "important":
            text = "**" + text + "**"
        lines.append(f"{i}. [{claim['id']}] {text}")
        for m in claim.get("measurements", []):
            lines.append(f"   - {t['measurement']}: **{m['value']} {m['unit']}** — {m['method']}")
        if claim.get("comparison"):
            cmp = claim["comparison"]
            lines.append(f"   - **{t['change'][cmp['status']]}:** {cmp['reason']}")
    if not s["claims"]:
        lines.append(t["no_claims"])
    if s["mode"] == "comparison" and s["claims"]:
        lines.extend(["", f"## {t['h_timeline']}", ""])
        dates = {st["uid"]: date_text(st["date"], lang) for st in s["studies"]}
        for claim in s["claims"]:
            lines.extend([f"### {claim['id']}", ""])
            timeline = {tp["study_uid"]: tp for tp in claim["timeline"]}
            for study in s["studies"]:
                lines.append(f"- **{dates[study['uid']]}:** {timeline[study['uid']]['text']}")
            lines.append("")
    if s.get("recommendations"):
        lines.extend(["", f"## {t['h_recommendations']}", ""] + ["- " + r for r in s["recommendations"]])
    lines.extend(["", f"## {t['h_limitations']}", ""] + ["- " + x for x in s["limitations"]])
    if s.get("identity_evidence"):
        lines.append("- " + t["identity_note"])
    lines.extend(["", f"## {t['h_sources']}", "", t["sources_note"], ""])
    for claim in s["claims"]:
        lines.append(f"- **{claim['id']}:** " + " | ".join(source_text(r) for r in claim["refs"]))
    fingerprint = hashlib.sha256("|".join(f["sha256"] for st in s["studies"] for f in st["source_files"]).encode()).hexdigest()
    lines.extend(["", f"{t['integrity']}: `{fingerprint}`.", "", f"{t['guide_link']}: [{guide_name}]({guide_name}).", ""])

    lay = [marker, "# " + t["guide_title"], "", "> " + t["guide_banner"], "",
           f"## {t['g_how_to_read']}", "", t["g_how_to_read_text"], "",
           f"## {t['g_what']}", "", s["patient_context"], "",
           f"{t['g_report_link']}: [{report_name}]({report_name}).", "",
           f"## {t['g_key']}", ""]
    for claim in s["claims"]:
        patient = claim["patient"]
        lay.extend([f"### {claim['id']} — " + ("**" + claim["text"] + "**" if claim["priority"] == "important" else claim["text"]), "",
                    f"**{t['g_meaning']}** " + patient["meaning"], "",
                    f"**{t['g_importance']}** " + patient["importance"], "",
                    f"**{t['g_uncertainty']}** " + patient["uncertainty"], "",
                    f"**{t['g_discuss']}** " + patient["discuss_with_doctor"], ""])
        for m in claim.get("measurements", []):
            lay.append(f"- {t['measurement']}: **{m['value']} {m['unit']}** — {m['method']}")
        if s["mode"] == "comparison":
            lay.extend(["", f"**{t['g_by_date']}**"])
            timeline = {tp["study_uid"]: tp for tp in claim["timeline"]}
            for study in s["studies"]:
                lay.append(f"- **{date_text(study['date'], lang)}:** {timeline[study['uid']]['explanation']}")
        lay.append("")
    lay.extend([f"## {t['g_regions']}", ""])
    for study in s["studies"]:
        lay.extend([f"### {date_text(study['date'], lang)}", ""])
        for name, region in study["regions"].items():
            label_ = names.get(name.split(":")[-1], name)
            lay.extend([f"**{label_} — {t['status'][region['status']]}:** {region['explanation']}", ""])
    if s["mode"] == "comparison":
        lay.extend([f"## {t['g_compare']}", "", s["comparison"]["explanation"], ""])
    if s["claims"]:
        lay.extend([f"## {t['g_questions']}", "", t["g_questions_intro"], ""])
        for claim in s["claims"]:
            lay.append(f"- [{claim['id']}] {claim['patient']['discuss_with_doctor']}")
        lay.append("")
    lay.extend([f"## {t['g_limits']}", "", s["patient_limitations"], ""])
    if s.get("glossary"):
        lay.extend([f"## {t['g_glossary']}", ""])
        for item in s["glossary"]:
            lay.append(f"- **{item['term']}:** {item['explanation']}")
    lay.extend(["", t["g_footer"], ""])
    return "\n".join(lines), "\n".join(lay)


def finish(path: Path, lang: Optional[str] = None, output_dir: Optional[Path] = None,
           cfg: Optional[Settings] = None) -> tuple[Path, Path]:
    cfg = cfg or load_settings()
    s = load_session(path)
    errors = validate(s)
    if errors:
        raise ValidationError("Review is not ready:\n- " + "\n- ".join(errors))
    lang = lang or s.get("language") or cfg.lang
    if lang not in LANGUAGES:
        raise UsageError(f"--lang must be one of {LANGUAGES}")
    t = _texts(lang)
    repo = Path(s["repo"])
    out = _resolve(repo, output_dir or cfg.output_dir, repo / "reports")
    out.mkdir(parents=True, exist_ok=True)
    signature = hashlib.sha256("|".join(st["uid"] for st in s["studies"]).encode()).hexdigest()[:8]
    stem = s["studies"][0]["folder"] if s["mode"] == "single" else (
        s["studies"][0]["date"] + "_" + s["studies"][-1]["date"] + f"_{len(s['studies'])}studies_{signature}")
    report, guide = out / (stem + t["report_suffix"]), out / (stem + t["guide_suffix"])
    marker_prefix = "<!-- generated-by:"
    for existing in (report, guide):
        if existing.exists() and not existing.read_text(encoding="utf-8").startswith(marker_prefix):
            raise IntegrityError(f"Refusing to overwrite nonstandard/user file: {existing}")
    stamp = now()
    body, plain = render_documents(s, lang, report.name, guide.name, stamp)
    s["finalized_utc"] = stamp
    s["language"] = lang
    s["report_sha256"] = hashlib.sha256(body.encode()).hexdigest()
    s["guide_sha256"] = hashlib.sha256(plain.encode()).hexdigest()
    s["report_file"], s["guide_file"] = str(report), str(guide)
    write_json(path, s)  # the session itself records finalization, files and document hashes
    for claim in s["claims"]:
        for m in claim.get("measurements", []):
            m["tool_output"] = Path(m["evidence_file"]).read_text(encoding="utf-8")
    # Machine evidence stays in the ignored run directory; exactly two user-facing Markdown files.
    write_json(Path(s["work_dir"]) / "evidence.json", s)
    for dest, text in ((report, body), (guide, plain)):
        if dest.exists():
            backup = Path(s["work_dir"]) / (dest.name + ".previous")
            if not backup.exists():
                backup.write_bytes(dest.read_bytes())
        temp = dest.with_suffix(dest.suffix + ".tmp")
        with temp.open("x", encoding="utf-8") as stream:
            stream.write(text)
        temp.replace(dest)
    print(report)
    print(guide)
    return report, guide


# --------------------------------------------------------------------- CLI
def build_parser(cfg: Optional[Settings] = None) -> argparse.ArgumentParser:
    cfg = cfg or load_settings()
    p = argparse.ArgumentParser(prog="openrad", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="create a review session")
    prep.add_argument("folders", nargs="+", help="study folders (names inside --studies-root, or paths)")
    prep.add_argument("--repo", type=Path, default=Path.cwd(), help="review repository root")
    prep.add_argument("--studies-root", type=Path, default=None, help=f"directory holding the study folders (default {cfg.studies_root or '<repo>/DCIM'})")
    prep.add_argument("--cache-dir", type=Path, default=None, help=f"scratch/evidence directory (default {cfg.cache_dir or '<repo>/.cache/create-report'})")
    prep.add_argument("--identity-source", type=Path,
                      help="document explicitly confirming same patient for anonymized exports; never an automatic inference")
    prep.add_argument("--lang", choices=LANGUAGES, default=None, help=f"report language (default {cfg.lang})")
    reg = sub.add_parser("register", help="register rendered pages")
    reg.add_argument("session", type=Path)
    reg.add_argument("--directory", type=Path, required=True)
    chk = sub.add_parser("check", help="validate the session")
    chk.add_argument("session", type=Path)
    chk.add_argument("--json", action="store_true", help="print {\"ok\": bool, \"errors\": [...]}")
    fin = sub.add_parser("finish", help="write the two Markdown documents")
    fin.add_argument("session", type=Path)
    fin.add_argument("--lang", choices=LANGUAGES, help="override the session language")
    fin.add_argument("--output-dir", type=Path, default=None, help=f"destination (default {cfg.output_dir or '<repo>/reports'})")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    cfg = load_settings()
    a = build_parser(cfg).parse_args(argv)
    if a.command == "prepare":
        prepare(a.repo, a.folders, a.identity_source, a.studies_root, a.lang, a.cache_dir, cfg)
        return 0
    if a.command == "register":
        register(a.session, a.directory)
        return 0
    if a.command == "finish":
        finish(a.session, a.lang, a.output_dir, cfg)
        return 0
    errors = validate(load_session(a.session))
    if a.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2, ensure_ascii=False))
    else:
        print("\n".join(errors) if errors else "Structural checks passed; interpretation still needs clinical review")
    if errors:
        raise ValidationError(f"{len(errors)} validation error(s)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
