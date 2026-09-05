"""PET/CT review renderer: SUV conversion, rotating MIPs, hotspot table, native and fused axial tiles.

    openrad pet-render STUDY --pt 5 --ct 2 --weight 73 --output OUT
    openrad pet-render STUDY --pt 5 --ct 2 --weight 73 --output OUT --suv-thr 2.5 --axial-step 12 --zmin -400 --zmax 100
    openrad pet-render STUDY --pt 5 --ct 2 --weight 73 --output OUT --at -152 --at -230   # fused tiles at given z (mm)

Outputs:
  suv_info.json           factor, uptake time, dose, weight source, scan-start source (QIBA logic)
  mip_rot.png             coronal MIPs rotated 0..315 deg (SUV 0..--mip-max, inverted grey)
  coronal_slices.png      PET coronal slices across the body (thin), same scale
  pet_native_NN.png       every native PET slice (inverted grey, SUV scale) -> coverage pass "pet:native"
  hotspots.md/json        26-connected components with SUVmax >= thr: SUVmax, volume, centroid, z, nearest CT instance
  hot_XX_*.png            per hotspot: CT soft | PET | fused axial at the SUVmax slice (+/- 2 slices)
  axial_fused_NN.png      fused axial tiles every --axial-step mm inside zmin..zmax -> coverage pass "pet:fused"

Physiologic uptake (brain, myocardium, kidneys/ureters/bladder, bowel, vocal cords, brown fat,
injection site, thymus) is NOT filtered automatically; judge every hotspot on the CT.
The hotspot threshold is a detection aid, not a malignancy criterion (EANM 2015; PERCIST 1.0).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .config import Settings, load_settings
from .dcmlib import (WINDOWS, Volume, contact_sheet, label, mark_source, orientation_marks, save_page,
                     series_by_number, window)
from .errors import GeometryError, QuantitationError, UsageError
from .grid import fit_grid, font_size_for
from .log import progress
from .suv import suv_factor


def inv_gray(suv: np.ndarray, vmax: float) -> np.ndarray:
    return (255 - np.clip(suv / vmax * 255, 0, 255)).astype(np.uint8)


def hot_rgb(suv: np.ndarray, vmax: float) -> np.ndarray:
    x = np.clip(suv / vmax, 0, 1)
    r = np.clip(x * 3, 0, 1)
    g = np.clip(x * 3 - 1, 0, 1)
    b = np.clip(x * 3 - 2, 0, 1)
    return (np.stack([r, g, b], -1) * 255).astype(np.uint8)


def rotate_axial(vol: np.ndarray, deg: float) -> np.ndarray:
    """Rotate every axial slice about the z axis (nearest neighbour)."""
    nz, ny, nx = vol.shape
    cy, cx = (ny - 1) / 2, (nx - 1) / 2
    t = math.radians(deg)
    yy, xx = np.mgrid[0:ny, 0:nx]
    ys = (yy - cy) * math.cos(t) - (xx - cx) * math.sin(t) + cy
    xs = (yy - cy) * math.sin(t) + (xx - cx) * math.cos(t) + cx
    ys = np.clip(np.round(ys).astype(int), 0, ny - 1)
    xs = np.clip(np.round(xs).astype(int), 0, nx - 1)
    return vol[:, ys, xs]


def resample_to_ct(pt: Volume, k_pt: int, ct: Volume, k_ct: int, factor: float) -> np.ndarray:
    """Resample one PET slice onto the CT slice pixel grid using patient coordinates (bilinear)."""
    pt_sl = pt.vol[k_pt] * factor
    xs = ct.ipp0[0] + np.arange(ct.vol.shape[2]) * ct.col_sp
    ys = float(ct.ds[k_ct].ImagePositionPatient[1]) + np.arange(ct.vol.shape[1]) * ct.row_sp
    px = (xs - pt.ipp0[0]) / pt.col_sp
    py = (ys - float(pt.ds[k_pt].ImagePositionPatient[1])) / pt.row_sp
    PX, PY = np.meshgrid(px, py)
    x0 = np.clip(np.floor(PX).astype(int), 0, pt_sl.shape[1] - 2)
    y0 = np.clip(np.floor(PY).astype(int), 0, pt_sl.shape[0] - 2)
    fx = np.clip(PX - x0, 0, 1)
    fy = np.clip(PY - y0, 0, 1)
    out = (pt_sl[y0, x0] * (1 - fx) * (1 - fy) + pt_sl[y0, x0 + 1] * fx * (1 - fy)
           + pt_sl[y0 + 1, x0] * (1 - fx) * fy + pt_sl[y0 + 1, x0 + 1] * fx * fy)
    outside = (PX < 0) | (PX > pt_sl.shape[1] - 1) | (PY < 0) | (PY > pt_sl.shape[0] - 1)
    out[outside] = 0
    return out


def fused_tiles(pt: Volume, ct: Volume, z: float, factor: float, bbox: Tuple[int, int, int, int], vmax: float,
                alpha: float = 0.55, note: str = "") -> Tuple[List[Image.Image], float]:
    k_pt, k_ct = pt.index_of_z(z), ct.index_of_z(z)
    r0, r1, c0, c1 = bbox
    ct_sl = ct.vol[k_ct, r0:r1, c0:c1]
    suv = resample_to_ct(pt, k_pt, ct, k_ct, factor)[r0:r1, c0:c1]
    c, w = WINDOWS["soft"]
    g = window(ct_sl, c, w)
    im_ct = Image.fromarray(g).convert("RGB")
    im_pt = Image.fromarray(inv_gray(suv, vmax)).convert("RGB")
    rgb = hot_rgb(suv, vmax).astype(np.float32)
    a = np.clip(suv / (0.6 * vmax), 0, 1)[..., None] * alpha
    fused = (g[..., None].astype(np.float32) * (1 - a) + rgb * a).astype(np.uint8)
    im_f = Image.fromarray(fused)
    for im, t in ((im_ct, "CT"), (im_pt, "PET"), (im_f, "fused")):
        label(im, f"{t} z {z:.0f} ct#{ct.instance(k_ct)} pt#{pt.instance(k_pt)} {note}", size=13, fill=(255, 255, 255), bg=(0, 0, 0))
        orientation_marks(im, "AX", 12, iop=ct.iop)
    mark_source(im_ct, ct, k_ct, "ct:fused")
    mark_source(im_pt, pt, k_pt, "pet:native")
    mark_source(im_f, pt, k_pt, "pet:fused")
    return [im_ct, im_pt, im_f], float(suv.max())


def components(mask: np.ndarray) -> List[List[Tuple[int, int, int]]]:
    """26-connected components on a boolean volume via DFS on the sparse voxel set."""
    idx = np.argwhere(mask)
    if len(idx) == 0:
        return []
    voxels: Dict[Tuple[int, int, int], Optional[int]] = {tuple(v): None for v in idx}
    comps: List[List[Tuple[int, int, int]]] = []
    for start in list(voxels):
        if voxels[start] is not None:
            continue
        cid = len(comps)
        stack = [start]
        voxels[start] = cid
        members: List[Tuple[int, int, int]] = []
        while stack:
            z, y, x = stack.pop()
            members.append((z, y, x))
            for dz in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        n = (z + dz, y + dy, x + dx)
                        if n in voxels and voxels[n] is None:
                            voxels[n] = cid
                            stack.append(n)
        comps.append(members)
    return comps


def render_pet_native(pt: Volume, suv: np.ndarray, out: Path, vmax: float, grid: str = "2x2", max_side: int = 1568) -> List[Path]:
    """Every native PET slice, superior -> inferior, inverted grey on a fixed SUV scale (2x nearest upscale)."""
    spec = fit_grid(pt.shape[2] * 2, pt.shape[1] * 2, grid, max_side)
    cols, per = spec.cols, spec.per_page
    tiles: List[Image.Image] = []
    files: List[Path] = []
    page = 0
    for k in range(len(pt.z) - 1, -1, -1):
        im = Image.fromarray(inv_gray(suv[k], vmax)).resize((pt.shape[2] * 2, pt.shape[1] * 2), Image.BICUBIC)
        size = font_size_for(min(im.width, im.height))
        label(im, f"PET img {pt.instance(k)} z {pt.z[k]:.0f} SUV 0-{vmax:g}", size=size, fill=0, bg=255)
        orientation_marks(im, iop=pt.iop, size=size)
        mark_source(im, pt, k, "pet:native")
        tiles.append(im)
        if len(tiles) == per:
            page += 1
            files.append(save_page(tiles, cols, out / f"pet_native_{page:02d}.png"))
            tiles = []
    if tiles:
        page += 1
        files.append(save_page(tiles, cols, out / f"pet_native_{page:02d}.png"))
    return files


def build_parser(cfg: Optional[Settings] = None) -> argparse.ArgumentParser:
    from .ct_render import add_grid_arguments
    cfg = cfg or load_settings()
    ap = argparse.ArgumentParser(prog="openrad pet-render", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--pt", required=True, help="PET SeriesNumber/UID (BQML, attenuation corrected)")
    ap.add_argument("--ct", required=True, help="CT SeriesNumber/UID sharing the FrameOfReference")
    ap.add_argument("--weight", type=float, help="patient weight kg when PatientWeight is missing; document the source")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--suv-thr", type=float, default=cfg.suv_threshold, help="hotspot detection threshold (SUVbw); not a malignancy cut-off")
    ap.add_argument("--min-voxels", type=int, default=2)
    ap.add_argument("--mip-max", type=float, default=cfg.suv_display_max)
    ap.add_argument("--fused-max", type=float, default=cfg.suv_display_max)
    ap.add_argument("--axial-step", type=float, default=1.0, help="mm; 0 disables full fused axial coverage (overview only)")
    ap.add_argument("--zmin", type=float)
    ap.add_argument("--zmax", type=float)
    ap.add_argument("--at", type=float, action="append", help="render fused tiles at this z (repeatable)")
    ap.add_argument("--max-hotspots", type=int, default=40)
    ap.add_argument("--no-mip", action="store_true")
    ap.add_argument("--no-native", action="store_true", help="skip the native PET slice sheets")
    add_grid_arguments(ap, cfg)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .ct_render import resolve_max_side
    cfg = load_settings()
    a = build_parser(cfg).parse_args(argv)
    max_side = resolve_max_side(a, cfg)
    if min(a.suv_thr, a.mip_max, a.fused_max) <= 0 or a.axial_step < 0 or a.max_hotspots < 1 or a.min_voxels < 1:
        raise UsageError("Invalid PET rendering parameters")
    a.output.mkdir(parents=True, exist_ok=True)

    pt = Volume(series_by_number(a.study_dir, a.pt))
    pt.require_axial()
    factor, info = suv_factor(pt.ds[0], a.weight, datasets=pt.ds, uptake_window=cfg.uptake_window_min)
    (a.output / "suv_info.json").write_text(json.dumps(info, indent=2, default=str))
    progress("SUV: " + json.dumps(info, default=str))
    if factor is None:
        raise QuantitationError("cannot compute SUV factor: " + info.get("error", ""))
    ct = Volume(series_by_number(a.study_dir, a.ct))
    ct.require_axial()
    if pt.modality != "PT" or ct.modality != "CT":
        raise GeometryError("--pt must be a PT series and --ct a CT series")
    if pt.study_uid != ct.study_uid or not pt.frame_uid or pt.frame_uid != ct.frame_uid:
        raise GeometryError("PET/CT study or FrameOfReferenceUID mismatch; no fusion without registration")
    common_lo, common_hi = max(pt.z[0], ct.z[0]), min(pt.z[-1], ct.z[-1])
    if common_lo >= common_hi:
        raise GeometryError("PET/CT coverage does not overlap")
    progress(f"PT {pt.shape} px {pt.col_sp:.2f} dz {pt.dz:.2f} z {pt.z[0]:.0f}..{pt.z[-1]:.0f} | "
             f"CT {ct.shape} px {ct.col_sp:.3f} dz {ct.dz:.2f} z {ct.z[0]:.0f}..{ct.z[-1]:.0f}")
    suv = pt.vol * factor
    ct_bbox = (0, ct.shape[1], 0, ct.shape[2])
    written: List[Path] = []

    # ---- rotating MIP
    if not a.no_mip:
        tiles: List[Image.Image] = []
        aspect = abs(pt.dz) / pt.col_sp
        for deg in range(0, 360, 45):
            rv = rotate_axial(suv, deg) if deg else suv
            mip = np.flipud(rv.max(axis=1))  # project along y -> (z, x), superior up
            im = Image.fromarray(inv_gray(mip, a.mip_max))
            im = im.resize((int(im.width * 2), int(im.height * 2 * aspect)), Image.BICUBIC)
            label(im, f"MIP {deg} deg  (0=anterior view, SUV 0-{a.mip_max:g})", size=13)
            tiles.append(im)
        h = max(t.height for t in tiles)
        tiles = [t.resize((t.width, h)) if t.height != h else t for t in tiles]
        sheet = contact_sheet(tiles, 4)
        assert sheet is not None
        if sheet.width > 3000:
            sheet = sheet.resize((sheet.width // 2, sheet.height // 2), Image.LANCZOS)
        sheet.save(a.output / "mip_rot.png")
        written.append(a.output / "mip_rot.png")
        ny = suv.shape[1]
        occ = np.where((suv > 0.5).any(axis=(0, 2)))[0]
        y0, y1 = (occ[0], occ[-1]) if len(occ) else (0, ny - 1)
        tiles = []
        for y in np.linspace(y0, y1, 12).astype(int):
            sl = np.flipud(suv[:, y, :])
            im = Image.fromarray(inv_gray(sl, a.mip_max))
            im = im.resize((int(im.width * 2), int(im.height * 2 * aspect)), Image.BICUBIC)
            label(im, f"cor y={y} ({(y - ny / 2) * pt.row_sp:+.0f}mm)", size=13)
            tiles.append(im)
        sheet = contact_sheet(tiles, 6)
        assert sheet is not None
        sheet.save(a.output / "coronal_slices.png")
        written.append(a.output / "coronal_slices.png")

    # ---- native PET coverage
    if not a.no_native:
        written += render_pet_native(pt, suv, a.output, a.mip_max, a.grid, max_side)

    # ---- hotspots
    mask = suv >= a.suv_thr
    comps = [c for c in components(mask) if len(c) >= a.min_voxels]
    vox_ml = pt.voxel_volume_mm3 / 1000.0
    rows: List[Dict[str, Any]] = []
    for c in comps:
        arr = np.array(c)
        vals = suv[arr[:, 0], arr[:, 1], arr[:, 2]]
        imax = int(np.argmax(vals))
        z, y, x = arr[imax]
        x_mm = pt.ipp0[0] + x * pt.col_sp
        rows.append({"suv_max": round(float(vals.max()), 2), "suv_mean": round(float(vals.mean()), 2),
                     "voxels": len(c), "volume_ml": round(len(c) * vox_ml, 1),
                     "z_mm": round(float(pt.z[z]), 1),
                     "z_extent_mm": round(float((arr[:, 0].max() - arr[:, 0].min() + 1) * abs(pt.dz)), 1),
                     "pt_row_col": [int(y), int(x)], "x_mm": round(x_mm, 1),
                     "y_mm": round(float(pt.ds[z].ImagePositionPatient[1]) + y * pt.row_sp, 1),
                     "side": "R" if x_mm < 0 else "L",
                     "ct_instance": ct.instance(ct.index_of_z(pt.z[z])) if common_lo <= pt.z[z] <= common_hi else None,
                     "pt_instance": pt.instance(z), "pt_sop_uid": pt.sop_uid(z)})
    rows.sort(key=lambda r: -r["suv_max"])
    (a.output / "hotspots.json").write_text(json.dumps(rows, indent=2))
    md = [f"# Hotspots SUVbw >= {a.suv_thr} (weight {info['weight_kg']} kg, {info['weight_source']}; uptake {info.get('uptake_time_min')} min)",
          f"All {len(rows)} components listed; only the first {a.max_hotspots} receive automatic zooms. "
          "A threshold list is not exhaustive lesion detection; sub-threshold and sub-resolution lesions need the CT read.", "",
          "| # | SUVmax | SUVmean | vol mL | z mm | z-ext mm | side | x mm | y mm | CT img | PT img |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        md.append(f"| {i} | {r['suv_max']} | {r['suv_mean']} | {r['volume_ml']} | {r['z_mm']} | {r['z_extent_mm']} | {r['side']} | "
                  f"{r['x_mm']} | {r['y_mm']} | {r['ct_instance']} | {r['pt_instance']} |")
    (a.output / "hotspots.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))

    # ---- per-hotspot fused tiles
    for i, r in enumerate(rows[:a.max_hotspots], 1):
        tiles = []
        for dz in (abs(pt.dz) * 2, 0, -abs(pt.dz) * 2):
            if not common_lo <= r["z_mm"] + dz <= common_hi:
                continue
            t, _ = fused_tiles(pt, ct, r["z_mm"] + dz, factor, ct_bbox, a.fused_max, note=f"hot#{i} SUVmax {r['suv_max']}")
            tiles += t
        if tiles:
            written.append(save_page(tiles, 3, a.output / f"hot_{i:02d}_z{int(r['z_mm'])}_suv{r['suv_max']:.1f}.png"))
    if a.at:
        for z in a.at:
            t, m = fused_tiles(pt, ct, z, factor, ct_bbox, a.fused_max)
            written.append(save_page(t, 3, a.output / f"at_z{int(z)}.png"))
            progress(f"at z {z}: SUVmax in slice {m:.2f}")
    if a.axial_step:
        zs = np.arange(min(a.zmax, common_hi) if a.zmax is not None else common_hi,
                       (max(a.zmin, common_lo) if a.zmin is not None else common_lo) - 0.01, -a.axial_step)
        tiles = []
        page = 0
        for z in zs:
            t, _ = fused_tiles(pt, ct, z, factor, ct_bbox, a.fused_max)
            tiles.append(t[2])
            if len(tiles) == 4:
                page += 1
                written.append(save_page(tiles, 2, a.output / f"axial_fused_{page:02d}.png"))
                tiles = []
        if tiles:
            page += 1
            written.append(save_page(tiles, 2, a.output / f"axial_fused_{page:02d}.png"))
        progress(f"{page} fused axial sheets")
    for p in written:
        print(p)
    progress(f"[done] -> {a.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
