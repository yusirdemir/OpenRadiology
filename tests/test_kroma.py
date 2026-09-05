"""Synthetic engineering phantoms only; these tests do not establish clinical sensitivity."""
import json
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from openrad.cli import main as cli_main
from openrad.errors import GeometryError, InputError, UsageError
from openrad.kroma import (
    KromaConfig,
    create_passport,
    extract_candidates,
    hessian_features,
    lung_mask,
    resample_iso,
    rgb_overlay,
)
from tests.test_pipeline import make_study


@pytest.fixture(scope="module")
def phantom():
    z, y, x = np.ogrid[:64, :96, :128]
    # Thick closed shell; keep the test objects >4*sigma_max from its edge.
    body = (y > 2) & (y < 94) & (x > 2) & (x < 126)
    lungs = ((y - 48) / 35) ** 2 + ((x - 32) / 26) ** 2 < 1
    lungs = lungs | (((y - 48) / 35) ** 2 + ((x - 96) / 26) ** 2 < 1)
    volume = np.broadcast_to(np.where(body, 40, -1000), (64, 96, 128)).astype(np.float32).copy()
    volume[np.broadcast_to(lungs, volume.shape)] = -800
    sphere = (z - 32) ** 2 + (y - 48) ** 2 + (x - 32) ** 2 <= 4 ** 2
    cylinder = np.broadcast_to((y - 48) ** 2 + (x - 96) ** 2 <= 4 ** 2, volume.shape)
    volume[sphere | cylinder] = -100
    return volume, (32, 48, 32), (32, 48, 96)


@pytest.fixture(scope="module")
def computed(phantom):
    volume, _, _ = phantom
    mask = lung_mask(volume)
    return mask, hessian_features(volume, mask)


def test_mask_retains_solid_nodule_and_open_z_lungs(phantom, computed):
    volume, sphere, cylinder = phantom
    mask, _ = computed
    assert mask[sphere] and mask[cylinder]
    assert mask[0, 48, 96] and mask[-1, 48, 96]
    assert not mask[:, 0, :].any()
    assert not mask[:, :, 0].any()
    assert not mask[:, 48, 64].any()  # mediastinum
    assert mask.shape == volume.shape


def test_sphere_blob_red_and_cylinder_vessel_green(phantom, computed):
    volume, sphere, cylinder = phantom
    mask, f = computed
    assert f.blob[sphere] > 0.8
    assert f.blob[sphere] > 10 * f.blob[cylinder]
    assert f.vessel[cylinder] > 0.6
    assert f.vessel[cylinder] > 3 * f.vessel[sphere]
    rgb = rgb_overlay(volume, f.blob, f.vessel)
    assert rgb[sphere][0] > rgb[sphere][1] > rgb[sphere][2]
    assert rgb[cylinder][1] > rgb[cylinder][0]
    assert rgb[cylinder][1] > rgb[cylinder][2]
    candidates = extract_candidates(f, volume, mask)
    assert any(np.linalg.norm(np.asarray(c["center_zyx"]) - sphere) <= 1 for c in candidates)
    assert all(np.linalg.norm(np.asarray(c["center_zyx"]) - cylinder) > 4 for c in candidates)
    assert all(a.dtype == np.float32 for a in (f.blob, f.vessel, f.scale_mm))
    assert np.isfinite(f.blob).all() and np.isfinite(f.vessel).all()


def test_chunk_halo_equivalence_and_constant_dc_rejection():
    z, y, x = np.ogrid[:25, :27, :29]
    volume = (-800 + 600 * np.exp(-((z - 15) ** 2 + (y - 16) ** 2 + (x - 16) ** 2) / 8)).astype(np.float32)
    cfg = KromaConfig(scales_mm=(1, 2.2), block_size=8)
    a = hessian_features(volume, config=cfg)
    b = hessian_features(volume, config=replace(cfg, block_size=64))
    np.testing.assert_allclose(a.blob, b.blob, atol=1e-6)
    np.testing.assert_allclose(a.vessel, b.vessel, atol=1e-6)
    np.testing.assert_array_equal(a.scale_mm, b.scale_mm)
    flat = hessian_features(np.full((12, 13, 14), -800, np.float32), config=cfg)
    assert not flat.blob.any() and not flat.vessel.any()


def test_resampling_geometry_linear_ramp():
    z, y, x = np.indices((5, 7, 9))
    volume = (10 * z + 2 * y + x).astype(np.float32)
    iso = resample_iso(volume, (2, 1.5, 0.7))
    assert iso.shape == (9, 10, 6)
    assert iso.dtype == np.float32
    assert iso[2, 3, 4] == pytest.approx(10 + 4 + 4 / 0.7, abs=1e-5)
    assert iso[0, 0, 0] == volume[0, 0, 0]


def test_pages_json_coordinates_and_optional_3x_cards(tmp_path, phantom):
    volume, sphere, _ = phantom
    output = tmp_path / "passport"
    cfg = KromaConfig(scales_mm=(2.2, 3.3))
    result = create_passport(volume, output, origin_lps=(10, -20, 30), config=cfg, detail_cards=True)
    assert result == json.loads((output / "passport.json").read_text())
    assert result["candidates"]
    c = min(result["candidates"], key=lambda c: np.linalg.norm(np.asarray(c["center_zyx"]) - sphere))
    z, y, x = c["center_zyx"]
    assert c["center_lps_mm"] == [10 + x, -20 + y, 30 + z]
    assert c["source_center_zyx"] == [z, y, x]
    for name in ["overview.png", *result["images"]["passport_pages"]]:
        with Image.open(output / name) as page:
            assert page.size == (1024, 1024) and page.mode == "RGB"
            assert np.asarray(page).std() > 10
    with Image.open(output / result["images"]["detail_cards"][0]) as detail:
        assert detail.size == (512, 174)
    with pytest.raises(InputError, match="never overwrite"):
        create_passport(volume, output, config=cfg)


