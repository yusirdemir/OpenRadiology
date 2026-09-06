"""Seeded CT segmentation in physical space. No measurement-derived clipping.

The graph separates explicitly marked touching tissues; a region/edge level set
refines the interface on the native grid. Results are always reviewable drafts.
A crop boundary, weak edge or non-convergence is a limitation, never anatomy.
"""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull, QhullError

from .errors import InputError
from .pleural import effusion_envelope, enclosed_air, pleural_envelope, thoracic_cavity

VERSION = "physical-levelset-2-auto"
PROFILES = {"auto", "solid", "effusion", "bulla", "cyst"}
MAX_WORK_VOXELS = 8_000_000


def row_runs(plane: np.ndarray, ro: int = 0, co: int = 0) -> List[int]:
    out: List[int] = []
    for r in np.flatnonzero(plane.any(axis=1)):
        edges = np.diff(np.pad(plane[r].astype(np.int8), (1, 1)))
        for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            out.extend((int(r + ro), int(a + co), int(b - 1 + co)))
    return out


def gradient(a: np.ndarray, spacing: Sequence[float]) -> List[np.ndarray]:
    return [np.gradient(a, s, axis=i) if a.shape[i] > 1 else np.zeros_like(a) for i, s in enumerate(spacing)]


def seeded(mask: np.ndarray, points: Sequence[Sequence[int]]) -> np.ndarray:
    labels, _ = ndi.label(mask)
    ids = [labels[tuple(p)] for p in points if labels[tuple(p)] > 0]
    return np.isin(labels, ids) if ids else np.zeros_like(mask)


def marks(shape: Sequence[int], points: Sequence[Sequence[int]], spacing: np.ndarray, radius: float) -> np.ndarray:
    out = np.zeros(shape, bool)
    for p in points:
        bounds = [
            slice(max(0, int(v - np.ceil(radius / s))), min(n, int(v + np.ceil(radius / s)) + 1)) for v, s, n in zip(p, spacing, shape)
        ]
        grid = np.ogrid[tuple(slice(b.start, b.stop) for b in bounds)]
        d = sum(((g - v) * s) ** 2 for g, v, s in zip(grid, p, spacing))
        out[tuple(bounds)] |= d <= radius**2
    return out


def caliper(mask: np.ndarray, spacing: Sequence[float], offset: Sequence[int]) -> Dict[str, Any]:
    """Longest tested internal chord between boundary voxel centres.

    Convex-hull antipodes are tested first. Concave masks use all boundary
    candidates (bounded angular sampling), and reject chords through holes.
    It is explicitly a sampled chord, not a RECIST or subvoxel truth claim.
    """
    edge = mask & ~ndi.binary_erosion(mask)
    pts = np.argwhere(edge)
    if len(pts) < 2:
        return {"diameter_mm": 0.0, "caliper": None, "measurement_method": "internal-chord-voxel-centres"}
    physical = pts * np.asarray(spacing)
    try:
        hull = ConvexHull(physical)
        candidates = pts[hull.vertices]
    except QhullError:
        candidates = pts
    if len(candidates) > 128:
        candidates = candidates[np.linspace(0, len(candidates) - 1, 128).astype(int)]

    def best_chord(points: np.ndarray) -> Tuple[float, Any]:
        d = np.sum(((points[:, None] - points[None]) * spacing) ** 2, axis=2)
        for flat in np.argsort(d.ravel())[::-1][:512]:
            i, j = np.unravel_index(flat, d.shape)
            if i == j:
                continue
            a, b = points[i], points[j]
            # Four samples per voxel, both axes; no segment across air/holes.
            samples = np.linspace(a, b, max(2, int(np.max(np.abs(b - a))) * 4 + 1))
            ij = np.rint(samples).astype(int)
            if mask[ij[:, 0], ij[:, 1]].all():
                return float(np.sqrt(d[i, j])), (a, b)
        return 0.0, None

    length, pair = best_chord(candidates)
    if pair is None:
        candidates = pts[np.linspace(0, len(pts) - 1, min(128, len(pts))).astype(int)]
        length, pair = best_chord(candidates)
    ends = None if pair is None else [{"row": float(p[0] + offset[0]), "col": float(p[1] + offset[1])} for p in pair]
    return {"diameter_mm": round(length, 3), "caliper": ends, "measurement_method": "sampled-internal-chord-voxel-centres"}


