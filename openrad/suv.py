"""Standardized uptake value (SUV) conversion from PET DICOM headers.

Implements the vendor-neutral logic of the QIBA FDG-PET/CT Profile
(RSNA QIBA, "Vendor-neutral pseudo-code for SUV calculation", rev. 2018) and
the EANM tumour-imaging procedure guideline (Boellaard et al., EJNMMI 2015,
doi:10.1007/s00259-014-2961-x):

    SUVbw [g/mL] = C_tissue [Bq/mL] * weight [g] / dose_at_scan_start [Bq]
    dose_at_scan_start = injected_dose * 2 ** (-(t_scan - t_injection) / T_half)

Decision rules
--------------
* ``Units`` (0054,1001) must be ``BQML`` for a header-derived factor.
  ``GML`` means the vendor already stored SUVbw (factor 1, unverifiable).
  ``CNTS`` is handled only for Philips exports that carry the private
  activity-concentration scale factor (7053,1009) -> Bq/mL, or the private
  SUV scale factor (7053,1000) -> SUVbw directly.
* ``DecayCorrection`` (0054,1102):
  - ``START``: pixels are decay-corrected to the *scan start*; the dose must be
    decayed from injection to scan start. Scan start is taken from
    ``DecayCorrectionDateTime`` (0018,9701) when present, else
    ``SeriesDate``+``SeriesTime``; if the earliest ``AcquisitionTime`` precedes
    ``SeriesTime`` (a known GE quirk) the earlier value is used and flagged.
  - ``ADMIN``: pixels are already corrected to injection time; no dose decay.
  - ``NONE``: unsupported for SUV.
* Injection time: ``RadiopharmaceuticalStartDateTime`` (0018,1078) preferred;
  ``RadiopharmaceuticalStartTime`` (0018,1072) is combined with the series
  date. A negative uptake interval is *not* silently wrapped across midnight;
  it is reported as an error for the operator to resolve.
* ``PatientWeight`` (0010,1030) is frequently stripped by anonymisation. A
  weight passed explicitly is recorded with its source; no default is assumed.
* SUL (lean-body-mass normalised, PERCIST 1.0, Wahl et al. J Nucl Med 2009,
  doi:10.2967/jnumed.108.057307) is offered as an auxiliary factor using the
  Janmahasatian 2005 formula recommended by QIBA. PERCIST's original James
  formula is also available. Neither replaces SUVbw in this pipeline.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, Iterable, Optional, Tuple

import pydicom
from pydicom.tag import Tag

PHILIPS_SUV_SCALE = Tag(0x7053, 0x1000)
PHILIPS_ACTIVITY_SCALE = Tag(0x7053, 0x1009)

SuvResult = Tuple[Optional[float], Dict[str, Any]]


def parse_dicom_datetime(ds_time: Any, ds_date: Any) -> Optional[dt.datetime]:
    """Combine DICOM DA + TM (fractional seconds allowed) into a naive datetime."""
    if not ds_time:
        return None
    s = str(ds_time)
    t = s.split(".")[0].ljust(6, "0")
    frac = float("0." + s.split(".")[1]) if "." in s else 0.0
    d = str(ds_date or "19000101")
    try:
        base = dt.datetime.strptime(d[:8] + t[:6], "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return base + dt.timedelta(seconds=frac)


def parse_dicom_dt(value: Any) -> Optional[dt.datetime]:
    """Parse a DICOM DT value (YYYYMMDDHHMMSS.FFFFFF&ZZXX). Timezone offset is dropped."""
    if not value:
        return None
    s = str(value)
    for sep in ("+", "-"):
        if sep in s[8:]:
            s = s[:8] + s[8:].split(sep)[0]
    if len(s) < 8:
        return None
    return parse_dicom_datetime(s[8:] or "000000", s[:8])


def lean_body_mass_kg(weight_kg: float, height_m: float, sex: str, formula: str = "janmahasatian") -> Optional[float]:
    """Lean body mass for SUL. ``sex`` is DICOM PatientSex ('M'/'F')."""
    if not (weight_kg > 0 and height_m > 0):
        return None
    sex = (sex or "").upper()[:1]
    if formula == "james":
        if sex == "M":
            return 1.10 * weight_kg - 120 * (weight_kg / (height_m * 100)) ** 2
        if sex == "F":
            return 1.07 * weight_kg - 148 * (weight_kg / (height_m * 100)) ** 2
        return None
    bmi = weight_kg / (height_m ** 2)
    if sex == "M":
        return 9270 * weight_kg / (6680 + 216 * bmi)
    if sex == "F":
        return 9270 * weight_kg / (8780 + 244 * bmi)
    return None


def _scan_start(ds: pydicom.Dataset, datasets: Optional[Iterable[pydicom.Dataset]], info: Dict[str, Any]) -> Optional[dt.datetime]:
    dc_dt = parse_dicom_dt(ds.get("DecayCorrectionDateTime"))
    series = parse_dicom_datetime(ds.get("SeriesTime"), ds.get("SeriesDate") or ds.get("StudyDate"))
    acq_candidates = []
    for d in (datasets or [ds]):
        a = parse_dicom_datetime(d.get("AcquisitionTime"), d.get("AcquisitionDate") or d.get("SeriesDate") or d.get("StudyDate"))
        if a is not None:
            acq_candidates.append(a)
    acq = min(acq_candidates) if acq_candidates else None
    info["series_start"] = str(series)
    info["earliest_acquisition"] = str(acq)
    if dc_dt is not None:
        info["scan_start_source"] = "DecayCorrectionDateTime"
        return dc_dt
    if series is not None and acq is not None and acq < series:
        info["scan_start_source"] = "earliest AcquisitionTime (precedes SeriesTime)"
        info.setdefault("warnings", []).append("AcquisitionTime precedes SeriesTime; earliest acquisition used as scan start")
        return acq
    if series is not None:
        info["scan_start_source"] = "SeriesTime"
        return series
    if acq is not None:
        info["scan_start_source"] = "AcquisitionTime"
        return acq
    return None


def suv_factor(ds: pydicom.Dataset, weight_kg: Optional[float] = None,
               datasets: Optional[Iterable[pydicom.Dataset]] = None,
               uptake_window: Tuple[float, float] = (45.0, 90.0)) -> SuvResult:
    """Return ``(factor, info)``; ``stored_value * factor`` is SUVbw. ``factor`` is None on failure.

    ``ds`` is the header of one slice (rescale already applied to pixels by the
    caller). ``datasets`` may hold every slice header to find the earliest
    acquisition time.
    """
    info: Dict[str, Any] = {"units": str(ds.get("Units", "")), "manufacturer": str(ds.get("Manufacturer", ""))}
    units = info["units"]
    rp = ds.get("RadiopharmaceuticalInformationSequence")
    if not rp:
        return None, {**info, "error": "no RadiopharmaceuticalInformationSequence"}
    r = rp[0]
    info["radiopharmaceutical"] = str(r.get("Radiopharmaceutical", ""))
    dose = float(r.get("RadionuclideTotalDose", 0) or 0)
    half = float(r.get("RadionuclideHalfLife", 0) or 0)
    if not math.isfinite(dose) or not math.isfinite(half) or dose <= 0 or half <= 0:
        return None, {**info, "error": "invalid dose or radionuclide half-life"}
    inj = parse_dicom_dt(r.get("RadiopharmaceuticalStartDateTime"))
    if inj is None:
        inj = parse_dicom_datetime(r.get("RadiopharmaceuticalStartTime"), ds.get("SeriesDate") or ds.get("StudyDate"))
        info["injection_source"] = "RadiopharmaceuticalStartTime + SeriesDate"
    else:
        info["injection_source"] = "RadiopharmaceuticalStartDateTime"
    w: Any = ds.get("PatientWeight")
    weight_src = "header PatientWeight"
    if weight_kg is not None:
        w = weight_kg
        weight_src = "argument (documented externally)"
    if not w:
        return None, {**info, "error": "PatientWeight missing; pass --weight with a documented source"}
    w = float(w)
    if not math.isfinite(w) or not (20 <= w <= 400):
        return None, {**info, "error": f"implausible patient weight {w} kg"}
    decay = str(ds.get("DecayCorrection", ""))
    scan = _scan_start(ds, datasets, info)
    info.update({"dose_MBq": round(dose / 1e6, 3), "half_life_s": half, "injection": str(inj),
                 "decay_correction": decay, "weight_kg": w, "weight_source": weight_src})
    height = ds.get("PatientSize")
    if height:
        try:
            lbm = lean_body_mass_kg(w, float(height), str(ds.get("PatientSex", "")))
            if lbm:
                info["lean_body_mass_kg_janmahasatian"] = round(lbm, 1)
                info["sul_over_suv"] = round(lbm / w, 4)
        except (TypeError, ValueError):
            pass

    # ---- vendor unit handling
    if units == "GML":
        info["note"] = "Units GML: vendor already stored SUVbw; decay and weight cannot be re-verified"
        info["factor"] = 1.0
        return 1.0, info
    if units == "CNTS":
        if PHILIPS_ACTIVITY_SCALE in ds:
            scale = float(ds[PHILIPS_ACTIVITY_SCALE].value)
            info["note"] = "Philips CNTS: private (7053,1009) activity concentration scale -> Bq/mL"
            units = "BQML"
            cnts_to_bqml = scale
        elif PHILIPS_SUV_SCALE in ds:
            scale = float(ds[PHILIPS_SUV_SCALE].value)
            info["note"] = "Philips CNTS: private (7053,1000) SUV scale factor -> SUVbw (vendor weight/decay)"
            info["factor"] = scale
            return scale, info
        else:
            return None, {**info, "error": "Units CNTS without Philips private scale factors; SUV not computable"}
    else:
        cnts_to_bqml = 1.0
    if units != "BQML":
        return None, {**info, "error": f"Units {units} not BQML; SUV factor not computed"}

    # ---- decay handling
    if decay == "START":
        if inj is None or scan is None:
            return None, {**info, "error": "missing injection or scan start time"}
        dt_s = (scan - inj).total_seconds()
        if dt_s < 0:
            return None, {**info, "error": "injection is after scan start; date/overnight ambiguity must be resolved manually"}
        if dt_s > 6 * 3600:
            info.setdefault("warnings", []).append(f"uptake interval {dt_s / 60:.0f} min is implausible; check dates")
        dose_at_scan = dose * math.exp(-math.log(2) * dt_s / half)
        info["uptake_time_min"] = round(dt_s / 60, 1)
        lo, hi = uptake_window
        if not lo <= dt_s / 60 <= hi:
            info.setdefault("warnings", []).append(f"uptake time outside the accepted window ({lo:g}-{hi:g} min; EANM target 55-75)")
    elif decay == "ADMIN":
        dose_at_scan = dose
        if inj is not None and scan is not None:
            info["uptake_time_min"] = round((scan - inj).total_seconds() / 60, 1)
    else:
        return None, {**info, "error": f"unsupported DecayCorrection '{decay}' (need START or ADMIN)"}
    info["dose_at_scan_MBq"] = round(dose_at_scan / 1e6, 3)
    factor = cnts_to_bqml * (w * 1000.0) / dose_at_scan  # Bq/mL -> g/mL
    info["factor"] = factor
    return factor, info
