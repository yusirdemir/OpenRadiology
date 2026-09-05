"""Synthetic engineering regression tests. Phantom data only; no claim of clinical sensitivity.

Runs with ``pytest`` or directly: ``python3 tests/test_pipeline.py`` (no PYTHONPATH needed).
"""
from __future__ import annotations

import copy
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # allow `python3 tests/test_pipeline.py` without installation
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402
import pydicom  # noqa: E402
from PIL import Image  # noqa: E402
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset  # noqa: E402
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid  # noqa: E402

from openrad import __version__  # noqa: E402
from openrad.anonymize import Pseudonymizer, anonymize_tree, deidentify  # noqa: E402
from openrad.cli import main as cli_main  # noqa: E402
from openrad.config import VISION_PROFILES, load_settings  # noqa: E402
from openrad.create_report import finish, prepare, register, validate, write_json  # noqa: E402
from openrad.ct_render import render_axial  # noqa: E402
from openrad.dcmlib import Volume, contact_sheet, direction_label, orientation_marks, read_headers, series_by_number  # noqa: E402
from openrad.errors import (EXIT_GEOMETRY, EXIT_INPUT, EXIT_USAGE, EXIT_VALIDATION, GeometryError, InputError,  # noqa: E402
                            IntegrityError, UsageError, ValidationError)
from openrad.grid import fit_grid, font_size_for, parse_grid, scale_bar_mm  # noqa: E402
from openrad.locales import available_languages, load_locale  # noqa: E402
from openrad.measure import main as measure_main, reported_diameter  # noqa: E402
from openrad.suv import lean_body_mass_kg, parse_dicom_dt, suv_factor  # noqa: E402

# Hermetic configuration: no user config file, no OPENRAD_* from the developer's shell.
_CFG_HOME = tempfile.TemporaryDirectory()
os.environ["XDG_CONFIG_HOME"] = _CFG_HOME.name
for _k in [k for k in os.environ if k.startswith("OPENRAD_")]:
    del os.environ[_k]
logging.getLogger("openrad").setLevel(logging.ERROR)


def make_study(root, name, date="20260101", patient="TEST", iop=(1, 0, 0, 0, 1, 0), gaps=(0, 1, 2),
               shear=(0.0, 0.0, 0.0), tilt_tag=None, matrix=32, extra=None):
    """Write a tiny synthetic CT series. ``shear`` adds an in-plane origin drift per mm of z (gantry tilt)."""
    path = root / "DCIM" / name
    path.mkdir(parents=True)
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    normal = np.cross(iop[:3], iop[3:])
    for k, z in enumerate(gaps):
        meta = FileMetaDataset()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        ds = FileDataset(str(path / f"{k}.dcm"), {}, file_meta=meta, preamble=b"\0" * 128)
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.FrameOfReferenceUID = study, series, frame
        ds.PatientID, ds.PatientName, ds.PatientBirthDate = patient, patient, "19700101"
        ds.StudyDate, ds.StudyTime = date, "120000"
        ds.SeriesDate, ds.SeriesTime = date, "120100"
        ds.Modality, ds.SeriesNumber, ds.InstanceNumber = "CT", 1, k + 1
        ds.ImageOrientationPatient = list(iop)
        ds.ImagePositionPatient = list(normal * z + np.asarray(shear) * z)
        ds.PixelSpacing, ds.SliceThickness = [2, 1], 1
        if tilt_tag is not None:
            ds.GantryDetectorTilt = tilt_tag
        ds.Rows, ds.Columns = matrix, matrix
        ds.PhotometricInterpretation, ds.SamplesPerPixel = "MONOCHROME2", 1
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
        ds.RescaleSlope, ds.RescaleIntercept = 2, -1000
        arr = np.zeros((matrix, matrix), dtype=np.int16)
        arr[10:16, 12:18] = 500
        ds.PixelData = arr.tobytes()
        if extra:
            extra(ds)
        ds.save_as(path / f"{k}.dcm")
    return path


def make_pet_header(units="BQML", decay="START", weight=73.0, dc_datetime=None, philips=None):
    ds = Dataset()
    ds.Modality, ds.Units, ds.DecayCorrection = "PT", units, decay
    ds.SeriesDate, ds.SeriesTime = "20260101", "110000"
    ds.AcquisitionDate, ds.AcquisitionTime = "20260101", "110000"
    if weight is not None:
        ds.PatientWeight = weight
    if dc_datetime:
        ds.DecayCorrectionDateTime = dc_datetime
    rp = Dataset()
    rp.RadionuclideTotalDose, rp.RadionuclideHalfLife = 300e6, 6586.2
    rp.RadiopharmaceuticalStartDateTime = "20260101100000"
    ds.RadiopharmaceuticalInformationSequence = [rp]
    if philips:
        for tag, value in philips.items():
            ds.add_new(tag, "DS", str(value))
    return ds


