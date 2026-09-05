"""Resident study and volume caches.

The desktop application's whole performance argument rests here. A decoded
series (``dcmlib.Volume.vol``) is a float32 array of roughly 400 x 512 x 512 for
a thoracic CT -- about 420 MB. Re-decoding it per request, the way a
command-per-click design would, makes fluid scrolling impossible. So the server
decodes once and keeps the volume resident under an LRU byte budget.

Two caches, two lifetimes:

``HeaderCache``
    ``(path, dataset)`` pairs with pixels skipped. Cheap, invalidated by a
    directory signature (file count plus modification times), so a study that
    grows on disk is picked up without a restart.

``VolumeCache``
    Fully decoded volumes, keyed by study path and SeriesInstanceUID. Eviction
    is least-recently-used against a configurable byte budget. Decoding is
    guarded per key so that a burst of slice requests for a cold series
    decodes it exactly once instead of once per request.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..dcmlib import Header, Volume, group_series, iter_dicom_files, plane_of, read_headers
from ..errors import InputError, UsageError

log = logging.getLogger("openrad.server.volumes")

DEFAULT_BUDGET_BYTES = 4 * 1024 ** 3
PLANES = ("ax", "cor", "sag")


def _signature(root: Path) -> Tuple[int, int, int]:
    """Cheap fingerprint of a study folder: file count, size sum, newest mtime."""
    count = total = newest = 0
    for path in iter_dicom_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        count += 1
        total += st.st_size
        newest = max(newest, st.st_mtime_ns)
    return count, total, newest


class HeaderCache:
    """Pixel-free headers per study folder, invalidated by directory signature."""

    def __init__(self) -> None:
        self._entries: Dict[str, Tuple[Tuple[int, int, int], List[Header]]] = {}
        self._lock = threading.Lock()

    def get(self, root: Path) -> List[Header]:
        root = root.resolve()
        key = str(root)
        signature = _signature(root)
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and hit[0] == signature:
                return hit[1]
        if signature[0] == 0:
            raise InputError(f"No DICOM files found under {root}")
        headers = read_headers(root)
        if not headers:
            raise InputError(f"No DICOM files found under {root}")
        with self._lock:
            self._entries[key] = (signature, headers)
        return headers

    def invalidate(self, root: Optional[Path] = None) -> None:
        with self._lock:
            if root is None:
                self._entries.clear()
            else:
                self._entries.pop(str(Path(root).resolve()), None)


def select_series(headers: List[Header], series: str) -> Tuple[str, List[Header]]:
    """Resolve a SeriesInstanceUID or an unambiguous SeriesNumber to its headers.

    Mirrors :func:`openrad.dcmlib.series_by_number` -- UID first, then number,
    and an ambiguous number is an error rather than a guess -- but works off
    cached headers instead of re-reading the folder.
    """
    wanted = str(series)
    items = [(p, d) for p, d in headers if str(d.get("SeriesInstanceUID", "")) == wanted]
    if not items:
        items = [(p, d) for p, d in headers if str(d.get("SeriesNumber", "")) == wanted]
    if not items:
        raise InputError(f"Series {series} not found in this study")
    groups = group_series(items)
    if len(groups) != 1:
        raise InputError(f"Ambiguous SeriesNumber {series}; use the SeriesInstanceUID")
    return next(iter(groups)), items


class VolumeCache:
    """LRU cache of decoded volumes with a byte budget."""

    def __init__(self, headers: HeaderCache, budget_bytes: int = DEFAULT_BUDGET_BYTES) -> None:
        self._headers = headers
        self.budget = max(budget_bytes, 256 * 1024 ** 2)
        self._items: OrderedDict[Tuple[str, str, bool], Volume] = OrderedDict()
        self._lock = threading.Lock()
        self._loading: Dict[Tuple[str, str, bool], threading.Lock] = {}

    # -- introspection -----------------------------------------------------
    @property
    def bytes_used(self) -> int:
        with self._lock:
            return sum(int(v.vol.nbytes) for v in self._items.values())

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            entries = [{"study": key[0], "series_uid": key[1], "bytes": int(vol.vol.nbytes),
                        "shape": list(vol.vol.shape)} for key, vol in self._items.items()]
        return {"budget_bytes": self.budget, "used_bytes": sum(e["bytes"] for e in entries), "entries": entries}

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # -- access ------------------------------------------------------------
    def get(self, study_dir: Path, series: str, allow_tilt: bool = False) -> Volume:
        study_dir = Path(study_dir).resolve()
        headers = self._headers.get(study_dir)
        series_uid, items = select_series(headers, series)
        key = (str(study_dir), series_uid, allow_tilt)
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return hit
            gate = self._loading.setdefault(key, threading.Lock())
        with gate:
            with self._lock:
                hit = self._items.get(key)
                if hit is not None:
                    self._items.move_to_end(key)
                    return hit
            log.info("decoding series %s (%d instances)", series_uid, len(items))
            volume = Volume(items, allow_tilt=allow_tilt)
            with self._lock:
                self._items[key] = volume
                self._items.move_to_end(key)
                self._evict_locked(protect=key)
                self._loading.pop(key, None)
            return volume

    def peek(self, study_dir: Path, series_uid: str, allow_tilt: bool = False) -> Optional[Volume]:
        with self._lock:
            return self._items.get((str(Path(study_dir).resolve()), series_uid, allow_tilt))

    def _evict_locked(self, protect: Tuple[str, str, bool]) -> None:
        used = sum(int(v.vol.nbytes) for v in self._items.values())
        while used > self.budget and len(self._items) > 1:
            key, volume = next(iter(self._items.items()))
            if key == protect:
                break
            self._items.pop(key)
            used -= int(volume.vol.nbytes)
            log.info("evicted series %s from the volume cache", key[1])


# --------------------------------------------------------------------- planes
def _half_slices(volume: Volume, mip_mm: float) -> int:
    if mip_mm <= 0:
        return 0
    return max(0, int(round(mip_mm / (2 * abs(volume.dz)))))


def plane_length(volume: Volume, plane: str) -> int:
    """Number of addressable images in a plane."""
    z, rows, cols = volume.vol.shape
    return {"ax": z, "cor": rows, "sag": cols}[plane]


def extract_plane(volume: Volume, plane: str, index: int, mip_mm: float = 0.0,
                  thick_px: int = 1) -> Tuple[np.ndarray, Dict[str, Any]]:
    """One image out of the volume plus the metadata a viewer needs to be honest.

    ``mm_per_px`` is ``[vertical, horizontal]`` for the returned image, so the
    frontend can apply the correct anisotropic aspect for reformats instead of
    inventing one.
    """
    if plane not in PLANES:
        raise UsageError(f"plane must be one of {', '.join(PLANES)}")
    limit = plane_length(volume, plane)
    if not 0 <= index < limit:
        raise UsageError(f"index {index} outside 0..{limit - 1} for plane {plane}")
    meta: Dict[str, Any] = {"plane": plane, "index": index, "count": limit,
                            "series_uid": volume.series_uid, "study_uid": volume.study_uid}
    if plane == "ax":
        half = _half_slices(volume, mip_mm)
        image = volume.slab_mip(index, half) if half > 0 else volume.vol[index]
        meta.update(sop_uid=volume.sop_uid(index), instance=volume.instance(index),
                    z_mm=float(volume.z[index]), mm_per_px=[volume.row_sp, volume.col_sp],
                    mip_mm=float(mip_mm) if half > 0 else 0.0)
    elif plane == "cor":
        image = volume.coronal(index, thick=max(1, thick_px))
        meta.update(sop_uid="", instance="", z_mm=None,
                    mm_per_px=[abs(volume.dz), volume.col_sp], mip_mm=0.0)
    else:
        image = volume.sagittal(index, thick=max(1, thick_px))
        meta.update(sop_uid="", instance="", z_mm=None,
                    mm_per_px=[abs(volume.dz), volume.row_sp], mip_mm=0.0)
    array = np.ascontiguousarray(image, dtype=np.float32)
    meta["shape"] = [int(array.shape[0]), int(array.shape[1])]
    return array, meta


def suggested_windows(volume: Volume) -> List[Dict[str, Any]]:
    """Window presets that make sense for this modality, best first."""
    from ..dcmlib import WINDOWS

    if volume.modality == "CT":
        names = ["lung", "soft", "bone", "liver", "brain"]
    elif volume.modality == "MR":
        names = []
    else:
        names = []
    out = [{"name": n, "center": WINDOWS[n][0], "width": WINDOWS[n][1]} for n in names if n in WINDOWS]
    lo, hi = float(np.percentile(volume.vol[::max(1, len(volume.z) // 8) or 1], 0.5)), \
        float(np.percentile(volume.vol[::max(1, len(volume.z) // 8) or 1], 99.5))
    if hi > lo:
        out.append({"name": "auto", "center": round((lo + hi) / 2, 2), "width": round(hi - lo, 2)})
    return out


def series_cards(headers: List[Header]) -> List[Dict[str, Any]]:
    """One row per series: enough to populate a picker without decoding pixels."""
    cards: List[Dict[str, Any]] = []
    for uid, items in group_series(headers).items():
        ds = items[0][1]
        rows, cols = int(ds.get("Rows", 0) or 0), int(ds.get("Columns", 0) or 0)
        spacing = ds.get("PixelSpacing", None)
        cards.append({
            "series_uid": uid,
            "number": str(ds.get("SeriesNumber", "")),
            "modality": str(ds.get("Modality", "")),
            "description": str(ds.get("SeriesDescription", "")),
            "instances": len(items),
            "rows": rows,
            "cols": cols,
            "pixel_spacing_mm": [float(spacing[0]), float(spacing[1])] if spacing is not None else None,
            "slice_thickness_mm": float(ds.get("SliceThickness", 0) or 0) or None,
            "plane": plane_of(ds),
            "kernel": str(ds.get("ConvolutionKernel", "")),
            "body_part": str(ds.get("BodyPartExamined", "")),
            "renderable": len(items) > 1 and rows > 0 and cols > 0,
        })
    cards.sort(key=lambda c: (c["modality"], int(c["number"]) if c["number"].isdigit() else 0))
    return cards


def wire_dtype(volume: Volume) -> str:
    """Whether this volume can travel losslessly as ``int16``.

    CT rescales to integral Hounsfield units, so it halves the bytes per slice.
    Anything else -- PET activity, MR signal, a scanner with a fractional
    RescaleSlope -- stays ``float32``. The test is exact and cheap: integral
    rescale coefficients guarantee integral values because the stored pixels are
    integers, and the range check is one pass over the array. Decided once per
    volume, because a viewer that silently quantised pixel values would be lying
    about what it shows, and the answer cannot change slice to slice.
    """
    decision = getattr(volume, "_wire_int16", None)
    if decision is None:
        d0 = volume.ds[0]
        try:
            slope = float(d0.get("RescaleSlope", 1) or 1)
            intercept = float(d0.get("RescaleIntercept", 0) or 0)
        except (TypeError, ValueError):
            slope, intercept = 0.5, 0.5
        integral = slope.is_integer() and intercept.is_integer()
        lo, hi = float(volume.vol.min()), float(volume.vol.max())
        decision = bool(integral and np.isfinite([lo, hi]).all() and lo >= -32768.0 and hi <= 32767.0)
        volume._wire_int16 = decision  # type: ignore[attr-defined]
    return "int16" if decision else "float32"


def encode_image(volume: Volume, array: np.ndarray) -> Tuple[bytes, str]:
    """Serialise one image for the wire, losslessly, in this volume's wire dtype."""
    dtype = wire_dtype(volume)
    code = "<i2" if dtype == "int16" else "<f4"
    return np.ascontiguousarray(array, dtype=code).tobytes(order="C"), dtype
