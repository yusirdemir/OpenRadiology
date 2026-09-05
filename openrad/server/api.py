"""HTTP surface of the desktop sidecar.

Every route is a thin adapter over the existing engine. Nothing here reimplements
radiology: geometry comes from :class:`openrad.dcmlib.Volume`, numbers from
:mod:`openrad.measure`, the ledger from :mod:`openrad.create_report`. That is
deliberate -- a second implementation of HU or SUV arithmetic living in the
application layer is exactly how a desktop build would start disagreeing with
its own evidence files.

Two conventions worth knowing:

* Slice pixels leave as raw little-endian ``float32`` with the calibration in
  the response headers. Windowing happens on the GPU, so the same bytes serve
  every window setting without a round trip.
* Long commands (renders, de-identification) are jobs, not requests. They run
  as child processes and stream the engine's own progress lines over SSE.
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from .. import __version__
from ..config import Settings, load_settings
from ..create_report import REGIONS, load_session, write_json
from ..create_report import finish as cr_finish
from ..create_report import prepare as cr_prepare
from ..create_report import register as cr_register
from ..create_report import validate as cr_validate
from ..dcmlib import WINDOWS, sha256_file
from ..errors import InputError, IntegrityError, UsageError
from .agent import DisplayGate, Transcript, summarise_call
from .attest import AttestationLedger, DwellPolicy, ViewEvent
from .httpd import HttpError, Request, Response, Router, binary_response, json_response, sse_response
from .jobs import JobManager
from .volumes import (
    HeaderCache,
    VolumeCache,
    encode_image,
    extract_plane,
    plane_length,
    series_cards,
    suggested_windows,
    wire_dtype,
)

log = logging.getLogger("openrad.server.api")

RENDER_KINDS = {"ct": "ct-render", "mr": "mr-render", "pet": "pet-render", "zoom": "zoom", "passport": "passport"}


# ------------------------------------------------------------------- state
class AppState:
    """Everything the routes share: caches, jobs, path jail and the agent log."""

    def __init__(self, settings: Optional[Settings] = None, budget_bytes: Optional[int] = None,
                 policy: Optional[DwellPolicy] = None) -> None:
        self.settings = settings or load_settings()
        self.headers = HeaderCache()
        self.volumes = VolumeCache(self.headers, budget_bytes or _default_budget())
        self.jobs = JobManager()
        self.policy = policy or DwellPolicy()
        self.started = time.time()
        self._ledgers: Dict[str, AttestationLedger] = {}
        self._roots: List[Path] = []
        self.agent_events: Deque[Dict[str, Any]] = deque(maxlen=500)
        self.display = DisplayGate()
        self.transcript = Transcript()
        self._mcp: Any = None
        self._mcp_lock = threading.Lock()
        for candidate in (getattr(self.settings, "studies_root", None), Path.cwd()):
            if candidate:
                self.allow(Path(candidate))

    # -- path jail ---------------------------------------------------------
    def allow(self, path: Path) -> Path:
        """Trust a directory the user explicitly opened, and everything under it."""
        resolved = Path(path).expanduser().resolve()
        if not any(resolved == root or root in resolved.parents for root in self._roots):
            self._roots.append(resolved)
        return resolved

    def ensure_allowed(self, path: Any, what: str = "path") -> Path:
        if not path:
            raise UsageError(f"A {what} is required")
        resolved = Path(str(path)).expanduser().resolve()
        for root in self._roots:
            if resolved == root or root in resolved.parents:
                return resolved
        raise HttpError(403, f"{what} is outside the folders opened in this session: {resolved}")

    @property
    def roots(self) -> List[str]:
        return [str(r) for r in self._roots]

    # -- attestation -------------------------------------------------------
    def ledger_for(self, work_dir: Path) -> AttestationLedger:
        key = str(Path(work_dir).resolve())
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = AttestationLedger(Path(key), self.policy)
            self._ledgers[key] = ledger
        return ledger

    def ledger_for_session(self, session_path: Path) -> Tuple[Dict[str, Any], AttestationLedger]:
        session = load_session(session_path)
        work_dir = Path(session.get("work_dir") or session_path.parent)
        return session, self.ledger_for(work_dir)

    # -- agent bridge ------------------------------------------------------
    @property
    def mcp(self) -> Any:
        """The MCP server, shared with the window so both see one session."""
        with self._mcp_lock:
            if self._mcp is None:
                from ..mcp.protocol import build_server

                workspace = default_workspace()
                workspace.mkdir(parents=True, exist_ok=True)
                self.allow(workspace)
                self._mcp = build_server(workspace)
            return self._mcp

    def note_agent(self, event: Dict[str, Any]) -> None:
        self.agent_events.append({"ts": time.time(), **event})

    def shutdown(self) -> None:
        self.jobs.shutdown()
        self.volumes.clear()


def default_workspace() -> Path:
    """Where review sessions are written when the caller does not say."""
    from .handshake import state_dir

    return state_dir() / "workspace"


def _default_budget() -> int:
    raw = os.environ.get("OPENRAD_VOLUME_BUDGET_MB")
    if raw:
        try:
            return max(256, int(raw)) * 1024 ** 2
        except ValueError:
            pass
    return 4 * 1024 ** 3


# ----------------------------------------------------------------- helpers
def _study_dir(state: AppState, payload: Dict[str, Any], key: str = "study") -> Path:
    path = state.ensure_allowed(payload.get(key), "study folder")
    if not path.is_dir():
        raise InputError(f"Study folder not found: {path}")
    return path


def _volume(state: AppState, payload: Dict[str, Any]):
    study = _study_dir(state, payload)
    series = payload.get("series")
    if not series:
        raise UsageError("A series number or SeriesInstanceUID is required")
    return state.volumes.get(study, str(series), allow_tilt=bool(payload.get("allow_tilt")))


def _session_path(state: AppState, value: Any) -> Path:
    path = state.ensure_allowed(value, "session file")
    if not path.is_file():
        raise InputError(f"Session file not found: {path}")
    return path


def _float_list(values: Sequence[Any]) -> List[float]:
    return [float(v) for v in values]


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ------------------------------------------------------------------ router
def build_router(state: AppState) -> Router:
    router = Router()
    route = router.route

    # -- environment ------------------------------------------------------
    @route("GET", "/health")
    def health(_: Request) -> Response:
        return json_response({"ok": True, "version": __version__, "pid": os.getpid(),
                              "uptime_s": round(time.time() - state.started, 1)})

    @route("GET", "/status")
    def status(_: Request) -> Response:
        return json_response({"version": __version__, "roots": state.roots,
                              "cache": state.volumes.stats(), "jobs": state.jobs.list()[:10],
                              "policy": state.policy.as_dict(),
                              "regions": {mod: list(keys) for mod, keys in REGIONS.items()},
                              "windows": [{"name": n, "center": c, "width": w} for n, (c, w) in WINDOWS.items()]})

    @route("GET", "/locale")
    def locale(request: Request) -> Response:
        """Region names and report headings, from the engine's own locale files.

        The interface must not keep its own copy of these: a translation that
        drifts from the report would label a region one way on screen and
        another way in the document the patient reads.
        """
        from ..locales import DEFAULT_LANGUAGE, available_languages, load_locale

        lang = request.q("lang") or state.settings.lang or DEFAULT_LANGUAGE
        if lang not in available_languages():
            lang = DEFAULT_LANGUAGE
        return json_response({"language": lang, "available": list(available_languages()), "locale": load_locale(lang)})

    @route("GET", "/doctor")
    def doctor(_: Request) -> Response:
        from ..doctor import main as doctor_main

        buffer = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(buffer):
            code = doctor_main(["--json"])
        try:
            payload = json.loads(buffer.getvalue() or "{}")
        except json.JSONDecodeError:
            payload = {"raw": buffer.getvalue()}
        return json_response({"exit_code": code, "report": payload})

    # -- studies ----------------------------------------------------------
    @route("POST", "/studies/scan")
    def studies_scan(request: Request) -> Response:
        payload = request.json()
        root = Path(str(payload.get("root", ""))).expanduser()
        if not root.is_dir():
            raise InputError(f"Folder not found: {root}")
        state.allow(root)
        depth = int(payload.get("depth", 2) or 2)
        return json_response({"root": str(root.resolve()), "studies": _scan(state, root.resolve(), depth)})

    @route("POST", "/studies/inventory")
    def studies_inventory(request: Request) -> Response:
        from ..dicom_inventory import inventory

        payload = request.json()
        study = _study_dir(state, payload)
        return json_response(inventory(study, redact=bool(payload.get("redact", True))))

    @route("POST", "/series/list")
    def series_list(request: Request) -> Response:
        study = _study_dir(state, request.json())
        return json_response({"study": str(study), "series": series_cards(state.headers.get(study))})

    @route("POST", "/series/meta")
    def series_meta(request: Request) -> Response:
        payload = request.json()
        volume = _volume(state, payload)
        summary = volume.geometry_summary()
        z, rows, cols = volume.vol.shape
        return json_response({
            "geometry": summary,
            "planes": {"ax": {"count": z, "mm_per_px": [volume.row_sp, volume.col_sp]},
                       "cor": {"count": rows, "mm_per_px": [abs(volume.dz), volume.col_sp]},
                       "sag": {"count": cols, "mm_per_px": [abs(volume.dz), volume.row_sp]}},
            "value_range": [float(volume.vol.min()), float(volume.vol.max())],
            # Decided here so the cost lands on opening the series, not on the first slice.
            "wire_dtype": wire_dtype(volume),
            "windows": suggested_windows(volume),
            "z_mm": _float_list(volume.z.tolist()),
            "instances": [volume.instance(k) for k in range(z)],
            "sop_uids": [volume.sop_uid(k) for k in range(z)],
            "bytes": int(volume.vol.nbytes),
            "cache": state.volumes.stats(),
        })

    @route("GET", "/series/slice")
    def series_slice(request: Request) -> Response:
        payload = {"study": request.q("study"), "series": request.q("series"),
                   "allow_tilt": request.q_bool("allow_tilt")}
        volume = _volume(state, payload)
        plane = (request.q("plane") or "ax").lower()
        index = request.q_int("index", 0) or 0
        array, meta = extract_plane(volume, plane, index,
                                    mip_mm=request.q_float("mip", 0.0) or 0.0,
                                    thick_px=request.q_int("thick", 1) or 1)
        payload, dtype = encode_image(volume, array)
        headers = {
            "X-Shape": f"{meta['shape'][0]},{meta['shape'][1]}",
            "X-Dtype": dtype,
            "X-Mm-Per-Px": ",".join(f"{v:.6f}" for v in meta["mm_per_px"]),
            "X-Plane": meta["plane"],
            "X-Index": str(meta["index"]),
            "X-Count": str(meta["count"]),
            "X-Sop-Uid": meta["sop_uid"],
            "X-Instance": meta["instance"],
            "X-Series-Uid": meta["series_uid"],
            "X-Study-Uid": meta["study_uid"],
            "X-Mip-Mm": str(meta["mip_mm"]),
            "Cache-Control": "no-store",
        }
        if meta["z_mm"] is not None:
            headers["X-Z-Mm"] = f"{meta['z_mm']:.4f}"
        return binary_response(payload, "application/octet-stream", headers)

    @route("GET", "/series/atlas")
    def series_atlas(request: Request) -> Response:
        """Filmstrip: one PNG holding evenly spaced thumbnails of a plane."""
        from PIL import Image

        payload = {"study": request.q("study"), "series": request.q("series")}
        volume = _volume(state, payload)
        plane = (request.q("plane") or "ax").lower()
        total = plane_length(volume, plane)
        count = max(1, min(request.q_int("count", 48) or 48, total))
        tile = max(24, min(request.q_int("tile", 64) or 64, 160))
        center = request.q_float("center")
        width = request.q_float("width")
        if center is None or width is None:
            preset = suggested_windows(volume)[0] if suggested_windows(volume) else {"center": 40, "width": 400}
            center, width = float(preset["center"]), float(preset["width"])
        indices = [int(round(i * (total - 1) / max(1, count - 1))) for i in range(count)] if count > 1 else [0]
        strip = Image.new("L", (tile * len(indices), tile), color=0)
        for slot, index in enumerate(indices):
            array, _ = extract_plane(volume, plane, index)
            lo = center - width / 2.0
            scaled = np.clip((array - lo) / max(width, 1e-6), 0.0, 1.0)
            thumb = Image.fromarray((scaled * 255).astype(np.uint8)).resize((tile, tile), Image.BILINEAR)
            strip.paste(thumb, (slot * tile, 0))
        buffer = io.BytesIO()
        strip.save(buffer, format="PNG", optimize=True)
        return binary_response(buffer.getvalue(), "image/png", {
            "X-Atlas-Tile": str(tile),
            "X-Atlas-Count": str(len(indices)),
            "X-Atlas-Indices": ",".join(str(i) for i in indices),
            "X-Plane": plane,
        })

    @route("POST", "/series/point")
    def series_point(request: Request) -> Response:
        """Patient coordinates and the value under one pixel: used to lock two studies together."""
        payload = request.json()
        volume = _volume(state, payload)
        index = int(payload.get("index", 0) or 0)
        row, col = float(payload.get("row", 0) or 0), float(payload.get("col", 0) or 0)
        if not 0 <= index < volume.vol.shape[0]:
            raise UsageError("slice index outside the volume")
        point = volume.patient_point(index, row, col)
        value = None
        r, c = int(round(row)), int(round(col))
        if 0 <= r < volume.vol.shape[1] and 0 <= c < volume.vol.shape[2]:
            value = float(volume.vol[index, r, c])
        return json_response({"patient_mm": _float_list(point.tolist()), "value": value,
                              "modality": volume.modality, "z_mm": float(volume.z[index]),
                              "sop_uid": volume.sop_uid(index), "instance": volume.instance(index)})

    @route("POST", "/series/locate")
    def series_locate(request: Request) -> Response:
        """Inverse of ``/series/point``: which slice of this series holds a patient point."""
        payload = request.json()
        volume = _volume(state, payload)
        target = np.array(_float_list(payload.get("patient_mm", [0, 0, 0])), dtype=float)
        offsets = target - np.array(volume.ipp0, dtype=float)
        z_along = float(np.dot(offsets, volume.normal)) + float(volume.z[0])
        index = volume.index_of_z(z_along)
        origin = np.array(volume.patient_point(index, 0, 0), dtype=float)
        delta = target - origin
        row = float(np.dot(delta, np.array(volume.iop[3:], dtype=float)) / volume.row_sp)
        col = float(np.dot(delta, np.array(volume.iop[:3], dtype=float)) / volume.col_sp)
        return json_response({"index": index, "row": row, "col": col, "z_mm": float(volume.z[index]),
                              "sop_uid": volume.sop_uid(index), "instance": volume.instance(index),
                              "in_plane": 0 <= row < volume.vol.shape[1] and 0 <= col < volume.vol.shape[2]})

    # -- measurement ------------------------------------------------------
    @route("POST", "/measure")
    def measure_route(request: Request) -> Response:
        from .. import measure as measure_mod

        payload = request.json()
        study = _study_dir(state, payload)
        volume = _volume(state, payload)
        argv = _measure_argv(study, payload)
        parser = measure_mod.build_parser(state.settings)
        namespace = parser.parse_args(argv)
        report = measure_mod.run(namespace, state.settings, volume=volume)
        evidence = None
        output = payload.get("output")
        if output:
            evidence = _write_evidence(state, Path(str(output)), report)
        return json_response({"text": report.text(), "data": report.data, "evidence": evidence,
                              "command": ["openrad", "measure", *argv]})

    # -- session ----------------------------------------------------------
    @route("POST", "/session/prepare")
    def session_prepare(request: Request) -> Response:
        payload = request.json()
        # Sessions live in the application's own directory, never inside the
        # DICOM archive: a review must not write a cache into the folder that
        # holds the source images.
        repo = Path(str(payload.get("repo") or default_workspace())).expanduser().resolve()
        repo.mkdir(parents=True, exist_ok=True)
        state.allow(repo)
        studies_root = payload.get("studies_root")
        if studies_root:
            studies_root = state.allow(Path(str(studies_root)))
        folders = payload.get("folders") or []
        if not folders:
            raise UsageError("At least one study folder is required")
        cache_dir = payload.get("cache_dir")
        session_path = cr_prepare(
            repo=repo,
            folders=[str(f) for f in folders],
            identity_source=Path(str(payload["identity_source"])) if payload.get("identity_source") else None,
            studies_root=Path(studies_root) if studies_root else None,
            lang=payload.get("lang"),
            cache_dir=Path(str(cache_dir)) if cache_dir else None,
            cfg=state.settings,
        )
        state.allow(Path(session_path).parent)
        session = load_session(Path(session_path))
        state.allow(Path(session["work_dir"]))
        return json_response({"session_path": str(session_path), "session": session})

    @route("GET", "/session")
    def session_get(request: Request) -> Response:
        path = _session_path(state, request.q("path"))
        session, ledger = state.ledger_for_session(path)
        return json_response({"session_path": str(path), "session": session,
                              "attestation": ledger.coverage(session)})

    @route("PUT", "/session")
    def session_put(request: Request) -> Response:
        """Write the ledger back, refusing any ``reviewed`` flag no attestation covers."""
        payload = request.json()
        path = _session_path(state, payload.get("path"))
        session = payload.get("session")
        if not isinstance(session, dict):
            raise UsageError("A session object is required")
        work_dir = Path(session.get("work_dir") or path.parent)
        ledger = state.ledger_for(work_dir)
        session, refusals = ledger.enforce(session)
        write_json(path, session)
        return json_response({"session_path": str(path), "session": session, "refused_reviews": refusals,
                              "attestation": ledger.coverage(session)})

    @route("POST", "/session/register")
    def session_register(request: Request) -> Response:
        payload = request.json()
        path = _session_path(state, payload.get("path"))
        directory = state.ensure_allowed(payload.get("directory"), "render directory")
        count = cr_register(path, directory)
        session, ledger = state.ledger_for_session(path)
        return json_response({"registered": count, "session": session, "attestation": ledger.coverage(session)})

    @route("POST", "/session/check")
    def session_check(request: Request) -> Response:
        payload = request.json()
        path = _session_path(state, payload.get("path"))
        session, ledger = state.ledger_for_session(path)
        errors = cr_validate(session)
        coverage = ledger.coverage(session)
        blocking = list(errors)
        unattested = [row for row in coverage["pages"] if row["reviewed"] and not row["attested"]]
        blocking += [f"page marked reviewed without a view attestation: {row['path']} ({row['reason']})"
                     for row in unattested]
        return json_response({"ok": not blocking, "errors": errors, "attestation": coverage,
                              "unattested_reviews": unattested, "blocking": blocking})

    @route("POST", "/session/finish")
    def session_finish(request: Request) -> Response:
        payload = request.json()
        path = _session_path(state, payload.get("path"))
        session, ledger = state.ledger_for_session(path)
        _, refusals = ledger.enforce(session)
        if refusals:
            write_json(path, session)
            raise IntegrityError(
                f"Refusing to finish: {len(refusals)} page(s) were marked reviewed without a view attestation")
        output_dir = payload.get("output_dir")
        report, guide = cr_finish(path, payload.get("lang"),
                                  state.ensure_allowed(output_dir, "output directory") if output_dir else None,
                                  state.settings)
        state.allow(report.parent)
        return json_response({"report": str(report), "guide": str(guide),
                              "report_sha256": sha256_file(report), "guide_sha256": sha256_file(guide),
                              "session": load_session(path)})

    # -- attestation ------------------------------------------------------
    @route("POST", "/attest")
    def attest(request: Request) -> Response:
        payload = request.json()
        work_dir = state.ensure_allowed(payload.get("work_dir"), "work directory")
        events = [ViewEvent.from_json(raw) for raw in payload.get("events", []) if isinstance(raw, dict)]
        if not events:
            raise UsageError("No view events supplied")
        return json_response(state.ledger_for(work_dir).record(events))

    @route("GET", "/attest/coverage")
    def attest_coverage(request: Request) -> Response:
        path = _session_path(state, request.q("session"))
        session, ledger = state.ledger_for_session(path)
        return json_response(ledger.coverage(session))

    # -- jobs -------------------------------------------------------------
    @route("POST", "/render/{kind}")
    def render(request: Request) -> Response:
        kind = request.params["kind"]
        if kind not in RENDER_KINDS:
            raise UsageError(f"render kind must be one of {', '.join(RENDER_KINDS)}")
        payload = request.json()
        study = _study_dir(state, payload)
        output = state.ensure_allowed(payload.get("output"), "output directory")
        output.mkdir(parents=True, exist_ok=True)
        argv = [RENDER_KINDS[kind], str(study), "--output", str(output)]
        argv += _extra_args(payload.get("args") or {})
        job = state.jobs.submit(f"render:{kind}", argv, label=payload.get("label") or f"{kind} render",
                                meta={"output": str(output), "study": str(study), "kind": kind})
        return json_response(job.snapshot(), 202)

    @route("POST", "/anonymize")
    def anonymize(request: Request) -> Response:
        payload = request.json()
        source = _study_dir(state, payload, "source")
        target = Path(str(payload.get("target", ""))).expanduser().resolve()
        state.allow(target.parent)
        salt = str(payload.get("salt") or "")
        if len(salt) < 8:
            raise UsageError("A de-identification salt of at least 8 characters is required")
        job = state.jobs.submit("anonymize", ["anonymize", str(source), str(target), "--salt", salt],
                                label="de-identify", meta={"source": str(source), "target": str(target)})
        return json_response(job.snapshot(), 202)

    @route("POST", "/lock")
    def lock(request: Request) -> Response:
        payload = request.json()
        target = state.ensure_allowed(payload.get("path"), "review file")
        argv = ["lock", str(target)] + (["--verify"] if payload.get("verify") else [])
        job = state.jobs.submit("lock", argv, label="seal review", meta={"path": str(target)})
        return json_response(job.snapshot(), 202)

    @route("GET", "/jobs")
    def jobs_list(_: Request) -> Response:
        return json_response({"jobs": state.jobs.list()})

    @route("GET", "/jobs/{job_id}")
    def job_get(request: Request) -> Response:
        job = state.jobs.get(request.params["job_id"])
        if job is None:
            raise InputError("Unknown job")
        return json_response({**job.snapshot(), "stdout": job.stdout})

    @route("GET", "/jobs/{job_id}/events")
    def job_events(request: Request) -> Response:
        job = state.jobs.get(request.params["job_id"])
        if job is None:
            raise InputError("Unknown job")
        return sse_response(job.follow())

    @route("POST", "/jobs/{job_id}/cancel")
    def job_cancel(request: Request) -> Response:
        job = state.jobs.get(request.params["job_id"])
        if job is None:
            raise InputError("Unknown job")
        return json_response({"cancelled": job.cancel(), **job.snapshot()})

    # -- files ------------------------------------------------------------
    @route("GET", "/files")
    def files(request: Request) -> Response:
        target = state.ensure_allowed(request.q("path"), "file")
        if not target.is_file():
            raise InputError(f"File not found: {target}")
        suffix = target.suffix.lower()
        content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                        ".json": "application/json; charset=utf-8", ".md": "text/markdown; charset=utf-8",
                        ".txt": "text/plain; charset=utf-8"}.get(suffix, "application/octet-stream")
        return binary_response(target.read_bytes(), content_type,
                               {"Cache-Control": "no-store", "X-Sha256": sha256_file(target)})

    @route("GET", "/files/list")
    def files_list(request: Request) -> Response:
        directory = state.ensure_allowed(request.q("path"), "directory")
        if not directory.is_dir():
            raise InputError(f"Directory not found: {directory}")
        pattern = request.q("glob", "*") or "*"
        entries = [{"path": str(p), "name": p.name, "size": p.stat().st_size,
                    "modified": p.stat().st_mtime, "dir": p.is_dir()}
                   for p in sorted(directory.glob(pattern))]
        return json_response({"path": str(directory), "entries": entries})

    # -- agent bridge -----------------------------------------------------
    @route("POST", "/mcp")
    def mcp_endpoint(request: Request) -> Response:
        """One JSON-RPC message from a bridged MCP client.

        The bridge is a thin stdio shim; the protocol logic stays in
        ``openrad.mcp``. What happens here that cannot happen over plain stdio
        is the display gate: a bridged ``page_view`` must pass through the
        application window before the underlying tool is allowed to run.
        """
        message = request.json()
        server = state.mcp
        notifications: List[Dict[str, Any]] = []
        server._writer = notifications.append  # collect instead of writing to a stream
        try:
            if _is_page_view(message):
                blocked = _gate_page_view(state, message)
                if blocked is not None:
                    return json_response({"response": blocked, "notifications": notifications})
            if message.get("method") == "tools/call":
                params = message.get("params") or {}
                name = str(params.get("name", ""))
                arguments = params.get("arguments") or {}
                state.transcript.record("tool", name=name, arguments=arguments,
                                        summary=summarise_call(name, arguments))
            response = server.handle(message)
        finally:
            server._writer = lambda _m: None
        return json_response({"response": response, "notifications": notifications})

    @route("GET", "/agent/state")
    def agent_state(_: Request) -> Response:
        return json_response({"window_connected": state.display.connected,
                              "pending_displays": state.display.pending(),
                              "transcript": state.transcript.entries()})

    @route("GET", "/agent/stream")
    def agent_stream(_: Request) -> Response:
        """Live transcript and display requests for the agent panel."""
        import queue

        outbox: queue.Queue[Tuple[str, Dict[str, Any]]] = queue.Queue()
        stop_transcript = state.transcript.subscribe(lambda e: outbox.put(("entry", e)))
        stop_display = state.display.subscribe(lambda r: outbox.put(("display", r)))

        def events() -> Iterator[Tuple[str, Any]]:
            try:
                yield "hello", {"transcript": state.transcript.entries(),
                                "pending_displays": state.display.pending()}
                while True:
                    try:
                        yield outbox.get(timeout=20)
                    except queue.Empty:
                        yield "keepalive", {"ts": time.time()}
            finally:
                stop_transcript()
                stop_display()

        return sse_response(events())

    @route("POST", "/agent/display/{request_id}/shown")
    def display_shown(request: Request) -> Response:
        ok = state.display.shown(request.params["request_id"])
        return json_response({"acknowledged": ok})

    @route("POST", "/agent/display/{request_id}/declined")
    def display_declined(request: Request) -> Response:
        reason = str(request.json().get("reason", "declined by the reader"))
        return json_response({"acknowledged": state.display.decline(request.params["request_id"], reason)})

    @route("GET", "/agent/events")
    def agent_events(_: Request) -> Response:
        return json_response({"events": list(state.agent_events)})

    return router


# ------------------------------------------------------------ display gate
def _is_page_view(message: Dict[str, Any]) -> bool:
    return (message.get("method") == "tools/call"
            and str(((message.get("params") or {}).get("name")) or "") == "page_view")


def _gate_page_view(state: AppState, message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Refuse a bridged ``page_view`` the window did not actually display.

    Returns a JSON-RPC result to send back instead of running the tool, or
    ``None`` when the page was shown and attested and the tool may proceed.
    """
    params = message.get("params") or {}
    arguments = params.get("arguments") or {}
    try:
        session_path = state.mcp.store.resolve(str(arguments.get("session", "")))
        session = load_session(Path(session_path))
    except Exception as e:
        return _tool_error(message, f"Session could not be opened: {e}")

    wanted = str(arguments.get("page", ""))
    pages = [p for p in session.get("pages", [])
             if p.get("path") == wanted or Path(str(p.get("path", ""))).name == Path(wanted).name]
    if not pages:
        return None  # let the tool produce its own, better error
    page = pages[0]
    ledger = state.ledger_for(Path(session.get("work_dir") or Path(session_path).parent))

    already, _ = ledger.page_is_attested(page)
    if already:
        return None

    if not state.display.connected:
        return _tool_error(
            message,
            "The OpenRadiology window is not connected, so this page cannot be displayed and therefore "
            "cannot be marked reviewed. Ask the person to open the application and try again.",
        )

    state.transcript.record("display_request", page=page.get("path", ""), purpose=page.get("purpose", ""),
                            summary=f"sayfa gösterilmesi istendi: {Path(str(page.get('path', ''))).name}")
    outcome = state.display.require_display(str(page.get("path", "")), str(session_path),
                                            str(page.get("purpose", "")))
    if outcome.declined:
        return _tool_error(message, f"The reader declined to display this page: {outcome.declined}")
    if outcome.shown_at is None:
        return _tool_error(
            message,
            "The window did not display this page in time. Ask the person to bring the OpenRadiology "
            "window to the front, then call page_view again.",
        )
    attested, reason = ledger.page_is_attested(page)
    if not attested:
        return _tool_error(
            message,
            f"The page was opened but not looked at long enough to count ({reason}). "
            "It has not been marked reviewed.",
        )
    state.transcript.record("display_shown", page=page.get("path", ""),
                            summary=f"sayfa görüntülendi ve tasdik edildi: {Path(str(page.get('path', ''))).name}")
    return None