def quiet(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = fn(*args)
    return rc, out.getvalue(), err.getvalue()


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)  # no project .openrad.toml / pyproject in scope

    def tearDown(self):
        os.chdir(self._cwd)
        self.temp.cleanup()


class GeometryTests(TempCase):
    def test_hu_and_patient_coordinates(self):
        p = make_study(self.root, "a")
        v = Volume(read_headers(p))
        self.assertEqual(v.vol[0, 0, 0], -1000)
        self.assertEqual(v.vol[0, 12, 14], 0)
        np.testing.assert_allclose(v.patient_point(1, 3, 4), [4, 6, 1])
        self.assertEqual(v.mpr_aspect("SAG"), .5)
        self.assertEqual(v.mpr_aspect("COR"), 1)
        self.assertFalse(v.is_tilted)
        self.assertEqual(v.voxel_volume_mm3, 2.0)
        self.assertIn("gantry_tilt_deg_geometry", v.geometry_summary())

    def test_reversed_orientation_labels_and_ct_gate(self):
        p = make_study(self.root, "reverse", iop=(-1, 0, 0, 0, -1, 0))
        v = Volume(read_headers(p))
        self.assertEqual(direction_label(v.iop[:3]), "R")
        self.assertEqual(direction_label(v.iop[3:]), "A")
        with self.assertRaises(GeometryError):
            v.require_axial()
        with self.assertRaises(ValueError):
            orientation_marks(Image.new("L", (32, 32)))

    def test_gaps_and_duplicates_are_rejected(self):
        with self.assertRaises(GeometryError):
            Volume(read_headers(make_study(self.root, "gap", gaps=(0, 1, 4))))
        with self.assertRaises(GeometryError):
            Volume(read_headers(make_study(self.root, "duplicate", gaps=(0, 0, 1))))

    def test_gantry_tilt_detected_and_optionally_accepted(self):
        # origin drifts 0.5 mm along +y per mm of z: tan(tilt) = 0.5 -> 26.57 deg; 0.25 row px per slice (row_sp 2 mm)
        p = make_study(self.root, "tilt", gaps=(0, 1, 2, 3), shear=(0, 0.5, 0), tilt_tag=26.6)
        with self.assertRaises(GeometryError) as ctx:
            Volume(read_headers(p))
        self.assertIn("26.57", str(ctx.exception))
        v = Volume(read_headers(p), allow_tilt=True)
        self.assertTrue(v.is_tilted)
        self.assertAlmostEqual(v.gantry_tilt_deg, 26.565, places=2)
        self.assertEqual(v.tilt_info()["gantry_tilt_deg_tag"], 26.6)
        dr, dc = v.shear_px_per_slice()
        self.assertAlmostEqual(dr, 0.25, places=6)
        self.assertAlmostEqual(dc, 0.0, places=6)
        np.testing.assert_allclose(v.patient_point(2, 0, 0), [0, 1.0, 2])
        rect = v.rectilinear()
        self.assertEqual(rect.shape, v.vol.shape)
        self.assertEqual(rect[0, 12, 14], 0)
        self.assertEqual(rect[3, 13, 14], 0)
        with self.assertRaises(GeometryError):
            v.require_rectilinear("3D")

    def test_ambiguous_series_number_rejected(self):
        p = make_study(self.root, "a")
        q = make_study(self.root, "b")
        for f in q.iterdir():
            (p / ("other" + f.name)).write_bytes(f.read_bytes())
        with self.assertRaises(InputError):
            series_by_number(p, 1)
        with self.assertRaises(InputError):
            series_by_number(p, 99)

    def test_rgb_survives_sheet(self):
        im = Image.new("RGB", (4, 4), (255, 0, 0))
        sheet = contact_sheet([im], 1)
        self.assertEqual(sheet.mode, "RGB")
        self.assertEqual(sheet.getpixel((1, 1)), (255, 0, 0))

    def test_position_outside_volume_rejected(self):
        v = Volume(read_headers(make_study(self.root, "a")))
        with self.assertRaises(InputError):
            v.index_of_z(100)


