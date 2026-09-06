"""Pleural tangent closure: geometry phantoms."""

import numpy as np

from openrad.pleural import close_slice, enclosed_air, hermite_arc, pleural_envelope, rolling_closure
from openrad.segmentation import prepare, solve
from tests.test_segmentation import unpack


def disc(shape, centre, radius, spacing=(1.0, 1.0)):
    rr, cc = np.indices(shape)
    return ((rr - centre[0]) * spacing[0]) ** 2 + ((cc - centre[1]) * spacing[1]) ** 2 <= radius**2


def test_hermite_arc_recovers_a_circle_from_its_flanks():
    # Flanks on a circle of radius 90 mm; the gap spans 60 degrees.
    radius = 90.0
    angles = np.deg2rad(np.arange(-40, 41, 2.0))
    gap = np.abs(angles) <= 30 * np.pi / 180
    circle = np.stack([radius * np.cos(angles), radius * np.sin(angles)], axis=1)
    p1, p2 = circle[np.flatnonzero(gap)[0]], circle[np.flatnonzero(gap)[-1]]
    flank = circle[~gap]
    arc, chord, bulge = hermite_arc(p1, p2, flank, np.ones(len(flank)), 64)
    assert abs(chord - 2 * radius * np.sin(np.deg2rad(30))) < 1e-6
    # Sagitta of a 60 degree arc: R (1 - cos 30).
    assert abs(bulge - radius * (1 - np.cos(np.deg2rad(30)))) < 1.5
    assert np.all(np.abs(np.hypot(arc[:, 0], arc[:, 1]) - radius) < 1.5)
    assert np.allclose(arc[0], p1) and np.allclose(arc[-1], p2)


def test_straight_chord_without_flanks():
    arc, chord, bulge = hermite_arc(np.array([0.0, 0.0]), np.array([10.0, 0.0]), np.zeros((0, 2)), np.zeros(0), 8)
    assert chord == 10 and bulge == 0 and np.all(arc[:, 1] == 0)


def test_enclosed_air_ignores_outside_air_and_fills_vessels():
    image = np.full((120, 120), 40.0, np.float32)
    image[:15, :] = -1000  # outside air touching the border
    image[disc(image.shape, (70, 60), 30)] = -850  # lung
    image[disc(image.shape, (70, 60), 3)] = 60  # vessel inside lung
    lung = enclosed_air(image, (1.0, 1.0))
    assert lung[70, 60]  # vessel hole filled
    assert not lung[5, 5]  # outside air excluded
    assert lung.sum() > 2500


def test_rolling_closure_bridges_concavity_but_not_wider_gaps():
    region = disc((100, 100), (50, 50), 30)
    notch = disc((100, 100), (50, 82), 12)
    region &= ~notch
    closed = rolling_closure(region, (1.0, 1.0), 14.0)
    assert closed[50, 71]  # notch bridged: the disc arc sags to ~col 72
    assert not rolling_closure(region, (1.0, 1.0), 4.0)[50, 71]  # disc too small to bridge
    assert not closed[5, 5] and not closed[50, 95]  # nothing grows out of the frame


def juxtapleural_slice(shape=(160, 160), lung_radius=60.0, mass_radius=18.0):
    """Circular lung with a mass sitting on its wall; the mass bulges into the lung."""
    centre = (80, 80)
    lung = disc(shape, centre, lung_radius)
    wall_point = (80, 80 + lung_radius)
    mass = disc(shape, wall_point, mass_radius)
    return lung & ~mass, mass & disc(shape, centre, lung_radius), mass


def test_close_slice_closes_the_juxtapleural_gap_along_the_wall_arc():
    lung, mass_in_lung, mass = juxtapleural_slice()
    virtual, closures = close_slice(lung, (1.0, 1.0), 30.0)
    cap = virtual & ~lung
    dice = 2 * (cap & mass_in_lung).sum() / (cap.sum() + mass_in_lung.sum())
    assert dice > 0.9
    main = max(closures, key=lambda c: c["chord_mm"])
    assert 30 < main["chord_mm"] < 40  # ~2 * mass chord across the lung outline
    assert main["bulge_mm"] > 1.0  # the arc bows outward like the wall
    assert main["flank_points"] >= 6
    # Nothing beyond the wall (outside the original lung circle) is inside the envelope.
    assert not (virtual & ~disc(lung.shape, (80, 80), 61.5)).any()


def test_envelope_needs_seed_inside_and_widens_radius():
    lung, _, mass = juxtapleural_slice()
    stack = np.stack([lung] * 5)
    virtual, info = pleural_envelope(stack, (2, 80, 130), (1.0, 1.0, 1.0))
    assert info["active"] and info["radius_mm"] >= 10
    assert virtual[2, 80, 130]
    _, info = pleural_envelope(stack, (2, 80, 155), (1.0, 1.0, 1.0))  # chest wall beyond the mass
    assert not info["active"]


class Phantom:
    modality = "CT"
    series_uid = "phantom"
    frame_uid = ""
    dz = row_sp = col_sp = 1.0
    iop = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    normal = np.array([0.0, 0.0, 1.0])

    def __init__(self, vol):
        self.vol = vol

    def require_rectilinear(self, _):
        pass

    def require_axial(self):
        pass

    def patient_point(self, z, r, c):
        return np.array([c, r, z], float)


def test_solve_clips_a_wall_touching_mass_at_the_pleural_arc():
    lung_full = disc((160, 160), (80, 80), 60)
    vol = np.full((40, 160, 160), 45.0, np.float32)  # chest wall / soft tissue everywhere
    vol[:, :5, :] = -1000  # outside air so the body has a border
    truth = np.zeros(vol.shape, bool)
    for z in range(40):
        vol[z][lung_full] = -880
        rad = 18.0**2 - (z - 20) ** 2
        if rad > 0:
            sphere = disc((160, 160), (80, 140), np.sqrt(rad))
            vol[z][sphere] = 45  # mass tissue, same HU as the wall it sits on
            truth[z] = sphere & lung_full
    data = prepare(Phantom(vol), [20, 80, 130], "solid", [], [], 1.5)
    assert data["lung"].shape[0] == vol.shape[0]
    result = solve(data)
    assert result["pleural_closure"]["active"]
    assert result["voxels"] > 0 and result["quality"]["crop_contact_voxels"] == 0
    assert any("Plevral" in r for r in result["reasons"])
    mask = unpack(result)
    dice = 2 * (mask & truth).sum() / (mask.sum() + truth.sum())
    assert dice > 0.8
    # Nothing beyond the pleural line.
    wall = ~np.stack([disc((160, 160), (80, 80), 62.0)] * 40)
    assert (mask & wall).sum() < 0.03 * mask.sum()
    closure_slices = [s for s in result["planes"]["ax"] if s.get("closure")]
    assert closure_slices and closure_slices[0]["closure"][0]["chord_mm"] > 5
    assert result["planes"]["ax"][0]["index"] >= 2
