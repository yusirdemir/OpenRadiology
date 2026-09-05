"""Tests for the desktop sidecar.

The server is exercised over real HTTP against a real synthetic study, because
the interesting failures live in the seams: authentication, the path jail, the
binary slice contract, and the refusal to record a review that no attestation
covers.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openrad.server.app import create_app  # noqa: E402
from openrad.server.attest import AttestationLedger, DwellPolicy, ViewEvent  # noqa: E402
from tests.test_pipeline import make_study  # noqa: E402


def request(url, method="GET", token=None, payload=None, raw=False):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
        headers = dict(response.headers)
        if raw:
            return response.status, data, headers
        return response.status, json.loads(data or b"{}"), headers


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="openrad-server-")).resolve()
        cls.study = make_study(cls.tmp, "study_a", date="20260101", gaps=tuple(range(8)))
        cls.state, cls.server = create_app(budget_bytes=512 * 1024 ** 2)
        cls.state.allow(cls.tmp)
        cls.server.start()
        cls.base = f"http://{cls.server.host}:{cls.server.port}"
        cls.token = cls.server.token

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        cls.state.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def get(self, path, **kw):
        return request(f"{self.base}{path}", token=self.token, **kw)

    def post(self, path, payload, **kw):
        return request(f"{self.base}{path}", method="POST", token=self.token, payload=payload, **kw)

    # -- transport ---------------------------------------------------------
    def test_health_is_public_and_everything_else_is_not(self):
        status, body, _ = request(f"{self.base}/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request(f"{self.base}/status")
        self.assertEqual(ctx.exception.code, 401)

    def test_a_wrong_token_is_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request(f"{self.base}/status", token="not-the-token")
        self.assertEqual(ctx.exception.code, 401)

    def test_unknown_route_is_404_and_wrong_verb_is_405(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/health", {})
        self.assertEqual(ctx.exception.code, 405)

    def test_path_jail_rejects_a_folder_that_was_never_opened(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/series/list", {"study": "/etc"})
        self.assertEqual(ctx.exception.code, 403)

    # -- studies -----------------------------------------------------------
    def test_scan_groups_by_study_uid_not_by_folder_name(self):
        make_study(self.tmp, "study_b", date="20250701", gaps=(0, 1, 2))
        self.state.headers.invalidate()
        _, body, _ = self.post("/studies/scan", {"root": str(self.tmp / "DCIM")})
        uids = {card["study_uid"] for card in body["studies"]}
        self.assertEqual(len(uids), 2)
        self.assertEqual([c["date"] for c in body["studies"]], ["20250701", "20260101"])
        self.assertTrue(all(card["supported"] for card in body["studies"]))

    def test_series_list_and_meta(self):
        _, body, _ = self.post("/series/list", {"study": str(self.study)})
        self.assertEqual(len(body["series"]), 1)
        card = body["series"][0]
        self.assertEqual(card["modality"], "CT")
        self.assertEqual(card["instances"], 8)

        _, meta, _ = self.post("/series/meta", {"study": str(self.study), "series": "1"})
        self.assertEqual(meta["geometry"]["shape_zyx"], [8, 32, 32])
        self.assertEqual(meta["planes"]["ax"]["count"], 8)
        self.assertEqual(meta["planes"]["cor"]["count"], 32)
        self.assertEqual(len(meta["sop_uids"]), 8)
        self.assertEqual(meta["planes"]["ax"]["mm_per_px"], [2.0, 1.0])

    # -- pixels ------------------------------------------------------------
    def test_slice_is_raw_float32_with_calibration_in_the_headers(self):
        url = f"/series/slice?study={self.study}&series=1&plane=ax&index=3"
        status, data, headers = self.get(url, raw=True)
        self.assertEqual(status, 200)
        # CT rescales to integral Hounsfield units, so it travels losslessly as int16.
        self.assertEqual(headers["X-Dtype"], "int16")
        self.assertEqual(headers["X-Shape"], "32,32")
        self.assertEqual(len(data), 32 * 32 * 2)
        array = np.frombuffer(data, dtype="<i2").reshape(32, 32)
        # make_study writes 500 into a block, with slope 2 / intercept -1000.
        self.assertAlmostEqual(float(array[12, 14]), 0.0, places=3)
        self.assertAlmostEqual(float(array[0, 0]), -1000.0, places=3)
        self.assertEqual(headers["X-Mm-Per-Px"].split(",")[0], "2.000000")
        self.assertTrue(headers["X-Sop-Uid"])

    def test_reformats_report_their_own_anisotropic_spacing(self):
        _, _, headers = self.get(f"/series/slice?study={self.study}&series=1&plane=cor&index=12", raw=True)
        self.assertEqual(headers["X-Plane"], "cor")
        vertical, horizontal = headers["X-Mm-Per-Px"].split(",")
        self.assertAlmostEqual(float(vertical), 1.0, places=3)     # dz
        self.assertAlmostEqual(float(horizontal), 1.0, places=3)   # column spacing
        self.assertEqual(headers["X-Shape"], "8,32")

    def test_slice_index_outside_the_volume_is_a_usage_error(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get(f"/series/slice?study={self.study}&series=1&plane=ax&index=99", raw=True)
        self.assertEqual(ctx.exception.code, 400)

    def test_atlas_is_a_png_filmstrip(self):
        _, data, headers = self.get(f"/series/atlas?study={self.study}&series=1&count=4&tile=32", raw=True)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(headers["X-Atlas-Count"], "4")
        self.assertEqual(len(headers["X-Atlas-Indices"].split(",")), 4)

    def test_point_and_locate_are_inverses(self):
        _, point, _ = self.post("/series/point", {"study": str(self.study), "series": "1",
                                                  "index": 4, "row": 11, "col": 13})
        _, found, _ = self.post("/series/locate", {"study": str(self.study), "series": "1",
                                                  "patient_mm": point["patient_mm"]})
        self.assertEqual(found["index"], 4)
        self.assertAlmostEqual(found["row"], 11.0, places=3)
        self.assertAlmostEqual(found["col"], 13.0, places=3)
        self.assertTrue(found["in_plane"])

    # -- volume cache ------------------------------------------------------
    def test_the_volume_is_decoded_once_and_reused(self):
        self.post("/series/meta", {"study": str(self.study), "series": "1"})
        first = self.state.volumes.get(self.study, "1")
        second = self.state.volumes.get(self.study, "1")
        self.assertIs(first, second)

    # -- measurement -------------------------------------------------------
    def test_measurement_goes_through_the_engine_and_can_write_evidence(self):
        work = self.tmp / "work"
        work.mkdir(exist_ok=True)
        self.state.allow(work)
        _, body, _ = self.post("/measure", {
            "study": str(self.study), "series": "1", "instance": "4",
            "roi": "12,14,3", "output": str(work / "roi.txt"),
        })
        roi = [r for r in body["data"]["results"] if r["kind"] == "roi"][0]
        self.assertEqual(roi["unit"], "HU")
        self.assertEqual(body["evidence"]["path"], str(work / "roi.txt"))
        self.assertEqual(len(body["evidence"]["sha256"]), 64)
        self.assertTrue((work / "roi.txt.sha256").is_file())
        self.assertIn("measure", body["command"])

    def test_evidence_is_never_overwritten(self):
        work = self.tmp / "work2"
        work.mkdir(exist_ok=True)
        self.state.allow(work)
        payload = {"study": str(self.study), "series": "1", "instance": "4",
                   "roi": "12,14,3", "output": str(work / "once.txt")}
        self.post("/measure", payload)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/measure", payload)
        self.assertEqual(ctx.exception.code, 409)

    def test_a_measurement_outside_the_image_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/measure", {"study": str(self.study), "series": "1", "instance": "4", "roi": "999,999,3"})
        self.assertEqual(ctx.exception.code, 400)


class AttestationCase(unittest.TestCase):
    """The rule the command line cannot enforce."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="openrad-attest-")).resolve()
        self.ledger = AttestationLedger(self.tmp, DwellPolicy(min_dwell_ms=400, min_scale=1.0))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def event(self, **kw):
        base = {"sop_uid": "1.2.3", "dwell_ms": 900, "scale": 1.4, "focused": True, "visible": True}
        base.update(kw)
        return ViewEvent.from_json(base)

    def test_a_proper_view_is_recorded(self):
        result = self.ledger.record([self.event()])
        self.assertEqual(result["accepted"], 1)
        self.assertIn("1.2.3", self.ledger.attested_sops())

    def test_a_glance_too_short_to_read_is_rejected(self):
        result = self.ledger.record([self.event(dwell_ms=120)])
        self.assertEqual(result["accepted"], 0)
        self.assertIn("dwell", result["rejected"][0]["reason"])

    def test_an_image_too_small_to_read_is_rejected(self):
        result = self.ledger.record([self.event(scale=0.3)])
        self.assertEqual(result["accepted"], 0)
        self.assertIn("magnification", result["rejected"][0]["reason"])

    def test_a_background_window_does_not_attest(self):
        result = self.ledger.record([self.event(focused=False)])
        self.assertEqual(result["accepted"], 0)

    def test_the_ledger_survives_a_restart(self):
        self.ledger.record([self.event()])
        self.assertIn("1.2.3", AttestationLedger(self.tmp).attested_sops())

    def test_reviewed_is_cleared_when_no_attestation_covers_the_page(self):
        session = {"pages": [{"path": str(self.tmp / "sheet.png"), "reviewed": True, "purpose": "lung:native",
                              "sources": [{"sop_uid": "1.2.3"}, {"sop_uid": "4.5.6"}]}]}
        self.ledger.record([self.event()])          # only one of the two sources
        session, refusals = self.ledger.enforce(session)
        self.assertFalse(session["pages"][0]["reviewed"])
        self.assertEqual(len(refusals), 1)
        self.assertIn("1 of 2", refusals[0]["reason"])

    def test_reviewed_survives_when_every_source_slice_was_displayed(self):
        session = {"pages": [{"path": str(self.tmp / "sheet.png"), "reviewed": True, "purpose": "lung:native",
                              "sources": [{"sop_uid": "1.2.3"}, {"sop_uid": "4.5.6"}]}]}
        self.ledger.record([self.event(), self.event(sop_uid="4.5.6")])
        session, refusals = self.ledger.enforce(session)
        self.assertTrue(session["pages"][0]["reviewed"])
        self.assertEqual(refusals, [])

    def test_viewing_the_contact_sheet_itself_also_attests(self):
        sheet = self.tmp / "sheet.png"
        sheet.write_bytes(b"")
        session = {"pages": [{"path": str(sheet), "reviewed": True, "purpose": "lung:native",
                              "sources": [{"sop_uid": "9.9.9"}]}]}
        self.ledger.record([ViewEvent.from_json({"source": "page", "page_path": str(sheet),
                                                 "dwell_ms": 1200, "scale": 1.0,
                                                 "focused": True, "visible": True})])
        session, refusals = self.ledger.enforce(session)
        self.assertTrue(session["pages"][0]["reviewed"])
        self.assertEqual(refusals, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