class GridTests(unittest.TestCase):
    def test_parse_and_fit(self):
        self.assertEqual(parse_grid("3x3"), (3, 3))
        self.assertIsNone(parse_grid("auto"))
        with self.assertRaises(UsageError):
            parse_grid("3by3")
        spec = fit_grid(512, 512, "3x3", 1568)
        self.assertEqual((spec.cols, spec.rows), (3, 3))
        self.assertEqual(spec.sheet_size(512, 512), (1542, 1542))
        spec = fit_grid(512, 512, "4x4", 1568)          # too big -> shrinks, never down-samples
        self.assertEqual((spec.cols, spec.rows), (3, 3))
        with self.assertRaises(UsageError):
            fit_grid(512, 512, "4x4", 1568, strict=True)
        spec = fit_grid(512, 512, "auto", 2048)
        self.assertEqual((spec.cols, spec.rows), (3, 3))
        spec = fit_grid(768, 768, "2x2", 1568)
        self.assertEqual((spec.cols, spec.rows), (2, 2))
        with self.assertRaises(UsageError):
            fit_grid(1600, 1600, "1x1", 1568)
        self.assertEqual(VISION_PROFILES["claude"], 1568)

    def test_scaling_helpers(self):
        self.assertEqual(font_size_for(512), 16)
        self.assertEqual(font_size_for(64), 11)
        self.assertEqual(font_size_for(2000), 22)
        self.assertEqual(scale_bar_mm(350), 100.0)   # 20 % of 350 mm = 70 -> next nice length
        self.assertEqual(scale_bar_mm(40), 10.0)


class ConfigTests(TempCase):
    def test_precedence_env_over_file_over_default(self):
        st = load_settings(environ={})
        self.assertEqual(st.lang, "en")
        self.assertEqual(st.effective_max_side, 1568)
        self.assertEqual(st.sources["lang"], "default")
        (self.root / ".openrad.toml").write_text('[general]\nlang = "tr"\n[render]\nvision_profile = "gpt"\n'
                                                 '[render.windows]\npe = [100, 700]\n[pet]\nuptake_window_min = [50, 80]\n')
        try:
            st = load_settings(environ={})
        except UsageError as e:  # no TOML parser on this interpreter
            self.skipTest(str(e))
        self.assertEqual(st.lang, "tr")
        self.assertEqual(st.effective_max_side, 2048)
        self.assertEqual(st.windows, {"pe": (100.0, 700.0)})
        self.assertEqual(st.uptake_window_min, (50.0, 80.0))
        st = load_settings(environ={"OPENRAD_LANG": "en", "OPENRAD_MAX_SIDE": "1024", "OPENRAD_THREADS": "2"})
        self.assertEqual(st.lang, "en")
        self.assertEqual(st.sources["lang"], "OPENRAD_LANG")
        self.assertEqual(st.effective_max_side, 1024)
        st = load_settings({"lang": "tr"}, environ={"OPENRAD_LANG": "en"})
        self.assertEqual(st.lang, "tr")
        self.assertEqual(st.sources["lang"], "cli")

    def test_invalid_values_are_usage_errors(self):
        with self.assertRaises(UsageError):
            load_settings(environ={"OPENRAD_LANG": "fr"})
        with self.assertRaises(UsageError):
            load_settings(environ={"OPENRAD_VISION_PROFILE": "custom"})
        with self.assertRaises(UsageError):
            load_settings(environ={"OPENRAD_MAX_SIDE": "10"})
        st = load_settings(environ={"OPENRAD_WINDOWS": "pe:100/700,liverx:60/160"})
        self.assertEqual(st.windows["pe"], (100.0, 700.0))

    def test_config_and_doctor_commands(self):
        rc, out, err = quiet(cli_main, ["config", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["settings"]["general"]["lang"], "en")
        rc, out, err = quiet(cli_main, ["config", "--init"])
        self.assertEqual(rc, 0)
        self.assertTrue((self.root / ".openrad.toml").is_file())
        rc, out, err = quiet(cli_main, ["config", "--init"])
        self.assertEqual(rc, EXIT_USAGE)
        (self.root / ".openrad.toml").unlink()
        rc, out, err = quiet(cli_main, ["doctor", "--json"])
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])
        rc, out, err = quiet(cli_main, ["doctor"])
        self.assertIn("OK", out)