def test_empty_mask_produces_explicit_non_diagnostic_pages(tmp_path):
    result = create_passport(np.full((10, 12, 14), -1000, np.float32), tmp_path / "empty")
    assert result["candidates"] == []
    assert result["lung_mask_voxels"] == 0
    assert any("Empty lung mask" in w for w in result["warnings"])
    assert (tmp_path / "empty" / "passport_01.png").is_file()


def test_pagination_32_candidates_and_boundary_padding(tmp_path, monkeypatch):
    import openrad.kroma as kroma
    from openrad.kroma import Features
    volume = np.full((16, 32, 32), -800, np.float32)
    mask = np.ones_like(volume, bool)
    blob = np.zeros_like(volume)
    for i in range(32):
        blob[0 if i < 16 else 15, (i % 16 // 4) * 8, (i % 4) * 8] = 1
    f = Features(blob, np.zeros_like(blob), np.ones_like(blob), 1)
    monkeypatch.setattr(kroma, "lung_mask", lambda _: mask)
    monkeypatch.setattr(kroma, "hessian_features", lambda *args: f)
    result = create_passport(volume, tmp_path / "many", config=KromaConfig(max_candidates=32))
    assert len(result["candidates"]) == 32
    assert len(result["images"]["passport_pages"]) == 2
    for c in result["candidates"]:
        x0, y0, x1, y1 = c["card_bbox_xyxy"]
        assert 0 <= x0 < x1 <= 1024 and 0 <= y0 < y1 <= 1024
    assert result["candidates"][16]["passport_page"] == "passport_02.png"


@pytest.mark.parametrize("cfg", [KromaConfig(tau=0), KromaConfig(gamma=-1), KromaConfig(scales_mm=(float("nan"),)),
                                  KromaConfig(max_candidates=33), KromaConfig(block_size=0)])
def test_invalid_config(cfg):
    with pytest.raises(UsageError):
        cfg.validate()


def test_invalid_input_and_memory_guard(tmp_path):
    with pytest.raises(GeometryError):
        resample_iso(np.zeros((4, 4, 4)), (0, 1, 1))
    with pytest.raises(InputError):
        resample_iso(np.full((4, 4, 4), np.nan), (1, 1, 1))
    with pytest.raises(GeometryError):
        hessian_features(np.zeros((4, 4, 4)), np.ones((4, 4, 4), np.uint8))
    # Huge target must be rejected BEFORE allocating a resampled array.
    with pytest.raises(UsageError, match="working set"):
        create_passport(np.zeros((4, 4, 4)), tmp_path / "too_big", spacing_zyx=(100, 100, 100),
                        config=KromaConfig(memory_mb=64))
    assert not (tmp_path / "too_big").exists()


def test_cli_synthetic_dicom_and_errors(tmp_path, capsys):
    study = make_study(tmp_path, "synthetic", gaps=tuple(range(5)))
    out = tmp_path / "out"
    assert cli_main(["passport", str(study), "--output", str(out), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["geometry"]["source_spacing_zyx_mm"] == [1, 2, 1]
    assert len(result["source"]["sop_uids"]) == 5
    assert cli_main(["passport", "--help"]) == 0
    assert cli_main(["passport", str(study), "--output", str(out), "--tau", "nan"]) == 2
    assert cli_main(["passport", str(tmp_path / "missing"), "--output", str(out)]) == 3
    tilted = make_study(tmp_path, "tilted", gaps=(0, 1, 2), shear=(0, 0.5, 0))
    assert cli_main(["passport", str(tilted), "--output", str(tmp_path / "tilted_out")]) == 4


def test_cli_config_environment_and_flag_precedence(tmp_path, monkeypatch, capsys):
    study = make_study(tmp_path, "config_synthetic")
    config = tmp_path / "settings.toml"
    config.write_text("[kroma]\nkroma_max_candidates = 8\nkroma_gamma = 1.5\n")
    monkeypatch.setenv("OPENRAD_CONFIG", str(config))
    monkeypatch.setenv("OPENRAD_KROMA_MAX_CANDIDATES", "12")
    assert cli_main(["passport", str(study), "--output", str(tmp_path / "env"), "--json"]) == 0
    cfg = json.loads(capsys.readouterr().out)["config"]
    assert cfg["max_candidates"] == 12 and cfg["gamma"] == 1.5
    assert cli_main(["passport", str(study), "--output", str(tmp_path / "flags"), "--max-candidates", "3", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["config"]["max_candidates"] == 3


def test_bright_overlay_saturation_is_explicit():
    # The requested additive colour formula cannot preserve red/green separation
    # at bright HU. Preserve that mathematical behaviour, do not claim otherwise.
    rgb = rgb_overlay(np.array([150], np.float32), np.array([1]), np.array([0]))
    np.testing.assert_array_equal(rgb, [[1, 1, 1]])
