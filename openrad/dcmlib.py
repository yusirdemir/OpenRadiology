"""Deterministic DICOM geometry, windowing and contact-sheet helpers.

Only depends on ``pydicom``, ``numpy`` and ``Pillow``. Nothing in this module
interprets images; it establishes *where* every pixel is in patient space and
renders it faithfully so that a reviewer (human or model) can cite it.

Geometry conventions (DICOM PS3.3 C.7.6.2, Image Plane Module)
---------------------------------------------------------------
* Patient coordinates are LPS millimetres. ``ImagePositionPatient`` (IPP) is
  the centre of the first transmitted voxel; ``ImageOrientationPatient`` (IOP)
  gives the row and column direction cosines.
* The slice normal is ``cross(row_cosine, col_cosine)``. Slices are sorted by
  the projection of IPP onto that normal, never by ``InstanceNumber`` or
  ``SliceLocation``, which vendors fill inconsistently.
* Slice spacing is the median IPP increment. ``SliceThickness`` and
  ``SpacingBetweenSlices`` are recorded but never trusted for geometry.
* Anisotropic voxels are the norm (e.g. 0.7 x 0.7 x 1.0 mm). Every reformat
  carries its own aspect ratio (``mpr_aspect``) and every distance uses the
  axis-specific spacing.
* Gantry tilt (head CT) shears the stack: IPP drifts in-plane as z increases.
  The tilt is detected from geometry, compared with ``GantryDetectorTilt`` and
  rejected by default. With ``allow_tilt=True`` native axial slices and
  in-plane measurements remain valid; reformats use an integer-pixel
  de-shear (``rectilinear()``) whose residual error is recorded.

Vendor notes
------------
* Siemens/GE/Philips CT all provide ``RescaleSlope``/``RescaleIntercept``;
  GE pads outside the reconstruction circle with ``PixelPaddingValue``
  (often -2000 HU), which is left untouched and is visible in bone windows.
* PET rescale is per slice (Siemens); it is applied per dataset here.
* Enhanced multi-frame IODs (CT/MR/PET) are rejected explicitly instead of
  being decoded with a guessed frame geometry.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pydicom
from PIL import Image, ImageDraw, ImageFont

from .errors import GeometryError, InputError, QuantitationError

Header = Tuple[Path, pydicom.Dataset]

# Standard display windows (centre, width) in HU. Sources: ACR/RSNA teaching
# conventions; values are display presets, not diagnostic thresholds.
WINDOWS: Dict[str, Tuple[float, float]] = {
    "lung": (-600, 1500),
    "soft": (40, 400),          # mediastinum / soft tissue
    "mediastinum": (50, 350),
    "bone": (450, 1800),
    "liver": (60, 160),
    "abdomen": (40, 350),
    "angio": (100, 700),        # CT angiography / pulmonary embolism
    "brain": (40, 80),
    "stroke": (32, 8),          # narrow acute ischaemia window
    "subdural": (75, 215),
}

CANONICAL_AXIAL_IOP = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)


# --------------------------------------------------------------------------- IO
def val(ds: pydicom.Dataset, key: str, default: Any = None) -> Any:
    """Return a header value, converting MultiValue to a list of strings."""
    x = ds.get(key, default)
    if x is None:
        return default
    if isinstance(x, pydicom.multival.MultiValue):
        return [str(v) for v in x]
    return x


def iter_dicom_files(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith("."):
            yield p


def read_headers(root: Path | str) -> List[Header]:
    """Return ``(path, dataset)`` for every DICOM file below ``root`` (pixels skipped)."""
    root = Path(root)
    out: List[Header] = []
    for p in iter_dicom_files(root):
        try:
            ds = pydicom.dcmread(p, stop_before_pixels=True, force=True)
        except Exception:
            continue
        if "SOPInstanceUID" not in ds and "Modality" not in ds:
            continue
        out.append((p, ds))
    return out


def group_series(headers: Iterable[Header]) -> Dict[str, List[Header]]:
    """Group ``(path, ds)`` by SeriesInstanceUID (fallback SeriesNumber)."""
    g: Dict[str, List[Header]] = defaultdict(list)
    for p, ds in headers:
        key = str(ds.get("SeriesInstanceUID", ds.get("SeriesNumber", "unknown")))
        g[key].append((p, ds))
    return g


MR_STACK_TAGS = ("SequenceName", "EchoTime", "EchoNumbers", "DiffusionBValue", "TemporalPositionIdentifier")


def split_mr_stacks(items: Sequence[Header]) -> List[List[Header]]:
    """Split explicit acquisition dimensions; never deduplicate repeated positions blindly."""
    groups: Dict[Tuple[str, ...], List[Header]] = defaultdict(list)
    for p, d in items:
        key = tuple(str(d.get(tag, "")) for tag in MR_STACK_TAGS)
        groups[key].append((p, d))
    return list(groups.values())


def series_by_number(root: Path | str, number: str | int) -> List[Header]:
    """Select a series by SeriesInstanceUID, or by an unambiguous SeriesNumber."""
    root = Path(root)
    if not root.is_dir():
        raise InputError(f"Study folder not found: {root}")
    hs = read_headers(root)
    if not hs:
        raise InputError(f"No DICOM files found under {root}")
    items = [(p, d) for p, d in hs if str(d.get("SeriesInstanceUID")) == str(number)]
    if not items:
        items = [(p, d) for p, d in hs if str(d.get("SeriesNumber")) == str(number)]
    if not items:
        raise InputError(f"Series {number} not found under {root}")
    if len(group_series(items)) != 1:
        raise InputError(f"Ambiguous SeriesNumber {number}; use SeriesInstanceUID")
    return items


def plane_of(ds: pydicom.Dataset) -> str:
    """'AX', 'COR', 'SAG' from the dominant component of the slice normal, else '?'."""
    iop = ds.get("ImageOrientationPatient")
    if not iop:
        return "?"
    r = np.array([float(v) for v in iop[:3]])
    c = np.array([float(v) for v in iop[3:]])
    n = np.cross(r, c)
    ax = int(np.argmax(np.abs(n)))
    return ["SAG", "COR", "AX"][ax]


def z_of(ds: pydicom.Dataset) -> float:
    """Position along the slice normal (mm). Falls back to SliceLocation, then InstanceNumber."""
    try:
        ipp = ds.ImagePositionPatient
        iop = ds.get("ImageOrientationPatient")
        if iop:
            r = np.array([float(v) for v in iop[:3]])
            c = np.array([float(v) for v in iop[3:]])
            n = np.cross(r, c)
            return float(np.dot(n, np.array([float(v) for v in ipp])))
        return float(ipp[2])
    except Exception:
        try:
            return float(ds.SliceLocation)
        except Exception:
            return float(ds.get("InstanceNumber", 0))


# --------------------------------------------------------------------- volumes
REQUIRED_GEOMETRY_TAGS = ("ImageOrientationPatient", "ImagePositionPatient", "PixelSpacing",
                          "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID")


class Volume:
    """Sorted stack of one series with validated geometry.

    ``vol[k]`` is slice ``k`` sorted by increasing position along the slice
    normal. For canonical axial HFS acquisitions: k increasing = inferior ->
    superior, row increasing = anterior -> posterior, col increasing = patient
    right -> left.

    Parameters
    ----------
    items
        ``(path, header)`` pairs of exactly one series.
    rescale
        Apply ``RescaleSlope``/``RescaleIntercept`` (HU for CT, Bq/mL for PET).
    allow_tilt
        Accept a gantry-tilted (sheared) stack. Native slices and in-plane
        measurements stay valid; reformats use :meth:`rectilinear`.
    """

    def __init__(self, items: Sequence[Header], rescale: bool = True, allow_tilt: bool = False) -> None:
        if len(group_series(items)) != 1:
            raise InputError("Mixed series; choose one SeriesInstanceUID")
        slices: List[Tuple[float, pydicom.Dataset, np.ndarray]] = []
        for p, _ in items:
            ds = pydicom.dcmread(p, force=True)
            for key in REQUIRED_GEOMETRY_TAGS:
                if key not in ds:
                    raise GeometryError(f"Missing {key}: cannot establish geometry for {p.name}")
            if int(ds.get("NumberOfFrames", 1)) != 1:
                raise GeometryError("Enhanced/multi-frame DICOM requires a frame-aware decoder; not supported")
            try:
                arr = ds.pixel_array.astype(np.float32)
            except Exception as e:  # pragma: no cover - depends on installed codecs
                raise InputError(f"Cannot decode pixels of {p}: {e}") from e
            if arr.ndim != 2 or not np.isfinite(arr).all():
                raise GeometryError("Only finite, single-frame monochrome image arrays are supported")
            if str(ds.get("PhotometricInterpretation", "")) not in ("MONOCHROME1", "MONOCHROME2"):
                raise GeometryError("Unsupported photometric interpretation")
            if str(ds.get("Modality")) == "CT" and rescale and ("RescaleSlope" not in ds or "RescaleIntercept" not in ds):
                raise QuantitationError("CT HU calibration (RescaleSlope/Intercept) missing")
            if rescale:
                arr = arr * float(ds.get("RescaleSlope", 1)) + float(ds.get("RescaleIntercept", 0))
            slices.append((z_of(ds), ds, arr))
        if not slices:
            raise InputError("No readable slices")
        slices.sort(key=lambda s: s[0])
        self.z: np.ndarray = np.array([s[0] for s in slices])
        self.ds: List[pydicom.Dataset] = [s[1] for s in slices]
        shapes = {s[2].shape for s in slices}
        if len(shapes) != 1:
            raise GeometryError(f"Inconsistent slice shapes in series: {shapes}")
        self.vol: np.ndarray = np.stack([s[2] for s in slices])
        d0 = self.ds[0]
        ps = d0.get("PixelSpacing", [1, 1])
        self.row_sp: float = float(ps[0])
        self.col_sp: float = float(ps[1])
        self.dz: float = float(np.median(np.diff(self.z))) if len(self.z) > 1 else float(d0.get("SliceThickness", 1) or 1)
        self.slice_thickness: Optional[float] = _float_or_none(d0.get("SliceThickness"))
        self.spacing_between_slices: Optional[float] = _float_or_none(d0.get("SpacingBetweenSlices"))
        self.plane: str = plane_of(d0)
        self.modality: str = str(d0.get("Modality", "?"))
        self.series_number: str = str(d0.get("SeriesNumber", "?"))
        self.manufacturer: str = str(d0.get("Manufacturer", ""))
        self.ipp0: List[float] = [float(v) for v in d0.ImagePositionPatient]
        self.iop: List[float] = [float(v) for v in d0.ImageOrientationPatient]
        self.series_uid: str = str(d0.SeriesInstanceUID)
        self.study_uid: str = str(d0.StudyInstanceUID)
        self.frame_uid: str = str(d0.get("FrameOfReferenceUID", ""))
        self.normal: np.ndarray = np.cross(self.iop[:3], self.iop[3:])
        self.gantry_tilt_deg: float = 0.0
        self.tilt_tag_deg: Optional[float] = _float_or_none(d0.get("GantryDetectorTilt"))
        self._shear_mm_per_mm: np.ndarray = np.zeros(3)
        self._rectilinear: Optional[np.ndarray] = None
        self._validate(allow_tilt)

    # ------------------------------------------------------------- validation
    def _validate(self, allow_tilt: bool) -> None:
        if (not np.isfinite(self.iop).all() or not np.isfinite([self.row_sp, self.col_sp]).all()
                or min(self.row_sp, self.col_sp) <= 0
                or not np.isclose(np.linalg.norm(self.iop[:3]), 1, atol=1e-4)
                or not np.isclose(np.linalg.norm(self.iop[3:]), 1, atol=1e-4)
                or not np.isclose(np.dot(self.iop[:3], self.iop[3:]), 0, atol=1e-4)):
            raise GeometryError("Invalid direction cosines or pixel spacing")
        if len({str(d.SOPInstanceUID) for d in self.ds}) != len(self.ds):
            raise GeometryError("Duplicate SOPInstanceUID")
        for d in self.ds:
            if str(d.StudyInstanceUID) != self.study_uid or str(d.get("FrameOfReferenceUID", "")) != self.frame_uid:
                raise GeometryError("Mixed study/frame of reference")
            if (not np.allclose(d.ImageOrientationPatient, self.iop, atol=1e-4)
                    or not np.allclose(d.PixelSpacing, [self.row_sp, self.col_sp], atol=1e-5)):
                raise GeometryError("Changing orientation/spacing; split acquisition dimensions first")
        if not np.isfinite(self.z).all() or self.dz <= 0:
            raise GeometryError("Invalid or duplicate slice position")
        if len(self.z) > 1:
            gaps = np.diff(self.z)
            if np.any(gaps <= 1e-4) or not np.allclose(gaps, self.dz, atol=max(0.05, self.dz * 0.02), rtol=0):
                raise GeometryError("Duplicate, missing or irregularly spaced slices; geometry is not a regular volume")
            origins = np.array([d.ImagePositionPatient for d in self.ds], dtype=float)
            dzs = (self.z - self.z[0])[:, None]
            expected = origins[0] + dzs * self.normal
            residual = origins - expected
            if not np.allclose(residual, 0, atol=0.1):
                # Least-squares in-plane drift per mm of z: residual ~= dz * shear
                shear, *_ = np.linalg.lstsq(dzs, residual, rcond=None)
                shear = shear[0]
                fit = dzs * shear
                if not np.allclose(fit, residual, atol=0.1):
                    raise GeometryError("Slice origins are not on a line: non-linear shear or mixed stacks")
                self._shear_mm_per_mm = shear
                self.gantry_tilt_deg = math.degrees(math.atan(float(np.linalg.norm(shear))))
                tag = f", GantryDetectorTilt tag {self.tilt_tag_deg:.1f} deg" if self.tilt_tag_deg is not None else ""
                if not allow_tilt:
                    raise GeometryError(
                        f"Gantry-tilted/sheared stack: {self.gantry_tilt_deg:.2f} deg from geometry{tag}. "
                        "Native axial slices and in-plane measurements are valid; reformats need de-shearing. "
                        "Re-run with --allow-tilt to accept, or use a validated resampler.")

    @property
    def is_tilted(self) -> bool:
        return self.gantry_tilt_deg > 1e-3

    def shear_px_per_slice(self) -> Tuple[float, float]:
        """In-plane drift of the slice origin per slice, in (row, col) pixels."""
        drift_mm = self._shear_mm_per_mm * self.dz
        return (float(np.dot(drift_mm, self.iop[3:]) / self.row_sp),
                float(np.dot(drift_mm, self.iop[:3]) / self.col_sp))

    def rectilinear(self) -> np.ndarray:
        """Volume on a rectilinear grid for reformats.

        For untilted stacks this is ``vol`` itself. For tilted stacks each slice
        is translated by the integer pixel count that undoes the origin drift,
        padded with the volume minimum (air). The residual misregistration is
        below half a pixel per slice; it is recorded in ``tilt_info()``.
        """
        if not self.is_tilted:
            return self.vol
        if self._rectilinear is None:
            dr, dc = self.shear_px_per_slice()
            fill = float(self.vol.min())
            out = np.full_like(self.vol, fill)
            for k in range(len(self.z)):
                sr, sc = int(round(dr * k)), int(round(dc * k))
                src = self.vol[k]
                # pixel (r, c) of slice k sits at patient position origin_k + ...;
                # move it to where it would be if origin_k were on the normal line
                r_src0, r_src1 = max(0, -sr), min(src.shape[0], src.shape[0] - sr)
                c_src0, c_src1 = max(0, -sc), min(src.shape[1], src.shape[1] - sc)
                if r_src1 > r_src0 and c_src1 > c_src0:
                    out[k, r_src0 + sr:r_src1 + sr, c_src0 + sc:c_src1 + sc] = src[r_src0:r_src1, c_src0:c_src1]
            self._rectilinear = out
        return self._rectilinear

    def tilt_info(self) -> Dict[str, Any]:
        dr, dc = self.shear_px_per_slice()
        return {"gantry_tilt_deg_geometry": round(self.gantry_tilt_deg, 3),
                "gantry_tilt_deg_tag": self.tilt_tag_deg,
                "shear_px_per_slice_row_col": [round(dr, 4), round(dc, 4)],
                "deshear_method": "integer pixel translation per slice, residual < 0.5 px" if self.is_tilted else None}

    def require_axial(self) -> None:
        """Current CT/PET reformat algorithms support canonical LPS axial stacks only."""
        if not np.allclose(self.iop, CANONICAL_AXIAL_IOP, atol=1e-4):
            raise GeometryError("CT/PET reformat requires canonical axial LPS orientation "
                                "(IOP 1\\0\\0\\0\\1\\0); use a validated viewer/resampler")

    def require_rectilinear(self, what: str = "this operation") -> None:
        if self.is_tilted:
            raise GeometryError(f"{what} needs a rectilinear stack; volume is gantry-tilted "
                                f"({self.gantry_tilt_deg:.2f} deg)")

    # -------------------------------------------------------------- geometry
    def patient_point(self, k: int, r: float, c: float) -> np.ndarray:
        """LPS coordinate (mm) of native pixel (row r, col c) of slice k."""
        return (np.asarray(self.ds[k].ImagePositionPatient, dtype=float)
                + np.asarray(self.iop[:3]) * c * self.col_sp
                + np.asarray(self.iop[3:]) * r * self.row_sp)

    @property
    def shape(self) -> Tuple[int, ...]:
        return self.vol.shape

    @property
    def voxel_volume_mm3(self) -> float:
        return abs(self.dz) * self.row_sp * self.col_sp

    def instance(self, k: int) -> str:
        return str(self.ds[k].get("InstanceNumber", k))

    def sop_uid(self, k: int) -> str:
        return str(self.ds[k].SOPInstanceUID)

    def index_of_z(self, z: float) -> int:
        if not np.isfinite(z) or z < self.z[0] - self.dz / 2 or z > self.z[-1] + self.dz / 2:
            raise InputError("Requested position is outside the acquired volume")
        return int(np.argmin(np.abs(self.z - z)))

    def index_of_instance(self, inst: str | int) -> int:
        for k, d in enumerate(self.ds):
            if str(d.get("InstanceNumber")) == str(inst):
                return k
        raise InputError(f"Instance {inst} not in series")

    def body_bbox(self, thr: float = -500, margin: int = 8) -> Tuple[int, int, int, int]:
        """Bounding box (r0, r1, c0, c1) of tissue above ``thr``, sampling every 4th slice."""
        sub = self.vol[::4]
        mask = (sub > thr).any(axis=0)
        cnt = (sub > thr).sum(axis=0)
        mask &= cnt >= max(2, len(sub) // 20)
        rows = np.where(mask.any(axis=1))[0]
        cols = np.where(mask.any(axis=0))[0]
        if len(rows) == 0 or len(cols) == 0:
            return 0, self.vol.shape[1], 0, self.vol.shape[2]
        r0, r1 = max(0, rows[0] - margin), min(self.vol.shape[1], rows[-1] + margin + 1)
        c0, c1 = max(0, cols[0] - margin), min(self.vol.shape[2], cols[-1] + margin + 1)
        return int(r0), int(r1), int(c0), int(c1)

    # ------------------------------------------------------------- reformats
    def slab_mip(self, k: int, half_slices: int) -> np.ndarray:
        v = self.rectilinear()
        a, b = max(0, k - half_slices), min(len(self.z), k + half_slices + 1)
        return v[a:b].max(axis=0)

    def slab_minip(self, k: int, half_slices: int) -> np.ndarray:
        v = self.rectilinear()
        a, b = max(0, k - half_slices), min(len(self.z), k + half_slices + 1)
        return v[a:b].min(axis=0)

    def coronal(self, row: int, thick: int = 1) -> np.ndarray:
        """Coronal reformat at ``row`` (superior at top). ``thick`` is in rows (row_sp)."""
        v = self.rectilinear()
        a, b = max(0, row - thick // 2), min(v.shape[1], row + thick // 2 + 1)
        img = v[:, a:b, :].max(axis=1) if thick > 1 else v[:, row, :]
        return np.flipud(img)

    def sagittal(self, col: int, thick: int = 1) -> np.ndarray:
        """Sagittal reformat at ``col`` (superior at top, anterior at left). ``thick`` in cols."""
        v = self.rectilinear()
        a, b = max(0, col - thick // 2), min(v.shape[2], col + thick // 2 + 1)
        img = v[:, :, a:b].max(axis=2) if thick > 1 else v[:, :, col]
        return np.flipud(img)

    def mpr_aspect(self, plane: str = "COR") -> float:
        """Vertical stretch for coronal/sagittal tiles: z spacing / in-plane spacing of the horizontal axis."""
        return abs(self.dz) / (self.row_sp if plane == "SAG" else self.col_sp)

    def geometry_summary(self) -> Dict[str, Any]:
        return {"modality": self.modality, "series_number": self.series_number, "series_uid": self.series_uid,
                "study_uid": self.study_uid, "frame_uid": self.frame_uid, "shape_zyx": list(self.vol.shape),
                "pixel_spacing_mm": [self.row_sp, self.col_sp], "slice_spacing_mm": self.dz,
                "slice_thickness_mm": self.slice_thickness, "spacing_between_slices_tag": self.spacing_between_slices,
                "plane": self.plane, "iop": self.iop, "z_range_mm": [float(self.z[0]), float(self.z[-1])],
                "manufacturer": self.manufacturer, **self.tilt_info()}


def _float_or_none(x: Any) -> Optional[float]:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------- imaging
def window(arr: np.ndarray, center: float, width: float, invert: bool = False) -> np.ndarray:
    """Linear window to uint8 (DICOM PS3.3 C.11.2.1.2 linear VOI LUT semantics)."""
    lo = center - width / 2.0
    out = np.clip((arr - lo) * (255.0 / width), 0, 255).astype(np.uint8)
    return 255 - out if invert else out


def percentile_window(arr: np.ndarray, lo: float = 0.5, hi: float = 99.5) -> np.ndarray:
    a, b = np.percentile(arr, [lo, hi])
    if b <= a:
        b = a + 1
    return np.clip((arr - a) * (255.0 / (b - a)), 0, 255).astype(np.uint8)


def font(size: int = 16) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def to_image(arr8: np.ndarray, scale_xy: Tuple[float, float] = (1.0, 1.0)) -> Image.Image:
    im = Image.fromarray(arr8)
    sx, sy = scale_xy
    if abs(sx - 1) > 1e-3 or abs(sy - 1) > 1e-3:
        im = im.resize((max(1, round(im.width * sx)), max(1, round(im.height * sy))), Image.LANCZOS)
    return im


def label(im: Image.Image, text: str, corner: str = "tl", size: Optional[int] = None, fill: Any = 255, bg: Any = 0) -> Image.Image:
    """Boxed text label in a corner; font scales with the tile when ``size`` is None."""
    if size is None:
        from .grid import font_size_for
        size = font_size_for(min(im.width, im.height))
    d = ImageDraw.Draw(im)
    f = font(size)
    bbox = d.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0] + 8, bbox[3] - bbox[1] + 6
    x = 0 if corner in ("tl", "bl") else im.width - w
    y = 0 if corner in ("tl", "tr") else im.height - h
    d.rectangle((x, y, x + w, y + h), fill=bg)
    d.text((x + 4, y + 2), text, fill=fill, font=f)
    return im


def scale_bar(im: Image.Image, mm_per_px: float, length_mm: Optional[float] = None, size: Optional[int] = None) -> Image.Image:
    """Draw a horizontal scale bar (bottom right). Length and font adapt to the tile when not given."""
    from .grid import font_size_for, scale_bar_mm
    if length_mm is None:
        length_mm = scale_bar_mm(im.width * mm_per_px)
    if size is None:
        size = font_size_for(min(im.width, im.height))
    d = ImageDraw.Draw(im)
    px = int(round(length_mm / mm_per_px))
    x0, y0 = im.width - px - 12, im.height - 14
    d.line((x0, y0, x0 + px, y0), fill=255, width=3)
    d.line((x0, y0 - 5, x0, y0 + 5), fill=255, width=2)
    d.line((x0 + px, y0 - 5, x0 + px, y0 + 5), fill=255, width=2)
    d.text((x0, y0 - 20), f"{length_mm:g} mm", fill=255, font=font(size))
    return im


def contact_sheet(tiles: Sequence[Image.Image], cols: int, pad: int = 2, bg: int = 20) -> Optional[Image.Image]:
    """Paste equally sized tiles into a grid; RGB is preserved if any tile is RGB."""
    if not tiles:
        return None
    w, h = tiles[0].width, tiles[0].height
    rows = math.ceil(len(tiles) / cols)
    mode = "RGB" if any(t.mode == "RGB" for t in tiles) else "L"
    sheet = Image.new(mode, (cols * (w + pad), rows * (h + pad)), (bg, bg, bg) if mode == "RGB" else bg)
    for i, t in enumerate(tiles):
        if t.size != (w, h):
            t = t.resize((w, h))
        sheet.paste(t, ((i % cols) * (w + pad), (i // cols) * (h + pad)))
    return sheet


def grid_for(w: int, h: int, max_side: int = 1568, min_cols: int = 1, grid: str = "2x2") -> Tuple[int, int]:
    """(cols, rows) for tiles of size w x h; see :mod:`openrad.grid`. Kept for backwards compatibility."""
    from .grid import fit_grid
    spec = fit_grid(w, h, grid, max_side, min_cols=min_cols)
    return spec.cols, spec.rows


def direction_label(vector: Sequence[float]) -> str:
    """Anatomical label ('R', 'AS', ...) of an LPS direction vector, dominant axis first."""
    vector = np.asarray(vector, dtype=float)
    labels = [("R", "L"), ("A", "P"), ("I", "S")]
    return "".join(labels[i][int(vector[i] > 0)] for i in np.argsort(-np.abs(vector)) if abs(vector[i]) > 0.1)


def orientation_marks(im: Image.Image, plane: str = "AX", size: Optional[int] = None,
                      iop: Optional[Sequence[float]] = None) -> Image.Image:
    """Draw R/L/A/P/S/I letters. Native images pass their IOP; reformats pass explicit display directions."""
    if iop is None:
        raise ValueError("Explicit display orientation is required")
    if size is None:
        from .grid import font_size_for
        size = font_size_for(min(im.width, im.height))
    d = ImageDraw.Draw(im)
    f = font(size)
    x, y = np.asarray(iop[:3]), np.asarray(iop[3:])
    fill = (255, 255, 255) if im.mode == "RGB" else 255
    for vec, pos in ((-x, (3, im.height // 2)), (x, (im.width - 35, im.height // 2)),
                     (-y, (im.width // 2, 18)), (y, (im.width // 2, im.height - 18))):
        d.text(pos, direction_label(vec), fill=fill, font=f)
    return im


def z_ruler(im: Image.Image, z_top: float, z_bottom: float, tick_mm: float = 10.0, size: Optional[int] = None,
            major_every: int = 5) -> Image.Image:
    """Millimetre depth ruler along the left edge of a reformat (superior at top).

    ``z_top``/``z_bottom`` are the patient z (mm) of the first and last image row.
    Minor ticks every ``tick_mm``; every ``major_every``-th tick is labelled.
    """
    if size is None:
        from .grid import font_size_for
        size = font_size_for(min(im.width, im.height), lo=9, hi=16)
    d = ImageDraw.Draw(im)
    f = font(size)
    span = z_top - z_bottom
    if abs(span) < 1e-6 or im.height < 2:
        return im
    fill = (120, 200, 255) if im.mode == "RGB" else 255
    line = (0, 90, 160) if im.mode == "RGB" else 128
    first = math.ceil(min(z_top, z_bottom) / tick_mm) * tick_mm
    z = first
    while z <= max(z_top, z_bottom):
        y = int(round((z_top - z) / span * (im.height - 1)))
        major = int(round(z / tick_mm)) % major_every == 0
        d.line((0, y, 10 if major else 5, y), fill=line, width=1)
        if major:
            d.text((12, y - size // 2), f"{z:.0f}", fill=fill, font=f)
        z += tick_mm
    return im


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def save_json(obj: Any, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def mark_source(im: Image.Image, v: Volume, k: int, purpose: str) -> Image.Image:
    """Attach the DICOM provenance of a tile so ``save_page`` can index coverage."""
    im.info["review_source"] = {"study_uid": v.study_uid, "series_uid": v.series_uid,
                                "sop_uid": v.sop_uid(k), "instance": v.instance(k), "purpose": purpose}
    return im


def save_page(tiles: Sequence[Image.Image], cols: int, path: Path | str) -> Path:
    """Save a contact sheet and append its provenance + SHA-256 to ``render_index.json``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet = contact_sheet(tiles, cols)
    if sheet is None:
        raise ValueError("No tiles to save")
    sheet.save(path)
    index_path = path.parent / "render_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[path.name] = {"sources": [t.info["review_source"] for t in tiles if "review_source" in t.info],
                        "sha256": sha256_file(path)}
    save_json(index, index_path)
    return path
