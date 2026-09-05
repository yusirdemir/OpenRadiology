"""Experimental KROMA-3D candidate summaries, not diagnostic or coverage evidence.

Arrays use (z, y, x), millimetres and calibrated HU. The supported DICOM grid
is canonical axial LPS. Scores are heuristic, not probabilities; diameter is
an equivalent sphere estimate of the winning filter scale, not a measurement.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage as ndi

from .config import load_settings
from .dcmlib import Volume, font, group_series, read_headers, series_by_number
from .errors import GeometryError, InputError, UsageError
from .log import progress

LIMITATION = "Experimental candidates only; misses possible. Not a diagnosis or complete study review."
SCALES = (1.0, 1.5, 2.2, 3.3, 5.0)


@dataclass(frozen=True)
class KromaConfig:
    scales_mm: tuple = SCALES
    c_hu: float = 50.0
    gamma: float = 2.0
    tau: float = 0.25
    max_candidates: int = 16
    block_size: int = 64
    memory_mb: int = 4096

    def validate(self) -> None:
        if (not self.scales_mm or not np.isfinite(self.scales_mm).all()
                or min(self.scales_mm) <= 0 or max(self.scales_mm) > 16):
            raise UsageError("scales must be finite and in (0, 16] mm")
        if not np.isfinite([self.c_hu, self.gamma, self.tau]).all() or self.c_hu <= 0 or self.gamma < 0 or not 0 < self.tau <= 1:
            raise UsageError("c must be positive, gamma nonnegative and tau in (0, 1]")
        if any(not isinstance(v, int) for v in (self.max_candidates, self.block_size, self.memory_mb)):
            raise UsageError("candidate count, block size and memory budget must be integers")
        if not 1 <= self.max_candidates <= 32 or not 8 <= self.block_size <= 128 or self.memory_mb < 64:
            raise UsageError("candidates: 1..32; block size: 8..128; memory budget: >=64 MiB")


@dataclass
class Features:
    blob: np.ndarray
    vessel: np.ndarray
    scale_mm: np.ndarray
    blob_normalizer: float


def _volume_check(volume: np.ndarray) -> None:
    if volume.ndim != 3 or min(volume.shape) < 2 or not np.issubdtype(volume.dtype, np.number) or np.iscomplexobj(volume):
        raise GeometryError("Expected a real numeric 3D HU volume with at least two voxels per axis")
    # Slice-wise validation avoids a volume-sized temporary boolean array.
    if any(not np.isfinite(s).all() for s in volume):
        raise InputError("HU volume contains NaN or infinity")


def _iso_shape(shape: Sequence[int], spacing: Sequence[float]) -> tuple:
    if len(spacing) != 3 or not np.isfinite(spacing).all() or min(spacing) <= 0:
        raise GeometryError("spacing_zyx must contain three positive finite millimetre spacings")
    # Centre-aligned 1 mm grid, never invent centres beyond the last source centre.
    return tuple(int(math.floor((n - 1) * float(s) + 1e-6)) + 1 for n, s in zip(shape, spacing))


def _budget(shape: Sequence[int], source_bytes: int, cfg: KromaConfig) -> int:
    # Conservative live-array estimate, excluding interpreter/decoder/BLAS overhead.
    # HU + mask + three fields + segmentation/ranking temporaries; bounded halo work.
    halo = int(4 * max(cfg.scales_mm) + 0.5)
    edge = cfg.block_size + 2 * halo
    estimate = source_bytes + math.prod(shape) * 40 + edge ** 3 * 32 + cfg.block_size ** 3 * 96
    if estimate > cfg.memory_mb * 1024 ** 2:
        raise UsageError(f"Estimated array working set {math.ceil(estimate / 1024 ** 2)} MiB exceeds "
                         f"--memory-mb {cfg.memory_mb}; raise the budget or use a smaller block/volume")
    return estimate


def resample_iso(volume: np.ndarray, spacing_zyx: Sequence[float], *, memory_mb: int = 4096) -> np.ndarray:
    """Linear, centre-aligned resampling to exactly 1 mm; no zoom rounding drift."""
    _volume_check(volume)
    shape = _iso_shape(volume.shape, spacing_zyx)
    if min(shape) < 2:
        raise GeometryError("Physical extent must cover at least 1 mm on each axis")
    if volume.nbytes + math.prod(shape) * 4 > memory_mb * 1024 ** 2:
        raise UsageError("Resampled volume exceeds memory budget")
    return ndi.affine_transform(volume, np.diag(1 / np.asarray(spacing_zyx)), output_shape=shape,
                                output=np.float32, order=1, mode="nearest", prefilter=False)


def lung_mask(volume: np.ndarray) -> np.ndarray:
    """Slice-wise enclosed air, morphology and hole filling to retain solid islands.

    Exterior air is removed in 2D, so lungs touching the first/last z plane
    survive. This is a heuristic mask: pleural disease, severe opacification,
    open/truncated body contours and other air cavities remain limitations.
    """
    _volume_check(volume)
    result = np.zeros(volume.shape, dtype=bool)
    structure = ndi.generate_binary_structure(2, 1)
    for k, plane in enumerate(volume):
        air = plane < -320
        labels, _ = ndi.label(air)
        exterior = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
        inside = air & ~np.isin(labels, exterior)
        opened = ndi.binary_opening(inside, structure)
        closed = ndi.binary_closing(opened, structure, iterations=2)
        # Retain original peripheral air while filling vessel/nodule islands.
        result[k] = ndi.binary_fill_holes(inside | closed)
    labels, count = ndi.label(result)
    if count:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        keep = np.argsort(sizes[1:])[-2:] + 1
        result = np.isin(labels, keep)
    return result


def _blocks(shape: Sequence[int], size: int):
    for start in itertools.product(*(range(0, n, size) for n in shape)):
        yield tuple(slice(a, min(a + size, n)) for a, n in zip(start, shape))


def _kernels(sigma: float) -> tuple:
    radius = int(4 * sigma + 0.5)
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    g = np.exp(-0.5 * (x / sigma) ** 2)
    g /= g.sum()
    d = -x / sigma ** 2 * g
    dd = (x ** 2 / sigma ** 4 - 1 / sigma ** 2) * g
    dd -= dd.sum() * g  # Exact DC rejection: truncated kernels must not label constant air as blobs.
    return g, d, dd


def _eigenvalues(block: np.ndarray, core: tuple, selected: np.ndarray, sigma: float) -> np.ndarray:
    """Separable derivatives; only core mask voxels get a 3x3 symmetric matrix.

    Cache the first two axis convolutions (3 + 6 + 6 passes instead of 18).
    Halo radius = kernel support, so scores are invariant to block boundaries.
    """
    kernels = _kernels(sigma)
    h = np.zeros((int(selected.sum()), 3, 3), dtype=np.float32)
    orders = {(2, 0, 0): (0, 0), (0, 2, 0): (1, 1), (0, 0, 2): (2, 2),
              (1, 1, 0): (0, 1), (1, 0, 1): (0, 2), (0, 1, 1): (1, 2)}
    for a in range(3):
        first = ndi.correlate1d(block, kernels[a], axis=0, mode="reflect")
        for b in range(3 - a):
            c = 2 - a - b
            second = ndi.correlate1d(first, kernels[b], axis=1, mode="reflect")
            last = ndi.correlate1d(second, kernels[c], axis=2, mode="reflect")
            i, j = orders[(a, b, c)]
            h[:, i, j] = last[core][selected] * (sigma * sigma)
            h[:, j, i] = h[:, i, j]
    eigen = np.linalg.eigvalsh(h)
    return np.take_along_axis(eigen, np.argsort(np.abs(eigen), axis=-1), axis=-1)


def hessian_features(volume: np.ndarray, mask: np.ndarray | None = None, config: KromaConfig | None = None) -> Features:
    """Scale-normalized bright blob/Frangi responses on a 1 mm grid.

    B is globally max-normalized once (never per block). V already has a
    dimensionless [0,1] range and is not independently stretched, preserving
    its discrimination against spheres. No full-volume Hessian is allocated.
    """
    cfg = config or KromaConfig()
    cfg.validate()
    _volume_check(volume)
    _budget(volume.shape, volume.nbytes, cfg)
    if mask is not None and (mask.shape != volume.shape or mask.dtype != bool):
        raise GeometryError("Mask must be boolean with the same shape as the HU volume")
    blob = np.zeros(volume.shape, np.float32)
    vessel = np.zeros_like(blob)
    scale = np.zeros_like(blob)
    for sigma in sorted(set(cfg.scales_mm)):
        radius = int(4 * sigma + 0.5)
        for sl in _blocks(volume.shape, cfg.block_size):
            selected = np.ones(tuple(s.stop - s.start for s in sl), bool) if mask is None else mask[sl]
            if not selected.any():
                continue
            expanded = tuple(slice(max(0, s.start - radius), min(n, s.stop + radius)) for s, n in zip(sl, volume.shape))
            core = tuple(slice(s.start - e.start, s.stop - e.start) for s, e in zip(sl, expanded))
            block = np.asarray(volume[expanded], dtype=np.float32)
            eigen = _eigenvalues(block, core, selected, sigma)
            l1, l2, l3 = eigen.T
            a1, a2, a3 = np.abs(eigen).T
            eps = np.finfo(np.float32).eps
            strength = -np.expm1(-(a1 * a1 + a2 * a2 + a3 * a3) / (2 * cfg.c_hu ** 2))
            b = a1 * (a2 / np.maximum(a3, eps)) * strength
            b *= (l1 < -eps) & (l2 < -eps) & (l3 < -eps)
            ra = a2 / np.maximum(a3, eps)
            rb2 = a1 * a1 / np.maximum(a2 * a3, eps)
            v = -np.expm1(-2 * ra * ra) * np.exp(-2 * rb2) * strength
            v *= (l2 < -eps) & (l3 < -eps)
            old = blob[sl][selected]
            scale[sl][selected] = np.where(b > old, sigma, scale[sl][selected])
            blob[sl][selected] = np.maximum(old, b)
            vessel[sl][selected] = np.maximum(vessel[sl][selected], v)
        progress(f"KROMA Hessian sigma={sigma:g} mm")
    normalizer = float(blob.max())
    if normalizer > 0:
        blob /= normalizer
    return Features(blob, vessel, scale, normalizer)


def extract_candidates(features: Features, volume: np.ndarray, mask: np.ndarray, config: KromaConfig | None = None) -> list:
    """5-voxel local maxima, bounded top-k and deterministic plateau suppression."""
    cfg = config or KromaConfig()
    cfg.validate()
    b = features.blob
    # At most one representative per connected maximum plateau; bounded by a block.
    # Global greedy Chebyshev NMS below also removes plateaus crossing block edges.
    pool = []
    for sl in _blocks(b.shape, cfg.block_size):
        ext = tuple(slice(max(0, s.start - 2), min(n, s.stop + 2)) for s, n in zip(sl, b.shape))
        core = tuple(slice(s.start - e.start, s.stop - e.start) for s, e in zip(sl, ext))
        maximum = ndi.maximum_filter(b[ext], size=5, mode="constant", cval=0)[core]
        peaks = (b[sl] >= cfg.tau) & (b[sl] == maximum) & mask[sl]
        labels, count = ndi.label(peaks, structure=np.ones((3, 3, 3), bool))
        if not count:
            continue
        coords = np.asarray(ndi.maximum_position(b[sl], labels, range(1, count + 1))).reshape(-1, 3)
        coords += np.array([s.start for s in sl])
        for coord in coords:
            p = tuple(int(x) for x in coord)
            pool.append((float(b[p]), p))
        # Keep a bounded shortlist. 5^3 neighbours can be rejected per accepted peak.
        pool = sorted(pool, key=lambda item: (-item[0], item[1]))[:cfg.max_candidates * 125]
    candidates = []
    for score, p in pool:
        if any(max(abs(x - y) for x, y in zip(p, c["center_zyx"])) <= 2 for c in candidates):
            continue
        sigma = float(features.scale_mm[p])
        candidates.append({"id": f"N{len(candidates) + 1:02d}", "center_zyx": list(p),
                           "hu_center": float(volume[p]), "blob": score, "vessel": float(features.vessel[p]),
                           # A uniform 3D sphere has peak scale-normalized centre curvature at sigma=R/sqrt(3).
                           "sigma_mm": sigma, "diameter_estimate_mm": 2 * math.sqrt(3) * sigma,
                           "diameter_method": "winning-scale equivalent solid sphere; not a measured lesion diameter"})
        if len(candidates) == cfg.max_candidates:
            break
    return candidates


def _intensity(hu: np.ndarray) -> np.ndarray:
    return np.clip((hu + 1350) / 1500, 0, 1)


def rgb_overlay(hu: np.ndarray, blob: np.ndarray, vessel: np.ndarray) -> np.ndarray:
    i = _intensity(hu)
    return np.stack((np.clip(i + 1.5 * blob, 0, 1), np.clip(i + vessel, 0, 1), i), axis=-1)


def _plane(array: np.ndarray, center: Sequence[int], axis: int, fill: float) -> np.ndarray:
    # Exactly 48 samples at 1 mm; candidate is pixel (24,24). Pad, never wrap.
    other = [i for i in range(3) if i != axis]
    sl = [slice(max(0, center[i] - 24), min(array.shape[i], center[i] + 24)) for i in range(3)]
    sl[axis] = center[axis]
    patch = array[tuple(sl)]
    padding = [(max(0, 24 - center[i]), max(0, center[i] + 24 - array.shape[i])) for i in other]
    result = np.pad(patch, padding, constant_values=fill)
    return result[::-1] if axis in (1, 2) else result


def _badge(candidate: dict) -> str:
    return (f"{candidate['id']} ~{candidate['diameter_estimate_mm']:.1f}mm HU{candidate['hu_center']:+.0f} "
            f"BLOB{candidate['blob']:.2f} VES{candidate['vessel']:.2f} | ax cor sag")


def _card(volume: np.ndarray, features: Features, candidate: dict, zoom: int = 2) -> Image.Image:
    side = 48 * zoom
    card = Image.new("RGB", (512, side + 30), (12, 16, 22))
    left = (512 - 3 * side) // 2
    center = candidate["center_zyx"]
    for axis in range(3):
        rgb = rgb_overlay(_plane(volume, center, axis, -1350), _plane(features.blob, center, axis, 0),
                          _plane(features.vessel, center, axis, 0))
        im = Image.fromarray(np.rint(rgb * 255).astype(np.uint8)).resize((side, side), Image.Resampling.NEAREST)
        d = ImageDraw.Draw(im)
        # Small ticks around, not on top of, the centre voxel.
        cy = (23 if axis else 24) * zoom + zoom // 2
        cx = 24 * zoom + zoom // 2
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            d.line((cx + dx * 5, cy + dy * 5, cx + dx * 9, cy + dy * 9), fill="yellow")
        d.text((2, 1), ("AX R>L A>P", "COR R>L S>I", "SAG A>P S>I")[axis], font=font(10), fill="white")
        card.paste(im, (left + axis * side, 0))
    d = ImageDraw.Draw(card)
    d.text((8, side + 2), _badge(candidate), font=font(12), fill="white")
    d.text((8, side + 16), f"48mm FOV | {zoom}x | ~size: filter estimate | experimental", font=font(10), fill=(180, 185, 190))
    return card


def render_overview(volume: np.ndarray, mask: np.ndarray, features: Features, candidates: list, gamma: float) -> Image.Image:
    """Coronal depth-coded MIP. Depth and B come from the winning intensity voxel."""
    shape = (volume.shape[0], volume.shape[2])
    mip = np.full(shape, -1350, np.float32)
    depth = np.zeros(shape, np.float32)
    blob = np.zeros(shape, np.float32)
    for y in range(volume.shape[1]):
        # Shift before multiplication: suppressing negative HU directly brightens vessels.
        hu = (volume[:, y, :] + 1350) * (1 - features.vessel[:, y, :]) ** gamma - 1350
        update = mask[:, y, :] & (hu > mip)
        mip[update] = hu[update]
        depth[update] = y
        blob[update] = features.blob[:, y, :][update]
    hsv = np.stack((depth / max(1, volume.shape[1] - 1) * 255, np.clip(blob * 3, 0, 1) * 255,
                    _intensity(mip) * 255), axis=-1)
    im = Image.frombytes("HSV", (shape[1], shape[0]), hsv[::-1].astype(np.uint8).tobytes()).convert("RGB")
    im.thumbnail((960, 840), Image.Resampling.NEAREST)
    factor = min(960 / im.width, 840 / im.height)
    if factor > 1:
        im = im.resize((int(im.width * factor), int(im.height * factor)), Image.Resampling.NEAREST)
    page = Image.new("RGB", (1024, 1024), (12, 16, 22))
    x0, y0 = (1024 - im.width) // 2, 80
    page.paste(im, (x0, y0))
    draw = ImageDraw.Draw(page)
    draw.text((24, 16), "KROMA-3D | coronal depth MIP | R > L, S > I", font=font(20), fill="white")
    draw.text((24, 46), "Hue: anterior > posterior; saturation: blobness; brightness: vessel-suppressed HU", font=font(14), fill="white")
    for c in candidates:
        z, _, x = c["center_zyx"]
        px = x0 + (x + 0.5) * im.width / shape[1]
        py = y0 + (shape[0] - z - 0.5) * im.height / shape[0]
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), outline="yellow", width=2)
        draw.text((min(px + 9, 965), py - 8), c["id"], font=font(14), fill="yellow")
    draw.text((24, 940), f"{len(candidates)} selected candidates | scores are not probabilities", font=font(16), fill="white")
    draw.text((24, 969), LIMITATION, font=font(14), fill=(255, 210, 100))
    return page


def create_passport(volume: np.ndarray, output: Path, *, spacing_zyx: Sequence[float] = (1, 1, 1),
                    origin_lps: Sequence[float] = (0, 0, 0), config: KromaConfig | None = None,
                    detail_cards: bool = False, source: dict | None = None) -> dict:
    """Public synthetic-array API. Output directory must be empty to prevent stale evidence."""
    cfg = config or KromaConfig()
    cfg.validate()
    _volume_check(volume)
    if len(origin_lps) != 3 or not np.isfinite(origin_lps).all():
        raise GeometryError("origin_lps must contain three finite millimetre coordinates")
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise InputError("Passport output must be a new or empty directory; never overwrite existing artifacts")
    shape = _iso_shape(volume.shape, spacing_zyx)
    estimate = _budget(shape, volume.nbytes, cfg)
    iso = resample_iso(volume, spacing_zyx, memory_mb=cfg.memory_mb)
    mask = lung_mask(iso)
    features = hessian_features(iso, mask, cfg)
    candidates = extract_candidates(features, iso, mask, cfg)
    for index, c in enumerate(candidates):
        z, y, x = c["center_zyx"]
        c["center_lps_mm"] = (np.asarray(origin_lps) + [x, y, z]).tolist()
        native = np.array([z, y, x]) / np.asarray(spacing_zyx)
        c["source_center_zyx"] = native.tolist()
        c["passport_page"] = f"passport_{index // 16 + 1:02d}.png"
        c["card_bbox_xyxy"] = [(index % 2) * 512, ((index % 16) // 2) * 128,
                               (index % 2 + 1) * 512, ((index % 16) // 2 + 1) * 128]
        if source and "sop_uids" in source:
            nearest = int(np.clip(np.rint(native[0]), 0, len(source["sop_uids"]) - 1))
            c["nearest_source_sop_uid"] = source["sop_uids"][nearest]
    output.mkdir(parents=True, exist_ok=True)
    render_overview(iso, mask, features, candidates, cfg.gamma).save(output / "overview.png")
    pages = []
    for start in range(0, max(1, len(candidates)), 16):
        page = Image.new("RGB", (1024, 1024), (12, 16, 22))
        if not candidates:
            d = ImageDraw.Draw(page)
            d.text((24, 32), "No candidates passed the configured mask / scale / threshold.", font=font(20), fill="white")
            d.text((24, 72), LIMITATION, font=font(14), fill="yellow")
        for j, c in enumerate(candidates[start:start + 16]):
            page.paste(_card(iso, features, c), ((j % 2) * 512, (j // 2) * 128))
        name = f"passport_{start // 16 + 1:02d}.png"
        page.save(output / name)
        pages.append(name)
    details = []
    if detail_cards:
        for c in candidates:
            name = f"{c['id']}_detail.png"
            _card(iso, features, c, zoom=3).save(output / name)
            details.append(name)
    warnings = [LIMITATION, "1 mm resampling and candidate truncation can lose small lesions; inspect native images.",
                "Heuristic air mask may exclude pleural/opaque lesions or include non-lung air cavities.",
                "RGB overlays may saturate; HU is the centre sample and diameter is a filter-scale estimate."]
    if not mask.any():
        warnings.append("Empty lung mask: no evaluable lung region was identified.")
    if len(candidates) == cfg.max_candidates:
        warnings.append("Candidate limit reached; additional candidates may be omitted.")
    if max(spacing_zyx) > 1:
        warnings.append("Source spacing exceeds 1 mm; interpolation does not restore missing resolution.")
    result = {"schema": "openrad.kroma.v1", "experimental": True, "warnings": warnings,
              "config": {**asdict(cfg), "scales_mm": list(cfg.scales_mm)},
              "geometry": {"source_shape_zyx": list(volume.shape), "source_spacing_zyx_mm": list(spacing_zyx),
              "iso_shape_zyx": list(iso.shape), "iso_spacing_zyx_mm": [1, 1, 1], "origin_lps_mm": list(origin_lps),
              "iop": [1, 0, 0, 0, 1, 0], "resampling": "linear; first voxel centre preserved; endpoint cropped by <1mm"},
              "estimated_array_memory_mib": math.ceil(estimate / 1024 ** 2), "lung_mask_voxels": int(mask.sum()),
              "blob_normalizer": features.blob_normalizer, "vessel_normalization": "raw dimensionless Frangi response [0,1]",
              "source": source, "candidates": candidates,
              "images": {"overview": "overview.png", "passport_pages": pages, "detail_cards": details},
              "layout": {"page_px": [1024, 1024], "candidates_per_page": 16, "fov_mm": 48,
                         "sheet_zoom": 2, "detail_zoom": 3, "planes": ["axial", "coronal", "sagittal"]}}
    (output / "passport.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    ap = argparse.ArgumentParser(prog="openrad passport", description=__doc__)
    ap.add_argument("study_dir", type=Path)
    ap.add_argument("--series", help="SeriesNumber or UID; optional only when exactly one CT series exists")
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--max-candidates", type=int, default=settings.kroma_max_candidates, help="1..32; 16 per passport page")
    ap.add_argument("--tau", type=float, default=settings.kroma_tau)
    ap.add_argument("--gamma", type=float, default=settings.kroma_gamma)
    ap.add_argument("--c-hu", type=float, default=settings.kroma_c_hu, help="positive Hessian structure-strength scale in HU")
    ap.add_argument("--block-size", type=int, default=settings.kroma_block_size)
    ap.add_argument("--memory-mb", type=int, default=settings.kroma_memory_mb,
                    help="array working-set guard in MiB, excluding DICOM decoder overhead")
    ap.add_argument("--detail-cards", action="store_true", help="also save individual 3x cards (summary sheets use 2x)")
    ap.add_argument("--json", action="store_true", help="also emit passport.json on stdout")
    a = ap.parse_args(argv)
    cfg = KromaConfig(tau=a.tau, gamma=a.gamma, c_hu=a.c_hu, max_candidates=a.max_candidates,
                      block_size=a.block_size, memory_mb=a.memory_mb)
    cfg.validate()
    if a.series:
        items = series_by_number(a.study_dir, a.series)
    else:
        if not a.study_dir.is_dir():
            raise InputError(f"Study folder not found: {a.study_dir}")
        groups = group_series((p, d) for p, d in read_headers(a.study_dir) if d.get("Modality") == "CT")
        if len(groups) != 1:
            raise InputError("Expected exactly one CT series; select one with --series")
        items = next(iter(groups.values()))
    # Preflight before Volume eagerly decodes/stacks source pixels.
    ds = items[0][1]
    source_shape = (len(items), int(ds.get("Rows", 0)), int(ds.get("Columns", 0)))
    _budget(source_shape, math.prod(source_shape) * 8, cfg)
    v = Volume(items)
    if v.modality != "CT":
        raise GeometryError("Passport requires calibrated CT HU")
    v.require_axial()
    v.require_rectilinear("Passport")
    if any("RescaleSlope" not in d or "RescaleIntercept" not in d for d in v.ds):
        raise GeometryError("CT HU rescale slope/intercept are required")
    result = create_passport(v.vol, a.output, spacing_zyx=(v.dz, v.row_sp, v.col_sp), origin_lps=v.ipp0,
                             config=cfg, detail_cards=a.detail_cards,
                             source={"series_uid": v.series_uid, "sop_uids": [v.sop_uid(k) for k in range(len(v.ds))]})
    if a.json:
        print(json.dumps(result, allow_nan=False))
    else:
        for name in ["overview.png", *result["images"]["passport_pages"], *result["images"]["detail_cards"], "passport.json"]:
            print(a.output / name)
    return 0
