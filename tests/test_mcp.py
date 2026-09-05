"""Synthetic end-to-end tests for the MCP layer. Phantom DICOM only; the wire is driven with plain dicts."""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PIL import Image  # noqa: E402

from openrad import __version__  # noqa: E402
from openrad.mcp import PROTOCOL_VERSION, McpServer  # noqa: E402
from openrad.mcp.install import CLIENTS, merge_into, snippet  # noqa: E402
from openrad.mcp.sessions import SessionStore  # noqa: E402
from tests.test_pipeline import make_study  # noqa: E402

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
for _k in [k for k in os.environ if k.startswith("OPENRAD_")]:
    del os.environ[_k]
logging.getLogger("openrad").setLevel(logging.ERROR)


class McpCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.sent = []
        self.server = McpServer(SessionStore(self.root / ".cache" / "create-report"), self.root, writer=self.sent.append)
        self.n = 0

    def tearDown(self):
        os.chdir(self._cwd)
        self.temp.cleanup()

    def rpc(self, method, params=None):
        self.n += 1
        return self.server.handle({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})

    def call(self, name, **arguments):
        r = self.rpc("tools/call", {"name": name, "arguments": arguments})
        self.assertIn("result", r, r)
        return r["result"]

    def structured(self, name, **arguments):
        res = self.call(name, **arguments)
        self.assertFalse(res.get("isError"), res["content"][0].get("text"))
        return res["structuredContent"]