def _tool_error(message: Dict[str, Any], text_message: str) -> Dict[str, Any]:
    """An MCP tool result that reports failure to the model rather than the transport."""
    return {"jsonrpc": "2.0", "id": message.get("id"),
            "result": {"content": [{"type": "text", "text": text_message}], "isError": True}}


# ------------------------------------------------------------------ pieces
def _scan(state: AppState, root: Path, depth: int = 2) -> List[Dict[str, Any]]:
    """Study cards for a folder tree.

    One header pass over the whole tree, then grouping by StudyInstanceUID --
    not by folder name. Two studies that happen to share a directory are
    reported as such instead of being silently merged, and a study split across
    subdirectories is still one card whose folder is their common ancestor.
    Folder names are never used to infer identity, date or order; DICOM says.
    """
    from ..dcmlib import group_series

    headers = state.headers.get(root)
    groups: Dict[str, List[Any]] = {}
    for path, ds in headers:
        groups.setdefault(str(ds.get("StudyInstanceUID", "")), []).append((path, ds))

    cards: List[Dict[str, Any]] = []
    for uid, items in groups.items():
        parents = [str(path.parent) for path, _ in items]
        folder = Path(os.path.commonpath(parents)) if parents else root
        d0 = items[0][1]
        modalities = sorted({str(d.get("Modality", "")) for _, d in items if d.get("Modality")})
        dates = sorted({str(d.get("StudyDate", "")) for _, d in items if d.get("StudyDate")})
        state.allow(folder)
        cards.append({
            "path": str(folder),
            "folder": folder.name or str(folder),
            "study_uid": uid,
            "shares_folder": sum(1 for other, others in groups.items()
                                 if other != uid and Path(os.path.commonpath(
                                     [str(p.parent) for p, _ in others])) == folder) > 0,
            "date": dates[0] if dates else "",
            "inconsistent_date": len(dates) > 1,
            "time": str(d0.get("StudyTime", "")),
            "description": str(d0.get("StudyDescription", "")),
            "modalities": modalities,
            "series_count": len(group_series(items)),
            "instances": len(items),
            "identity_removed": str(d0.get("PatientIdentityRemoved", "")) == "YES",
            "supported": bool(set(modalities) & set(REGIONS)),
        })
    cards.sort(key=lambda c: (c.get("date", ""), c.get("time", "")))
    return cards


