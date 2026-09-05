"""Inventory every series of a DICOM study folder (any modality).

    openrad inventory STUDY_DIR --output OUT_DIR [--json]

Writes ``OUT_DIR/inventory.json`` and ``OUT_DIR/inventory.md`` and prints the
Markdown table (or the JSON with ``--json``). Never opens any report text;
safe for blinded mode. Vendor-specific tags that matter for quantitation are
surfaced (PixelPaddingValue, DecayCorrectionDateTime, Philips CNTS scale
factors, GantryDetectorTilt, SpacingBetweenSlices vs measured spacing).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pydicom

from .config import load_settings
from .dcmlib import plane_of, read_headers, save_json, z_of
from .errors import InputError
from .log import progress
from .suv import PHILIPS_ACTIVITY_SCALE, PHILIPS_SUV_SCALE

REDACTED = "<redacted>"


def f(x: Any, nd: int = 2) -> Optional[float]:
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def _list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    try:
        return [str(v) for v in x]
    except TypeError:
        return [str(x)]


def mr_guess(ds: pydicom.Dataset) -> str:
    """Heuristic sequence label from timing parameters and ImageType. A hint, never a fact."""
    it = [str(v).upper() for v in ds.get("ImageType", [])]
    ss = _list(ds.get("ScanningSequence", []))
    te, tr, ti = f(ds.get("EchoTime")), f(ds.get("RepetitionTime")), f(ds.get("InversionTime"))
    name = str(ds.get("SequenceName", "") or ds.get("ProtocolName", "") or "").lower()
    tags: List[str] = []
    if "DIFFUSION" in it:
        if "ADC" in it:
            return "DWI ADC map"
        m = re.search(r"b(\d+)", name)
        b = f" b={m.group(1)}" if m else ""
        return f"DWI trace{b}"
    if "SWI" in it:
        return "SWI (magnitude/processed)"
    if "MNIP" in it or "MINIP" in it:
        return "SWI minIP"
    if "P" in it and "swi" in name:
        return "SWI phase"
    if "M" in it and "swi" in name:
        return "SWI raw magnitude"
    if "MIP" in it:
        return "MIP reformat"
    derived = "DERIVED" in it
    thin = (f(ds.get("SliceThickness")) or 9) <= 1.5
    if ti and ti > 1500 and te and te > 80:
        base = "T2 FLAIR" + (" 3D" if "3D" in name or "spc" in name or thin else "")
    elif ti and 600 <= ti <= 1300 and te and te < 10:
        base = "T1 MPRAGE/3D GRE"
    elif "SE" in ss and te and te >= 70:
        base = "T2 TSE"
    elif "SE" in ss and te and te < 30 and tr and tr < 900:
        base = "T1 SE"
    elif "GR" in ss and te and te < 6 and tr and tr < 60:
        base = "T1 GRE (localizer / fast)"
    elif "GR" in ss and te and te > 15:
        base = "T2* GRE"
    else:
        base = "unclassified"
    if derived and "PARALLEL" in it:
        tags.append("reformat of 3D acquisition")
    if ds.get("ContrastBolusAgent"):
        tags.append(f"contrast agent tag: {ds.get('ContrastBolusAgent')}")
    return base + (" [" + "; ".join(tags) + "]" if tags else "")


def inventory(root: Path, redact: bool = True) -> Dict[str, Any]:
    """Series table for one study. With ``redact`` (default) direct identifiers never enter the JSON/Markdown."""
    headers = read_headers(root)
    if not headers:
        raise InputError("No DICOM files found")
    if len({str(d.get("StudyInstanceUID", "")) for _, d in headers}) != 1:
        raise InputError("Folder contains multiple studies; select one study per folder")
    series: Dict[str, Dict[str, Any]] = {}
    for p, ds in headers:
        key = str(ds.get("SeriesInstanceUID", ds.get("SeriesNumber", "?")))
        s = series.setdefault(key, {"paths": [], "ds": [], "folders": Counter()})
        s["paths"].append(p)
        s["ds"].append(ds)
        s["folders"][str(p.parent.relative_to(root))] += 1

    d0 = headers[0][1]
    study = {
        "study_dir": str(root),
        "StudyInstanceUID": str(d0.get("StudyInstanceUID", "")),
        "StudyDate": str(d0.get("StudyDate", "")),
        "StudyTime": str(d0.get("StudyTime", "")),
        "StudyDescription": str(d0.get("StudyDescription", "")),
        "AccessionNumber": REDACTED if redact else str(d0.get("AccessionNumber", "")),
        "PatientID": REDACTED if redact else str(d0.get("PatientID", "")),
        "PatientIdentityRemoved": str(d0.get("PatientIdentityRemoved", "")),
        "InstitutionName": REDACTED if redact else str(d0.get("InstitutionName", "")),
        "redacted": redact,
        "Manufacturer": f"{d0.get('Manufacturer', '')} {d0.get('ManufacturerModelName', '')}".strip(),
        "SoftwareVersions": " ".join(_list(d0.get("SoftwareVersions"))),
        "PatientPosition": str(d0.get("PatientPosition", "")),
        "PatientWeight": f(d0.get("PatientWeight")),
        "PatientSize": f(d0.get("PatientSize")),
        "PatientSex": str(d0.get("PatientSex", "")),
        "BodyPartExamined": str(d0.get("BodyPartExamined", "")),
        "n_files": len(headers),
        "modalities": sorted({str(ds.get("Modality")) for _, ds in headers}),
    }

    rows: List[Dict[str, Any]] = []
    for key, s in series.items():
        ds = s["ds"][0]
        zs_sorted = sorted(z_of(d) for d in s["ds"])
        dz = float(np.median(np.diff(zs_sorted))) if len(zs_sorted) > 1 else None
        mod = str(ds.get("Modality", "?"))
        it = "/".join(str(v) for v in ds.get("ImageType", []))
        row: Dict[str, Any] = {
            "series_uid": key,
            "study_uid": str(ds.get("StudyInstanceUID", "")),
            "frame_uid": str(ds.get("FrameOfReferenceUID", "")),
            "series_number": str(ds.get("SeriesNumber", "?")),
            "folder": ", ".join(sorted(s["folders"])),
            "modality": mod,
            "description": str(ds.get("SeriesDescription", "")),
            "image_type": it,
            "images": len(s["ds"]),
            "multiframe": any(int(d.get("NumberOfFrames", 1)) != 1 for d in s["ds"]),
            "rows_cols": [int(ds.get("Rows", 0)), int(ds.get("Columns", 0))],
            "pixel_spacing_mm": [f(v, 4) for v in ds.get("PixelSpacing", [])],
            "slice_thickness_mm": f(ds.get("SliceThickness")),
            "spacing_between_slices_tag_mm": f(ds.get("SpacingBetweenSlices")),
            "slice_spacing_mm": f(dz, 3) if dz is not None else None,
            "plane": plane_of(ds),
            "iop": [f(v, 4) for v in ds.get("ImageOrientationPatient", [])],
            "z_range_mm": [f(zs_sorted[0], 1), f(zs_sorted[-1], 1)] if zs_sorted else None,
            "coverage_mm": f(zs_sorted[-1] - zs_sorted[0], 1) if len(zs_sorted) > 1 else None,
            "series_date": str(ds.get("SeriesDate", "")),
            "series_time": str(ds.get("SeriesTime", "")),
            "acquisition_time": str(ds.get("AcquisitionTime", "")),
            "transfer_syntax": str(getattr(ds.file_meta, "TransferSyntaxUID", "")) if hasattr(ds, "file_meta") else "",
            "photometric": str(ds.get("PhotometricInterpretation", "")),
            "bits_stored": ds.get("BitsStored"),
            "contrast_agent": str(ds.get("ContrastBolusAgent", "") or ""),
            "pixel_padding_value": ds.get("PixelPaddingValue"),
        }
        if mod == "CT":
            row.update({
                "kernel": "/".join(_list(ds.get("ConvolutionKernel"))),
                "kvp": f(ds.get("KVP")), "exposure_mAs": f(ds.get("Exposure")),
                "ctdi_vol": f(ds.get("CTDIvol")), "recon_diameter_mm": f(ds.get("ReconstructionDiameter")),
                "gantry_tilt_deg": f(ds.get("GantryDetectorTilt")),
                "rescale": [f(ds.get("RescaleSlope")), f(ds.get("RescaleIntercept"))],
            })
        if mod == "MR":
            row.update({
                "TR": f(ds.get("RepetitionTime")), "TE": f(ds.get("EchoTime")), "TI": f(ds.get("InversionTime")),
                "flip": f(ds.get("FlipAngle")), "scanning_sequence": _list(ds.get("ScanningSequence")),
                "sequence_name": str(ds.get("SequenceName", "")), "field_strength_T": f(ds.get("MagneticFieldStrength")),
                "b_value": f(ds.get("DiffusionBValue")),
                "guess": mr_guess(ds),
                "sequence_variants": sorted({str(d.get("SequenceName", "")) for d in s["ds"]}),
                "echo_numbers": sorted({str(d.get("EchoNumbers", "")) for d in s["ds"]}),
                "b_values": sorted({str(d.get("DiffusionBValue", "")) for d in s["ds"]}),
            })
        if mod == "PT":
            rp = ds.get("RadiopharmaceuticalInformationSequence")
            r = rp[0] if rp else None
            row.update({
                "units": str(ds.get("Units", "")), "decay_correction": str(ds.get("DecayCorrection", "")),
                "decay_correction_datetime": str(ds.get("DecayCorrectionDateTime", "")),
                "corrected_image": _list(ds.get("CorrectedImage")),
                "recon": str(ds.get("ReconstructionMethod", "")), "kernel": str(ds.get("ConvolutionKernel", "")),
                "radiopharmaceutical": str(r.get("Radiopharmaceutical", "")) if r else "",
                "total_dose_Bq": f(r.get("RadionuclideTotalDose")) if r else None,
                "injection_time": str(r.get("RadiopharmaceuticalStartDateTime", r.get("RadiopharmaceuticalStartTime", ""))) if r else "",
                "half_life_s": f(r.get("RadionuclideHalfLife")) if r else None,
                "patient_weight_kg": f(ds.get("PatientWeight")),
                "frame_duration_ms": ds.get("ActualFrameDuration"),
                "philips_suv_scale": f(ds[PHILIPS_SUV_SCALE].value, 6) if PHILIPS_SUV_SCALE in ds else None,
                "philips_activity_scale": f(ds[PHILIPS_ACTIVITY_SCALE].value, 6) if PHILIPS_ACTIVITY_SCALE in ds else None,
            })
        rows.append(row)

    def sort_key(r: Dict[str, Any]) -> Any:
        try:
            return (0, int(r["series_number"]))
        except (TypeError, ValueError):
            return (1, r["series_number"])
    rows.sort(key=sort_key)

    if "MR" in study["modalities"]:
        seen: Dict[Any, str] = {}
        for r in rows:
            if r["modality"] != "MR":
                continue
            sig = (r.get("sequence_name"), r["TR"], r["TE"], r["TI"], r["plane"], r["image_type"])
            if sig in seen:
                r["guess"] += f" [REPEAT of series {seen[sig]} later in time -> check pre/post-contrast]"
            else:
                seen[sig] = r["series_number"]
    return {"study": study, "series": rows}


def to_markdown(inv: Dict[str, Any], root: Path) -> str:
    study, rows = inv["study"], inv["series"]
    lines = [f"# Inventory: {root.name}", "",
             f"- StudyDate/Time: {study['StudyDate']} {study['StudyTime']} | Modalities: {', '.join(study['modalities'])} | Files: {study['n_files']}",
             f"- Scanner: {study['Manufacturer']} | Position: {study['PatientPosition']} | BodyPart: {study['BodyPartExamined']} | "
             f"Weight tag: {study['PatientWeight']} | Accession: {study['AccessionNumber']}",
             "", "| Ser | Folder | Mod | n | Plane | Matrix | Px (mm) | Thk/Sp (mm) | Coverage (mm) | ImageType | Details |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        det = [f"UID `{r['series_uid']}`"]
        if r["multiframe"]:
            det.append("MULTIFRAME (unsupported)")
        if r["modality"] == "CT":
            det.append(f"kernel {r.get('kernel')}")
            if r.get("contrast_agent"):
                det.append(f"contrast {r['contrast_agent']}")
            if r.get("gantry_tilt_deg"):
                det.append(f"tilt {r['gantry_tilt_deg']} deg")
        if r["modality"] == "MR":
            det.append(f"TR {r['TR']} TE {r['TE']} TI {r['TI']} -> **{r['guess']}**")
            if len(r["b_values"]) > 1 or len(r["echo_numbers"]) > 1:
                det.append(f"stacks: b {r['b_values']} echoes {r['echo_numbers']}")
        if r["modality"] == "PT":
            det.append(f"{r['units']} decay={r['decay_correction']} dose={r['total_dose_Bq']} inj={r['injection_time']} "
                       f"weight={r['patient_weight_kg']} recon={r['recon']}")
        z = r["z_range_mm"]
        base = f"| {r['series_number']} | {r['folder']} | {r['modality']} | {r['images']} | {r['plane']} | {r['rows_cols'][0]}x{r['rows_cols'][1]} | "
        if z:
            lines.append(base + f"{'x'.join(str(v) for v in r['pixel_spacing_mm']) or '-'} | {r['slice_thickness_mm']}/{r['slice_spacing_mm']} | "
                         f"{r['coverage_mm']} ({z[0]}..{z[1]}) | {r['image_type']} | {'; '.join(det)} |")
        else:
            lines.append(base + f"- | - | - | {r['image_type']} | {'; '.join(det)} |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openrad inventory", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--output", type=Path, required=True, help="scratch directory for inventory.json/.md")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the Markdown table")
    ap.add_argument("--no-redact", action="store_true", help="include AccessionNumber/PatientID/Institution (privacy.redact_identifiers=false)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    cfg = load_settings()
    a = build_parser().parse_args(argv)
    root = a.study_dir.resolve()
    if not root.is_dir():
        raise InputError(f"Study folder not found: {root}")
    a.output.mkdir(parents=True, exist_ok=True)
    inv = inventory(root, redact=cfg.redact_identifiers and not a.no_redact)
    md = to_markdown(inv, root)
    save_json(inv, a.output / "inventory.json")
    (a.output / "inventory.md").write_text(md, encoding="utf-8")
    if a.json:
        print(json.dumps(inv, indent=2, ensure_ascii=False, default=str))
    else:
        print(md)
    progress(f"[written] {a.output / 'inventory.json'}  {a.output / 'inventory.md'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
