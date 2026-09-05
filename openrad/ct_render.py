"""Render systematic review sheets for one CT series.

    openrad ct-render STUDY --series 4 --output OUT --windows lung --step 1 --mip 10 --mpr
    openrad ct-render STUDY --series 3 --output OUT --windows soft,bone --grid 3x3
    openrad ct-render STUDY --series 4 --output OUT --windows lung --zmin -180 --zmax -120 --vision gpt

Design (see docs/references.md):
* Native slices at native pixel size. The grid (``--grid``, default from
  ``render.grid``) is fitted to the vision budget (``--max-side`` /
  ``--vision`` profile); tiles are never down-sampled, the grid shrinks instead.
  For a 512 matrix: 2x2 = detail mode (4 slices, 1028 px), 3x3 = fast systematic
  scan (9 slices, 1542 px) within a 1568 px budget.
* Sliding-slab MIP for nodule detection (8-10 mm optimal: Kawel 2009,
  Jankowski 2019); default ``render.mip_mm``, lung window only.
* Coronal/sagittal reformats with anisotropy-correct aspect, superior at top,
  a millimetre depth ruler and optional slab MIP thickness.
* Labels, orientation letters and scale bars scale with tile size. Every tile
  carries its SOPInstanceUID in ``render_index.json``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .config import VISION_PROFILES, Settings, load_settings
from .dcmlib import (WINDOWS, Volume, contact_sheet, label, mark_source, orientation_marks, save_page, scale_bar,
                     series_by_number, to_image, window, z_ruler)
from .errors import GeometryError, UsageError
from .grid import GridSpec, fit_grid, font_size_for
from .log import progress, warn

BBox = Tuple[int, int, int, int]


def render_axial(v: Volume, out: Path, prefix: str, wname: str, step_mm: float, bbox: BBox, mip_mm: float = 0,
                 zmin: Optional[float] = None, zmax: Optional[float] = None, grid: str = "2x2",
                 max_side: int = 1568) -> List[Path]:
    c, w = WINDOWS[wname]
    r0, r1, c0, c1 = bbox
    step = max(1, int(round(step_mm / abs(v.dz))))
    half = int(round((mip_mm / 2) / abs(v.dz))) if mip_mm else 0
    idx = list(range(0, len(v.z), step))
    if zmin is not None:
        idx = [k for k in idx if v.z[k] >= zmin]
    if zmax is not None:
        idx = [k for k in idx if v.z[k] <= zmax]
    idx = idx[::-1]  # superior -> inferior reading order (like scrolling down)
    tw, th = c1 - c0, r1 - r0
    spec = fit_grid(tw, th, grid, max_side)
    size = font_size_for(min(tw, th))
    tiles: List[Image.Image] = []
    pages = 0
    tag = f"{wname}" + (f"_mip{int(mip_mm)}mm" if mip_mm else "")
    files: List[Path] = []
    for k in idx:
        arr = v.slab_mip(k, half) if half else v.vol[k]
        im = to_image(window(arr[r0:r1, c0:c1], c, w))
        label(im, f"img {v.instance(k)}  z {v.z[k]:.0f}" + ("  MIP" if half else ""), size=size)
        orientation_marks(im, "AX", size=size, iop=v.iop)
        scale_bar(im, v.col_sp, size=size)
        mark_source(im, v, k, f"{wname}:mip" if half else f"{wname}:native")
        tiles.append(im)
        if len(tiles) == spec.per_page:
            pages += 1
            files.append(save_page(tiles, spec.cols, out / f"{prefix}_{tag}_axial_{pages:02d}.png"))
            tiles = []
    if tiles:
        pages += 1
        files.append(save_page(tiles, spec.cols, out / f"{prefix}_{tag}_axial_{pages:02d}.png"))
    return files


def render_mpr(v: Volume, out: Path, prefix: str, wname: str, bbox: BBox, n: int = 12, thick_mm: float = 0,
               grid: str = "2x2", max_side: int = 1568) -> List[Path]:
    c, w = WINDOWS[wname]
    r0, r1, c0, c1 = bbox
    files: List[Path] = []
    for kind in ("coronal", "sagittal"):
        aspect = v.mpr_aspect("SAG" if kind == "sagittal" else "COR")
        # slab thickness is measured along the axis being collapsed: rows for coronal, columns for sagittal
        sp = v.row_sp if kind == "coronal" else v.col_sp
        thick = max(1, int(round(thick_mm / sp))) if thick_mm else 1
        lo, hi = (r0, r1) if kind == "coronal" else (c0, c1)
        positions = np.linspace(lo + (hi - lo) * 0.06, hi - (hi - lo) * 0.06, n).astype(int)
        tiles: List[Image.Image] = []
        for pos in positions:
            arr = v.coronal(pos, thick) if kind == "coronal" else v.sagittal(pos, thick)
            arr = arr[:, c0:c1] if kind == "coronal" else arr[:, r0:r1]
            im = to_image(window(arr, c, w), scale_xy=(1.0, aspect))
            cap = max(700, max_side // 2)
            if im.height > cap:
                s = cap / im.height
                im = im.resize((max(1, int(im.width * s)), cap), Image.LANCZOS)
            size = font_size_for(min(im.width, im.height))
            mm = (pos - v.vol.shape[1] // 2) * v.row_sp if kind == "coronal" else (pos - v.vol.shape[2] // 2) * v.col_sp
            side = ("A" if mm < 0 else "P") if kind == "coronal" else ("R" if mm < 0 else "L")
            label(im, f"{kind[:3]} idx {pos} ({side}{abs(mm):.0f}mm)" + (f" slab {thick_mm:g}mm" if thick > 1 else ""), size=size)
            orientation_marks(im, size=size, iop=[1, 0, 0, 0, 0, -1] if kind == "coronal" else [0, 1, 0, 0, 0, -1])
            z_ruler(im, float(v.z[-1]), float(v.z[0]), size=max(9, size - 3))
            scale_bar(im, (v.col_sp if kind == "coronal" else v.row_sp) * (arr.shape[1] / im.width), size=size)
            tiles.append(im)
        tw, th = max(t.width for t in tiles), max(t.height for t in tiles)
        tiles = [t.resize((tw, th)) if t.size != (tw, th) else t for t in tiles]
        spec = fit_grid(tw, th, grid, max_side, min_cols=2)
        for i in range(0, len(tiles), spec.per_page):
            p = out / f"{prefix}_{wname}_{kind}_{i // spec.per_page + 1:02d}.png"
            sheet = contact_sheet(tiles[i:i + spec.per_page], spec.cols)
            assert sheet is not None
            sheet.save(p)
            files.append(p)
    return files


def render_regions(v: Volume, out: Path, wname: str, step_mm: float = 1, size: int = 256, overlap: int = 64) -> List[Path]:
    """Overlapping full-FOV crops at 2x, all slices; no anatomy segmentation claim."""
    center, width = WINDOWS[wname]
    step = max(1, int(round(step_mm / v.dz)))

    def starts(length: int) -> List[int]:
        return sorted(set(list(range(0, max(1, length - size + 1), size - overlap)) + [max(0, length - size)]))

    files: List[Path] = []
    for r in starts(v.shape[1]):
        for c in starts(v.shape[2]):
            tiles: List[Image.Image] = []
            for k in reversed(range(0, len(v.z), step)):
                arr = v.vol[k, r:r + size, c:c + size]
                im = to_image(window(arr, center, width), (2, 2))
                label(im, f"img {v.instance(k)} z {v.z[k]:.1f} r{r} c{c}")
                orientation_marks(im, iop=v.iop)
                mark_source(im, v, k, f"{wname}:region")
                tiles.append(im)
            for page in range(0, len(tiles), 4):
                files.append(save_page(tiles[page:page + 4], 2, out / f"S{v.series_number}_{wname}_region_r{r}_c{c}_{page // 4 + 1:03d}.png"))
    return files


def add_grid_arguments(ap: argparse.ArgumentParser, cfg: Settings) -> None:
    ap.add_argument("--grid", default=cfg.grid, help=f"tiles per sheet COLSxROWS or auto (default {cfg.grid})")
    ap.add_argument("--max-side", type=int, default=None, help=f"longest sheet edge in px (default {cfg.effective_max_side})")
    ap.add_argument("--vision", choices=sorted(VISION_PROFILES), default=None,
                    help="vision-model profile setting max-side: " + ", ".join(f"{k}={v}" for k, v in VISION_PROFILES.items()))


def resolve_max_side(a: argparse.Namespace, cfg: Settings) -> int:
    if a.max_side:
        return int(a.max_side)
    if a.vision:
        return VISION_PROFILES[a.vision]
    return cfg.effective_max_side


def build_parser(cfg: Optional[Settings] = None) -> argparse.ArgumentParser:
    cfg = cfg or load_settings()
    ap = argparse.ArgumentParser(prog="openrad ct-render", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--series", required=True, help="SeriesNumber or SeriesInstanceUID")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--windows", default="lung,soft,bone", help=f"comma list from {', '.join(WINDOWS)} plus render.windows presets")
    ap.add_argument("--step", type=float, default=cfg.step_mm, help="axial sampling step in mm; native spacing = exhaustive review")
    ap.add_argument("--regions", action="store_true", help="overlapping 256 px crops at 2x, four per page, full FOV")
    ap.add_argument("--mip", type=float, default=cfg.mip_mm, help="axial sliding-slab MIP thickness in mm (lung window only); 0 = off")
    ap.add_argument("--mpr", action="store_true", help="also render coronal/sagittal sheets")
    ap.add_argument("--mpr-n", type=int, default=12)
    ap.add_argument("--mpr-thick", type=float, default=0.0, help="MIP slab thickness (mm) for reformats")
    ap.add_argument("--zmin", type=float)
    ap.add_argument("--zmax", type=float)
    ap.add_argument("--no-axial", action="store_true")
    ap.add_argument("--allow-tilt", action="store_true", help="accept gantry-tilted stacks (reformats are de-sheared per slice)")
    add_grid_arguments(ap, cfg)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    cfg = load_settings()
    WINDOWS.update(cfg.windows)
    a = build_parser(cfg).parse_args(argv)
    if a.step <= 0 or a.mip < 0 or a.mpr_n < 1 or a.mpr_thick < 0:
        raise UsageError("Invalid sampling parameters")
    wnames = [w.strip() for w in a.windows.split(",") if w.strip()]
    for wname in wnames:
        if wname not in WINDOWS:
            raise UsageError(f"unknown window {wname}; choose from {list(WINDOWS)}")
    max_side = resolve_max_side(a, cfg)
    v = Volume(series_by_number(a.study_dir, a.series), allow_tilt=a.allow_tilt)
    if v.modality != "CT":
        raise GeometryError("CT renderer requires CT modality")
    v.require_axial()
    a.output.mkdir(parents=True, exist_ok=True)
    # Full FOV is the default: automatic body crops can remove chest wall/peripheral tissue.
    bbox: BBox = (0, v.vol.shape[1], 0, v.vol.shape[2])
    prefix = f"S{v.series_number}"
    spec: GridSpec = fit_grid(bbox[3] - bbox[2], bbox[1] - bbox[0], a.grid, max_side)
    progress(f"series {v.series_number}: shape {v.shape}, dz {v.dz:.2f} mm, px {v.row_sp:.3f}x{v.col_sp:.3f} mm, "
             f"z {v.z[0]:.1f}..{v.z[-1]:.1f}; grid {spec.label()} within {max_side} px")
    if spec.label() != a.grid and a.grid != "auto":
        warn(f"requested grid {a.grid} does not fit native {v.shape[2]}x{v.shape[1]} tiles in {max_side} px; using {spec.label()}")
    if v.is_tilted:
        warn(f"tilt {v.tilt_info()}")
    written: List[Path] = []
    for wname in wnames:
        if not a.no_axial:
            written += render_axial(v, a.output, prefix, wname, a.step, bbox, 0, a.zmin, a.zmax, a.grid, max_side)
            if a.mip and wname == "lung":
                written += render_axial(v, a.output, prefix, wname, max(a.step, a.mip / 2), bbox, a.mip, a.zmin, a.zmax, a.grid, max_side)
        if a.mpr:
            written += render_mpr(v, a.output, prefix, wname, bbox, a.mpr_n, a.mpr_thick, a.grid, max_side)
        if a.regions:
            written += render_regions(v, a.output, wname, a.step)
    for p in written:
        print(p)
    progress(f"[done] {len(written)} sheets -> {a.output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