class SuvTests(unittest.TestCase):
    def test_invalid_suv_inputs_stop(self):
        self.assertIsNone(suv_factor(Dataset())[0])
        ds = Dataset()
        rp = Dataset()
        rp.RadionuclideTotalDose, rp.RadionuclideHalfLife = 0, 6586
        ds.RadiopharmaceuticalInformationSequence = [rp]
        self.assertIsNone(suv_factor(ds, 73)[0])
        self.assertIsNone(suv_factor(make_pet_header(weight=None))[0])
        self.assertIsNone(suv_factor(make_pet_header(decay="NONE"))[0])
        self.assertIsNone(suv_factor(make_pet_header(units="CNTS"))[0])

    def test_start_decay_uses_uptake_interval(self):
        factor, info = suv_factor(make_pet_header())
        self.assertEqual(info["uptake_time_min"], 60.0)
        self.assertEqual(info["scan_start_source"], "SeriesTime")
        expected = 73000.0 / (300e6 * 2 ** (-3600 / 6586.2))
        self.assertAlmostEqual(factor, expected, places=12)

    def test_admin_decay_skips_dose_decay(self):
        factor, info = suv_factor(make_pet_header(decay="ADMIN"))
        self.assertAlmostEqual(factor, 73000.0 / 300e6, places=15)

    def test_decay_correction_datetime_is_preferred(self):
        factor, info = suv_factor(make_pet_header(dc_datetime="20260101103000"))
        self.assertEqual(info["scan_start_source"], "DecayCorrectionDateTime")
        self.assertEqual(info["uptake_time_min"], 30.0)
        self.assertIn("warnings", info)
        factor, info = suv_factor(make_pet_header(dc_datetime="20260101103000"), uptake_window=(20, 90))
        self.assertNotIn("warnings", info)

    def test_earliest_acquisition_before_series_time(self):
        ds = make_pet_header()
        early = Dataset()
        early.AcquisitionDate, early.AcquisitionTime = "20260101", "105000"
        factor, info = suv_factor(ds, datasets=[ds, early])
        self.assertEqual(info["uptake_time_min"], 50.0)
        self.assertIn("earliest AcquisitionTime", info["scan_start_source"])

    def test_philips_cnts_private_scale(self):
        factor, info = suv_factor(make_pet_header(units="CNTS", philips={0x70531009: 0.5}))
        self.assertAlmostEqual(factor, 0.5 * 73000.0 / (300e6 * 2 ** (-3600 / 6586.2)), places=12)
        factor, info = suv_factor(make_pet_header(units="CNTS", philips={0x70531000: 0.0021}))
        self.assertAlmostEqual(factor, 0.0021)

    def test_gml_is_identity(self):
        self.assertEqual(suv_factor(make_pet_header(units="GML"))[0], 1.0)

    def test_negative_uptake_is_error_not_wrapped(self):
        ds = make_pet_header()
        ds.SeriesTime = "090000"
        ds.AcquisitionTime = "090000"
        factor, info = suv_factor(ds)
        self.assertIsNone(factor)
        self.assertIn("after scan", info["error"])

    def test_lbm_and_dt_parsing(self):
        self.assertAlmostEqual(lean_body_mass_kg(80, 1.80, "M"), 9270 * 80 / (6680 + 216 * 80 / 1.8 ** 2), places=6)
        self.assertIsNone(lean_body_mass_kg(80, 1.80, "O"))
        self.assertEqual(parse_dicom_dt("20260101100000.500000+0300").second, 0)
        self.assertEqual(parse_dicom_dt("20260101100000.500000+0300").microsecond, 500000)


class MeasureConventionTests(unittest.TestCase):
    def test_reported_diameter_rules(self):
        self.assertEqual(reported_diameter(7, 5, "fleischner", "nodule")[0], 6)
        self.assertEqual(reported_diameter(14, 9, "fleischner", "nodule")[0], 14)
        self.assertEqual(reported_diameter(7, 5, "recist", "nodule")[0], 7)
        value, rule = reported_diameter(18, 12, "recist", "node")
        self.assertEqual(value, 12)
        self.assertIn("short axis", rule)


