"""MCP tool catalogue: every tool is an ``openrad`` CLI command with a JSON schema.

Handlers build an argv, run the command in-process with stdout captured, and
turn the machine-readable output into MCP content blocks. Session tools edit
``session.json`` through the same validator the CLI uses.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .. import __version__
from ..config import load_settings
from ..create_report import (CLAIM_PRIORITIES, COMPARISON_STATUSES, CONFIDENCES, LANGUAGES, REGION_STATUSES,
                             TIMEPOINT_STATUSES, prepare, register, validate)
from ..errors import InputError, OpenRadError, UsageError
from .content import ToolContext, ToolResult, error_result, inline_images, json_block, resource_link, text
from .sessions import SessionStore, errors_for, find_series, plan, region_keys, status

Handler = Callable[[Dict[str, Any], ToolContext], ToolResult]


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: Dict[str, Any]
    handler: Handler
    read_only: bool = True
    destructive: bool = False
    idempotent: bool = True

    def to_wire(self) -> Dict[str, Any]:
        return {"name": self.name, "title": self.title, "description": self.description, "inputSchema": self.input_schema,
                "annotations": {"title": self.title, "readOnlyHint": self.read_only, "destructiveHint": self.destructive,
                                "idempotentHint": self.idempotent, "openWorldHint": False}}


def _schema(props: Dict[str, Any], required: Sequence[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


S = {
    "session": {"type": "string", "description": "Session id (run-xxxx), run directory or session.json path"},
    "study_dir": {"type": "string", "description": "Folder holding one DICOM study"},
    "series": {"type": "string", "description": "SeriesNumber or SeriesInstanceUID"},
    "inline": {"type": "integer", "minimum": 0, "maximum": 6, "default": 1,
               "description": "How many rendered pages to return inline as images; the rest come back as resource links"},
    "grid": {"type": "string", "description": "Tiles per sheet COLSxROWS or auto (3x3 systematic scan, 2x2 detail)"},
    "vision": {"type": "string", "enum": ["claude", "gpt", "gemini", "local"], "description": "Vision budget profile"},
    "allow_tilt": {"type": "boolean", "default": False, "description": "Accept a gantry-tilted stack"},
}


class Catalogue:
    def __init__(self, store: SessionStore, repo_default: Path) -> None:
        self.store = store
        self.repo_default = repo_default
        self.tools: Dict[str, Tool] = {}
        for t in self._build():
            self.tools[t.name] = t

    def list(self) -> List[Dict[str, Any]]:
        return [t.to_wire() for t in self.tools.values()]

    def call(self, name: str, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(name)
        problems = _check_args(tool.input_schema, args or {})
        if problems:
            return error_result("invalid arguments: " + "; ".join(problems), tool=name)
        try:
            return tool.handler(args or {}, ctx)
        except OpenRadError as e:
            return error_result(f"{e.__class__.__name__}: {e.message}", exit_code=e.exit_code, tool=name)
        except (KeyError, ValueError, OSError) as e:
            return error_result(f"{e.__class__.__name__}: {e}", tool=name)

    # ---------------------------------------------------------------- helpers
    def _work(self, args: Dict[str, Any]) -> Optional[Path]:
        if not args.get("session"):
            return None
        path = self.store.resolve(args["session"])
        return Path(self.store.load(path)["work_dir"])

    def _session_uri(self, args: Dict[str, Any]) -> str:
        return self.store.session_id(self.store.resolve(args["session"])) if args.get("session") else ""

    @staticmethod
    def _out_dir(work: Optional[Path], explicit: Optional[str], tag: str) -> Path:
        if explicit:
            return Path(explicit).expanduser()
        if work is None:
            raise UsageError("Give a session (renders go into its work directory) or an explicit output folder")
        return work / "render" / re.sub(r"[^A-Za-z0-9_.-]+", "_", tag)

    def _render_result(self, ctx: ToolContext, rc: int, out: str, err: str, inline: int, session_id: str, summary: str) -> ToolResult:
        if rc != 0:
            return error_result(err.strip() or f"render failed with exit code {rc}", exit_code=rc)
        paths = [Path(line.strip()) for line in out.splitlines() if line.strip().endswith(".png") and Path(line.strip()).is_file()]
        blocks = [text(summary + f"\n{len(paths)} sheet(s) written.")]
        images, rest = inline_images(paths, inline)
        for p, block in zip(paths[:len(images)], images):
            blocks.append(text(f"[inline] {p.name}"))
            blocks.append(block)
        for p in rest:
            uri = f"openrad://session/{session_id}/page/{p.name}" if session_id else p.resolve().as_uri()
            blocks.append(resource_link(uri, p.name, "rendered sheet; open with page_view to mark it reviewed"))
        index = paths[0].parent / "render_index.json" if paths else None
        provenance = json.loads(index.read_text()) if index and index.is_file() else {}
        return ToolResult(blocks, {"pages": [str(p) for p in paths], "inline": [str(p) for p in paths[:len(images)]],
                                   "purposes": sorted({src["purpose"] for e in provenance.values() for src in e.get("sources", [])}),
                                   "next": "session_register then page_view for every page" if session_id else "register into a session to audit coverage"})

    # ------------------------------------------------------------------ tools
    def _build(self) -> List[Tool]:
        store = self.store

        def doctor(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            rc, payload, err = ctx.run_json(["doctor", "--json"])
            return ToolResult([json_block(payload)], payload, rc != 0)

        def config(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            rc, payload, err = ctx.run_json(["config", "--json"])
            return ToolResult([json_block(payload)], payload, rc != 0)

        def study_inventory(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            study = Path(args["study_dir"]).expanduser()
            work = self._work(args)
            out_dir = (work / "inventory" / study.name) if work else (store.cache_dir / "inventory" / hashlib.sha256(str(study.resolve()).encode()).hexdigest()[:12])
            argv = ["inventory", str(study), "--output", str(out_dir), "--json"]
            if args.get("include_identifiers"):
                argv.append("--no-redact")
            rc, payload, err = ctx.run_json(argv)
            if rc != 0 or payload is None:
                return error_result(err.strip() or "inventory failed", exit_code=rc)
            md = (out_dir / "inventory.md").read_text(encoding="utf-8")
            hints = _inventory_hints(payload)
            blocks = [text(md)]
            if hints:
                blocks.append(text("Technical hints (not findings):\n- " + "\n- ".join(hints)))
            return ToolResult(blocks, {"inventory": payload, "hints": hints, "files": [str(out_dir / "inventory.json"), str(out_dir / "inventory.md")]})

        def session_open(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            studies = [str(Path(s).expanduser()) for s in args["studies"]]
            repo = Path(args.get("repo") or self.repo_default).expanduser().resolve()
            studies_root = args.get("studies_root")
            absolute = [Path(s) for s in studies if Path(s).is_absolute()]
            if not studies_root and absolute and len({p.parent for p in absolute}) == 1 and len(absolute) == len(studies):
                studies_root = str(absolute[0].parent)
                studies = [p.name for p in absolute]
            path = prepare(repo, studies, Path(args["identity_source"]) if args.get("identity_source") else None,
                           Path(studies_root) if studies_root else None, args.get("lang"), None, load_settings())
            s = store.load(path)
            sid = store.session_id(path)
            summary = (f"Session {sid} opened ({s['mode']}, language {s['language']}). Studies in acquisition order: "
                       + "; ".join(f"{st['date']} {','.join(st['modalities'])} `{st['folder']}` ({len(st['series'])} series)" for st in s["studies"])
                       + f".\nRegions to resolve: {sum(len(st['regions']) for st in s['studies'])}. Follow session_plan.")
            return ToolResult([text(summary), json_block({"plan": plan(s, sid)})],
                              {"session": sid, "path": str(path), "work_dir": s["work_dir"], "mode": s["mode"], "language": s["language"],
                               "studies": [{"folder": st["folder"], "uid": st["uid"], "date": st["date"], "modalities": st["modalities"],
                                            "series": [{"uid": se["uid"], "number": se["number"], "modality": se["modality"],
                                                        "description": se["description"], "slices": len(se["sops"])} for se in st["series"]]}
                                           for st in s["studies"]],
                               "region_keys": region_keys(s), "plan": plan(s, sid)})

        def session_list(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            items = store.list()
            return ToolResult([json_block(items)], {"sessions": items})

        def session_status_tool(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            st = status(store.load(path), store.session_id(path))
            cov = st["coverage"]
            head = (f"Pages reviewed {cov['pages_reviewed']}/{cov['pages_total']}; regions pending {len(cov['regions_pending'])}/{cov['regions_total']}; "
                    f"claims {cov['claims']}; validation errors {st['validation_errors']}; alerts {len(st['alerts'])}.")
            return ToolResult([text(head), json_block(st)], st)

        def session_plan_tool(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            steps = plan(store.load(path), store.session_id(path))
            return ToolResult([json_block(steps)], {"plan": steps})

        def render_ct(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            work = self._work(args)
            out = self._out_dir(work, args.get("output"), f"{Path(args['study_dir']).name}_S{args['series']}_{args.get('windows', 'lung,soft,bone')}")
            argv = ["ct-render", args["study_dir"], "--series", str(args["series"]), "--output", str(out),
                    "--windows", args.get("windows", "lung,soft,bone")]
            for key, flag in (("step", "--step"), ("mip", "--mip"), ("grid", "--grid"), ("vision", "--vision"), ("zmin", "--zmin"), ("zmax", "--zmax"),
                              ("mpr_n", "--mpr-n"), ("mpr_thick", "--mpr-thick")):
                if args.get(key) is not None:
                    argv += [flag, str(args[key])]
            if args.get("mpr"):
                argv.append("--mpr")
            if args.get("allow_tilt"):
                argv.append("--allow-tilt")
            rc, o, e = ctx.run_cli(argv)
            return self._render_result(ctx, rc, o, e, int(args.get("inline", 1)), self._session_uri(args),
                                       f"CT render of series {args['series']} ({args.get('windows', 'lung,soft,bone')}) -> {out}")

        def render_pet(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            work = self._work(args)
            out = self._out_dir(work, args.get("output"), f"{Path(args['study_dir']).name}_PT{args['pt']}_CT{args['ct']}")
            argv = ["pet-render", args["study_dir"], "--pt", str(args["pt"]), "--ct", str(args["ct"]), "--output", str(out)]
            for key, flag in (("weight", "--weight"), ("suv_threshold", "--suv-thr"), ("axial_step", "--axial-step"), ("grid", "--grid"),
                              ("vision", "--vision"), ("zmin", "--zmin"), ("zmax", "--zmax")):
                if args.get(key) is not None:
                    argv += [flag, str(args[key])]
            rc, o, e = ctx.run_cli(argv)
            result = self._render_result(ctx, rc, o, e, int(args.get("inline", 1)), self._session_uri(args),
                                         f"PET/CT render PT {args['pt']} + CT {args['ct']} -> {out}")
            info = out / "suv_info.json"
            if info.is_file() and result.structured is not None:
                result.structured["suv_info"] = json.loads(info.read_text())
                result.content.insert(1, text("SUV info: " + json.dumps(result.structured["suv_info"], default=str)))
            hot = out / "hotspots.md"
            if hot.is_file():
                result.content.insert(2, text(hot.read_text(encoding="utf-8")))
            return result

        def render_mr(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            work = self._work(args)
            tag = f"{Path(args['study_dir']).name}_MR_" + ("pair_" + "_".join(args["pair"]) if args.get("pair") else "_".join(args.get("series") or ["all"]))
            out = self._out_dir(work, args.get("output"), tag)
            argv = ["mr-render", args["study_dir"], "--output", str(out)]
            if args.get("pair"):
                argv += ["--pair", *[str(x) for x in args["pair"]]]
            elif args.get("series"):
                argv += ["--series", *[str(x) for x in args["series"]]]
            for key, flag in (("step", "--step"), ("grid", "--grid"), ("vision", "--vision"), ("tile", "--tile")):
                if args.get(key) is not None:
                    argv += [flag, str(args[key])]
            if args.get("invert"):
                argv.append("--invert")
            rc, o, e = ctx.run_cli(argv)
            return self._render_result(ctx, rc, o, e, int(args.get("inline", 1)), self._session_uri(args), f"MR render -> {out}")

        def zoom(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            work = self._work(args)
            name = args.get("label") or f"zoom_S{args['series']}_{args.get('instance') or args.get('z')}_{args['center'].replace(',', '-')}_{args.get('plane', 'ax')}"
            out = Path(args["output"]).expanduser() if args.get("output") else (work / "zoom" / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.png" if work else None)
            if out is None:
                raise UsageError("Give a session or an explicit output path")
            argv = ["zoom", args["study_dir"], "--series", str(args["series"]), "--center", args["center"], "--output", str(out)]
            for key, flag in (("instance", "--instance"), ("z", "--z"), ("size", "--size"), ("scale", "--scale"), ("window", "--window"),
                              ("context", "--context"), ("stride", "--stride"), ("plane", "--plane")):
                if args.get(key) is not None:
                    argv += [flag, str(args[key])]
            if args.get("allow_tilt"):
                argv.append("--allow-tilt")
            rc, o, e = ctx.run_cli(argv)
            if rc != 0:
                return error_result(e.strip() or "zoom failed", exit_code=rc)
            images, _ = inline_images([out], 1)
            note = e.strip().splitlines()[-1] if e.strip() else ""
            return ToolResult([text(f"Zoom {args['center']} on series {args['series']} ({args.get('plane', 'ax')}). {note}\nGrid labels are native row/col; "
                                    "pass them to measure. Confirm on consecutive slices and a second plane before calling anything a lesion."),
                               *images, resource_link(out.resolve().as_uri(), out.name, "zoom sheet")],
                              {"path": str(out), "series": str(args["series"]), "center": args["center"], "plane": args.get("plane", "ax")}, False)

        def measure(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            work = self._work(args)
            label = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.get("label") or f"S{args['series']}_{args.get('instance') or args.get('z') or 'x'}")
            argv = ["measure", args["study_dir"], "--series", str(args["series"]), "--json"]
            for key, flag in (("instance", "--instance"), ("z", "--z"), ("roi", "--roi"), ("auto", "--auto"), ("auto3d", "--auto3d"), ("extent", "--extent"),
                              ("thr", "--thr"), ("thr_max", "--thr-max"), ("min_px", "--min-px"), ("radius", "--radius"), ("box", "--box"),
                              ("weight", "--weight"), ("convention", "--convention"), ("lesion_type", "--lesion-type")):
                if args.get(key) is not None:
                    argv += [flag, str(args[key])]
            for key, flag in (("points", "--points"), ("profile", "--profile")):
                if args.get(key):
                    argv += [flag, *[str(x) for x in args[key]]]
            if args.get("allow_tilt"):
                argv.append("--allow-tilt")
            evidence: Optional[Path] = None
            if work is not None:
                evidence_dir = work / "evidence"
                evidence_dir.mkdir(parents=True, exist_ok=True)
                evidence = evidence_dir / f"{label}.json"
                n = 1
                while evidence.exists():
                    n += 1
                    evidence = evidence_dir / f"{label}_{n}.json"
                argv += ["--output", str(evidence)]
            rc, payload, err = ctx.run_json(argv)
            if rc != 0 or payload is None:
                return error_result(err.strip() or "measure failed", exit_code=rc)
            structured: Dict[str, Any] = {"result": payload}
            lines = []
            for r in payload.get("results", []):
                if r["kind"] == "distance":
                    lines.append(f"distance {r['points'][0]}->{r['points'][1]} = {r['value_mm']} mm")
                elif r["kind"] == "roi":
                    lines.append(f"ROI mean {r['mean']:.1f} sd {r['sd']:.1f} min {r['min']:.1f} max {r['max']:.1f} {r['unit']}" + (f"; SUVmax {r['suv_max']:.2f}" if "suv_max" in r else ""))
                elif r["kind"] == "region_2d":
                    lines.append(f"region: long {r['long_axis_mm']} mm, short {r['short_axis_mm']} mm, average {r['average_diameter_mm']} mm; "
                                 f"reported {r['reported_diameter_mm']} mm ({r['reported_rule']})")
                elif r["kind"] == "region_3d":
                    lines.append(f"3D region: {r['volume_ml']} mL, craniocaudal {r['craniocaudal_extent_mm']} mm (exploratory)")
                elif r["kind"] == "extent":
                    lines.append(f"extent: {r['craniocaudal_extent_mm']} mm over instances {r['instance_range']}")
                elif r["kind"] == "slice_lookup":
                    lines.append(f"z {r['z_requested']} -> instance {r['instance']}")
            for w in payload.get("warnings", []):
                lines.append(f"[warn] {w}")
            if evidence is not None:
                sha = hashlib.sha256(evidence.read_bytes()).hexdigest()
                structured.update({"evidence_file": str(evidence), "sha256": sha,
                                   "measurement_template": {"value": None, "unit": payload.get("unit"), "method": "<state axis, plane, window, slice>",
                                                            "evidence_file": str(evidence), "sha256": sha, "ref": payload.get("slice")}})
                lines.append(f"evidence {evidence} sha256={sha}")
            return ToolResult([text("\n".join(lines) or "no measurement requested"), json_block(payload)], structured, False)

        def session_register(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            directory = Path(args.get("directory") or s["work_dir"])
            register(path, directory)
            s = store.load(path)
            sid = store.session_id(path)
            unreviewed = [p for p in s["pages"] if p.get("reviewed") is not True]
            blocks = [text(f"{len(s['pages'])} pages registered, {len(unreviewed)} not yet viewed. Call page_view for each; viewing marks it reviewed.")]
            for p in unreviewed:
                blocks.append(resource_link(f"openrad://session/{sid}/page/{Path(p['path']).name}", Path(p["path"]).name, p.get("purpose", "")))
            return ToolResult(blocks, {"pages": len(s["pages"]), "unreviewed": [p["path"] for p in unreviewed]}, False)

        def page_view(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            wanted = args["page"]
            match = [p for p in s["pages"] if p["path"] == wanted or Path(p["path"]).name == Path(wanted).name]
            if not match:
                raise InputError(f"Page '{wanted}' is not registered in this session; run session_register first")
            page = match[0]
            png = Path(page["path"])
            if not png.is_file():
                raise InputError(f"Page file missing: {png}")
            if hashlib.sha256(png.read_bytes()).hexdigest() != page["sha256"]:
                raise InputError(f"Page changed since registration: {png.name}; re-render under a new name")
            images, _ = inline_images([png], 1)
            if not images:
                raise InputError(f"Page {png.name} exceeds the inline image budget; open it via the resource URI in a client that supports blobs")
            page["reviewed"] = True
            page.setdefault("viewed_via", "mcp:page_view")
            store.save(path, s)
            sources = page.get("sources", [])
            label = (f"{png.name}: {page.get('purpose', '')} — {len(sources)} slice(s)"
                     + (f", instances {sources[0].get('instance')}..{sources[-1].get('instance')}" if sources and sources[0].get("instance") else "")
                     + ". Marked reviewed. Read superior to inferior; every tile carries img/z; cite SOPInstanceUIDs from structuredContent.")
            remaining = [p["path"] for p in s["pages"] if p.get("reviewed") is not True]
            return ToolResult([text(label), *images],
                              {"page": page["path"], "purpose": page.get("purpose"), "sources": sources, "remaining_unreviewed": len(remaining),
                               "next_page": remaining[0] if remaining else None}, False)

        def session_set_series(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            se = find_series(s, args.get("study_uid"), str(args["series"]))
            se["disposition"] = args["disposition"]
            if "reason" in args:
                se["reason"] = args["reason"]
            if "geometry_checked" in args:
                se["geometry_checked"] = bool(args["geometry_checked"])
            if "required_passes" in args:
                se["required_passes"] = list(args["required_passes"])
            store.save(path, s)
            errs = errors_for(validate(s, verify_files=False), f"Series {se['number']}")
            return ToolResult([text(f"Series {se['number']} -> {se['disposition']}" + (f"; open issues: {errs}" if errs else "; no open issues for this series"))],
                              {"series": se["number"], "errors": errs}, False)

        def session_set_region(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            targets = [st for st in s["studies"] if not args.get("study_uid") or st["uid"] == args["study_uid"]]
            if len(targets) != 1:
                raise UsageError("study_uid is required when the session holds several studies")
            study = targets[0]
            key = args["region"] if ":" in args["region"] else next((k for k in study["regions"] if k.endswith(":" + args["region"])), args["region"])
            if key not in study["regions"]:
                raise InputError(f"Unknown region '{args['region']}'. Valid: {list(study['regions'])}")
            region = study["regions"][key]
            region.update(status=args["status"], text=args["text"], explanation=args["explanation"])
            if "refs" in args:
                region["refs"] = args["refs"]
            if "pages" in args:
                region["pages"] = [str(Path(p)) for p in args["pages"]]
            store.save(path, s)
            errs = errors_for(validate(s, verify_files=False), f"{study['folder']} {key}")
            return ToolResult([text(f"{key} -> {args['status']}" + (f"; unmet: {errs}" if errs else "; complete"))], {"region": key, "errors": errs}, False)

        def session_add_claim(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            claim = dict(args["claim"])
            if not claim.get("id"):
                claim["id"] = f"L{len(s['claims']) + 1}"
            s["claims"] = [c for c in s["claims"] if c.get("id") != claim["id"]] + [claim]
            store.save(path, s)
            errs = errors_for(validate(s, verify_files=True), str(claim["id"]))
            return ToolResult([text(f"claim {claim['id']} stored" + (f"; unmet: {errs}" if errs else "; complete"))],
                              {"id": claim["id"], "errors": errs, "claims": len(s["claims"])}, False)

        def session_set_meta(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            s = store.load(path)
            allowed = {"reader", "context_disclosure", "blind_status", "clinical_context", "limitations", "recommendations",
                       "patient_context", "patient_limitations", "glossary", "reading_complete", "comparison", "language"}
            fields = args["fields"]
            unknown = sorted(set(fields) - allowed)
            if unknown:
                raise UsageError(f"unknown session fields {unknown}; allowed: {sorted(allowed)}")
            if "comparison" in fields:
                s["comparison"].update(fields.pop("comparison"))
            s.update(fields)
            store.save(path, s)
            errs = validate(s, verify_files=False)
            return ToolResult([text(f"session updated; {len(errs)} validation error(s) remain" + (": " + "; ".join(errs[:8]) if errs else ""))],
                              {"errors": errs}, False)

        def session_check(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            errs = validate(store.load(path), verify_files=True)
            return ToolResult([text("Structural checks passed; interpretation still needs clinical review" if not errs else
                                    f"{len(errs)} unmet requirement(s):\n- " + "\n- ".join(errs))], {"ok": not errs, "errors": errs}, bool(errs))

        def session_finish(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            path = store.resolve(args["session"])
            argv = ["finish", str(path)]
            if args.get("lang"):
                argv += ["--lang", args["lang"]]
            if args.get("output_dir"):
                argv += ["--output-dir", args["output_dir"]]
            rc, out, err = ctx.run_cli(argv)
            if rc != 0:
                return error_result(err.strip() or "finish failed", exit_code=rc)
            files = [Path(line.strip()) for line in out.splitlines() if line.strip()]
            blocks = [text(f"Documents written: {', '.join(str(f) for f in files)}")]
            for f in files:
                blocks.append(text(f"----- {f.name} -----\n" + f.read_text(encoding="utf-8")))
            return ToolResult(blocks, {"files": [str(f) for f in files]}, False)

        def anonymize(args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
            if not (os.environ.get("OPENRAD_PSEUDONYM_SALT") or load_settings().pseudonym_salt):
                raise UsageError("Set OPENRAD_PSEUDONYM_SALT in the server environment; the salt is never passed through the protocol")
            argv = ["anonymize", args["src"], args["dst"], "--json"]
            for key, flag in (("remove_dates", "--remove-dates"), ("strip_descriptors", "--strip-descriptors"),
                              ("no_retain_characteristics", "--no-retain-characteristics"), ("no_safe_private", "--no-safe-private"),
                              ("allow_burned_in", "--allow-burned-in")):
                if args.get(key):
                    argv.append(flag)
            if args.get("date_shift_days") is not None:
                argv += ["--date-shift-days", str(args["date_shift_days"])]
            rc, payload, err = ctx.run_json(argv)
            if rc != 0 or payload is None:
                return error_result(err.strip() or "anonymize failed", exit_code=rc)
            return ToolResult([text(f"{payload['files_out']} files de-identified into {args['dst']}; date shift {payload['date_shift_days']} days; "
                                    f"{len(payload['warnings'])} warning(s)."), json_block(payload)], payload, False)

        return [
            Tool("openrad_doctor", "Environment check", "Interpreter, dependencies, pixel decoders, effective configuration and writable paths. Run first.",
                 _schema({}), doctor),
            Tool("openrad_config", "Effective configuration", "Every setting with its source (default, file, env). Never edit config to make a check pass.",
                 _schema({}), config),
            Tool("study_inventory", "Inventory a DICOM study",
                 "Series table (matrix, spacing, kernel, contrast, multiframe, tilt, PET units/decay/timing, MR sequence hints) plus technical hints. "
                 "Identifiers are redacted unless include_identifiers is true.",
                 _schema({"study_dir": S["study_dir"], "session": S["session"], "include_identifiers": {"type": "boolean", "default": False}}, ["study_dir"]),
                 study_inventory),
            Tool("session_open", "Open a review session",
                 "Create the evidence ledger for one study or a chronological comparison. Validates identity, chronology and hashes inputs. "
                 "Absolute study paths sharing one parent set studies_root automatically. Returns the reading plan.",
                 _schema({"studies": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Study folders (absolute paths or names inside studies_root)"},
                          "repo": {"type": "string", "description": "Review workspace root (session cache and reports live here; default server cwd)"},
                          "lang": {"type": "string", "enum": list(LANGUAGES), "description": "Report language"},
                          "studies_root": {"type": "string"}, "identity_source": {"type": "string", "description": "Document confirming same patient for anonymized exports"}},
                         ["studies"]), session_open, read_only=False, idempotent=False),
            Tool("session_list", "List sessions", "Sessions in the cache directory with mode, language, studies and finalization state.", _schema({}), session_list),
            Tool("session_status", "Progress and technical alerts",
                 "Coverage per required pass, unreviewed pages, pending regions, claims, validation error count and technical alerts "
                 "(tilt, unsupported series, SUV warnings, unrendered/unviewed slices). Never anatomical.",
                 _schema({"session": S["session"]}, ["session"]), session_status_tool),
            Tool("session_plan", "Reading plan", "Ordered tool calls for this session's modalities, with the required_passes each render must declare.",
                 _schema({"session": S["session"]}, ["session"]), session_plan_tool),
            Tool("render_ct", "Render CT sheets",
                 "Native axial sheets per window, optional sliding-slab MIP (lung) and coronal/sagittal reformats with depth ruler. "
                 "Grid fits the vision budget; slices are never down-sampled. Returns inline pages and resource links.",
                 _schema({"session": S["session"], "study_dir": S["study_dir"], "series": S["series"],
                          "windows": {"type": "string", "default": "lung,soft,bone"}, "step": {"type": "number"}, "mip": {"type": "number", "description": "slab MIP mm (lung window); 8-10 recommended, 0 off"},
                          "mpr": {"type": "boolean", "default": False}, "mpr_n": {"type": "integer"}, "mpr_thick": {"type": "number"},
                          "grid": S["grid"], "vision": S["vision"], "zmin": {"type": "number"}, "zmax": {"type": "number"},
                          "allow_tilt": S["allow_tilt"], "inline": S["inline"], "output": {"type": "string", "description": "Output folder (default inside the session work dir)"}},
                         ["study_dir", "series"]), render_ct, read_only=False),
            Tool("render_pet", "Render PET/CT",
                 "SUVbw conversion (QIBA logic), rotating MIP, native PET sheets, hotspot table (detection aid, not a malignancy list) and fused axial tiles.",
                 _schema({"session": S["session"], "study_dir": S["study_dir"], "pt": {"type": "string"}, "ct": {"type": "string"},
                          "weight": {"type": "number", "description": "Patient weight kg when the header lacks it; document the source"},
                          "suv_threshold": {"type": "number"}, "axial_step": {"type": "number"}, "grid": S["grid"], "vision": S["vision"],
                          "zmin": {"type": "number"}, "zmax": {"type": "number"}, "inline": S["inline"], "output": {"type": "string"}},
                         ["study_dir", "pt", "ct"]), render_pet, read_only=False),
            Tool("render_mr", "Render MR sheets",
                 "Per-sequence contact sheets with one window per series, or a pre/post-contrast pair matched by patient position.",
                 _schema({"session": S["session"], "study_dir": S["study_dir"], "series": {"type": "array", "items": {"type": "string"}},
                          "pair": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}, "step": {"type": "integer"},
                          "tile": {"type": "integer"}, "invert": {"type": "boolean"}, "grid": S["grid"], "vision": S["vision"], "inline": S["inline"],
                          "output": {"type": "string"}}, ["study_dir"]), render_mr, read_only=False),
            Tool("zoom", "Magnify a region across slices",
                 "Multi-slice magnification with a native pixel grid and scale bar; plane cor/sag gives the second plane. Returns the image inline.",
                 _schema({"session": S["session"], "study_dir": S["study_dir"], "series": S["series"], "instance": {"type": "string"}, "z": {"type": "number"},
                          "center": {"type": "string", "description": "row,col in native pixels"}, "size": {"type": "integer"}, "scale": {"type": "integer"},
                          "window": {"type": "string"}, "context": {"type": "integer"}, "stride": {"type": "integer"},
                          "plane": {"type": "string", "enum": ["ax", "cor", "sag"]}, "allow_tilt": S["allow_tilt"], "label": {"type": "string"},
                          "output": {"type": "string"}}, ["study_dir", "series", "center"]), zoom, read_only=False),
            Tool("measure", "Calibrated measurement with hashed evidence",
                 "Distance, ROI statistics (HU/SUVbw), 2D/3D region growing with guideline diameter, craniocaudal extent, profile. "
                 "With a session the output is a write-once evidence file whose SHA-256 is returned for the ledger.",
                 _schema({"session": S["session"], "study_dir": S["study_dir"], "series": S["series"], "instance": {"type": "string"}, "z": {"type": "number"},
                          "points": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2, "description": "two 'row,col' endpoints"},
                          "roi": {"type": "string", "description": "row,col,radius_px"}, "auto": {"type": "string", "description": "row,col seed 2D"},
                          "auto3d": {"type": "string"}, "extent": {"type": "string", "description": "r0,r1,c0,c1"},
                          "profile": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2},
                          "thr": {"type": "number"}, "thr_max": {"type": "number"}, "min_px": {"type": "integer"}, "radius": {"type": "number"}, "box": {"type": "string"},
                          "weight": {"type": "number"}, "convention": {"type": "string", "enum": ["fleischner", "recist"]},
                          "lesion_type": {"type": "string", "enum": ["nodule", "node", "mass", "other"]}, "allow_tilt": S["allow_tilt"],
                          "label": {"type": "string", "description": "Evidence file stem, e.g. L1_long_axis"}},
                         ["study_dir", "series"]), measure, read_only=False),
            Tool("session_register", "Register rendered pages", "Index every PNG under the session work directory (or a sub-folder). Nothing is marked reviewed.",
                 _schema({"session": S["session"], "directory": {"type": "string"}}, ["session"]), session_register, read_only=False),
            Tool("page_view", "View a page and mark it reviewed",
                 "Returns the registered sheet as an image and, in the same step, marks it reviewed. This is the only way to mark a page; "
                 "a page you did not receive cannot be claimed as read.",
                 _schema({"session": S["session"], "page": {"type": "string", "description": "Registered page path or file name"}}, ["session", "page"]),
                 page_view, read_only=False),
            Tool("session_set_series", "Set series disposition",
                 "read (with geometry_checked and required_passes exactly as rendered), excluded or unsupported (with reason). Returns open issues for that series.",
                 _schema({"session": S["session"], "series": S["series"], "study_uid": {"type": "string"},
                          "disposition": {"type": "string", "enum": ["read", "excluded", "unsupported"]}, "reason": {"type": "string"},
                          "geometry_checked": {"type": "boolean"}, "required_passes": {"type": "array", "items": {"type": "string"}}},
                         ["session", "series", "disposition"]), session_set_series, read_only=False),
            Tool("session_set_region", "Record a region",
                 "status finding/no_finding/limited/not_covered with professional text, plain-language explanation, DICOM refs and inspected pages. "
                 "Returns the unmet requirements for this region immediately.",
                 _schema({"session": S["session"], "region": {"type": "string", "description": "e.g. CT:right_lung_all_lobes or right_lung_all_lobes"},
                          "study_uid": {"type": "string"}, "status": {"type": "string", "enum": list(REGION_STATUSES)}, "text": {"type": "string"},
                          "explanation": {"type": "string"},
                          "refs": {"type": "array", "items": {"type": "object", "properties": {"study_uid": {"type": "string"}, "series_uid": {"type": "string"},
                                                                                              "sop_uid": {"type": "string"}, "row": {"type": "integer"}, "col": {"type": "integer"}},
                                                               "required": ["study_uid", "series_uid", "sop_uid"]}},
                          "pages": {"type": "array", "items": {"type": "string"}}}, ["session", "region", "status", "text", "explanation"]),
                 session_set_region, read_only=False),
            Tool("session_add_claim", "Add or replace a claim",
                 "Impression item with priority, confidence, refs, pages, patient explanation (meaning, importance, uncertainty, discuss_with_doctor), "
                 "measurements (from measure) and, in comparison mode, comparison + timeline. Returns unmet requirements for this claim.",
                 _schema({"session": S["session"], "claim": {"type": "object", "properties": {
                     "id": {"type": "string"}, "text": {"type": "string"}, "priority": {"type": "string", "enum": list(CLAIM_PRIORITIES)},
                     "confidence": {"type": "string", "enum": list(CONFIDENCES)}, "refs": {"type": "array"}, "pages": {"type": "array"},
                     "patient": {"type": "object"}, "measurements": {"type": "array"},
                     "comparison": {"type": "object", "properties": {"status": {"type": "string", "enum": list(COMPARISON_STATUSES)}}},
                     "timeline": {"type": "array", "items": {"type": "object", "properties": {"status": {"type": "string", "enum": list(TIMEPOINT_STATUSES)}}}}},
                     "required": ["text", "priority", "confidence", "refs", "pages", "patient"]}}, ["session", "claim"]),
                 session_add_claim, read_only=False),
            Tool("session_set_meta", "Set session-level fields",
                 "reader, context_disclosure, blind_status, clinical_context, limitations, recommendations, patient_context, patient_limitations, "
                 "glossary, comparison {status, reason, explanation}, reading_complete.",
                 _schema({"session": S["session"], "fields": {"type": "object"}}, ["session", "fields"]), session_set_meta, read_only=False),
            Tool("session_check", "Validate the ledger", "Full structural validation incl. file hashes. Empty error list means finish is allowed.",
                 _schema({"session": S["session"]}, ["session"]), session_check),
            Tool("session_finish", "Generate the two documents", "Professional report and patient guide from the ledger, returned as text and written to output_dir.",
                 _schema({"session": S["session"], "lang": {"type": "string", "enum": list(LANGUAGES)}, "output_dir": {"type": "string"}}, ["session"]),
                 session_finish, read_only=False),
            Tool("anonymize", "De-identify a DICOM tree",
                 "DICOM PS3.15 basic profile with deterministic pseudonyms and shifted dates. The salt comes from the server environment only.",
                 _schema({"src": {"type": "string"}, "dst": {"type": "string", "description": "empty output folder"}, "remove_dates": {"type": "boolean"},
                          "strip_descriptors": {"type": "boolean"}, "no_retain_characteristics": {"type": "boolean"}, "no_safe_private": {"type": "boolean"},
                          "allow_burned_in": {"type": "boolean"}, "date_shift_days": {"type": "integer"}}, ["src", "dst"]), anonymize, read_only=False),
        ]


def _inventory_hints(inv: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    for r in inv.get("series", []):
        tag = f"S{r['series_number']} {r['modality']}"
        if r.get("multiframe"):
            hints.append(f"{tag}: enhanced multi-frame object; loader will refuse it (mark unsupported)")
        if r["modality"] == "CT" and r.get("gantry_tilt_deg"):
            hints.append(f"{tag}: GantryDetectorTilt {r['gantry_tilt_deg']} deg; render with allow_tilt, no 3D extents")
        if r["modality"] == "CT" and (r.get("slice_thickness_mm") or 0) > 1.5 and (r.get("images") or 0) > 20:
            hints.append(f"{tag}: {r['slice_thickness_mm']} mm sections; prefer a <=1.5 mm series for the lung pass if one exists")
        if r["modality"] == "PT":
            if r.get("units") != "BQML":
                hints.append(f"{tag}: Units {r.get('units')}; SUV needs BQML (or Philips CNTS scale factors)")
            if r.get("decay_correction") not in ("START", "ADMIN"):
                hints.append(f"{tag}: DecayCorrection {r.get('decay_correction')}; SUV not computable")
            if not r.get("patient_weight_kg"):
                hints.append(f"{tag}: PatientWeight missing; pass a documented weight to render_pet")
        if r["modality"] == "MR" and "REPEAT" in str(r.get("guess", "")):
            hints.append(f"{tag}: repeated acquisition; candidate post-contrast series, confirm with metadata and anatomy")
    if len({r["frame_uid"] for r in inv.get("series", []) if r["modality"] in ("PT", "CT")}) > 1 and "PT" in inv["study"]["modalities"]:
        hints.append("PET and CT series carry different FrameOfReferenceUIDs; fusion will be refused")
    return hints


def _check_args(schema: Dict[str, Any], args: Dict[str, Any]) -> List[str]:
    """Minimal JSON-schema check: required keys, unknown keys, primitive types and enums."""
    problems: List[str] = []
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in args:
            problems.append(f"missing '{key}'")
    for key, value in args.items():
        if key not in props:
            problems.append(f"unknown argument '{key}'")
            continue
        spec = props[key]
        t = spec.get("type")
        ok = {"string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": lambda v: isinstance(v, bool),
              "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict)}.get(t)
        if ok and not ok(value):
            problems.append(f"'{key}' must be {t}")
        if "enum" in spec and value not in spec["enum"]:
            problems.append(f"'{key}' must be one of {spec['enum']}")
    return problems


def server_instructions() -> str:
    return (f"OpenRadiology {__version__}: deterministic DICOM workbench. Three rules: (1) no look, no claim - only page_view marks a page reviewed; "
            "(2) every region and claim cites StudyInstanceUID/SeriesInstanceUID/SOPInstanceUID + row/col + inspected page; "
            "(3) numbers come only from the measure tool's evidence files. Start with openrad_doctor, then session_open and follow session_plan. "
            "Read the modality checklist resource before rendering. This server never diagnoses; it renders, measures and audits.")
