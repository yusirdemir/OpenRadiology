"""Pleural tangent closure: a geometric envelope for juxtapleural lesions.

A lesion that abuts the chest wall has no HU edge on its pleural side; the
mass and the wall are both soft tissue. Instead of guessing that edge, this
module reconstructs the pleural line the way a radiologist's eye does: it
takes the two anatomical junctions where the lesion leaves the lung and meets
the wall (P1, P2) and continues the smooth curvature of the surrounding
thoracic wall between them.

Per axial slice:

1. ``enclosed_air`` -- air enclosed by the body (lungs, holes filled).
2. ``rolling_closure`` -- morphological closing with a disc of radius R mm
   (distance-transform implementation). Every juxtapleural concavity of the
   lung outline, including the one a lesion carves, becomes a *cap* region.
3. On the ordered outer contour of the closed lung, each cap is a run of
   contour points that do not touch lung. Its ends are the tangent points
   P1 and P2.
4. A cubic Hermite arc through P1 and P2 whose two free parameters are fitted
   by weighted least squares to the pleural contour flanking the gap (the
   ribs' inner curvature). In the chord frame ``y = x (x - L) (a + b x)``.
5. The arcs are rasterised; ``fill_holes(lung | arcs)`` is the virtual lung
   envelope. Voxels outside it are chest wall, never lesion.

Everything returned is reviewable geometry: the tangent points, the arc, the
chord length and how far the arc bulges. It is a boundary model, reported as
such, not an observed edge.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage as ndi
from skimage.draw import line as draw_line
from skimage.measure import find_contours

AIR_HU = -450.0
RADII_MM = (8.0, 14.0, 22.0, 32.0, 45.0)
FLANK_MM = 25.0
MIN_CAP_MM2 = 2.0
MIN_CAP_DEPTH_MM = 2.0
MERGE_GAP = 3
MAX_BULGE_RATIO = 0.35


def enclosed_air(image2d: np.ndarray, spacing_rc: Sequence[float], min_area_mm2: float = 30.0) -> np.ndarray:
    """Air enclosed by the body on one axial slice, holes filled (lung parenchyma)."""
    smooth = ndi.gaussian_filter(np.nan_to_num(np.asarray(image2d, np.float32), nan=-1024.0), 1.0)
    air = smooth < AIR_HU
    labels, count = ndi.label(air)
    if count == 0:
        return np.zeros(air.shape, bool)
    border = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    lung = air & ~np.isin(labels, border[border > 0])
    labels, count = ndi.label(lung)
    if count:
        areas = ndi.sum(lung, labels, np.arange(1, count + 1)) * float(spacing_rc[0]) * float(spacing_rc[1])
        lung = np.isin(labels, np.flatnonzero(areas >= min_area_mm2) + 1)
    return ndi.binary_fill_holes(lung)


def rolling_closure(region: np.ndarray, spacing_rc: Sequence[float], radius_mm: float) -> np.ndarray:
    """Binary closing with a disc of ``radius_mm``; exact in millimetres via distance transforms."""
    if not region.any():
        return region.copy()
    # Beyond the image is background: pad by the radius so the erosion is
    # not blind at the border and the closing cannot grow out of the frame.
    pad = tuple(int(np.ceil(radius_mm / float(s))) + 1 for s in spacing_rc)
    padded = np.pad(region, [(p, p) for p in pad])
    dilated = ndi.distance_transform_edt(~padded, sampling=spacing_rc) <= radius_mm
    # Half a pixel of tolerance keeps the staircase of a digitised outline
    # from surfacing as one-pixel slivers along the wall.
    closed = ndi.distance_transform_edt(dilated, sampling=spacing_rc) >= radius_mm + 0.5 * float(min(spacing_rc))
    closed = closed[pad[0] : pad[0] + region.shape[0], pad[1] : pad[1] + region.shape[1]]
    return closed | region


def _cyclic_runs(flags: np.ndarray) -> List[Tuple[int, int]]:
    n = len(flags)
    if not flags.any():
        return []
    if flags.all():
        return [(0, n - 1)]
    k = int(np.argmin(flags))
    rolled = np.roll(flags, -k).astype(np.int8)
    edges = np.diff(np.concatenate([[0], rolled, [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1
    return [(int((s + k) % n), int((e + k) % n)) for s, e in zip(starts, ends)]


def _walk(pts_mm: np.ndarray, start: int, step: int, limit_mm: float, limit_count: int) -> List[int]:
    """Contour indices from ``start`` (inclusive) along ``step`` until ``limit_mm`` of arc length."""
    n = len(pts_mm)
    out = [start]
    length = 0.0
    while len(out) < limit_count:
        nxt = (out[-1] + step) % n
        length += float(np.hypot(*(pts_mm[nxt] - pts_mm[out[-1]])))
        if length > limit_mm:
            break
        out.append(nxt)
    return out


def hermite_arc(p1: np.ndarray, p2: np.ndarray, flank: np.ndarray, weights: np.ndarray, samples: int) -> Tuple[np.ndarray, float, float]:
    """Cubic arc through ``p1`` and ``p2`` (mm) whose curvature is fitted to ``flank`` points.

    Chord frame: ``x`` along P1->P2 (length L), ``y`` perpendicular. The arc
    ``y = x (x - L) (a + b x)`` is the unique cubic family that passes through
    both tangent points; ``a, b`` are the ridge-regularised weighted least
    squares solution against the flanking wall. Returns the sampled arc
    (mm), the chord length and the maximal signed bulge (positive away from
    the side the flank centre lies on, i.e. into the wall).
    """
    chord = p2 - p1
    length = float(np.hypot(*chord))
    ex = chord / length
    ey = np.array([-ex[1], ex[0]])
    if len(flank) and float(np.dot(flank.mean(axis=0) - p1, ey)) > 0:
        ey = -ey
    u = np.linspace(0.0, 1.0, samples)
    alpha = beta = 0.0
    if len(flank) >= 3:
        rel = flank - p1
        fx, fy = rel @ ex / length, rel @ ey / length
        g1, g2 = fx * (fx - 1), fx * fx * (fx - 1)
        design = np.stack([g1, g2], axis=1) * np.sqrt(weights)[:, None]
        target = fy * np.sqrt(weights)
        total = float(weights.sum())
        normal = design.T @ design + np.diag([1e-4 * total, 0.01 * total])
        alpha, beta = np.linalg.solve(normal, design.T @ target)
    y = u * (u - 1) * (alpha + beta * u)
    bulge = float(np.max(np.abs(y)))
    if bulge > MAX_BULGE_RATIO:
        y *= MAX_BULGE_RATIO / bulge
    arc = p1[None, :] + (u * length)[:, None] * ex[None, :] + (y * length)[:, None] * ey[None, :]
    signed = float(y[np.argmax(np.abs(y))] * length)
    return arc, length, signed


def close_slice(
    lung: np.ndarray, spacing_rc: Sequence[float], radius_mm: float, flank_mm: float = FLANK_MM
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """Virtual lung envelope for one slice plus the tangent closures used to build it."""
    spacing = np.asarray(spacing_rc, float)
    if not lung.any():
        return lung.copy(), []
    closed = rolling_closure(lung, spacing, radius_mm)
    caps, cap_count = ndi.label(closed & ~lung)
    if cap_count == 0:
        return lung.copy(), []
    ids = np.arange(1, cap_count + 1)
    areas = ndi.sum(closed & ~lung, caps, ids) * float(spacing.prod())
    # A cap is a concavity, not a sliver: it must be at least MIN_CAP_DEPTH_MM deep.
    depth = ndi.maximum(ndi.distance_transform_edt(caps > 0, sampling=spacing), caps, ids)
    areas = np.where(depth >= MIN_CAP_DEPTH_MM, areas, 0.0)
    near_lung = ndi.binary_dilation(lung, structure=np.ones((3, 3), bool))
    near_cap = ndi.grey_dilation(caps, size=(3, 3))
    arcs = np.zeros(lung.shape, bool)
    closures: List[Dict[str, Any]] = []
    for contour in find_contours(np.pad(closed, 1).astype(np.float32), 0.5):
        pts = np.asarray(contour[:-1], float) - 1.0
        if len(pts) < 8:
            continue
        idx = np.clip(np.rint(pts).astype(int), 0, np.asarray(lung.shape) - 1)
        flags = ~near_lung[idx[:, 0], idx[:, 1]]
        pts_mm = pts * spacing
        n = len(pts)
        # Runs of wall-only contour points. A corner can split one gap's run
        # by a point or two; runs of the same cap separated by at most
        # MERGE_GAP contour points are one gap. Distinct gaps of one cap
        # (a cap that wraps around a lobe) stay separate arcs.
        pieces: List[Tuple[int, int, int]] = []
        for start, end in _cyclic_runs(flags):
            run = np.arange(start, start + ((end - start) % n) + 1) % n
            labels = near_cap[idx[run, 0], idx[run, 1]]
            labels = labels[labels > 0]
            if not len(labels):
                continue
            pieces.append((start, end, int(np.bincount(labels).argmax())))
        pieces.sort()
        merged: List[Tuple[int, int, int]] = []
        for piece in pieces:
            if merged and merged[-1][2] == piece[2] and (piece[0] - merged[-1][1]) % n <= MERGE_GAP + 1:
                merged[-1] = (merged[-1][0], piece[1], piece[2])
            else:
                merged.append(piece)
        if len(merged) > 1 and merged[0][2] == merged[-1][2] and (merged[0][0] - merged[-1][1]) % n <= MERGE_GAP + 1:
            merged[0] = (merged[-1][0], merged[0][1], merged[0][2])
            merged.pop()
        for start, end, cap_id in merged:
            if areas[cap_id - 1] < MIN_CAP_MM2:
                continue
            run = np.arange(start, start + ((end - start) % n) + 1) % n
            i1, i2 = (start - 1) % n, (end + 1) % n
            if len(run) >= n - 2:
                continue
            budget = max(2, (n - len(run) - 2) // 2)
            before = _walk(pts_mm, i1, -1, flank_mm, budget)[1:]
            after = _walk(pts_mm, i2, +1, flank_mm, budget)[1:]
            p1, p2 = pts_mm[i1], pts_mm[i2]
            length = float(np.hypot(*(p2 - p1)))
            if length < 1e-3:
                continue
            flank_idx = np.array(before + after, int)
            flank = pts_mm[flank_idx] if len(flank_idx) else np.zeros((0, 2))
            dist = np.concatenate(
                [
                    np.cumsum([np.hypot(*(pts_mm[j] - pts_mm[k])) for j, k in zip(before, [i1, *before[:-1]])]),
                    np.cumsum([np.hypot(*(pts_mm[j] - pts_mm[k])) for j, k in zip(after, [i2, *after[:-1]])]),
                ]
            )
            weights = 1.0 / (1.0 + (dist / max(flank_mm / 2, 1.0)) ** 2)
            samples = int(max(8, np.ceil(length / (0.5 * float(spacing.min())))))
            arc, chord, bulge = hermite_arc(p1, p2, flank, weights, samples)
            arc_px = np.clip(np.rint(arc / spacing).astype(int), 0, np.asarray(lung.shape) - 1)
            for (r0, c0), (r1, c1) in zip(arc_px[:-1], arc_px[1:]):
                rr, cc = draw_line(int(r0), int(c0), int(r1), int(c1))
                arcs[rr, cc] = True
            closures.append(
                {
                    "p1_rc": [float(pts[i1, 0]), float(pts[i1, 1])],
                    "p2_rc": [float(pts[i2, 0]), float(pts[i2, 1])],
                    "arc_rc": (arc / spacing)[:: max(1, samples // 24)].round(2).tolist(),
                    "chord_mm": round(chord, 2),
                    "bulge_mm": round(bulge, 2),
                    "flank_points": int(len(flank_idx)),
                    "cap": cap_id,
                }
            )
    if not closures:
        return lung.copy(), []
    # Anchor every arc end onto the nearest lung pixel so the envelope is watertight.
    _, nearest = ndi.distance_transform_edt(~lung, return_distances=True, return_indices=True)
    for c in closures:
        for key in ("p1_rc", "p2_rc"):
            r, col = np.clip(np.rint(c[key]).astype(int), 0, np.asarray(lung.shape) - 1)
            rr, cc = draw_line(int(r), int(col), int(nearest[0, r, col]), int(nearest[1, r, col]))
            arcs[rr, cc] = True
    virtual = ndi.binary_fill_holes(lung | arcs)
    for c in closures:
        c["cap"] = int(((caps == c["cap"]) & virtual).sum())
    return virtual, closures


def choose_radius(lung: np.ndarray, seed_rc: Sequence[int], spacing_rc: Sequence[float]) -> Optional[float]:
    """Smallest closing radius whose envelope contains the seed on its own slice, or None."""
    r, c = int(seed_rc[0]), int(seed_rc[1])
    if not (0 <= r < lung.shape[0] and 0 <= c < lung.shape[1]):
        return None
    if lung[r, c]:
        return RADII_MM[0]
    for radius in RADII_MM:
        if rolling_closure(lung, spacing_rc, radius)[r, c]:
            return radius
    return None


def pleural_envelope(
    lung: np.ndarray, seed_zrc: Sequence[int], spacing_zrc: Sequence[float], flank_mm: float = FLANK_MM
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Virtual lung envelope for a stack of axial lung masks.

    ``lung`` is ``(z, rows, cols)`` enclosed air. The closing radius is chosen
    on the seed slice (the smallest disc that bridges the lesion's concavity)
    and widened by half so neighbouring slices where the lesion is broader
    still close. A seed outside every envelope means the target is not
    intrapulmonary; the caller then applies no clipping.
    """
    spacing_rc = np.asarray(spacing_zrc[1:], float)
    z = int(seed_zrc[0])
    if not 0 <= z < lung.shape[0]:
        return lung.copy(), {"active": False, "reason": "seed outside lung stack"}
    base = choose_radius(lung[z], seed_zrc[1:], spacing_rc)
    if base is None:
        return lung.copy(), {"active": False, "reason": "seed outside the pleural envelope"}
    radius = float(min(RADII_MM[-1], max(10.0, base * 1.5)))
    virtual = np.zeros(lung.shape, bool)
    slices: List[Dict[str, Any]] = []
    for k in range(lung.shape[0]):
        virtual[k], closures = close_slice(lung[k], spacing_rc, radius, flank_mm)
        for c in closures:
            c["index"] = k
        slices.extend(closures)
    if not virtual[z, int(seed_zrc[1]), int(seed_zrc[2])]:
        return lung.copy(), {"active": False, "reason": "seed outside the closed envelope"}
    return virtual, {"active": True, "radius_mm": radius, "flank_mm": flank_mm, "closures": slices}