class HandshakeTests(McpCase):
    def test_initialize_and_capabilities(self):
        r = self.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}})
        res = r["result"]
        self.assertEqual(res["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(res["serverInfo"]["version"], __version__)
        self.assertIn("tools", res["capabilities"])
        self.assertIn("no look, no claim", res["instructions"].lower())
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertTrue(self.server.initialized)
        self.assertEqual(self.rpc("ping")["result"], {})
        old = self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"]
        self.assertEqual(old, PROTOCOL_VERSION)

    def test_errors(self):
        r = self.rpc("no/such")
        self.assertEqual(r["error"]["code"], -32601)
        r = self.server.handle({"id": 9, "method": "ping"})
        self.assertEqual(r["error"]["code"], -32600)
        r = self.rpc("resources/read", {})
        self.assertEqual(r["error"]["code"], -32602)
        r = self.rpc("resources/read", {"uri": "openrad://nope"})
        self.assertEqual(r["error"]["code"], -32602)
        r = self.rpc("tools/call", {"name": "does_not_exist"})
        self.assertEqual(r["error"]["code"], -32602)
        r = self.rpc("logging/setLevel", {"level": "loud"})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(self.rpc("logging/setLevel", {"level": "warning"})["result"], {})
        self.server.notify("info", "hidden")
        self.server.notify("error", "shown")
        self.assertEqual([n["params"]["level"] for n in self.sent], ["error"])

    def test_tool_catalogue_and_argument_validation(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        for expected in ("openrad_doctor", "study_inventory", "session_open", "render_ct", "render_pet", "render_mr", "zoom", "measure",
                         "session_register", "page_view", "session_set_region", "session_add_claim", "session_check", "session_finish", "anonymize"):
            self.assertIn(expected, names)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertIn("readOnlyHint", t["annotations"])
        res = self.call("render_ct", study_dir="/x")
        self.assertTrue(res["isError"])
        self.assertIn("missing 'series'", res["content"][0]["text"])
        res = self.call("render_ct", study_dir="/x", series="1", bogus=1)
        self.assertIn("unknown argument", res["content"][0]["text"])
        res = self.call("zoom", study_dir="/x", series="1", center="1,1", plane="oblique")
        self.assertIn("must be one of", res["content"][0]["text"])

    def test_doctor_config_prompts_and_static_resources(self):
        st = self.structured("openrad_doctor")
        self.assertTrue(st["ok"])
        st = self.structured("openrad_config")
        self.assertEqual(st["settings"]["general"]["lang"], "en")
        prompts = self.rpc("prompts/list")["result"]["prompts"]
        self.assertEqual({p["name"] for p in prompts}, {"review_study", "compare_studies", "explain_for_patient", "blinded_audit"})
        msg = self.rpc("prompts/get", {"name": "compare_studies", "arguments": {"study_folders": "/a, /b", "language": "en"}})["result"]
        self.assertIn("/a", msg["messages"][0]["content"]["text"])
        self.assertEqual(self.rpc("prompts/get", {"name": "review_study", "arguments": {}})["error"]["code"], -32602)
        resources = self.rpc("resources/list")["result"]["resources"]
        uris = {r["uri"] for r in resources}
        self.assertIn("openrad://schema/session", uris)
        self.assertIn("openrad://checklist/ct_thorax", uris)
        body = self.rpc("resources/read", {"uri": "openrad://checklist/pet_ct"})["result"]["contents"][0]
        self.assertIn("SUV", body["text"])
        schema = json.loads(self.rpc("resources/read", {"uri": "openrad://schema/session"})["result"]["contents"][0]["text"])
        self.assertEqual(schema["properties"]["schema"]["const"], 2)
        templates = self.rpc("resources/templates/list")["result"]["resourceTemplates"]
        self.assertTrue(any(t["uriTemplate"].endswith("/page/{name}") for t in templates))


class ReviewFlowTests(McpCase):
    def test_full_session_over_the_protocol(self):
        study = make_study(self.root, "phantom", gaps=tuple(range(6)))
        # inventory with hints, redacted by default
        inv = self.structured("study_inventory", study_dir=str(study))
        self.assertEqual(inv["inventory"]["study"]["AccessionNumber"], "<redacted>")
        # open a session by absolute path -> studies_root inferred
        opened = self.structured("session_open", studies=[str(study)])
        sid = opened["session"]
        self.assertTrue(sid.startswith("run-"))
        self.assertEqual(opened["language"], "en")
        self.assertEqual(opened["plan"][0]["tool"], "study_inventory")
        self.assertIn("CT", opened["region_keys"])
        listed = self.structured("session_list")["sessions"]
        self.assertEqual(listed[0]["id"], sid)
        # render into the session work dir; one page inline, rest as resource links
        rendered = self.structured("render_ct", session=sid, study_dir=str(study), series="1", windows="lung", mip=2, grid="2x2", inline=1)
        self.assertEqual(sorted(rendered["purposes"]), ["lung:mip", "lung:native"])
        self.assertGreaterEqual(len(rendered["pages"]), 3)
        res = self.call("render_ct", session=sid, study_dir=str(study), series="1", windows="lung", mip=0, grid="2x2", inline=1, output=str(self.root / "elsewhere"))
        kinds = [c["type"] for c in res["content"]]
        self.assertEqual(kinds.count("image"), 1)
        self.assertIn("resource_link", kinds)
        # register, then view every page: page_view is what marks reviewed
        reg = self.structured("session_register", session=sid)
        self.assertEqual(reg["pages"], len(rendered["pages"]))
        before = self.structured("session_status", session=sid)
        self.assertEqual(before["coverage"]["pages_reviewed"], 0)
        self.assertTrue(any(a["code"] == "passes_missing" for a in before["alerts"]) is False)  # series still pending -> no read passes yet
        for page in reg["unreviewed"]:
            res = self.call("page_view", session=sid, page=page)
            self.assertFalse(res.get("isError"))
            img = [c for c in res["content"] if c["type"] == "image"][0]
            decoded = base64.b64decode(img["data"])
            self.assertEqual(Image.open(io.BytesIO(decoded)).format, "PNG")
            self.assertGreater(len(res["structuredContent"]["sources"]), 0)
        after = self.structured("session_status", session=sid)
        self.assertEqual(after["coverage"]["pages_reviewed"], after["coverage"]["pages_total"])
        self.assertEqual(self.call("page_view", session=sid, page="missing.png")["isError"], True)
        # series disposition with immediate feedback
        se = self.structured("session_set_series", session=sid, series="1", disposition="read", geometry_checked=True, required_passes=["lung:native", "lung:mip"])
        self.assertEqual(se["errors"], [])
        # measure with hashed evidence inside the session
        m = self.structured("measure", session=sid, study_dir=str(study), series="1", instance="3", points=["10,12", "13,16"], label="L1_long")
        self.assertTrue(Path(m["evidence_file"]).is_file())
        self.assertEqual(len(m["sha256"]), 64)
        self.assertEqual(m["measurement_template"]["ref"]["instance"], "3")
        ev = self.rpc("resources/read", {"uri": f"openrad://session/{sid}/evidence/{Path(m['evidence_file']).name}"})["result"]["contents"][0]
        self.assertIn("distance", ev["text"])
        # zoom returns an inline image
        z = self.call("zoom", session=sid, study_dir=str(study), series="1", instance="3", center="12,14", size=16, scale=2)
        self.assertEqual([c["type"] for c in z["content"]].count("image"), 1)
        # regions: unmet requirements are returned per region
        s = SessionStore.load(self.server.store.resolve(sid))
        study_uid = s["studies"][0]["uid"]
        series_uid = s["studies"][0]["series"][0]["uid"]
        sop = next(iter(s["studies"][0]["series"][0]["sops"]))
        page_path = s["pages"][0]["path"]
        r = self.structured("session_set_region", session=sid, region="right_lung_all_lobes", status="no_finding", text="x", explanation="y")
        self.assertTrue(any("source references missing" in e for e in r["errors"]))
        r = self.structured("session_set_region", session=sid, region="right_lung_all_lobes", status="no_finding", text="x", explanation="y",
                            refs=[{"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop, "row": 12, "col": 14}], pages=[page_path])
        self.assertEqual(r["errors"], [])
        for key in s["studies"][0]["regions"]:
            if key != "CT:right_lung_all_lobes":
                self.structured("session_set_region", session=sid, region=key, status="not_covered", text="phantom", explanation="phantom")
        claim = {"id": "L1", "text": "phantom block", "priority": "routine", "confidence": "high",
                 "refs": [{"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop, "row": 12, "col": 14}], "pages": [page_path],
                 "patient": {"meaning": "m", "importance": "i", "uncertainty": "u", "discuss_with_doctor": "d"},
                 "measurements": [{"value": 7.2, "unit": "mm", "method": "phantom diagonal", "evidence_file": m["evidence_file"], "sha256": m["sha256"],
                                   "ref": {"study_uid": study_uid, "series_uid": series_uid, "sop_uid": sop, "row": 10, "col": 12}}]}
        c = self.structured("session_add_claim", session=sid, claim=claim)
        self.assertEqual(c["errors"], [])
        chk = self.call("session_check", session=sid)
        self.assertTrue(chk["isError"])  # meta missing
        meta = self.structured("session_set_meta", session=sid, fields={"reader": "phantom test", "context_disclosure": "none", "limitations": ["phantom"],
                                                                        "patient_context": "phantom", "patient_limitations": "phantom", "reading_complete": True})
        self.assertEqual(meta["errors"], [])
        fin = self.structured("session_finish", session=sid, output_dir=str(self.root / "reports"))
        self.assertEqual(sorted(Path(f).name for f in fin["files"]), ["phantom__guide.md", "phantom__report.md"])
        # resources for the finished session
        status = json.loads(self.rpc("resources/read", {"uri": f"openrad://session/{sid}/status"})["result"]["contents"][0]["text"])
        self.assertTrue(status["coverage"]["finalized"])
        page_uri = f"openrad://session/{sid}/page/{Path(page_path).name}"
        blob = self.rpc("resources/read", {"uri": page_uri})["result"]["contents"][0]
        self.assertEqual(blob["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(blob["blob"]).startswith(b"\x89PNG"))
        # unknown session field is refused
        res = self.call("session_set_meta", session=sid, fields={"diagnosis": "x"})
        self.assertTrue(res["isError"])

    def test_comparison_plan_and_alerts(self):
        make_study(self.root, "a", "20260101")
        make_study(self.root, "b", "20260301")
        opened = self.structured("session_open", studies=["a", "b"], studies_root=str(self.root / "DCIM"))
        self.assertEqual(opened["mode"], "comparison")
        self.assertTrue(any(step.get("step") == 6.5 for step in opened["plan"]))
        st = self.structured("session_status", session=opened["session"])
        self.assertIn("comparison_pending", {a["code"] for a in st["alerts"]})
        # a read series without passes raises an alert, unrendered slices too
        self.structured("session_set_series", session=opened["session"], series="1", study_uid=opened["studies"][0]["uid"], disposition="read", geometry_checked=True)
        st = self.structured("session_status", session=opened["session"])
        self.assertIn("passes_missing", {a["code"] for a in st["alerts"]})
        self.structured("session_set_series", session=opened["session"], series="1", study_uid=opened["studies"][0]["uid"], disposition="read",
                        geometry_checked=True, required_passes=["lung:native"])
        st = self.structured("session_status", session=opened["session"])
        self.assertIn("unrendered_slices", {a["code"] for a in st["alerts"]})

    def test_tool_errors_are_results_not_protocol_errors(self):
        res = self.call("study_inventory", study_dir=str(self.root / "nowhere"))
        self.assertTrue(res["isError"])
        self.assertIn("exit_code", res["structuredContent"])
        res = self.call("anonymize", src=str(self.root), dst=str(self.root / "out"))
        self.assertTrue(res["isError"])
        self.assertIn("OPENRAD_PSEUDONYM_SALT", res["content"][0]["text"])
        res = self.call("session_status", session="run-does-not-exist")
        self.assertTrue(res["isError"])


class TransportTests(McpCase):
    def test_stdio_loop_and_notifications(self):
        study = make_study(self.root, "p")
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            "not json",
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "session_open", "arguments": {"studies": [str(study)]}}}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
        ]
        out = io.StringIO()
        logging.getLogger("openrad").setLevel(logging.INFO)
        try:
            rc = self.server.serve(io.StringIO("\n".join(lines) + "\n"), out)
        finally:
            logging.getLogger("openrad").setLevel(logging.ERROR)
        self.assertEqual(rc, 0)
        messages = [json.loads(line) for line in out.getvalue().splitlines()]
        by_id = {m.get("id"): m for m in messages if "id" in m and m.get("id") is not None}
        self.assertIn("result", by_id[1])
        self.assertEqual(by_id[2]["result"]["structuredContent"]["mode"], "single")
        self.assertEqual(by_id[3]["result"], {})
        parse_errors = [m for m in messages if m.get("error", {}).get("code") == -32700]
        self.assertEqual(len(parse_errors), 1)
        notes = [m for m in messages if m.get("method") == "notifications/message"]
        self.assertTrue(notes, "engine progress should be forwarded as logging notifications")
        self.assertTrue(all(m["jsonrpc"] == "2.0" for m in messages))
        self.assertEqual(sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else "", "")


class InstallTests(unittest.TestCase):
    def test_snippets_and_merge(self):
        repo = Path("/work/repo")
        for client in CLIENTS:
            text = snippet(client, repo, lang="en")
            self.assertIn("openradiology", text)
            self.assertIn("/work/repo", text)
        data = json.loads(snippet("claude-desktop", repo))
        entry = data["mcpServers"]["openradiology"]
        self.assertEqual(entry["args"][:3], ["-m", "openrad", "mcp"])
        self.assertEqual(entry["command"], sys.executable)
        self.assertEqual(json.loads(snippet("cursor", repo, use_entrypoint=True))["mcpServers"]["openradiology"]["command"], "openrad")
        self.assertTrue(snippet("claude-code", repo).startswith("claude mcp add openradiology"))
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "claude_desktop_config.json"
            cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
            merge_into(cfg, repo)
            merged = json.loads(cfg.read_text())
            self.assertEqual(set(merged["mcpServers"]), {"other", "openradiology"})
            self.assertEqual(merged["theme"], "dark")
            self.assertTrue(cfg.with_suffix(".json.bak").is_file())
            cfg.write_text("{not json")
            with self.assertRaises(Exception):
                merge_into(cfg, repo)

    def test_cli_print_and_list_tools(self):
        from openrad.cli import main as cli_main
        from tests.test_pipeline import quiet
        rc, out, err = quiet(cli_main, ["mcp", "--print", "--client", "windsurf"])
        self.assertEqual(rc, 0)
        self.assertIn("mcpServers", out)
        rc, out, err = quiet(cli_main, ["mcp", "--list-tools", "--json"])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(len(json.loads(out)), 20)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "mcp.json"
            rc, out, err = quiet(cli_main, ["mcp", "--install", "cursor", "--write", "--config-path", str(target)])
            self.assertEqual(rc, 0)
            self.assertIn("openradiology", json.loads(target.read_text())["mcpServers"])


if __name__ == "__main__":
    unittest.main()
