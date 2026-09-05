"""Render contact sheets for MR series (any plane) with per-series percentile windowing.

    openrad mr-render STUDY --output OUT                        # every series, <= --max-tiles per series
    openrad mr-render STUDY --output OUT --series 6 --step 1    # one series, every slice
    openrad mr-render STUDY --output OUT --pair 6 16 --step 3   # side by side by matched position (pre/post T1)
    openrad mr-render STUDY --output OUT --series 3 --invert    # inverted grey for DWI review

Tiles keep native pixel size (or scale to ``--tile`` px). Labels: series,
sequence guess, instance, position (mm along the slice normal). Orientation
letters come from the direction cosines. One window per series (not per
slice) so that enhancement is not mimicked by normalisation. Mixed echo /
b-value / temporal stacks are split explicitly (``split_mr_stacks``).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image

from .config import Settings, load_settings
from .dcmlib import (Volume, group_series, label, mark_source, orientation_marks, read_headers, save_page,
                     series_by_number, split_mr_stacks, window)
from .dicom_inventory import mr_guess
from .errors import GeometryError, InputError, UsageError
from .grid import fit_grid, font_size_for
from .log import progress


def tiles_for(v: Volume, step: int, tile_px: int, invert: bool, tag: str, lo: float, hi: float) -> List[Image.Image]:
    ks = list(range(0, len(v.z), max(1, step)))
    if v.plane == "AX":
        ks = ks[::-1]  # superior -> inferior
    out: List[Image.Image] = []
    low, high = np.percentile(v.vol, [lo, hi])
    for k in ks:
        arr8 = window(v.vol[k], (low + high) / 2, max(high - low, 1e-6),
                      str(v.ds[k].get("PhotometricInterpretation")) == "MONOCHROME1")
        if invert:
            arr8 = 255 - arr8
        im = Image.fromarray(arr8)
        if tile_px and max(im.size) != tile_px:
            s = tile_px / max(im.size)
            im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
        size = font_size_for(min(im.width, im.height))
        label(im, f"{tag} img {v.instance(k)} pos {v.z[k]:.0f}", size=size)
        orientation_marks(im, size=size, iop=v.iop)
        mark_source(im, v, k, "mr:native")
        out.append(im)
    return out


def write_sheets(tiles: List[Image.Image], out: Path, prefix: str, max_side: int = 1568, grid: str = "2x2") -> List[Path]:
    if not tiles:
        return []
    w = max(t.width for t in tiles)
    h = max(t.height for t in tiles)
    fixed: List[Image.Image] = []
    for t in tiles:
        if t.size != (w, h):
            r = t.resize((w, h), Image.LANCZOS)
            r.info.update(t.info)
            t = r
        fixed.append(t)
    spec = fit_grid(w, h, grid, max_side)
    per = spec.per_page
    files: List[Path] = []
    for i in range(0, len(fixed), per):
        files.append(save_page(fixed[i:i + per], spec.cols, out / f"{prefix}_{i // per + 1:02d}.png"))
    return files


def build_parser(cfg: Optional[Settings] = None) -> argparse.ArgumentParser:
    from .ct_render import add_grid_arguments
    cfg = cfg or load_settings()
    ap = argparse.ArgumentParser(prog="openrad mr-render", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--series", nargs="*", help="SeriesNumbers/UIDs (default all)")
    ap.add_argument("--pair", nargs=2, metavar="SER", help="two series to show side by side by matched position")
    ap.add_argument("--step", type=int, default=1, help="slice step; 0 = overview only, not diagnostic coverage")
    ap.add_argument("--max-tiles", type=int, default=24)
    ap.add_argument("--tile", type=int, default=0, help="tile long side px (0 = native)")
    ap.add_argument("--invert", action="store_true")
    ap.add_argument("--pct", default="0.5,99.5", help="percentile window lo,hi")
    add_grid_arguments(ap, cfg)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .ct_render import resolve_max_side
    cfg = load_settings()
    a = build_parser(cfg).parse_args(argv)
    max_side = resolve_max_side(a, cfg)
    if a.step < 0 or a.max_tiles < 2 or a.tile < 0:
        raise UsageError("Invalid rendering parameters")
    try:
        lo, hi = [float(x) for x in a.pct.split(",")]
    except ValueError as e:
        raise UsageError("--pct must be lo,hi") from e
    a.output.mkdir(parents=True, exist_ok=True)
    headers = read_headers(a.study_dir)
    if not headers:
        raise InputError(f"No DICOM files under {a.study_dir}")
    groups = group_series(headers)
    by_num = {str(items[0][1].get("SeriesNumber", "?")): str(items[0][1].get("SeriesInstanceUID")) for items in groups.values()}
    written: List[Path] = []

    if a.pair:
        va, vb = [Volume(series_by_number(a.study_dir, s)) for s in a.pair]
        if (not va.frame_uid or va.frame_uid != vb.frame_uid or not np.allclose(va.iop, vb.iop, atol=1e-4)
                or va.shape[1:] != vb.shape[1:] or not np.allclose([va.row_sp, va.col_sp], [vb.row_sp, vb.col_sp])):
            raise GeometryError("Pre/post pair geometry differs; registration is required, no automatic matching")
        step = a.step or max(1, len(va.z) // (a.max_tiles // 2))
        ta = tiles_for(va, step, a.tile or 0, a.invert, f"S{va.series_number} {mr_guess(va.ds[0])[:22]}", lo, hi)
        ks = list(range(0, len(va.z), step))
        if va.plane == "AX":
            ks = ks[::-1]
        tiles: List[Image.Image] = []
        blo, bhi = np.percentile(vb.vol, [lo, hi])
        for im_a, k in zip(ta, ks):
            kb = vb.index_of_z(va.z[k])
            if np.linalg.norm(va.patient_point(k, 0, 0) - vb.patient_point(kb, 0, 0)) > max(0.5, vb.dz / 2):
                raise GeometryError("Pre/post pair origins do not align; registration required")
            arr8 = window(vb.vol[kb], (blo + bhi) / 2, max(bhi - blo, 1e-6))
            im_b = Image.fromarray(255 - arr8 if a.invert else arr8)
            if im_b.size != im_a.size:
                im_b = im_b.resize(im_a.size, Image.LANCZOS)
            label(im_b, f"S{vb.series_number} {mr_guess(vb.ds[0])[:22]} img {vb.instance(kb)} pos {vb.z[kb]:.0f}")
            orientation_marks(im_b, iop=vb.iop)
            mark_source(im_b, vb, kb, "mr:pair")
            tiles += [im_a, im_b]
        written += write_sheets(tiles, a.output, f"pair_S{va.series_number}_S{vb.series_number}", max_side, a.grid)
        for p in written:
            print(p)
        return 0

    selected = a.series or sorted(by_num, key=lambda s: int(s) if s.isdigit() else 9999)
    for num in selected:
        items = series_by_number(a.study_dir, num)
        stacks = split_mr_stacks(items)
        for stack_index, stack in enumerate(stacks, 1):
            v = Volume(stack)
            step = a.step or max(1, int(np.ceil(len(v.z) / a.max_tiles)))
            guess = mr_guess(v.ds[0])
            suffix = f"_stack{stack_index}" if len(stacks) > 1 else ""
            tiles = tiles_for(v, step, a.tile, a.invert, f"S{v.series_number}{suffix} {v.plane} {guess[:26]}", lo, hi)
            safe = guess.split(" [")[0].replace(" ", "").replace("/", "-")[:18]
            files = write_sheets(tiles, a.output, f"S{v.series_number}{suffix}_{v.plane}_{safe}", max_side, a.grid)
            progress(f"S{v.series_number}{suffix} {v.plane} n={len(v.z)} step={step} {guess} -> {len(files)} sheet(s)")
            written += files
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
