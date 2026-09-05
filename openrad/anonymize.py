"""Deterministic de-identification after DICOM PS3.15 Annex E (Basic Application Confidentiality Profile).

    openrad anonymize IN_DIR OUT_DIR --salt SECRET            # or OPENRAD_PSEUDONYM_SALT
    openrad anonymize IN_DIR OUT_DIR --salt SECRET --map map.json --json

Profile and options implemented (codes from DICOM PS3.16 CID 7050):

* 113100 Basic Application Confidentiality Profile: identifying attributes are
  removed (X), emptied (Z) or replaced (D); UIDs are replaced consistently (U);
  private tags, overlays and curves are removed.
* 113107 Retain Longitudinal Temporal Information with Modified Dates: every
  DA/DT value is shifted by the same number of days (derived from the salt or
  ``privacy.date_shift_days``), times are kept, so uptake intervals, series
  order and follow-up intervals survive. ``--remove-dates`` disables the option
  and applies the strict profile (SUV and chronology become impossible).
* 113108 Retain Patient Characteristics: sex, age, weight, size are kept
  because SUV and dose calculations need them. ``--no-retain-characteristics``
  removes them.
* 113111 Retain Safe Private: a small allow-list of private tags that carry
  quantitation constants (Philips PET SUV/activity scale factors) is kept;
  everything else private is dropped. ``--no-safe-private`` drops all.
* 113105 Clean Descriptors (partial): Series/Protocol/Study descriptions and
  body part are kept because sequence identification depends on them; they are
  scanned for the patient's name/ID and emptied when a match is found.
  ``--strip-descriptors`` removes them unconditionally.

Pseudonymisation is deterministic: ``HMAC-SHA256(salt, value)`` drives new
UIDs (``2.25.<int>``), the patient ID (``OR-<hex>``), the patient name
(``ANON^<hex>``) and the date shift. The same salt therefore maps the same
patient and the same objects identically across exports and runs, which keeps
longitudinal comparison possible without storing a lookup table. The salt is
the secret; treat it like a key. A mapping file is written only on request.

Pixel data are copied bit-for-bit and verified by hash. Burned-in annotations
cannot be detected; objects flagged ``BurnedInAnnotation = YES`` are refused
unless ``--allow-burned-in`` is given. Output folders and file names contain
only pseudonyms.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Set

import pydicom
from pydicom.dataset import Dataset
from pydicom.sequence import Sequence as DicomSequence
from pydicom.tag import Tag

from .config import load_settings
from .errors import InputError, IntegrityError, UsageError

# Action codes after PS3.15 Table E.1-1: X remove, Z zero-length/dummy, D dummy value, U consistent UID.
BASIC_ACTIONS: Dict[int, str] = {
    0x00080014: "U",  # InstanceCreatorUID
    0x00080018: "U",  # SOPInstanceUID
    0x00080050: "Z",  # AccessionNumber
    0x00080080: "X", 0x00080081: "X", 0x00080082: "X",  # Institution name/address/code
    0x00080090: "Z",  # ReferringPhysicianName
    0x00080092: "X", 0x00080094: "X", 0x00080096: "X",
    0x00081010: "X",  # StationName
    0x00081040: "X", 0x00081048: "X", 0x00081049: "X", 0x00081050: "X", 0x00081052: "X",
    0x00081060: "X", 0x00081062: "X", 0x00081070: "X", 0x00081072: "X", 0x00081080: "X", 0x00081084: "X",
    0x00081110: "X", 0x00081111: "X", 0x00081120: "X", 0x00081195: "U", 0x00082111: "X", 0x00083010: "U",
    0x00084000: "X",  # IdentifyingComments
    0x00100010: "Z", 0x00100020: "Z", 0x00100021: "X", 0x00100030: "Z", 0x00100032: "X",
    0x00100050: "X", 0x00100101: "X", 0x00100102: "X",
    0x00101000: "X", 0x00101001: "X", 0x00101002: "X", 0x00101005: "X",
    0x00101040: "X", 0x00101050: "X", 0x00101060: "X", 0x00101080: "X", 0x00101081: "X", 0x00101090: "X",
    0x00102000: "X", 0x00102110: "X", 0x00102150: "X", 0x00102152: "X", 0x00102154: "X", 0x00102160: "X",
    0x00102180: "X", 0x001021A0: "X", 0x001021B0: "X", 0x001021C0: "X", 0x001021D0: "X", 0x001021F0: "X",
    0x00102203: "X", 0x00102297: "X", 0x00102299: "X", 0x00104000: "X",
    0x00181000: "X", 0x00181004: "X", 0x00181005: "X", 0x00181007: "X", 0x00181008: "X",  # device/plate/cassette/gantry IDs
    0x0018700A: "X",
    0x0020000D: "U", 0x0020000E: "U", 0x00200010: "Z", 0x00200052: "U", 0x00200200: "U", 0x00204000: "X", 0x00209161: "U",
    0x00321032: "X", 0x00321033: "X", 0x00321060: "X", 0x00321064: "X", 0x00324000: "X",
    0x00380010: "X", 0x00380011: "X", 0x00380020: "X", 0x00380021: "X", 0x00380040: "X", 0x00380050: "X",
    0x00380060: "X", 0x00380061: "X", 0x00380062: "X", 0x00380300: "X", 0x00380400: "X", 0x00380500: "X", 0x00384000: "X",
    0x00400001: "X", 0x00400002: "X", 0x00400003: "X", 0x00400004: "X", 0x00400005: "X", 0x00400006: "X",
    0x00400010: "X", 0x00400011: "X", 0x00400012: "X", 0x00400241: "X", 0x00400242: "X",
    0x00400243: "X", 0x00400253: "X", 0x00400254: "X", 0x00400275: "X", 0x00400280: "X",
    0x00401001: "X", 0x00401002: "X", 0x00401004: "X", 0x00401005: "X", 0x00401010: "X", 0x00401400: "X",
    0x00402001: "X", 0x00402008: "X", 0x00402009: "X", 0x00402010: "X", 0x00402016: "X", 0x00402017: "X", 0x00404037: "X",
    0x0040A124: "U", 0x0040A730: "X",
    0x00700084: "X",  # ContentCreatorName
    0x00880140: "U", 0x00880200: "X",
    0x30060024: "U", 0x300600C2: "U",
    0x30080054: "X",
    0x4008010A: "X", 0x4008010B: "X", 0x4008010C: "X", 0x40080111: "X", 0x40080114: "X", 0x40080115: "X",
    0x40080118: "X", 0x40080119: "X", 0x4008011A: "X", 0x40080202: "X", 0x40080300: "X", 0x40084000: "X",
}
# Retain Patient Characteristics Option (113108): kept unless --no-retain-characteristics.
CHARACTERISTICS = {0x00100040, 0x00101010, 0x00101020, 0x00101030, 0x00102160, 0x00102203, 0x001021C0}
# Descriptors kept (and scanned) under the partial Clean Descriptors option.
DESCRIPTORS = {0x00081030, 0x0008103E, 0x00181030, 0x00180015, 0x00400254}
# Retain Safe Private Option (113111): private creator -> allowed element offsets.
SAFE_PRIVATE: Dict[str, Set[int]] = {
    "Philips PET Private Group": {0x00, 0x09},  # (7053,xx00) SUV scale factor, (7053,xx09) activity concentration scale
}
DEID_CODES = [("113100", "Basic Application Confidentiality Profile")]


class Pseudonymizer:
    """Deterministic value mapping driven by an HMAC secret."""

    def __init__(self, salt: str, date_shift_days: Optional[int] = None) -> None:
        if not salt or len(salt) < 8:
            raise UsageError("A pseudonymisation salt of at least 8 characters is required (--salt or OPENRAD_PSEUDONYM_SALT)")
        self._key = salt.encode("utf-8")
        self.uids: Dict[str, str] = {}
        self.ids: Dict[str, str] = {}
        self.date_shift_days = date_shift_days if date_shift_days is not None else -(self._digest("date-shift")[0] % 365 + 1)

    def _digest(self, value: str) -> bytes:
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).digest()

    def uid(self, original: str) -> str:
        original = str(original).strip()
        if not original:
            return original
        if original not in self.uids:
            n = int.from_bytes(self._digest("uid:" + original)[:16], "big")
            self.uids[original] = f"2.25.{n}"
        return self.uids[original]

    def patient_id(self, original: str) -> str:
        key = "pid:" + str(original)
        if key not in self.ids:
            self.ids[key] = "OR-" + self._digest(key).hex()[:12].upper()
        return self.ids[key]

    def patient_name(self, original_id: str) -> str:
        return "ANON^" + self._digest("pid:" + str(original_id)).hex()[:8].upper()

    def shift_date(self, value: str) -> str:
        s = str(value)
        if len(s) < 8 or not s[:8].isdigit():
            return s
        try:
            d = dt.datetime.strptime(s[:8], "%Y%m%d") + dt.timedelta(days=self.date_shift_days)
        except ValueError:
            return s
        return d.strftime("%Y%m%d") + s[8:]


def _walk(ds: Dataset):
    """Yield (dataset, element) for every element, recursing into sequences."""
    for elem in list(ds):
        yield ds, elem
        if elem.VR == "SQ":
            for item in elem.value:
                yield from _walk(item)


def _descriptor_mentions(value: str, needles: Sequence[str]) -> bool:
    v = value.lower()
    return any(n and len(n) >= 3 and n.lower() in v for n in needles)


def deidentify(ds: Dataset, pseudo: Pseudonymizer, *, shift_dates: bool = True, retain_characteristics: bool = True,
               safe_private: bool = True, strip_descriptors: bool = False) -> Dict[str, Any]:
    """Apply the profile to one dataset in place. Returns a small report."""
    report: Dict[str, Any] = {"removed": 0, "emptied": 0, "uids": 0, "dates_shifted": 0, "private_removed": 0,
                              "private_kept": 0, "descriptors_cleaned": 0, "warnings": []}
    original_pid = str(ds.get("PatientID", "")) or str(ds.get("PatientName", "")) or "unknown"
    needles = [str(ds.get("PatientID", "")), *str(ds.get("PatientName", "")).replace("^", " ").split(),
               str(ds.get("AccessionNumber", ""))]
    if str(ds.get("BurnedInAnnotation", "")).upper() == "YES":
        report["warnings"].append("BurnedInAnnotation=YES: pixel data may contain identifiers")

    for parent, elem in _walk(ds):
        tag = int(elem.tag)
        if elem.tag.is_private:
            creator = parent.get(Tag(elem.tag.group, elem.tag.element >> 8 if elem.tag.element >= 0x100 else elem.tag.element))
            creator_name = str(creator.value).strip() if creator is not None and creator.tag != elem.tag else ""
            is_creator = elem.tag.element < 0x100
            allowed = safe_private and (
                (is_creator and str(elem.value).strip() in SAFE_PRIVATE)
                or (creator_name in SAFE_PRIVATE and (elem.tag.element & 0xFF) in SAFE_PRIVATE[creator_name]))
            if allowed:
                report["private_kept"] += 1
                continue
            del parent[elem.tag]
            report["private_removed"] += 1
            continue
        group = elem.tag.group
        if (0x5000 <= group <= 0x50FF) or (0x6000 <= group <= 0x60FF):  # curves and overlays
            del parent[elem.tag]
            report["removed"] += 1
            continue
        if tag in CHARACTERISTICS:
            if not retain_characteristics:
                del parent[elem.tag]
                report["removed"] += 1
            continue
        if tag in DESCRIPTORS:
            if strip_descriptors:
                del parent[elem.tag]
                report["removed"] += 1
            elif isinstance(elem.value, str) and _descriptor_mentions(elem.value, needles):
                elem.value = ""
                report["descriptors_cleaned"] += 1
            continue
        action = BASIC_ACTIONS.get(tag)
        if action == "U" or (action is None and elem.VR == "UI" and _looks_like_instance_uid(elem)):
            if elem.VR == "UI" and elem.value:
                if isinstance(elem.value, (list, pydicom.multival.MultiValue)):
                    elem.value = [pseudo.uid(v) for v in elem.value]
                else:
                    elem.value = pseudo.uid(str(elem.value))
                report["uids"] += 1
            continue
        if action == "X":
            del parent[elem.tag]
            report["removed"] += 1
            continue
        if action == "Z" or action == "D":
            if tag == 0x00100020:
                elem.value = pseudo.patient_id(original_pid)
            elif tag == 0x00100010:
                elem.value = pseudo.patient_name(original_pid)
            elif elem.VR == "SQ":
                elem.value = DicomSequence()
            elif elem.VR in ("DA", "DT") and shift_dates and tag != 0x00100030:
                # StudyDate under the modified-dates option is cleaned (shifted); birth date is always emptied
                elem.value = pseudo.shift_date(str(elem.value))
                report["dates_shifted"] += 1
            elif elem.VR == "TM" and shift_dates:
                pass  # times are retained under 113107
            else:
                elem.value = ""
            report["emptied"] += 1
            continue
        if elem.VR in ("DA", "DT"):
            if shift_dates:
                if isinstance(elem.value, (list, pydicom.multival.MultiValue)):
                    elem.value = [pseudo.shift_date(str(v)) for v in elem.value]
                else:
                    elem.value = pseudo.shift_date(str(elem.value))
                report["dates_shifted"] += 1
            else:
                del parent[elem.tag]
                report["removed"] += 1
            continue
        if elem.VR == "TM" and not shift_dates:
            del parent[elem.tag]
            report["removed"] += 1
    if "PatientID" not in ds:
        ds.PatientID = pseudo.patient_id(original_pid)
    if "PatientName" not in ds:
        ds.PatientName = pseudo.patient_name(original_pid)
    if not shift_dates:
        ds.StudyDate, ds.StudyTime = "", ""
    # De-identification bookkeeping (PS3.3 C.7.1.1 Patient Module / PS3.16 CID 7050)
    codes = list(DEID_CODES)
    if shift_dates:
        codes.append(("113107", "Retain Longitudinal Temporal Information Modified Dates Option"))
    if retain_characteristics:
        codes.append(("113108", "Retain Patient Characteristics Option"))
    if safe_private:
        codes.append(("113111", "Retain Safe Private Option"))
    if not strip_descriptors:
        codes.append(("113105", "Clean Descriptors Option"))
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = "OpenRadiology PS3.15 E.1 basic profile; " + "; ".join(c for c, _ in codes)
    seq = DicomSequence()
    for code, meaning in codes:
        item = Dataset()
        item.CodeValue, item.CodingSchemeDesignator, item.CodeMeaning = code, "DCM", meaning
        seq.append(item)
    ds.DeidentificationMethodCodeSequence = seq
    if hasattr(ds, "file_meta") and "MediaStorageSOPInstanceUID" in ds.file_meta:
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    return report


def _looks_like_instance_uid(elem: pydicom.DataElement) -> bool:
    """UI elements that are instance/reference identifiers (not class/transfer-syntax UIDs)."""
    name = elem.keyword or ""
    return any(k in name for k in ("Instance", "FrameOfReference", "Concatenation", "Referenced")) and "Class" not in name


def _pixel_hash(ds: Dataset) -> Optional[str]:
    if "PixelData" not in ds:
        return None
    return hashlib.sha256(ds.PixelData).hexdigest()


def anonymize_tree(src: Path, dst: Path, pseudo: Pseudonymizer, *, shift_dates: bool = True,
                   retain_characteristics: bool = True, safe_private: bool = True, strip_descriptors: bool = False,
                   allow_burned_in: bool = False) -> Dict[str, Any]:
    if not src.is_dir():
        raise InputError(f"Input folder not found: {src}")
    if dst.exists() and any(dst.iterdir()):
        raise InputError(f"Output folder must be empty: {dst}")
    dst.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {"files_in": 0, "files_out": 0, "skipped": 0, "date_shift_days": pseudo.date_shift_days,
                               "studies": {}, "warnings": [], "totals": {}}
    totals: Dict[str, int] = {}
    for p in sorted(x for x in src.rglob("*") if x.is_file() and not x.name.startswith(".")):
        summary["files_in"] += 1
        try:
            ds = pydicom.dcmread(p, force=True)
        except Exception:
            summary["skipped"] += 1
            continue
        if "SOPInstanceUID" not in ds or "PixelData" not in ds and "Modality" not in ds:
            summary["skipped"] += 1
            continue
        if str(ds.get("BurnedInAnnotation", "")).upper() == "YES" and not allow_burned_in:
            raise InputError(f"{p.name}: BurnedInAnnotation=YES; pixel de-identification is out of scope (use --allow-burned-in to proceed)")
        before = _pixel_hash(ds)
        rep = deidentify(ds, pseudo, shift_dates=shift_dates, retain_characteristics=retain_characteristics,
                         safe_private=safe_private, strip_descriptors=strip_descriptors)
        if _pixel_hash(ds) != before:
            raise IntegrityError(f"{p.name}: pixel data changed during de-identification")
        for k, v in rep.items():
            if isinstance(v, int):
                totals[k] = totals.get(k, 0) + v
        summary["warnings"] += [f"{p.name}: {w}" for w in rep["warnings"]]
        study = str(ds.StudyInstanceUID)
        series = str(ds.get("SeriesNumber", "0") or "0")
        try:
            series_dir = f"S{int(series):03d}"
        except ValueError:
            series_dir = "S" + re.sub(r"[^A-Za-z0-9]", "", series)[:8]
        study_dir = dst / ("ST-" + hashlib.sha256(study.encode()).hexdigest()[:10].upper()) / series_dir
        study_dir.mkdir(parents=True, exist_ok=True)
        inst = str(ds.get("InstanceNumber", "") or "")
        stem = f"{int(inst):05d}" if inst.isdigit() else hashlib.sha256(str(ds.SOPInstanceUID).encode()).hexdigest()[:10]
        target = study_dir / f"{stem}.dcm"
        if target.exists():
            target = study_dir / f"{stem}_{hashlib.sha256(str(ds.SOPInstanceUID).encode()).hexdigest()[:6]}.dcm"
        ds.save_as(target, write_like_original=False)
        summary["files_out"] += 1
        st = summary["studies"].setdefault(study_dir.parent.name, {"files": 0, "series": set()})
        st["files"] += 1
        st["series"].add(series_dir)
    for st in summary["studies"].values():
        st["series"] = sorted(st["series"])
    summary["totals"] = totals
    return summary


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openrad anonymize", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=Path, help="input study/archive folder")
    ap.add_argument("dst", type=Path, help="empty output folder")
    ap.add_argument("--salt", help="secret salt (or OPENRAD_PSEUDONYM_SALT / privacy.pseudonym_salt)")
    ap.add_argument("--date-shift-days", type=int, help="fixed shift instead of the salt-derived one (negative = past)")
    ap.add_argument("--remove-dates", action="store_true", help="strict profile: remove all dates/times (breaks SUV and chronology)")
    ap.add_argument("--no-retain-characteristics", action="store_true", help="also remove sex/age/weight/size")
    ap.add_argument("--no-safe-private", action="store_true", help="remove every private tag, including PET scale factors")
    ap.add_argument("--strip-descriptors", action="store_true", help="remove study/series/protocol descriptions")
    ap.add_argument("--allow-burned-in", action="store_true", help="proceed even if BurnedInAnnotation=YES")
    ap.add_argument("--map", type=Path, help="write the original->pseudonym mapping (re-identification key; protect it)")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    cfg = load_settings()
    salt = a.salt or os.environ.get("OPENRAD_PSEUDONYM_SALT") or cfg.pseudonym_salt
    if not salt:
        raise UsageError("Provide --salt or set OPENRAD_PSEUDONYM_SALT; a fixed secret makes pseudonyms reproducible")
    shift = a.date_shift_days if a.date_shift_days is not None else cfg.date_shift_days
    pseudo = Pseudonymizer(salt, shift)
    summary = anonymize_tree(a.src, a.dst, pseudo, shift_dates=not a.remove_dates,
                             retain_characteristics=not a.no_retain_characteristics, safe_private=not a.no_safe_private,
                             strip_descriptors=a.strip_descriptors, allow_burned_in=a.allow_burned_in)
    if a.map:
        a.map.parent.mkdir(parents=True, exist_ok=True)
        with a.map.open("x", encoding="utf-8") as stream:
            json.dump({"uids": pseudo.uids, "ids": pseudo.ids, "date_shift_days": pseudo.date_shift_days}, stream, indent=2)
        print(f"[warning] re-identification map written to {a.map}; store it like the salt", file=sys.stderr)
    if a.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"{summary['files_out']} files de-identified into {a.dst} ({summary['skipped']} skipped); "
              f"date shift {summary['date_shift_days']} days; studies: {len(summary['studies'])}")
        for w in summary["warnings"]:
            print(f"[warning] {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
