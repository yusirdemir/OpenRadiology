"""Calibrated measurements on native DICOM slices: distances, ROI statistics, extents.

Every number is derived from native pixel indices and the slice's own
``PixelSpacing`` / slice spacing; nothing is resampled. Output can be saved as
an immutable evidence file whose SHA-256 is printed so that it can be cited in
the session ledger (``measurements[].evidence_file`` + ``sha256``).

Usage:
  openrad measure STUDY --series 4 --instance 212 --points 318,240 331,252   # in-plane distance (mm)
  openrad measure STUDY --series 4 --instance 212 --roi 325,246,4            # circular ROI stats (row,col,radius px)
  openrad measure STUDY --series 4 --instance 212 --profile 300,246 350,246  # HU along a line
  openrad measure STUDY --series 4 --z -152                                  # which instance is at z?
  openrad measure STUDY --series 4 --extent 150,230,-160,-140 --thr -300     # craniocaudal extent inside a box
  openrad measure STUDY --series 5 --instance 700 --roi 100,98,3 --weight 73 # PET: SUVbw stats
  openrad measure STUDY --series 4 --instance 116 --auto 330,150 --thr -300 --thr-max 200 --radius 45
        # 2D region grow from seed inside HU range: area, long/short axis (Feret), endpoints, mean HU
  openrad measure STUDY --series 4 --instance 116 --auto3d 330,150 --thr -300 --thr-max 200 --radius 45
        # 3D region grow: craniocaudal extent, volume, per-slice area
  ... --output OUT/meas_L1.txt        # save exact output (write-once) and print its SHA-256
  ... --json                          # machine-readable result on stdout (and in --output)

Coordinates are native ``row,col`` pixels as labelled by ``openrad zoom``.
Measurement conventions follow RECIST 1.1 (longest in-plane diameter for
lesions, short axis for lymph nodes) and the Fleischner measurement statement
(Bankier et al. Radiology 2017: average of long and perpendicular short axis on
thin sections for nodules < 10 mm). The tool reports the raw axes; the reader
decides which convention applies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .config import Settings, load_settings
from .dcmlib import Volume, series_by_number
from .errors import InputError, QuantitationError, UsageError
from .log import progress
from .suv import suv_factor

LESION_TYPES = ("nodule", "node", "mass", "other")


def reported_diameter(long_ax: float, short_ax: float, convention: str, lesion_type: str) -> Tuple[float, str]:
    """Which single number a guideline expects, given raw axes (mm).

    * lymph node -> short axis (RECIST 1.1, IASLC nodal reporting)
    * fleischner -> average of long and perpendicular short axis when < 10 mm, else long axis (Bankier 2017)
    * recist -> longest in-plane diameter
    """
    if lesion_type == "node":
        return short_ax, "short axis (lymph node)"
    if convention == "fleischner" and (long_ax + short_ax) / 2 < 10:
        return (long_ax + short_ax) / 2, "average of long and short axis (Fleischner, < 10 mm)"
    return long_ax, "long axis (RECIST 1.1)" if convention == "recist" else "long axis (Fleischner, >= 10 mm)"

Index = Tuple[int, ...]


class Report:
    """Collects human-readable lines and a structured result in parallel."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.data: Dict[str, Any] = {"tool": "openrad measure", "results": [], "warnings": [], "notes": []}

    def say(self, text: str) -> None:
        self.lines.append(text)

    def warn(self, text: str) -> None:
        self.lines.append(f"  [warn] {text}")
        self.data["warnings"].append(text)

    def note(self, text: str) -> None:
        self.lines.append(f"  [note] {text}")
        self.data["notes"].append(text)

    def add(self, kind: str, **fields: Any) -> None:
        self.data["results"].append({"kind": kind, **fields})

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _grow(inside: Callable[[Index], bool], start: Index, dims_mm: Sequence[float],
          radius: float, neighbors: Sequence[Index]) -> set:
    """Generic BFS region growing limited to ``radius`` mm from the seed."""
    seen = {start}
    q = deque([start])
    while q:
        cur = q.popleft()
        for d in neighbors:
            n = tuple(c + dd for c, dd in zip(cur, d))
            if n in seen or not inside(n):
                continue
            if math.sqrt(sum(((nn - ss) * mm) ** 2 for nn, ss, mm in zip(n, start, dims_mm))) > radius:
                continue
            seen.add(n)
            q.append(n)
    return seen