class SessionTests(TempCase):
    def _prepare(self, folders, **kw):  # noqa: D401
        rc, out, err = quiet(lambda: prepare(self.root, folders, **kw))
        return rc

    def test_three_studies_sorted_by_header_not_name(self):
        for name, date in (("z", "20260101"), ("a", "20260301"), ("m", "20260201")):
            make_study(self.root, name, date)
        path = self._prepare(["a", "z", "m"])
        s = json.loads(path.read_text())
        self.assertEqual([t["folder"] for t in s["studies"]], ["z", "m", "a"])
        self.assertEqual(s["mode"], "comparison")
        self.assertEqual(s["schema"], 2)
        self.assertEqual(s["language"], "en")  # global default
        self.assertTrue(validate(s))

    def test_custom_studies_root_language_and_cache(self):
        make_study(self.root, "a")
        other = self.root / "elsewhere"
        (self.root / "DCIM").rename(other)
        path = self._prepare(["a"], studies_root=other, lang="tr", cache_dir=Path("scratch"))
        s = json.loads(path.read_text())
        self.assertEqual(s["language"], "tr")
        self.assertEqual(s["studies_root"], str(other.resolve()))
        self.assertTrue(str(path).startswith(str((self.root / "scratch").resolve())))

    def test_different_patients_blocked(self):
        make_study(self.root, "a", patient="A")
        make_study(self.root, "b", patient="B")
        with self.assertRaises(InputError):
            self._prepare(["a", "b"])

    def test_identity_source_default_fallback(self):
        make_study(self.root, "a", patient="A")
        make_study(self.root, "b", patient="B")
        index = self.root / "DCIM" / "PATIENT_INDEX.md"
        index.write_text("mapping: study a and study b belong to same patient")
        s = json.loads(self._prepare(["a", "b"]).read_text())
        self.assertEqual(s["identity_evidence"]["path"], str(index.resolve()))

    def test_duplicate_and_outside_folders_blocked(self):
        make_study(self.root, "a")
        with self.assertRaises(InputError):
            self._prepare(["a", "a"])
        with self.assertRaises(InputError):
            self._prepare([str(self.root)])

    def _complete_session(self, lang="en"):
        p = make_study(self.root, "a")
        # Also used by SchemaTests, which does not inherit SessionTests._prepare.
        path, _, _ = quiet(lambda: prepare(self.root, ["a"], lang=lang))
        v = Volume(read_headers(p))
        work = path.parent
        render_axial(v, work, "S1", "lung", 1, (0, 32, 0, 32))
        quiet(register, path, work)
        s = json.loads(path.read_text())
        s.update(reader="synthetic engineering test", context_disclosure="phantom only", limitations=["Synthetic only"],
                 recommendations=["None; phantom"], patient_context="Phantom test.", patient_limitations="Not a patient review.",
                 reading_complete=True)
        for page in s["pages"]:
            page["reviewed"] = True  # synthetic fixture acknowledgement, never real patient reading
        se = s["studies"][0]["series"][0]
        se.update(disposition="read", geometry_checked=True, required_passes=["lung:native"])
        for region in s["studies"][0]["regions"].values():
            region.update(status="not_covered", text="Phantom: anatomy absent", explanation="Phantom data; no organ.")
        return path, s

    def test_coverage_and_two_document_finish_defaults_to_english_reports_dir(self):
        path, s = self._complete_session()
        self.assertEqual(validate(s), [])
        bad = copy.deepcopy(s)
        bad["pages"][0]["sources"].pop()
        self.assertTrue(any("unread/unrendered" in e for e in validate(bad)))
        bad = copy.deepcopy(s)
        bad["studies"][0]["regions"]["CT:right_kidney"].update(status="no_finding")
        self.assertTrue(any("source references missing" in e for e in validate(bad)))
        write_json(path, s)
        quiet(finish, path)
        files = sorted(p.name for p in (self.root / "reports").iterdir())
        self.assertEqual(files, ["a__guide.md", "a__report.md"])
        quiet(finish, path)
        self.assertEqual(len(list((self.root / "reports").iterdir())), 2)
        report = (self.root / "reports" / "a__report.md").read_text()
        self.assertIn("## Recommendations", report)
        self.assertIn("generated-by: openrad finish", report)

    def test_every_locale_renders_and_output_dir_is_honoured(self):
        self.assertEqual(available_languages()[0], "en")
        for lang in available_languages():
            if lang == "en":
                continue
            loc = load_locale(lang)["text"]
            with tempfile.TemporaryDirectory() as tmp:
                inner = Path(tmp)
                cwd = os.getcwd()
                os.chdir(inner)
                try:
                    self.root = inner
                    path, s = self._complete_session(lang=lang)
                    write_json(path, s)
                    dest = inner / "custom" / "reports"
                    quiet(finish, path, None, dest)
                    files = sorted(p.name for p in dest.iterdir())
                    self.assertEqual(files, sorted(["a" + loc["guide_suffix"], "a" + loc["report_suffix"]]))
                    self.assertIn(loc["g_how_to_read"], (dest / ("a" + loc["guide_suffix"])).read_text())
                    self.assertIn("## " + loc["h_impression"], (dest / ("a" + loc["report_suffix"])).read_text())
                    self.assertNotIn("## Impression", (dest / ("a" + loc["report_suffix"])).read_text())
                finally:
                    os.chdir(cwd)

    def test_locales_share_keys_with_english(self):
        en = load_locale("en")
        for lang in available_languages():
            loc = load_locale(lang)
            self.assertEqual(set(loc["text"]), set(en["text"]), lang)
            self.assertEqual(set(loc["regions"]), set(en["regions"]), lang)

    def test_finish_refuses_invalid_session(self):
        make_study(self.root, "a")
        path = self._prepare(["a"])
        with self.assertRaises(ValidationError):
            quiet(finish, path)


