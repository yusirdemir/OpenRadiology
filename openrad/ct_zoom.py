"""Magnified view of a region across neighbouring slices, with pixel grid and scale bar.

    openrad zoom STUDY --series 4 --instance 212 --center 330,250 --size 120 --scale 4 \
        --window lung --context 3 --output OUT/zoom_rll_nodule.png
    openrad zoom STUDY --series 4 --z -152 ...                  # locate slice by z instead of instance
    openrad zoom STUDY --series 4 --instance 212 --center 330,250 --plane cor --output OUT/zoom_cor.png

``--center`` is ``row,col`` in native pixel coordinates of the FULL (uncropped)
image. ``--plane cor|sag`` renders a reformat through the centre, centred on
the chosen slice, with correct aspect and superior up; ``--context`` then
steps through neighbouring rows/cols. Use it to prove that a "nodule" is not a
vessel or heart contour in cross-section. Grid labels are native pixel
coordinates every 20 px so measurements can be read off and passed to
``openrad measure``. Tiles run superior -> inferior.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np
from PIL import Image, ImageDraw

from .dcmlib import WINDOWS, Volume, font, orientation_marks, series_by_number, window
from .errors import UsageError


def _sheet(tiles: List[Image.Image], output: Path) -> None:
    cols = min(len(tiles), max(1, 1568 // tiles[0].width))
    sheet = Image.new("RGB", (cols * (tiles[0].width + 2), ((len(tiles) + cols - 1) // cols) * (tiles[0].height + 2)), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * (t.width + 2), (i // cols) * (t.height + 2)))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)


def mpr_zoom(v: Volume, a: argparse.Namespace, k0: int, r: int, c: int, h: int, cen: float, wid: float) -> int:
    """Coronal/sagittal crops through (r,c) around slice k0."""
    v.require_rectilinear("reformat zoom")
    spacing = v.row_sp if a.plane == "sag" else v.col_sp
    aspect = abs(v.dz) / spacing
    half_z = int(round(h / aspect))
    z0, z1 = max(0, k0 - half_z), min(len(v.z), k0 + half_z)
    f = font(12)
    tiles: List[Image.Image] = []
    fixed = r if a.plane == "cor" else c
    idxs = [fixed + d * a.stride for d in range(-a.context, a.context + 1)]
    for pos in idxs:
        if a.plane == "cor":
            if not 0 <= pos < v.vol.shape[1]:
                continue
            arr = v.vol[z0:z1, pos, max(0, c - h):c + h]
            xlab = f"cols {max(0, c - h)}-{c + h}"
        else:
            if not 0 <= pos < v.vol.shape[2]:
                continue
            arr = v.vol[z0:z1, max(0, r - h):r + h, pos]
            xlab = f"rows {max(0, r - h)}-{r + h}"
        arr = arr[::-1]  # superior up
        im = Image.fromarray(window(arr, cen, wid)).resize(
            (arr.shape[1] * a.scale, int(arr.shape[0] * a.scale * aspect)), Image.BICUBIC).convert("RGB")
        d = ImageDraw.Draw(im)
        for zz in range(int(v.z[z0] // 10) * 10, int(v.z[z1 - 1]) + 1, 10):
            kk = v.index_of_z(zz)
            if z0 <= kk < z1:
                y = int((z1 - 1 - kk) * a.scale * aspect)
                d.line((0, y, 12, y), fill=(0, 90, 160), width=1)
                d.text((14, y - 6), f"z{zz}", fill=(120, 200, 255), font=f)
        orientation_marks(im, iop=[1, 0, 0, 0, 0, -1] if a.plane == "cor" else [0, 1, 0, 0, 0, -1])
        px = int(round(10.0 / spacing * a.scale))
        d.line((im.width - px - 10, im.height - 12, im.width - 10, im.height - 12), fill=(255, 255, 0), width=3)
        d.text((im.width - px - 10, im.height - 28), "10 mm", fill=(255, 255, 0), font=f)
        d.rectangle((0, 0, 230, 16), fill=(0, 0, 0))
        d.text((3, 2), f"{a.plane} {'row' if a.plane == 'cor' else 'col'} {pos} {xlab}" + ("  <==" if pos == fixed else ""),
               fill=(255, 255, 255), font=f)
        tiles.append(im)
    if not tiles:
        raise UsageError("No reformat tiles inside the image")
    _sheet(tiles, a.output)
    print(f"{a.plane} through ({r},{c}) z {v.z[z0]:.0f}..{v.z[z1 - 1]:.0f}, {len(tiles)} tiles", file=sys.stderr)
    print(a.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openrad zoom", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--series", required=True)
    ap.add_argument("--instance")
    ap.add_argument("--z", type=float)
    ap.add_argument("--center", required=True, help="row,col (native pixels)")
    ap.add_argument("--size", type=int, default=120, help="crop side length in native pixels")
    ap.add_argument("--scale", type=int, default=4)
    ap.add_argument("--window", default="lung", help=f"preset ({', '.join(WINDOWS)}) or 'center,width'")
    ap.add_argument("--context", type=int, default=2, help="slices before/after")
    ap.add_argument("--stride", type=int, default=1, help="slice stride for context")
    ap.add_argument("--grid", type=int, default=20, help="grid spacing in native px (0=off)")
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--pct", default="0.5,99.5", help="MR only: percentile window lo,hi over the whole series (default 0.5,99.5)")
    ap.add_argument("--plane", default="ax", choices=["ax", "cor", "sag"])
    ap.add_argument("--allow-tilt", action="store_true", help="accept gantry-tilted stacks (axial zoom only)")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.size < 2 or a.scale < 1 or a.context < 0 or a.stride < 1 or a.grid < 0:
        raise UsageError("Invalid zoom parameters")
    v = Volume(series_by_number(a.study_dir, a.series), allow_tilt=a.allow_tilt)
    if a.plane != "ax":
        v.require_axial()          # reformats need the canonical LPS stack; an in-plane crop does not
    if a.instance is not None:
        k0 = v.index_of_instance(a.instance)
    elif a.z is not None:
        k0 = v.index_of_z(a.z)
    else:
        raise UsageError("give --instance or --z")
    try:
        r, c = [int(x) for x in a.center.split(",")]
    except ValueError as e:
        raise UsageError("--center must be row,col integers") from e
    if not (0 <= r < v.shape[1] and 0 <= c < v.shape[2]):
        raise UsageError("Centre outside image")
    h = a.size // 2
    r0, r1 = max(0, r - h), min(v.vol.shape[1], r + h)
    c0, c1 = max(0, c - h), min(v.vol.shape[2], c + h)
    if a.window in WINDOWS and v.modality != "MR":
        cen, wid = WINDOWS[a.window]
    elif a.window in WINDOWS or a.window == "auto":
        # MR signal is not HU: window the whole series once by percentiles (as mr-render does),
        # so a zoom never invents contrast that the systematic sheets did not show.
        try:
            plo, phi = [float(x) for x in a.pct.split(",")]
        except ValueError as e:
            raise UsageError("--pct must be lo,hi") from e
        lo, hi = np.percentile(v.vol, [plo, phi])
        cen, wid = float((lo + hi) / 2), float(max(hi - lo, 1.0))
    else:
        try:
            cen, wid = [float(x) for x in a.window.split(",")]
        except ValueError as e:
            raise UsageError("--window must be a preset name, 'auto' or 'center,width'") from e
    if a.plane != "ax":
        return mpr_zoom(v, a, k0, r, c, h, cen, wid)
    ks = [k for k in range(k0 - a.context * a.stride, k0 + a.context * a.stride + 1, a.stride) if 0 <= k < len(v.z)][::-1]
    tiles: List[Image.Image] = []
    f = font(12)
    for k in ks:
        im = Image.fromarray(window(v.vol[k, r0:r1, c0:c1], cen, wid)).resize(
            ((c1 - c0) * a.scale, (r1 - r0) * a.scale), Image.NEAREST if a.scale >= 6 else Image.BICUBIC).convert("RGB")
        d = ImageDraw.Draw(im)
        orientation_marks(im, iop=v.iop)
        if a.grid:
            for rr in range((r0 // a.grid + 1) * a.grid, r1, a.grid):
                y = (rr - r0) * a.scale
                d.line((0, y, im.width, y), fill=(0, 90, 160), width=1)
                d.text((2, y + 1), str(rr), fill=(120, 200, 255), font=f)
            for cc in range((c0 // a.grid + 1) * a.grid, c1, a.grid):
                x = (cc - c0) * a.scale
                d.line((x, 0, x, im.height), fill=(0, 90, 160), width=1)
                d.text((x + 2, 2), str(cc), fill=(120, 200, 255), font=f)
        px = int(round(10.0 / v.col_sp * a.scale))
        d.line((im.width - px - 10, im.height - 12, im.width - 10, im.height - 12), fill=(255, 255, 0), width=3)
        d.text((im.width - px - 10, im.height - 28), "10 mm", fill=(255, 255, 0), font=f)
        d.rectangle((0, im.height - 18, 150, im.height), fill=(0, 0, 0))
        d.text((3, im.height - 16), f"img {v.instance(k)} z {v.z[k]:.1f}" + ("  <==" if k == k0 else ""), fill=(255, 255, 255), font=f)
        tiles.append(im)
    _sheet(tiles, a.output)
    print(f"crop rows {r0}-{r1} cols {c0}-{c1}, px {v.col_sp:.3f} mm, 1 native px = {a.scale} screen px; "
          f"centre SOP {v.sop_uid(k0)}", file=sys.stderr)
    print(a.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
