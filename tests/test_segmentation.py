"""Physical phantoms: anisotropy, concavity, leakage and voxel provenance."""

from types import SimpleNamespace

import numpy as np
import pytest

from openrad.errors import InputError
from openrad.segmentation import caliper, describe_planes, prepare, solve


def phantom(spacing=(1.0, 1.0, 1.0), profile="solid"):
    spacing = np.array(spacing)
    shape = np.maximum(9, np.ceil(np.array([40.0, 48.0, 48.0]) / spacing).astype(int))
    centre = shape // 2
    coords = np.indices(shape)
    distance = sum(((coords[i] - centre[i]) * spacing[i]) ** 2 for i in range(3))
    truth = distance <= 9**2
    image = np.where(truth, -950 if profile == "bulla" else 35, 30 if profile == "bulla" else -900).astype(np.float32)
    data = dict(
        image=image,
        spacing=spacing,
        offset=np.array([3, 5, 7]),
        full_shape=(shape + 20).tolist(),
        positive=[centre.tolist()],
        negative=[],
        profile=profile,
        seed_hu=float(image[tuple(centre)]),
        brush_mm=1.0,
        crop_limited=False,
        origin_lps=[12.0, -20.0, 30.0],
        direction=[0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0],
        series_uid="phantom",
    )
    return data, truth


def unpack(result):
    out = np.zeros(result["shape_zyx"], bool)
    for s in result["planes"]["ax"]:
        for row, first, last in np.array(s["runs"]).reshape(-1, 3):
            out[s["index"], row, first : last + 1] = True
    return out


@pytest.mark.parametrize("spacing", [(1.0, 0.7, 0.7), (3.0, 0.7, 0.7), (5.0, 1.2, 0.8)])
def test_physical_sphere_at_multiple_resolutions(spacing):
    data, truth = phantom(spacing)
    result = solve(data)
    mask = unpack(result)
    origin = data["offset"]
    reconstructed = mask[tuple(slice(o, o + n) for o, n in zip(origin, truth.shape))]
    dice = 2 * (truth & reconstructed).sum() / (truth.sum() + reconstructed.sum())
    assert dice > 0.86
    assert result["voxels"] > 0
    assert mask[tuple(result["focus_zyx"])]
    assert result["volume_ml"] == round(mask.sum() * np.prod(spacing) / 1000, 3)
    assert not result["established"]


@pytest.mark.parametrize("profile", ["effusion", "cyst", "bulla"])
def test_tissue_profiles_have_native_mask(profile):
    data, truth = phantom((2.0, 1.0, 1.0), profile)
    result = solve(data)
    assert result["voxels"] > truth.sum() * 0.6
    assert result["quality"]["crop_contact_voxels"] == 0


def test_three_planes_are_identical_voxels_and_preserve_holes():
    mask = np.zeros((7, 9, 11), bool)
    mask[1:6, 2:8, 3:10] = True
    mask[2:5, 4:6, 5:8] = False
    offset = np.array([4, 6, 8])
    shape = [20, 30, 40]
    planes = describe_planes(mask, offset, shape, np.array([5.0, 0.7, 0.9]))
    full = np.zeros(shape, bool)
    full[4:11, 6:15, 8:19] = mask
    for name, slices in planes.items():
        for s in slices:
            expected = full[s["index"]] if name == "ax" else np.flipud(full[:, s["index"], :] if name == "cor" else full[:, :, s["index"]])
            recovered = np.zeros_like(expected)
            for r, a, b in np.array(s["runs"]).reshape(-1, 3):
                recovered[r, a : b + 1] = True
            assert np.array_equal(recovered, expected)


def test_no_glowing_crop_when_region_leaks():
    data, _ = phantom()
    data["image"][:] = 35
    result = solve(data)
    assert result["status"] == "needs-review"
    assert result["quality"]["crop_contact_voxels"] > 0
    assert result["voxels"] == 0
    assert not result["slices"]


def test_caliper_is_oblique_and_does_not_cross_hole():
    rr, cc = np.indices((50, 50))
    mask = (abs(rr - cc) < 4) & (rr > 4) & (rr < 44)
    result = caliper(mask, [1.0, 1.0], [0, 0])
    assert result["diameter_mm"] > 45
    for p in result["caliper"]:
        assert mask[int(p["row"]), int(p["col"])]
    mask[20:30, 20:30] = False
    result = caliper(mask, [1.0, 1.0], [0, 0])
    a, b = result["caliper"]
    samples = np.rint(np.linspace([a["row"], a["col"]], [b["row"], b["col"]], 300)).astype(int)
    assert mask[samples[:, 0], samples[:, 1]].all()


def test_seed_validation_and_no_measured_diameter_dependency():
    data, _ = phantom()
    volume = SimpleNamespace(
        vol=data["image"],
        modality="CT",
        series_uid="x",
        dz=1.0,
        row_sp=1.0,
        col_sp=1.0,
        require_rectilinear=lambda _: None,
        require_axial=lambda: None,
        normal=np.array([0.0, 0.0, 1.0]),
        iop=[1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        patient_point=lambda k, r, c: np.array([c, r, k]),
    )
    with pytest.raises(InputError):
        prepare(volume, [0, 0, 0], "solid", [], [])
    with pytest.raises(InputError):
        prepare(volume, [-1, 1, 1], "auto", [], [])
    prepared = prepare(volume, data["positive"][0], "solid", [], [])
    assert prepared["image"].shape == volume.vol.shape


def test_negative_marks_separate_touching_equal_density_regions():
    data, _ = phantom()
    data["image"][:, :, 30:] = 35
    data["negative"] = [[20, 24, 38]]
    result = solve(data)
    mask = unpack(result)
    assert result["voxels"] > 0
    assert not mask[tuple(np.array(data["negative"][0]) + data["offset"])]