def describe_planes(mask: np.ndarray, offset: np.ndarray, full_shape: Sequence[int], spacing: np.ndarray) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, axis, step, origin in [
        ("ax", 0, spacing[1:], offset[1:]),
        ("cor", 1, spacing[[0, 2]], np.array([full_shape[0] - offset[0] - mask.shape[0], offset[2]])),
        ("sag", 2, spacing[:2], np.array([full_shape[0] - offset[0] - mask.shape[0], offset[1]])),
    ]:
        planes = []
        for idx in range(mask.shape[axis]):
            plane = np.take(mask, idx, axis=axis)
            if name != "ax":
                plane = np.flipud(plane)
            if not plane.any():
                continue
            rc = np.argwhere(plane)
            mid = rc.mean(axis=0) + origin
            # Twice the deepest inscribed radius: the thickness of a layer, which
            # is the number that describes fluid, where a longest chord does not.
            thickness = 2 * float(ndi.distance_transform_edt(np.pad(plane, 1), sampling=step).max())
            planes.append(
                {
                    "index": int(idx + offset[axis]),
                    "row": float(mid[0]),
                    "col": float(mid[1]),
                    "row_min": int(rc[:, 0].min() + origin[0]),
                    "row_max": int(rc[:, 0].max() + origin[0]),
                    "col_min": int(rc[:, 1].min() + origin[1]),
                    "col_max": int(rc[:, 1].max() + origin[1]),
                    "pixels": len(rc),
                    "thickness_mm": round(thickness, 2),
                    "runs": row_runs(plane, *origin),
                    **caliper(plane, step, origin),
                }
            )
        out[name] = planes
    return out


def prepare(
    volume: Any,
    seed: Sequence[int],
    profile: str,
    positive: Sequence[Sequence[int]],
    negative: Sequence[Sequence[int]],
    brush_mm: float = 1.5,
) -> Dict[str, Any]:
    if volume.modality != "CT":
        raise InputError("Segmentasyon için HU kalibrasyonlu BT gereklidir.")
    volume.require_rectilinear("3D segmentation")
    # MPR and segmentation must share the same native stack geometry.
    volume.require_axial()
    if profile not in PROFILES:
        raise InputError("Unknown tissue profile")
    if not np.isfinite(brush_mm) or not 0.25 <= brush_mm <= 10:
        raise InputError("Brush radius must be 0.25..10 mm")
    shape = np.asarray(volume.vol.shape)
    spacing = np.array([volume.dz, volume.row_sp, volume.col_sp])
    points = [list(seed), *[list(p) for p in positive]]
    for p in [*points, *negative]:
        if len(p) != 3 or not np.isfinite(p).all() or np.any(np.asarray(p) < 0) or np.any(np.asarray(p) >= shape):
            raise InputError("Seed is outside this series")
    # A diameter is intentionally not an input. Expand a rectangular search
    # region based on observed connectivity, retaining native samples.
    centre = np.asarray(seed, int)
    hu = float(volume.vol[tuple(centre)])
    selected = ("bulla" if hu < -650 else "solid") if profile == "auto" else profile
    if (selected in {"solid", "effusion", "cyst"} and hu < -500) or (selected == "bulla" and hu > -450):
        raise InputError(f"Başlangıç {hu:.0f} HU: seçilen {selected} profiliyle uyuşmuyor. Hedefin içini işaretleyin.")
    # A dependent effusion is a long thin crescent along the wall: wide in
    # plane, shallow craniocaudally. Solid targets get a compact cube.
    half = np.array([40.0, 110.0, 110.0]) if selected == "effusion" else np.array([48.0, 48.0, 48.0])
    all_pts = np.asarray([*points, *negative], int)
    clipped = False
    pad = np.ceil(half / spacing).astype(int)
    low = np.maximum(0, all_pts.min(axis=0) - pad)
    high = np.minimum(shape, all_pts.max(axis=0) + pad + 1)
    while np.prod(high - low) > MAX_WORK_VOXELS:
        clipped = True
        pad = np.maximum(1, (pad * 0.8).astype(int))
        low = np.maximum(0, all_pts.min(axis=0) - pad)
        high = np.minimum(shape, all_pts.max(axis=0) + pad + 1)
    crop = volume.vol[tuple(slice(a, b) for a, b in zip(low, high))]
    # Enclosed air per axial slice, padded so the pleural envelope sees the
    # chest wall flanking the crop. Outside air needs the full slice.
    pad = np.ceil(45.0 / spacing[1:]).astype(int)
    r0, c0 = int(max(0, low[1] - pad[0])), int(max(0, low[2] - pad[1]))
    r1, c1 = int(min(shape[1], high[1] + pad[0])), int(min(shape[2], high[2] + pad[1]))
    lung = np.stack([enclosed_air(volume.vol[z], spacing[1:])[r0:r1, c0:c1] for z in range(int(low[0]), int(high[0]))])
    cavity = None
    if selected == "effusion":
        cavity = np.zeros_like(lung)
        for k, z in enumerate(range(int(low[0]), int(high[0]))):
            if lung[k].any():
                cavity[k] = thoracic_cavity(volume.vol[z], spacing[1:])[r0:r1, c0:c1]
    return {
        "image": np.array(crop, dtype=np.float32),
        "lung": lung,
        "cavity": cavity,
        "lung_offset": [int(low[0]), r0, c0],
        "spacing": spacing,
        "offset": low,
        "full_shape": shape.tolist(),
        "positive": (np.asarray(points) - low).tolist(),
        "negative": (np.asarray(negative, int).reshape(-1, 3) - low).tolist(),
        "profile": selected,
        "seed_hu": hu,
        "brush_mm": brush_mm,
        "crop_limited": clipped,
        "origin_lps": volume.patient_point(int(low[0]), float(low[1]), float(low[2])).tolist(),
        "direction": [*volume.normal.tolist(), *volume.iop[3:], *volume.iop[:3]],
        "series_uid": volume.series_uid,
        "native_origin_lps": volume.patient_point(0, 0, 0).tolist(),
        "frame_uid": getattr(volume, "frame_uid", ""),
    }


