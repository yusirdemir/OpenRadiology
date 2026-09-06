"""Bounded process isolation and versioned cache for segmentation jobs."""

from __future__ import annotations

import base64
import multiprocessing
import threading
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Dict

import numpy as np
from scipy import ndimage

from ..segmentation import prepare, solve
from .httpd import HttpError


class SegmentationService:
    def __init__(self) -> None:
        self._pool: Any = None
        self._lock = threading.Lock()
        self._jobs: Any = OrderedDict()
        self._slots = threading.BoundedSemaphore(2)

    def run(self, volume: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
        seed = [int(payload.get(k, 0)) for k in ("index", "row", "col")]
        profile = str(payload.get("profile", "auto"))
        positive, negative = payload.get("positive", []), payload.get("negative", [])
        if len(positive) + len(negative) > 256:
            raise HttpError(400, "En fazla 256 düzeltme işareti kullanılabilir")
        radius = float(payload.get("brush_mm", 1.5))
        key = (id(volume), volume.series_uid, repr((seed, profile, positive, negative, radius)))
        with self._lock:
            future = self._jobs.get(key)
            if future is None:
                if not self._slots.acquire(blocking=False):
                    raise HttpError(429, "Segmentasyon işçisi meşgul; kısa süre sonra yeniden deneyin")
                try:
                    data = prepare(volume, seed, profile, positive, negative, radius)
                    if self._pool is None:
                        self._pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
                    future = self._pool.submit(solve, data)
                    future.add_done_callback(lambda _: self._slots.release())
                    self._jobs[key] = future
                    while len(self._jobs) > 8:
                        self._jobs.popitem(last=False)
                except BaseException:
                    self._slots.release()
                    raise
        try:
            return future.result(timeout=180)
        except BaseException:
            with self._lock:
                self._jobs.pop(key, None)
            raise

    def shutdown(self) -> None:
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)


def volume_preview(volume: Any) -> Dict[str, Any]:
    """Low-resolution CT context, with exact native-grid mapping.

    The lesion uses a separate native resolution texture; this volume is only
    translucent anatomical context. Cell-centre aligned resampling is explicit.
    """
    volume.require_rectilinear("3D volume preview")
    volume.require_axial()
    shape = np.array(volume.vol.shape)
    target = np.maximum(2, np.minimum(shape, 128))
    coords = [np.linspace(0, n - 1, int(t)) for n, t in zip(shape, target)]
    grid = np.meshgrid(*coords, indexing="ij")
    context = ndimage.map_coordinates(volume.vol, grid, order=1, mode="nearest", prefilter=False)
    hu = np.rint(np.nan_to_num(context, nan=-1024)).clip(-32768, 32767).astype("<i2")
    return {
        "shape_zyx": target.tolist(),
        "native_shape_zyx": shape.tolist(),
        "spacing_zyx": [volume.dz, volume.row_sp, volume.col_sp],
        "data": base64.b64encode(hu.tobytes()).decode("ascii"),
        "dtype": "int16-le",
        "series_uid": volume.series_uid,
        "frame_uid": volume.frame_uid,
        "origin_lps": volume.patient_point(0, 0, 0).tolist(),
        "sampling": "endpoint-aligned-linear-context-only",
    }