def _measure_argv(study: Path, payload: Dict[str, Any]) -> List[str]:
    """Translate a measurement request into the exact CLI argv the engine expects.

    Going through ``measure.build_parser`` rather than calling internals keeps
    defaults, validation and semantics identical to the terminal, and lets the
    interface show the user the command that produced their number.
    """
    argv: List[str] = [str(study), "--series", str(payload.get("series"))]
    simple = {"instance": "--instance", "z": "--z", "roi": "--roi", "auto": "--auto", "auto3d": "--auto3d",
              "extent": "--extent", "box": "--box", "thr": "--thr", "thr_max": "--thr-max",
              "min_px": "--min-px", "weight": "--weight", "radius": "--radius",
              "convention": "--convention", "lesion_type": "--lesion-type"}
    for key, flag in simple.items():
        value = payload.get(key)
        if value is not None and value != "":
            argv += [flag, str(value)]
    for key, flag in (("points", "--points"), ("profile", "--profile")):
        pair = payload.get(key)
        if pair:
            if len(pair) != 2:
                raise UsageError(f"{key} needs exactly two 'row,col' endpoints")
            argv += [flag, str(pair[0]), str(pair[1])]
    if payload.get("allow_tilt"):
        argv.append("--allow-tilt")
    argv.append("--json")
    return argv


def _write_evidence(state: AppState, output: Path, report: Any) -> Dict[str, Any]:
    """Write-once evidence file plus its sibling ``.sha256``, as the CLI does."""
    target = state.ensure_allowed(output.parent, "evidence directory") / output.name
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report.data, indent=2, ensure_ascii=False, default=str) + "\n"
    try:
        with target.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as e:
        raise IntegrityError(f"Evidence file exists; evidence is never overwritten: {target}") from e
    digest = sha256_file(target)
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="utf-8")
    return {"path": str(target), "sha256": digest}


def _extra_args(args: Dict[str, Any]) -> List[str]:
    """Whitelist-free passthrough of render flags, normalised to CLI form."""
    out: List[str] = []
    for key, value in args.items():
        flag = "--" + str(key).replace("_", "-")
        if value is True:
            out.append(flag)
        elif value is False or value is None:
            continue
        elif isinstance(value, (list, tuple)):
            out += [flag, *[str(v) for v in value]]
        else:
            out += [flag, str(value)]
    return out