# ----------------------------------------------------------------- effusion
# A dependent effusion does not carve a concavity into the lung: it is a layer
# between the lung (or diaphragm) and the inner thoracic wall. Its envelope is
# therefore the pleural space itself: inside the rib cage, outside the lung,
# and within reach of the lung along the wall.

BONE_HU = 150.0
CAVITY_RADIUS_MM = 45.0
PLEURAL_REACH_MM = 35.0
# Dependent fluid pools a little below the lung base in the sulcus, not deep
# into the abdomen: the craniocaudal reach is kept much shorter than in-plane.
PLEURAL_REACH_Z_MM = 15.0


def thoracic_cavity(image2d: np.ndarray, spacing_rc: Sequence[float]) -> np.ndarray:
    """Interior of the rib cage on one axial slice.

    Bone (ribs, sternum, vertebra, calcified cartilage) closed with a large disc
    becomes a ring; what the ring encloses, minus the ring, is the cavity. If the
    ring does not close (uncalcified anterior cartilage), nothing is enclosed
    and the caller falls back to the reach of the lung alone.
    """
    smooth = ndi.gaussian_filter(np.nan_to_num(np.asarray(image2d, np.float32), nan=-1024.0), 1.0)
    bone = smooth > BONE_HU
    labels, count = ndi.label(bone)
    if count:
        areas = ndi.sum(bone, labels, np.arange(1, count + 1)) * float(spacing_rc[0]) * float(spacing_rc[1])
        bone = np.isin(labels, np.flatnonzero(areas >= 8.0) + 1)
    if not bone.any():
        return np.zeros(bone.shape, bool)
    ring = rolling_closure(bone, spacing_rc, CAVITY_RADIUS_MM)
    inside = ndi.binary_fill_holes(ring) & ~ring
    labels, count = ndi.label(inside)
    if count == 0:
        return inside
    # The cavity is the largest enclosed area; anything else is a hole in a bone.
    areas = ndi.sum(inside, labels, np.arange(1, count + 1))
    return labels == (int(np.argmax(areas)) + 1)