class AnonymizeTests(TempCase):
    def _study(self, name="src", patient="DOE^JOHN"):
        def extra(ds):
            ds.AccessionNumber = "ACC123"
            ds.InstitutionName = "Example Hospital"
            ds.ReferringPhysicianName = "REF^DOC"
            ds.PatientSex, ds.PatientWeight, ds.PatientAge = "M", 80.0, "055Y"
            ds.SeriesDescription = "Thorax DOE 1mm"
            ds.add_new(0x00090010, "LO", "SECRET VENDOR")
            ds.add_new(0x00091001, "LO", "serial 42")
            ds.add_new(0x70530010, "LO", "Philips PET Private Group")
            ds.add_new(0x70531000, "DS", "0.0021")
            ds.add_new(0x70531009, "DS", "0.5")
            ds.add_new(0x70531234, "LO", "philips operator")
        return make_study(self.root, name, patient=patient, extra=extra)

    def test_deidentify_dataset(self):
        p = self._study()
        pseudo = Pseudonymizer("unit-test-salt")
        ds = pydicom.dcmread(next(p.glob("*.dcm")))
        original_uid, original_pixels = str(ds.SOPInstanceUID), bytes(ds.PixelData)
        rep = deidentify(ds, pseudo)
        self.assertNotEqual(str(ds.SOPInstanceUID), original_uid)
        self.assertTrue(str(ds.SOPInstanceUID).startswith("2.25."))
        self.assertEqual(pseudo.uid(original_uid), str(ds.SOPInstanceUID))
        self.assertEqual(ds.file_meta.MediaStorageSOPInstanceUID, ds.SOPInstanceUID)
        self.assertTrue(str(ds.PatientID).startswith("OR-"))
        self.assertTrue(str(ds.PatientName).startswith("ANON^"))
        self.assertEqual(ds.PatientBirthDate, "")
        self.assertEqual(ds.AccessionNumber, "")
        self.assertNotIn("InstitutionName", ds)
        self.assertNotIn(0x00091001, ds)                      # unknown private removed
        self.assertIn(0x70531009, ds)                         # safe private kept
        self.assertNotIn(0x70531234, ds)                      # non-allowlisted element of safe creator removed
        self.assertEqual(ds.PatientWeight, 80.0)              # characteristics retained
        self.assertEqual(ds.SeriesDescription, "")            # descriptor mentioned the patient name -> cleaned
        self.assertEqual(bytes(ds.PixelData), original_pixels)
        self.assertEqual(ds.PatientIdentityRemoved, "YES")
        codes = {item.CodeValue for item in ds.DeidentificationMethodCodeSequence}
        self.assertTrue({"113100", "113107", "113108", "113111"} <= codes)
        self.assertNotEqual(ds.StudyDate, "20260101")
        self.assertEqual(ds.StudyTime, "120000")               # times kept, dates shifted
        self.assertEqual(ds.SeriesDate, ds.StudyDate)          # consistent shift
        self.assertGreater(rep["uids"], 0)

    def test_tree_is_consistent_and_loadable(self):
        src = self._study()
        out1, out2 = self.root / "out1", self.root / "out2"
        summary = anonymize_tree(src, out1, Pseudonymizer("unit-test-salt"))
        self.assertEqual(summary["files_out"], 3)
        anonymize_tree(src, out2, Pseudonymizer("unit-test-salt"))
        files1 = sorted(p.relative_to(out1) for p in out1.rglob("*.dcm"))
        files2 = sorted(p.relative_to(out2) for p in out2.rglob("*.dcm"))
        self.assertEqual(files1, files2)
        self.assertTrue(all("DOE" not in str(f) and "src" not in str(f) for f in files1))
        self.assertTrue(all((out1 / f).read_bytes() == (out2 / f).read_bytes() for f in files1))
        v = Volume(read_headers(out1))                         # geometry intact
        self.assertEqual(v.shape, (3, 32, 32))
        self.assertEqual(v.vol[0, 12, 14], 0)
        with self.assertRaises(InputError):
            anonymize_tree(src, out1, Pseudonymizer("unit-test-salt"))   # never clobber
        out3 = self.root / "out3"
        anonymize_tree(src, out3, Pseudonymizer("another-salt-value"))
        self.assertNotEqual(sorted(p.parent.parent.name for p in out3.rglob("*.dcm"))[0],
                            sorted(p.parent.parent.name for p in out1.rglob("*.dcm"))[0])

    def test_cli_requires_salt_and_reports(self):
        src = self._study()
        rc, out, err = quiet(cli_main, ["anonymize", str(src), str(self.root / "o")])
        self.assertEqual(rc, EXIT_USAGE)
        rc, out, err = quiet(cli_main, ["anonymize", str(src), str(self.root / "o"), "--salt", "unit-test-salt", "--json",
                                        "--map", str(self.root / "map.json"), "--date-shift-days", "-10"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["date_shift_days"], -10)
        self.assertEqual(data["files_out"], 3)
        self.assertIn("re-identification map", err)
        mapping = json.loads((self.root / "map.json").read_text())
        self.assertEqual(len(mapping["uids"]), 6)             # 3 SOP + study + series + frame

    def test_strict_profile_removes_dates(self):
        src = self._study()
        out = self.root / "strict"
        anonymize_tree(src, out, Pseudonymizer("unit-test-salt"), shift_dates=False, retain_characteristics=False, safe_private=False)
        ds = pydicom.dcmread(next(out.rglob("*.dcm")))
        self.assertEqual(ds.StudyDate, "")
        self.assertNotIn("SeriesDate", ds)
        self.assertNotIn("PatientWeight", ds)
        self.assertNotIn(0x70531009, ds)


class SchemaTests(TempCase):
    def test_session_schema_is_valid_json_and_matches_a_complete_session(self):
        schema = json.loads((REPO_ROOT / "openrad" / "schema" / "session.schema.json").read_text())
        self.assertEqual(schema["properties"]["schema"]["const"], 2)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        path, s = SessionTests._complete_session(self)  # type: ignore[arg-type]
        jsonschema.Draft202012Validator(schema).validate(s)


class CliTests(TempCase):
    def test_help_and_version(self):
        rc, out, err = quiet(cli_main, [])
        self.assertEqual(rc, 0)
        self.assertIn("anonymize", out)
        rc, out, err = quiet(cli_main, ["--version"])
        self.assertEqual(rc, 0)
        self.assertIn(__version__, out)
        rc, out, err = quiet(cli_main, ["measure", "--help"])
        self.assertEqual(rc, 0)
        self.assertIn("--convention", out)

    def test_exit_codes(self):
        rc, out, err = quiet(cli_main, ["nonsense"])
        self.assertEqual(rc, EXIT_USAGE)
        rc, out, err = quiet(cli_main, ["measure", str(self.root / "missing"), "--series", "1", "--instance", "1"])
        self.assertEqual(rc, EXIT_INPUT)
        self.assertIn("error", err)
        self.assertEqual(out, "")
        p = make_study(self.root, "tilt", gaps=(0, 1, 2, 3), shear=(0, 0.5, 0))
        rc, out, err = quiet(cli_main, ["measure", str(p), "--series", "1", "--instance", "1", "--points", "0,0", "0,1"])
        self.assertEqual(rc, EXIT_GEOMETRY)
        rc, out, err = quiet(cli_main, ["measure", str(p), "--series", "1", "--instance", "1", "--points", "0,0", "0,1", "--allow-tilt"])
        self.assertEqual(rc, 0)
        self.assertIn("= 1.0 mm", out)
        rc, out, err = quiet(cli_main, ["measure", str(p), "--series", "1", "--instance", "1", "--auto3d", "12,14", "--allow-tilt",
                                        "--thr", "-500", "--thr-max", "500"])
        self.assertEqual(rc, EXIT_GEOMETRY)

    def test_measure_json_write_once_evidence_and_convention(self):
        p = make_study(self.root, "a")
        out_file = self.root / "ev" / "m1.txt"
        rc, out, err = quiet(measure_main, [str(p), "--series", "1", "--instance", "2", "--points", "10,12", "13,16",
                                            "--roi", "12,14,2", "--auto", "12,14", "--thr", "-500", "--thr-max", "500",
                                            "--json", "--output", str(out_file)])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        kinds = {r["kind"]: r for r in data["results"]}
        self.assertAlmostEqual(kinds["distance"]["value_mm"], (36 + 16) ** 0.5, places=2)
        self.assertEqual(kinds["roi"]["max"], 0.0)
        self.assertEqual(kinds["region_2d"]["convention"], "fleischner")
        region = kinds["region_2d"]
        expected, _ = reported_diameter(region["long_axis_mm"], region["short_axis_mm"], "fleischner", "nodule")
        self.assertLessEqual(abs(region["reported_diameter_mm"] - expected), 0.6)
        self.assertEqual(data["slice"]["instance"], "2")
        self.assertTrue(out_file.is_file())
        self.assertEqual(len(out_file.with_name("m1.txt.sha256").read_text().split()[0]), 64)
        rc, out2, err2 = quiet(cli_main, ["measure", str(p), "--series", "1", "--instance", "2", "--points", "10,12", "13,16",
                                          "--output", str(out_file)])
        self.assertEqual(rc, EXIT_INPUT)
        self.assertIn("never overwrite", err2)
        rc, out3, err3 = quiet(cli_main, ["measure", str(p), "--series", "1", "--instance", "2", "--auto", "12,14", "--thr", "-500",
                                          "--thr-max", "500", "--convention", "recist", "--lesion-type", "node", "--json"])
        self.assertEqual(rc, 0)
        region = [r for r in json.loads(out3)["results"] if r["kind"] == "region_2d"][0]
        self.assertIn("short axis", region["reported_rule"])

    def test_check_json_reports_validation_failure(self):
        make_study(self.root, "a")
        rc, out, err = quiet(cli_main, ["prepare", "a", "--repo", str(self.root)])
        self.assertEqual(rc, 0)
        session = out.strip().splitlines()[-1]
        rc, out, err = quiet(cli_main, ["check", session, "--json"])
        self.assertEqual(rc, EXIT_VALIDATION)
        self.assertFalse(json.loads(out)["ok"])

    def test_env_language_reaches_prepare(self):
        make_study(self.root, "a")
        os.environ["OPENRAD_LANG"] = "tr"
        try:
            rc, out, err = quiet(cli_main, ["prepare", "a", "--repo", str(self.root)])
        finally:
            del os.environ["OPENRAD_LANG"]
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(Path(out.strip().splitlines()[-1]).read_text())["language"], "tr")

    def test_lock_cannot_be_replaced(self):
        p = self.root / "report.md"
        p.write_text("first")
        rc, out, err = quiet(cli_main, ["lock", str(p)])
        self.assertEqual(rc, 0)
        p.write_text("changed")
        self.assertEqual(quiet(cli_main, ["lock", str(p)])[0], IntegrityError.exit_code)
        self.assertEqual(quiet(cli_main, ["lock", str(p), "--verify"])[0], IntegrityError.exit_code)

    def test_inventory_json_redacts_by_default(self):
        p = make_study(self.root, "a", extra=lambda ds: setattr(ds, "AccessionNumber", "ACC999"))
        rc, out, err = quiet(cli_main, ["inventory", str(p), "--output", str(self.root / "inv"), "--json"])
        self.assertEqual(rc, 0)
        inv = json.loads(out)
        self.assertEqual(inv["series"][0]["modality"], "CT")
        self.assertEqual(inv["series"][0]["slice_spacing_mm"], 1.0)
        self.assertEqual(inv["study"]["AccessionNumber"], "<redacted>")
        self.assertNotIn("ACC999", out)
        rc, out, err = quiet(cli_main, ["inventory", str(p), "--output", str(self.root / "inv2"), "--json", "--no-redact"])
        self.assertEqual(json.loads(out)["study"]["AccessionNumber"], "ACC999")

    def test_ct_render_grid_options(self):
        p = make_study(self.root, "a", gaps=tuple(range(10)))
        out_dir = self.root / "render"
        rc, out, err = quiet(cli_main, ["ct-render", str(p), "--series", "1", "--output", str(out_dir), "--windows", "lung",
                                        "--mip", "2", "--mpr", "--mpr-n", "2", "--grid", "3x3"])
        self.assertEqual(rc, 0)
        index = json.loads((out_dir / "render_index.json").read_text())
        native_pages = [k for k, v in index.items() if any(s["purpose"] == "lung:native" for s in v["sources"])]
        self.assertEqual(len(native_pages), 2)                 # 10 slices / 9 per page
        self.assertEqual(len(index[sorted(native_pages)[0]]["sources"]), 9)
        first = Image.open(out_dir / sorted(native_pages)[0])
        self.assertEqual(first.size, (3 * 34, 3 * 34))
        purposes = {src["purpose"] for entry in index.values() for src in entry["sources"]}
        self.assertEqual(purposes, {"lung:native", "lung:mip"})
        rc, out, err = quiet(cli_main, ["ct-render", str(p), "--series", "1", "--output", str(self.root / "r2"), "--windows", "lung",
                                        "--mip", "0", "--grid", "9x9", "--max-side", "200"])
        self.assertEqual(rc, 0)
        index = json.loads((self.root / "r2" / "render_index.json").read_text())
        self.assertEqual(len(index), 1)                                        # 200 // 34 = 5 -> 5x5 holds all 10 slices
        self.assertEqual(len(next(iter(index.values()))["sources"]), 10)
        self.assertEqual(Image.open(self.root / "r2" / next(iter(index))).size, (5 * 34, 2 * 34))


if __name__ == "__main__":
    unittest.main()