def _feret(points_mm: Sequence[Tuple[float, float]]) -> Tuple[float, float, Optional[np.ndarray], Optional[np.ndarray]]:
    """Maximum Feret diameter and the perpendicular extent (deterministic subsample above 1500 points)."""
    pts = np.array(points_mm)
    if len(pts) < 2:
        return 0.0, 0.0, None, None
    if len(pts) > 1500:
        pts = pts[np.random.RandomState(0).choice(len(pts), 1500, replace=False)]
    d = np.sqrt(((pts[:, None, :] - pts[None, :, :]) ** 2).sum(-1))
    i, j = np.unravel_index(np.argmax(d), d.shape)
    long_ax = float(d[i, j])
    u = (pts[j] - pts[i]) / (long_ax + 1e-9)
    perp = np.array([-u[1], u[0]])
    proj = pts @ perp
    short_ax = float(proj.max() - proj.min())
    return long_ax, short_ax, pts[i], pts[j]


def _ints(spec: str, n: Optional[int] = None) -> List[int]:
    try:
        vals = [int(x) for x in spec.split(",")]
    except ValueError as e:
        raise UsageError(f"Expected comma-separated integers, got '{spec}'") from e
    if n is not None and len(vals) != n:
        raise UsageError(f"Expected {n} integers, got '{spec}'")
    return vals