def solve(data: Dict[str, Any]) -> Dict[str, Any]:
    start = time.monotonic()
    image, spacing, offset = data["image"], data["spacing"], data["offset"]
    positive, negative = data["positive"], data["negative"]
    profile = data["profile"]
    valid = np.isfinite(image) & (image > -2000)
    smooth = ndi.gaussian_filter(np.where(valid, image, -1024), 0.7 / spacing)
    pos = marks(image.shape, positive, spacing, data["brush_mm"])
    neg = marks(image.shape, negative, spacing, data["brush_mm"])
    if (pos & neg).any():
        raise InputError("İçeri ve dışarı işaretleri çakışıyor; son işareti geri alın.")
    sample = smooth[pos & valid]
    if not len(sample):
        raise InputError("No valid HU samples in seed")
    centre = float(np.median(sample))
    noise = max(15.0, float(np.median(np.abs(sample - centre))) * 1.4826)
    if profile == "bulla":
        lo, hi = -1100.0, min(-450.0, centre + max(100, 3 * noise))
    elif profile == "effusion":
        # Simple fluid is a narrow physiological band; scaling it with slice
        # noise let a thick-slice effusion swallow fat and muscle.
        lo, hi = max(-40.0, centre - 45), min(75.0, centre + 45)
    elif profile == "cyst":
        lo, hi = max(-200.0, centre - max(70, 3 * noise)), centre + max(80, 3 * noise)
    elif centre > 450:
        lo, hi = 150.0, 4000.0
    else:
        lo, hi = max(-450.0, centre - max(180, 4 * noise)), centre + max(250, 4 * noise)
    admissible = valid & (smooth > lo) & (smooth < hi)
    # Pleural tangent closure: outside the virtual lung envelope is chest wall.
    closure: Dict[str, Any] = {"active": False}
    lung = data.get("lung")
    z0, r0, c0 = data.get("lung_offset", offset.tolist())
    shift = np.array([offset[0] - z0, offset[1] - r0, offset[2] - c0], int)
    if lung is not None and profile == "effusion" and data.get("cavity") is not None:
        envelope, closure = effusion_envelope(lung, data["cavity"], np.asarray(positive[0], int) + shift, spacing)
        closure = {**closure, "closures": [], "model": "pleural-space"}
        if closure["active"]:
            admissible &= envelope[
                shift[0] : shift[0] + image.shape[0], shift[1] : shift[1] + image.shape[1], shift[2] : shift[2] + image.shape[2]
            ]
    elif lung is not None:
        envelope, closure = pleural_envelope(lung, np.asarray(positive[0], int) + shift, spacing)
        if closure["active"]:
            admissible &= envelope[
                shift[0] : shift[0] + image.shape[0], shift[1] : shift[1] + image.shape[1], shift[2] : shift[2] + image.shape[2]
            ]
    pos &= admissible
    if not pos.any():
        raise InputError("Seed does not contain the selected tissue")
    mask = seeded(admissible & ~neg, positive)
    automatic_partition = False
    basin_prior = np.zeros_like(smooth)
    # The distance transform measures local tissue thickness in mm. Its
    # watershed splits a bulky lesion from a thinner attached wall or vessel.
    # This is a shape prior, explicitly reported, not an observed HU edge.
    # Fluid is not assumed to be compact and keeps its unrestricted footprint.
    if profile != "effusion" and not negative and mask.any():
        from skimage.morphology import h_maxima
        from skimage.segmentation import watershed

        thickness = ndi.distance_transform_edt(mask, sampling=spacing)
        thickness = ndi.gaussian_filter(thickness, 0.7 / spacing)
        peaks, count = ndi.label(h_maxima(thickness, 1.5) & mask)
        if count > 1:
            basins = watershed(-thickness, peaks, mask=mask)
            selected_ids = [int(basins[tuple(p)]) for p in positive if basins[tuple(p)] > 0]
            proposal = np.isin(basins, selected_ids)
            if proposal.any() and not np.array_equal(proposal, mask):
                automatic_partition = True
                mask = proposal
                basin_prior = np.where(proposal, 0.5, -1.5).astype(np.float32)
    grad = gradient(smooth, spacing)
    magnitude = np.sqrt(sum(g * g for g in grad))
    kappa = max(30.0, 2 * noise / min(spacing))
    edge = 1 / (1 + (magnitude / kappa) ** 2)
    # Explicit background marks define a competitive geodesic partition in
    # millimetres. Without them, equal-density contact remains unresolved.
    competition = np.zeros_like(smooth)
    if negative:
        from skimage.graph import MCP_Geometric

        costs = np.where(admissible, 1 + magnitude / kappa, np.inf)
        dp, _ = MCP_Geometric(costs, sampling=tuple(spacing)).find_costs(np.argwhere(pos))
        dn, _ = MCP_Geometric(np.where(valid, 1 + magnitude / kappa, np.inf), sampling=tuple(spacing)).find_costs(np.argwhere(neg))
        competition = np.divide(
            dn - np.minimum(dp, 1e6), dn + np.minimum(dp, 1e6) + 1e-6, out=np.zeros_like(smooth), where=np.isfinite(dn)
        ).astype(np.float32)
        mask &= competition >= 0
    if not mask.any():
        raise InputError("No connected target tissue found")
    # Negative inside, positive outside. Physical signed distance and curvature.
    phi = (ndi.distance_transform_edt(~mask, sampling=spacing) - ndi.distance_transform_edt(mask, sampling=spacing)).astype(np.float32)
    edge_grad = gradient(edge, spacing)
    # HU likelihood is a broad tissue prior; local inside/outside means refine
    # only where they are distinguishable. Fixed seeds survive every update.
    prior = np.minimum((smooth - lo), (hi - smooth)) / max(noise * 2, 50)
    prior = np.clip(prior, -2, 1)
    converged = False
    changes = []
    iterations = 0
    for iteration in range(48):
        old = phi <= 0
        inside = old.astype(np.float32)
        sigma = 2.5 / spacing
        wi = ndi.gaussian_filter(inside, sigma)
        mu_i = ndi.gaussian_filter(smooth * inside, sigma) / np.maximum(wi, 1e-4)
        mu_o = ndi.gaussian_filter(smooth * (1 - inside), sigma) / np.maximum(1 - wi, 1e-4)
        distinguishable = (wi > 0.05) & (wi < 0.95) & (np.abs(mu_i - mu_o) > noise)
        region = np.clip(((smooth - mu_o) ** 2 - (smooth - mu_i) ** 2) / max(noise**2 * 8, 2500), -2, 2)
        force = np.where(distinguishable, region, prior * 0.25) + competition * 1.5 + basin_prior
        gp = gradient(phi, spacing)
        norm = np.sqrt(sum(g * g for g in gp) + 1e-6)
        curvature = sum(gradient(g / norm, spacing)[axis] for axis, g in enumerate(gp))
        advection = sum(a * b for a, b in zip(edge_grad, gp))
        delta = (0.18 * edge * curvature - force) * norm + 0.25 * advection
        band = np.abs(phi) <= max(2.5, float(spacing.max()))
        dt = 0.25 * float(spacing.min()) / max(1.0, float(np.max(np.abs(delta[band]))))
        phi[band] += dt * delta[band]
        phi[~admissible | neg] = np.maximum(phi[~admissible | neg], spacing.min() * 0.5)
        phi[pos] = -spacing.min()
        change = int(np.count_nonzero((phi <= 0) != old))
        changes.append(change)
        iterations = iteration + 1
        if iteration >= 7 and max(changes[-6:]) == 0:
            converged = True
            break
        if iteration % 8 == 7:
            m = phi <= 0
            phi = (ndi.distance_transform_edt(~m, sampling=spacing) - ndi.distance_transform_edt(m, sampling=spacing)).astype(np.float32)
    mask = seeded((phi <= 0) & ~neg, positive) | pos
    # Vessel-calibre tendrils: a morphological opening scaled to the lesion
    # removes structures thinner than twice the radius; the seeded core is
    # then re-dressed with the original surface within that radius.
    pruned = 0
    equivalent = 2 * (3 * float(mask.sum()) * float(spacing.prod()) / (4 * np.pi)) ** (1 / 3)
    calibre = float(np.clip(0.12 * equivalent, 1.0, 3.0))
    if calibre > float(spacing.min()):
        eroded = ndi.distance_transform_edt(mask, sampling=spacing) >= calibre
        core = seeded(ndi.distance_transform_edt(~eroded, sampling=spacing) <= calibre, positive)
        if core.any():
            kept = mask & (ndi.distance_transform_edt(~core, sampling=spacing) <= calibre + float(spacing.min()))
            pruned = int(mask.sum() - kept.sum())
            mask = kept | pos
    boundary = mask & ~ndi.binary_erosion(mask)
    crop_edge = np.zeros_like(mask)
    for axis in range(3):
        for end in (0, -1):
            sl = [slice(None)] * 3
            sl[axis] = end
            crop_edge[tuple(sl)] = True
    contact = int((mask & crop_edge).sum())
    weak = float(np.mean(magnitude[boundary] < kappa * 0.5)) if boundary.any() else 1.0
    reasons = []
    if (contact or data["crop_limited"]) and closure.get("active"):
        reasons.append("Arama alanı sınırına ulaşıldı: hedef bunun ötesinde devam edebilir")
    elif contact or data["crop_limited"]:
        reasons.append("Arama alanına temas: anatomik kapsam kapanmadı")
    if weak > 0.25:
        reasons.append("Düşük sınır kontrastı")
    if automatic_partition:
        reasons.append("Temas yüzeyi fiziksel doku kalınlığı modeliyle ayrıldı")
    if pruned > max(8, 0.005 * mask.sum()):
        reasons.append(f"Damar kalibreli uzantılar budandı ({calibre:.1f} mm açma)")
    if not converged:
        reasons.append("Level-set iterasyon sınırına ulaştı")
    # Only closures whose arc actually bounds the final mask are reported.
    touched = ndi.binary_dilation(mask, iterations=2)
    used = []
    for c in closure.get("closures", []):
        z = c["index"] - shift[0]
        if not 0 <= z < mask.shape[0]:
            continue
        arc = np.rint(np.asarray(c["arc_rc"]) - shift[1:]).astype(int)
        inside = (arc[:, 0] >= 0) & (arc[:, 0] < mask.shape[1]) & (arc[:, 1] >= 0) & (arc[:, 1] < mask.shape[2])
        if inside.any() and touched[z, arc[inside, 0], arc[inside, 1]].any():
            used.append(
                {
                    "index": int(c["index"] + z0),
                    "p1": [c["p1_rc"][0] + r0, c["p1_rc"][1] + c0],
                    "p2": [c["p2_rc"][0] + r0, c["p2_rc"][1] + c0],
                    "arc": [[r + r0, col + c0] for r, col in c["arc_rc"]],
                    "chord_mm": c["chord_mm"],
                    "bulge_mm": c["bulge_mm"],
                    "flank_points": c["flank_points"],
                }
            )
    if used:
        reasons.append("Plevral temas: sınır göğüs duvarı yayıyla kapatıldı (P1-P2 spline)")
    # A region escaping the search domain must not become a giant glowing mass.
    # Inside an anatomical envelope the shape is bounded by anatomy, so a touch
    # at the crop edge is a coverage limit that is reported, not a leak.
    drawable = mask if (not contact or closure.get("active")) else np.zeros_like(mask)
    rc = np.argwhere(drawable)
    render_offset = offset.copy()
    render_mask = drawable
    render_image = image
    if len(rc):
        lo_render = np.maximum(0, rc.min(axis=0) - 1)
        hi_render = np.minimum(drawable.shape, rc.max(axis=0) + 2)
        render_sl = tuple(slice(a, b) for a, b in zip(lo_render, hi_render))
        render_offset += lo_render
        render_mask = drawable[render_sl]
        render_image = image[render_sl]
    else:
        render_mask = np.zeros((1, 1, 1), bool)
        render_image = np.full((1, 1, 1), -1024, np.float32)
    planes = describe_planes(render_mask, render_offset, data["full_shape"], spacing)
    by_index: Dict[int, List[Dict[str, Any]]] = {}
    for c in used:
        by_index.setdefault(c["index"], []).append(c)
    for plane in planes["ax"]:
        if plane["index"] in by_index:
            plane["closure"] = by_index[plane["index"]]
    focus = np.asarray(positive[0], int)
    if len(rc):
        # Closest in-mask voxel to the physical centroid: an effusion crescent's
        # arithmetic centroid can lie in lung, so never focus there blindly.
        centroid = rc.mean(axis=0)
        focus = rc[np.argmin(np.sum(((rc - centroid) * spacing) ** 2, axis=1))]
    focus_global = focus + offset
    digest = hashlib.sha256(VERSION.encode() + mask.tobytes() + repr((data["profile"], positive, negative)).encode()).hexdigest()[:20]
    voxels = int(drawable.sum())
    volume = voxels * float(np.prod(spacing)) / 1000
    return {
        "version": VERSION,
        "id": digest,
        "series_uid": data["series_uid"],
        "frame_uid": data.get("frame_uid", ""),
        "native_origin_lps": data.get("native_origin_lps", [0, 0, 0]),
        "mask_origin_lps": (
            np.asarray(data["origin_lps"]) + ((render_offset - offset) * spacing) @ np.asarray(data["direction"]).reshape(3, 3)
        ).tolist(),
        "status": "needs-review" if reasons else "draft",
        "reasons": reasons,
        "seed": dict(zip(("index", "row", "col"), map(int, np.asarray(positive[0]) + offset))),
        "seed_hu": data["seed_hu"],
        "band": [round(lo, 2), round(hi, 2)],
        "tissue": profile,
        "focus_zyx": focus_global.tolist(),
        "centroid_lps": (np.asarray(data["origin_lps"]) + (focus * spacing) @ np.asarray(data["direction"]).reshape(3, 3)).tolist(),
        "shape_zyx": data["full_shape"],
        "spacing_zyx": spacing.tolist(),
        "mask_origin_zyx": render_offset.tolist(),
        "mask_shape_zyx": list(render_mask.shape),
        "hu_data": base64.b64encode(np.nan_to_num(render_image, nan=-1024).clip(-32768, 32767).astype("<i2").tobytes()).decode("ascii"),
        "planes": planes,
        "slices": planes["ax"],
        "voxels": voxels,
        "volume_ml": round(volume, 3),
        "craniocaudal_mm": round(float((rc[:, 0].max() - rc[:, 0].min() + 1) * spacing[0]), 2) if len(rc) else 0,
        "established": False,
        "reason": "review-required",
        "extent": [planes["ax"][0]["index"], planes["ax"][-1]["index"]] if planes["ax"] else [int(focus_global[0])] * 2,
        "heat_range": [round(float(np.percentile(image[drawable], 2)), 2), round(float(np.percentile(image[drawable], 98)) + 1, 2)]
        if voxels
        else [lo, hi],
        "pleural_closure": {
            "active": bool(closure.get("active")),
            "radius_mm": closure.get("radius_mm"),
            "flank_mm": closure.get("flank_mm"),
            "method": closure.get("model", "rolling-disc-tangents+cubic-hermite-wall-fit"),
            "reach_mm": closure.get("reach_mm"),
            "slices": used,
        },
        "quality": {
            "converged": converged,
            "iterations": iterations,
            "weak_boundary_fraction": weak,
            "crop_contact_voxels": contact,
            "automatic_partition": automatic_partition,
            "pruned_voxels": pruned,
            "prune_calibre_mm": round(calibre, 2),
        },
        "provenance": {
            "positive_zyx": (np.asarray(positive) + offset).tolist(),
            "negative_zyx": (np.asarray(negative).reshape(-1, 3) + offset).tolist(),
            "brush_mm": data["brush_mm"],
            "native_grid": True,
            "sigma_mm": 0.7,
        },
        "elapsed_ms": round((time.monotonic() - start) * 1000),
    }