def effusion_envelope(
    lung: np.ndarray, cavity: np.ndarray, seed_zrc: Sequence[int], spacing_zrc: Sequence[float], reach_mm: float = PLEURAL_REACH_MM
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Pleural space envelope for a fluid seed: within ``reach_mm`` of lung, inside the rib cage, not lung."""
    spacing = np.asarray(spacing_zrc, float)
    if not lung.any():
        return np.zeros(lung.shape, bool), {"active": False, "reason": "no lung in the search volume"}
    stretched = spacing * np.array([reach_mm / PLEURAL_REACH_Z_MM, 1.0, 1.0])
    reach = ndi.distance_transform_edt(~lung, sampling=stretched) <= reach_mm
    envelope = reach & ~lung
    closed = cavity.any(axis=(1, 2))
    # Slices whose rib ring closed are bounded by it; the others by reach alone.
    envelope[closed] &= cavity[closed]
    z, r, c = (int(v) for v in seed_zrc)
    if not (0 <= z < envelope.shape[0] and 0 <= r < envelope.shape[1] and 0 <= c < envelope.shape[2]) or not envelope[z, r, c]:
        return envelope, {"active": False, "reason": "seed outside the pleural space"}
    return envelope, {
        "active": True,
        "reach_mm": reach_mm,
        "reach_z_mm": PLEURAL_REACH_Z_MM,
        "cavity_slices": int(closed.sum()),
        "slices": int(envelope.shape[0]),
    }