def region_2d(rep: Report, sl: np.ndarray, r: int, c: int, thr: float, thr_max: float, radius: float,
              row_sp: float, col_sp: float, unit: str, box: Optional[List[int]] = None,
              convention: str = "fleischner", lesion_type: str = "nodule") -> None:
    if not (thr < sl[r, c] < thr_max):
        rep.say(f"seed value {sl[r, c]:.0f} {unit} is outside ({thr}, {thr_max}); move the seed")
        return
    H, W = sl.shape
    b = box or [0, H, 0, W]
    inside = lambda n: (b[0] <= n[0] < b[1] and b[2] <= n[1] < b[3] and 0 <= n[0] < H and 0 <= n[1] < W  # noqa: E731
                        and thr < sl[n[0], n[1]] < thr_max)
    nb = [(dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0)]
    reg = _grow(inside, (r, c), (row_sp, col_sp), radius, nb)
    idx = np.array(list(reg))
    vals = sl[idx[:, 0], idx[:, 1]]
    area = len(reg) * row_sp * col_sp
    boundary = [p for p in reg if any((p[0] + d[0], p[1] + d[1]) not in reg for d in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
    long_ax, short_ax, p1, p2 = _feret([(p[0] * row_sp, p[1] * col_sp) for p in boundary])
    if p1 is None or p2 is None:
        rep.say("Region too small for reliable axis measurement")
        return
    # boundary points are pixel centres: add one pixel to each axis for edge-to-edge extent
    long_ax += (row_sp + col_sp) / 2
    short_ax += (row_sp + col_sp) / 2
    rmin, rmax, cmin, cmax = int(idx[:, 0].min()), int(idx[:, 0].max()), int(idx[:, 1].min()), int(idx[:, 1].max())
    e1 = (int(round(p1[0] / row_sp)), int(round(p1[1] / col_sp)))
    e2 = (int(round(p2[0] / row_sp)), int(round(p2[1] / col_sp)))
    eq_diam = 2 * math.sqrt(area / math.pi)
    rep.say(f"2D region from seed ({r},{c}): {len(reg)} px, area {area:.0f} mm2, equivalent diameter {eq_diam:.1f} mm")
    rep.say(f"  long axis {long_ax:.1f} mm ({e1[0]},{e1[1]})->({e2[0]},{e2[1]}); perpendicular short axis {short_ax:.1f} mm; "
            f"average {(long_ax + short_ax) / 2:.1f} mm")
    rep.say(f"  bbox rows {rmin}-{rmax} cols {cmin}-{cmax} ({(rmax - rmin + 1) * row_sp:.1f} x {(cmax - cmin + 1) * col_sp:.1f} mm); "
            f"mean {vals.mean():.0f} sd {vals.std():.0f} min {vals.min():.0f} max {vals.max():.0f} {unit}")
    value, rule = reported_diameter(long_ax, short_ax, convention, lesion_type)
    rep.say(f"  reported diameter [{convention}, {lesion_type}]: {value:.0f} mm = {rule}")
    rep.add("region_2d", seed=[r, c], pixels=len(reg), area_mm2=round(area, 1), equivalent_diameter_mm=round(eq_diam, 1),
            long_axis_mm=round(long_ax, 1), short_axis_mm=round(short_ax, 1), average_diameter_mm=round((long_ax + short_ax) / 2, 1),
            reported_diameter_mm=round(value), reported_rule=rule, convention=convention, lesion_type=lesion_type,
            long_axis_endpoints=[list(e1), list(e2)], bbox_rows=[rmin, rmax], bbox_cols=[cmin, cmax],
            stats={"mean": float(vals.mean()), "sd": float(vals.std()), "min": float(vals.min()), "max": float(vals.max()), "unit": unit},
            thresholds=[thr, thr_max], radius_mm=radius, box=box)
    if (rmax - rmin + 1) * row_sp > 1.8 * radius or (cmax - cmin + 1) * col_sp > 1.8 * radius:
        rep.warn("region reached the radius limit -> probable leak into adjacent tissue; check the bbox")
    if box and (rmin == box[0] or rmax == box[1] - 1 or cmin == box[2] or cmax == box[3] - 1):
        rep.warn("region touches the --box edge -> the box clips the lesion or the lesion abuts adjacent tissue there")
    rep.note("Exploratory threshold boundaries; verify morphology and caliper edges. HU half-height is not universal ground truth")


def region_3d(rep: Report, v: Volume, k: int, r: int, c: int, thr: float, thr_max: float, radius: float,
              factor: float, unit: str, box: Optional[List[int]] = None) -> None:
    v.require_rectilinear("3D region growing")
    vol = v.vol * factor if factor != 1.0 else v.vol
    if not (thr < vol[k, r, c] < thr_max):
        rep.say(f"seed value {vol[k, r, c]:.0f} {unit} is outside ({thr}, {thr_max}); move the seed")
        return
    Z, H, W = vol.shape
    b = box or [0, H, 0, W]
    if not (0 <= b[0] < b[1] <= H and 0 <= b[2] < b[3] <= W):
        raise UsageError("Invalid 3D ROI bounds")
    inside = lambda n: (0 <= n[0] < Z and b[0] <= n[1] < b[1] and b[2] <= n[2] < b[3]  # noqa: E731
                        and thr < vol[n[0], n[1], n[2]] < thr_max)
    nb = [(dz, dr, dc) for dz in (-1, 0, 1) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dz, dr, dc) != (0, 0, 0)]
    reg = _grow(inside, (k, r, c), (abs(v.dz), v.row_sp, v.col_sp), radius, nb)
    idx = np.array(list(reg))
    ks = np.unique(idx[:, 0])
    vox = v.voxel_volume_mm3
    extent = (ks.max() - ks.min() + 1) * abs(v.dz)
    rep.say(f"3D region from seed (img {v.instance(k)}, {r},{c}): {len(reg)} voxels = {len(reg) * vox / 1000:.1f} mL; "
            f"z {v.z[ks.min()]:.1f}..{v.z[ks.max()]:.1f} -> craniocaudal extent {extent:.1f} mm (img {v.instance(ks.min())}..{v.instance(ks.max())})")
    per = {int(kk): int((idx[:, 0] == kk).sum()) for kk in ks}
    rep.say("  per-slice px (img:px): " + " ".join(f"{v.instance(kk)}:{per[kk]}" for kk in ks))
    rep.say(f"  bbox rows {idx[:, 1].min()}-{idx[:, 1].max()} cols {idx[:, 2].min()}-{idx[:, 2].max()}")
    kmax = max(per, key=lambda kk: per[kk])
    rep.say(f"  largest cross-section at img {v.instance(kmax)} (z {v.z[kmax]:.1f}); run --auto on that instance for axial diameters")
    rep.add("region_3d", seed=[r, c], seed_instance=v.instance(k), voxels=len(reg), volume_ml=round(len(reg) * vox / 1000, 2),
            craniocaudal_extent_mm=round(float(extent), 1), z_range_mm=[float(v.z[ks.min()]), float(v.z[ks.max()])],
            instance_range=[v.instance(ks.min()), v.instance(ks.max())], largest_cross_section_instance=v.instance(kmax),
            per_slice_px={v.instance(kk): per[kk] for kk in ks}, thresholds=[thr, thr_max], radius_mm=radius, box=box)
    if (idx[:, 1].min() == b[0] or idx[:, 1].max() == b[1] - 1 or idx[:, 2].min() == b[2] or idx[:, 2].max() == b[3] - 1):
        rep.warn("Region touches box: clipped lesion or connected tissue; volume is not validated")
    rep.note("Threshold volume/axes are exploratory; inspect segmentation boundaries before reporting")


def build_parser(cfg: Optional[Settings] = None) -> argparse.ArgumentParser:
    cfg = cfg or load_settings()
    ap = argparse.ArgumentParser(prog="openrad measure", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--series", required=True, help="SeriesNumber or SeriesInstanceUID")
    ap.add_argument("--instance", help="InstanceNumber of the slice")
    ap.add_argument("--z", type=float, help="locate the slice nearest to this z (mm)")
    ap.add_argument("--points", nargs=2, metavar="R,C", help="two endpoints; in-plane Euclidean distance")
    ap.add_argument("--roi", help="row,col,radius_px circular ROI statistics")
    ap.add_argument("--profile", nargs=2, metavar="R,C", help="values along a line")
    ap.add_argument("--extent", help="r0,r1,c0,c1 box; reports z-extent of voxels inside (--thr, --thr-max)")
    ap.add_argument("--thr", type=float, default=-300, help="lower HU/SUV bound for --extent/--auto")
    ap.add_argument("--thr-max", type=float, default=200, help="upper bound (excludes bone/calcium)")
    ap.add_argument("--min-px", type=int, default=15, help="--extent: ignore slices with fewer voxels than this")
    ap.add_argument("--weight", type=float, help="patient weight kg (PET SUV); document the source")
    ap.add_argument("--auto", help="row,col seed for 2D region growing on --instance")
    ap.add_argument("--auto3d", help="row,col seed for 3D region growing starting at --instance")
    ap.add_argument("--radius", type=float, default=cfg.region_radius_mm, help="max distance from seed (mm) for region growing")
    ap.add_argument("--convention", choices=("fleischner", "recist"), default=cfg.convention,
                    help="which diameter is reported for --auto (raw axes are always printed)")
    ap.add_argument("--lesion-type", choices=LESION_TYPES, default="nodule", help="node -> short axis is reported")
    ap.add_argument("--box", help="r0,r1,c0,c1: confine region growing (lesion touching chest wall/vessels)")
    ap.add_argument("--allow-tilt", action="store_true", help="accept a gantry-tilted stack (in-plane measurements only)")
    ap.add_argument("--json", action="store_true", help="emit a JSON document instead of text")
    ap.add_argument("--output", type=Path, help="write-once evidence file; its SHA-256 is printed on stderr")
    return ap


def run(a: argparse.Namespace, cfg: Optional[Settings] = None) -> Report:
    cfg = cfg or load_settings()
    rep = Report()
    v = Volume(series_by_number(a.study_dir, a.series), allow_tilt=a.allow_tilt)
    unit, factor = "HU", 1.0
    if v.modality == "PT":
        factor_or_none, info = suv_factor(v.ds[0], a.weight, datasets=v.ds, uptake_window=cfg.uptake_window_min)
        if factor_or_none is None:
            raise QuantitationError("SUV unavailable: " + info.get("error", "unknown error"))
        factor, unit = factor_or_none, "SUVbw"
        rep.say("SUV info: " + json.dumps(info, default=str))
        rep.data["suv_info"] = info
    elif v.modality != "CT":
        unit = "signal (not HU)"
    rep.data["unit"] = unit
    rep.data["series"] = v.geometry_summary()
    rep.say(f"series {v.series_number} {v.modality}: {v.shape}, px {v.row_sp:.4f}x{v.col_sp:.4f} mm, dz {v.dz:.3f} mm")
    if v.is_tilted:
        rep.warn(f"gantry tilt {v.gantry_tilt_deg:.2f} deg: in-plane numbers valid, 3D extents need a rectilinear stack")

    if a.z is not None and a.instance is None:
        k = v.index_of_z(a.z)
        rep.say(f"z {a.z} -> instance {v.instance(k)} (index {k}, actual z {v.z[k]:.2f})")
        rep.add("slice_lookup", z_requested=a.z, instance=v.instance(k), index=k, z_actual=float(v.z[k]), sop_uid=v.sop_uid(k))
        if not (a.points or a.roi or a.profile or a.extent or a.auto or a.auto3d):
            return rep
        a.instance = v.instance(k)
    if a.extent:
        v.require_rectilinear("--extent")
        r0, r1, c0, c1 = _ints(a.extent, 4)
        box = v.vol[:, r0:r1, c0:c1] * factor
        hit = ((box > a.thr) & (box < a.thr_max)).sum(axis=(1, 2))
        ks = np.where(hit >= a.min_px)[0]
        if len(ks) == 0:
            rep.say("no voxels above threshold in box")
        else:
            ext = abs(v.z[ks[-1]] - v.z[ks[0]]) + abs(v.dz)
            rep.say(f"voxels in ({a.thr}, {a.thr_max}) with >= {a.min_px} px/slice: slices index {ks[0]}..{ks[-1]} "
                    f"(instances {v.instance(ks[0])}..{v.instance(ks[-1])}), z {v.z[ks[0]]:.1f}..{v.z[ks[-1]]:.1f} -> "
                    f"craniocaudal extent {ext:.1f} mm (incl. slice thickness)")
            rep.say("  per-slice px: " + " ".join(f"{v.instance(k)}:{hit[k]}" for k in ks))
            rep.say("  (check for gaps: a contiguous run = one lesion; split runs = separate structures/vessels)")
            rep.add("extent", box=[r0, r1, c0, c1], thresholds=[a.thr, a.thr_max], craniocaudal_extent_mm=round(float(ext), 1),
                    instance_range=[v.instance(ks[0]), v.instance(ks[-1])], z_range_mm=[float(v.z[ks[0]]), float(v.z[ks[-1]])],
                    per_slice_px={v.instance(k): int(hit[k]) for k in ks})
        if a.instance is None:
            return rep
    if a.instance is None:
        raise UsageError("give --instance or --z")
    k = v.index_of_instance(a.instance)
    sl = v.vol[k] * factor
    for spec in ([a.roi] if a.roi else []) + ([a.auto] if a.auto else []) + ([a.auto3d] if a.auto3d else []) + (a.points or []) + (a.profile or []):
        rr, cc = _ints(spec)[:2]
        if not (0 <= rr < sl.shape[0] and 0 <= cc < sl.shape[1]):
            raise UsageError(f"Measurement coordinate ({rr},{cc}) outside image {sl.shape}")
    ref = {"study_uid": v.study_uid, "series_uid": v.series_uid, "sop_uid": v.sop_uid(k), "instance": v.instance(k), "z_mm": float(v.z[k])}
    rep.data["slice"] = ref
    rep.say(f"instance {a.instance}: index {k}, z {v.z[k]:.2f} mm, SOP {v.sop_uid(k)}")
    if a.points:
        (r1, c1), (r2, c2) = [tuple(_ints(p, 2)) for p in a.points]
        d = math.hypot((r2 - r1) * v.row_sp, (c2 - c1) * v.col_sp)
        rep.say(f"distance ({r1},{c1})->({r2},{c2}) = {d:.1f} mm   [{unit} at ends: {sl[r1, c1]:.1f}, {sl[r2, c2]:.1f}]")
        rep.add("distance", points=[[r1, c1], [r2, c2]], value_mm=round(d, 2), unit="mm",
                method="native PixelSpacing Euclidean distance between pixel centres",
                end_values={"unit": unit, "values": [float(sl[r1, c1]), float(sl[r2, c2])]})
    if a.roi:
        r, c, rad = _ints(a.roi, 3)
        if rad <= 0 or r - rad < 0 or c - rad < 0 or r + rad >= sl.shape[0] or c + rad >= sl.shape[1]:
            raise UsageError("ROI must fit entirely inside the image")
        yy, xx = np.ogrid[:sl.shape[0], :sl.shape[1]]
        m = (yy - r) ** 2 + (xx - c) ** 2 <= rad ** 2
        vals = sl[m]
        rep.say(f"ROI ({r},{c}) r={rad}px ({rad * v.col_sp:.1f} mm), n={vals.size}: mean {vals.mean():.1f} sd {vals.std():.1f} "
                f"min {vals.min():.1f} max {vals.max():.1f} {unit}")
        entry: Dict[str, Any] = {"centre": [r, c], "radius_px": rad, "radius_mm": round(rad * v.col_sp, 2), "n": int(vals.size),
                                 "mean": float(vals.mean()), "sd": float(vals.std()), "min": float(vals.min()), "max": float(vals.max()), "unit": unit}
        if v.modality == "PT":
            rr, cc = np.unravel_index(np.argmax(np.where(m, sl, -1e9)), sl.shape)
            nb = sl[max(0, rr - 1):rr + 2, max(0, cc - 1):cc + 2]
            rep.say(f"  SUVmax at ({rr},{cc}) = {sl[rr, cc]:.2f}; 3x3 neighbourhood mean {nb.mean():.2f}")
            rep.note("A 2D neighbourhood mean is NOT SUVpeak/SULpeak (PERCIST needs a 1.2 cm diameter spherical VOI)")
            entry.update(suv_max=float(sl[rr, cc]), suv_max_row_col=[int(rr), int(cc)], neighbourhood_3x3_mean=float(nb.mean()))
        rep.add("roi", **entry)
    if a.auto:
        r, c = _ints(a.auto, 2)
        region_2d(rep, sl, r, c, a.thr, a.thr_max, a.radius, v.row_sp, v.col_sp, unit, _ints(a.box, 4) if a.box else None,
                  a.convention, a.lesion_type)
    if a.auto3d:
        r, c = _ints(a.auto3d, 2)
        region_3d(rep, v, k, r, c, a.thr, a.thr_max, a.radius, factor, unit, _ints(a.box, 4) if a.box else None)
    if a.profile:
        (r1, c1), (r2, c2) = [tuple(_ints(p, 2)) for p in a.profile]
        n = max(abs(r2 - r1), abs(c2 - c1)) + 1
        rs = np.linspace(r1, r2, n).round().astype(int)
        cs = np.linspace(c1, c2, n).round().astype(int)
        values = [float(sl[r, c]) for r, c in zip(rs, cs)]
        rep.say("profile: " + " ".join(f"{x:.0f}" for x in values))
        rep.add("profile", points=[[r1, c1], [r2, c2]], values=values, unit=unit)
    return rep


def main(argv: Optional[Sequence[str]] = None) -> int:
    cfg = load_settings()
    a = build_parser(cfg).parse_args(argv)
    rep = run(a, cfg)
    payload = json.dumps(rep.data, indent=2, ensure_ascii=False, default=str) + "\n" if a.json else rep.text()
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with a.output.open("x", encoding="utf-8") as stream:
                stream.write(payload)
        except FileExistsError as e:
            raise InputError(f"Evidence file exists; never overwrite evidence: {a.output}") from e
        sha = hashlib.sha256(a.output.read_bytes()).hexdigest()
        a.output.with_name(a.output.name + ".sha256").write_text(f"{sha}  {a.output.name}\n", encoding="utf-8")
        progress(f"[evidence] {a.output} sha256={sha}")
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
