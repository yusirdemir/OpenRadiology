"""Contact-sheet grid geometry for vision models.

A sheet is ``cols x rows`` tiles plus ``pad`` pixels between tiles. The rule
that never bends: **a native slice is never down-sampled**. If the requested
grid does not fit the model's longest-edge budget (``max_side``), the grid is
reduced (``auto``) or the request is refused with the largest grid that fits.

Typical budgets and layouts for a 512 x 512 matrix (pad 2):

| max_side | grid | sheet px | use |
|---|---|---|---|
| 1568 (Claude) | 3x3 | 1542 | fast systematic scan, 9 slices/page |
| 1568 | 2x2 | 1028 | detail mode, 4 slices/page |
| 2048 (GPT) | 4x4 | 2056 -> does not fit; 3x3 or 4x3 | |
| 3072 (Gemini) | 6x6 | 3084 -> 5x5 = 2570 fits | |

Labels scale with tile size: font = clamp(tile_px / 32, 11, 22); the scale
bar picks 10, 20, 50 or 100 mm so that it spans roughly a fifth of the tile.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, TypeVar

from .errors import UsageError

T = TypeVar("T")


@dataclass(frozen=True)
class GridSpec:
    cols: int
    rows: int
    max_side: int = 1568
    pad: int = 2

    @property
    def per_page(self) -> int:
        return self.cols * self.rows

    def sheet_size(self, tile_w: int, tile_h: int) -> Tuple[int, int]:
        return self.cols * (tile_w + self.pad), self.rows * (tile_h + self.pad)

    def fits(self, tile_w: int, tile_h: int) -> bool:
        w, h = self.sheet_size(tile_w, tile_h)
        return max(w, h) <= self.max_side

    def label(self) -> str:
        return f"{self.cols}x{self.rows}"


def parse_grid(spec: str) -> Optional[Tuple[int, int]]:
    """'3x3' -> (3, 3); 'auto' -> None."""
    s = str(spec).strip().lower()
    if s in ("auto", ""):
        return None
    try:
        c, r = s.split("x")
        cols, rows = int(c), int(r)
    except ValueError as e:
        raise UsageError(f"grid must be COLSxROWS or auto, got '{spec}'") from e
    if cols < 1 or rows < 1 or cols * rows > 144:
        raise UsageError(f"grid {spec} out of range (1..144 tiles)")
    return cols, rows


def largest_fitting(tile_w: int, tile_h: int, max_side: int, pad: int = 2, min_cols: int = 1) -> GridSpec:
    cols = max(min_cols, max_side // (tile_w + pad))
    rows = max(1, max_side // (tile_h + pad))
    return GridSpec(cols, rows, max_side, pad)


def fit_grid(tile_w: int, tile_h: int, grid: str = "2x2", max_side: int = 1568, pad: int = 2,
             strict: bool = False, min_cols: int = 1) -> GridSpec:
    """Choose the grid for tiles of the given native size.

    ``grid`` = 'COLSxROWS' or 'auto'. With ``strict`` a non-fitting explicit
    grid raises; otherwise it is reduced to the largest fitting grid and the
    caller may report the change. Tiles are never scaled to make a grid fit.
    """
    if tile_w + pad > max_side or tile_h + pad > max_side:
        raise UsageError(f"a single {tile_w}x{tile_h} tile exceeds max_side {max_side}; raise render.max_side, "
                         "the engine never down-samples native slices")
    wanted = parse_grid(grid)
    biggest = largest_fitting(tile_w, tile_h, max_side, pad, min_cols)
    if wanted is None:
        return biggest
    cols, rows = wanted
    spec = GridSpec(cols, rows, max_side, pad)
    if spec.fits(tile_w, tile_h):
        return spec
    if strict:
        raise UsageError(f"grid {grid} does not fit {tile_w}x{tile_h} tiles within {max_side} px; "
                         f"largest fitting grid is {biggest.label()}")
    return GridSpec(min(cols, biggest.cols), min(rows, biggest.rows), max_side, pad)


def font_size_for(tile_px: int, lo: int = 11, hi: int = 22) -> int:
    """Readable label size for a tile: about 1/32 of the tile side."""
    return int(max(lo, min(hi, round(tile_px / 32))))


def scale_bar_mm(tile_width_mm: float, fraction: float = 0.2) -> float:
    """Nice scale-bar length (10/20/50/100/200 mm) spanning about ``fraction`` of the tile."""
    target = tile_width_mm * fraction
    for candidate in (10.0, 20.0, 50.0, 100.0, 200.0):
        if candidate >= target:
            return candidate
    return 200.0


def paginate(items: Sequence[T], per_page: int) -> Iterable[List[T]]:
    per_page = max(1, per_page)
    for i in range(0, len(items), per_page):
        yield list(items[i:i + per_page])


def page_count(n: int, per_page: int) -> int:
    return math.ceil(n / max(1, per_page))
