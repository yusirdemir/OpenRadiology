"""View attestation: the ledger of what the application actually put on screen.

``skills/openrad/SKILL.md`` states the limitation plainly:

    "Marking a page ``reviewed: true`` without opening it is falsification;
    ``openrad check`` cannot detect it, so you must not do it."

A command-line tool cannot detect it because it never draws the pixels. A
desktop application does draw them, so it can. Every image the renderer puts on
screen produces a :class:`ViewEvent`; an event that satisfies the dwell policy
becomes an attestation, and the server refuses to write ``reviewed: true`` for
any page that no attestation covers.

Scope, stated honestly and repeated in the user interface: an attestation means
*the application displayed this image while the window had focus*. It does not
mean the viewer understood it, and it is not a substitute for competence. It
removes one specific failure -- claiming to have looked without looking.

The ledger lives beside the session as ``attestations.jsonl`` and is
append-only. ``session.json`` keeps schema version 2 untouched, so ``openrad
check`` behaves identically whether a session was driven from the terminal or
from the desktop application.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

LEDGER_NAME = "attestations.jsonl"


@dataclass(frozen=True)
class DwellPolicy:
    """What counts as having looked at an image.

    ``min_dwell_ms``
        Uninterrupted time the image was the displayed one. 400 ms is long
        enough to exclude a scroll blur and short enough not to punish a fast,
        competent reader.
    ``min_scale``
        Screen pixels per image pixel. Below 1.0 the image is downsampled and
        small findings cannot survive; such a view does not attest anything.
    ``require_focus``
        The window must be focused and the document visible.
    """

    min_dwell_ms: float = 400.0
    min_scale: float = 1.0
    require_focus: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {"min_dwell_ms": self.min_dwell_ms, "min_scale": self.min_scale,
                "require_focus": self.require_focus}


@dataclass
class ViewEvent:
    """One image displayed by the renderer."""

    study_uid: str = ""
    series_uid: str = ""
    sop_uid: str = ""
    plane: str = "ax"
    index: int = -1
    dwell_ms: float = 0.0
    scale: float = 0.0
    focused: bool = False
    visible: bool = True
    window_center: Optional[float] = None
    window_width: Optional[float] = None
    page_path: str = ""
    source: str = "viewer"
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: Dict[str, Any]) -> ViewEvent:
        def num(key: str, default: float = 0.0) -> float:
            try:
                return float(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        return cls(
            study_uid=str(raw.get("study_uid", "")),
            series_uid=str(raw.get("series_uid", "")),
            sop_uid=str(raw.get("sop_uid", "")),
            plane=str(raw.get("plane", "ax")),
            index=int(raw.get("index", -1) or -1),
            dwell_ms=num("dwell_ms"),
            scale=num("scale"),
            focused=bool(raw.get("focused", False)),
            visible=bool(raw.get("visible", True)),
            window_center=raw.get("window_center"),
            window_width=raw.get("window_width"),
            page_path=str(raw.get("page_path", "")),
            source=str(raw.get("source", "viewer")),
        )

    def rejection(self, policy: DwellPolicy) -> Optional[str]:
        """Why this event does not attest anything, or ``None`` if it does."""
        if self.source == "page":
            if not self.page_path:
                return "page attestation without a page path"
        elif not self.sop_uid:
            return "viewer attestation without a SOPInstanceUID"
        if self.dwell_ms < policy.min_dwell_ms:
            return f"dwell {self.dwell_ms:.0f} ms below the {policy.min_dwell_ms:.0f} ms threshold"
        if self.scale < policy.min_scale:
            return f"magnification {self.scale:.2f} below {policy.min_scale:.2f} screen px per image px"
        if policy.require_focus and not (self.focused and self.visible):
            return "window was not focused and visible for the whole dwell"
        return None

    def as_record(self) -> Dict[str, Any]:
        return {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "study_uid": self.study_uid,
                "series_uid": self.series_uid, "sop_uid": self.sop_uid, "plane": self.plane,
                "index": self.index, "dwell_ms": round(self.dwell_ms, 1), "scale": round(self.scale, 3),
                "window_center": self.window_center, "window_width": self.window_width,
                "page_path": self.page_path, "source": self.source}


class AttestationLedger:
    """Append-only record of displayed images for one session work directory."""

    def __init__(self, work_dir: Path, policy: Optional[DwellPolicy] = None) -> None:
        self.work_dir = Path(work_dir)
        self.path = self.work_dir / LEDGER_NAME
        self.policy = policy or DwellPolicy()
        self._lock = threading.Lock()
        self._sops: Set[str] = set()
        self._pages: Set[str] = set()
        self._count = 0
        self._loaded = False

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.is_file():
            return
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._count += 1
                if record.get("sop_uid"):
                    self._sops.add(str(record["sop_uid"]))
                if record.get("page_path"):
                    self._pages.add(self._normalise(record["page_path"]))

    def _normalise(self, page_path: str) -> str:
        path = Path(page_path)
        try:
            return str(path.resolve())
        except OSError:  # pragma: no cover - unresolvable path stays as given
            return str(path)

    # -- writing -----------------------------------------------------------
    def record(self, events: Iterable[ViewEvent]) -> Dict[str, Any]:
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for event in events:
            reason = event.rejection(self.policy)
            if reason is None:
                accepted.append(event.as_record())
            else:
                rejected.append({"sop_uid": event.sop_uid, "page_path": event.page_path, "reason": reason})
        if accepted:
            with self._lock:
                self._load()
                self.work_dir.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    for record in accepted:
                        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                for record in accepted:
                    self._count += 1
                    if record.get("sop_uid"):
                        self._sops.add(str(record["sop_uid"]))
                    if record.get("page_path"):
                        self._pages.add(self._normalise(record["page_path"]))
        return {"accepted": len(accepted), "rejected": rejected, "policy": self.policy.as_dict()}

    # -- reading -----------------------------------------------------------
    def attested_sops(self) -> Set[str]:
        with self._lock:
            self._load()
            return set(self._sops)

    def attested_pages(self) -> Set[str]:
        with self._lock:
            self._load()
            return set(self._pages)

    def total(self) -> int:
        with self._lock:
            self._load()
            return self._count

    # -- policy over a session --------------------------------------------
    def page_is_attested(self, page: Dict[str, Any]) -> Tuple[bool, str]:
        """A page counts as looked at by either route.

        1. The sheet itself was displayed and dwelled on (contact-sheet reading).
        2. Every native slice it was rendered from was displayed in the viewer
           (slice-by-slice reading, which the terminal workflow cannot offer).
        """
        pages, sops = self.attested_pages(), self.attested_sops()
        if self._normalise(str(page.get("path", ""))) in pages:
            return True, "sheet displayed"
        sources = [str(s.get("sop_uid", "")) for s in page.get("sources", []) if s.get("sop_uid")]
        if sources and all(uid in sops for uid in sources):
            return True, f"all {len(sources)} source slices displayed"
        missing = [uid for uid in sources if uid not in sops]
        if not sources:
            return False, "page has no registered sources to attest against"
        return False, f"{len(missing)} of {len(sources)} source slices were never displayed"

    def reconcile(self, session: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
        """Make ``reviewed`` mean exactly what the ledger says, in both directions.

        This is the whole point of the mechanism: ``reviewed`` is not something
        anyone asserts, it is derived. A page an attestation covers becomes
        reviewed; a page marked reviewed that nothing covers is cleared, and the
        refusal is reported so the reader is told which page still needs
        looking at rather than being failed silently.

        Returns the session, the refusals, and the pages newly promoted.
        """
        refusals: List[Dict[str, Any]] = []
        promoted: List[str] = []
        for page in session.get("pages", []):
            ok, reason = self.page_is_attested(page)
            if ok and not page.get("reviewed"):
                page["reviewed"] = True
                page.setdefault("viewed_via", "desktop:attestation")
                promoted.append(str(page.get("path", "")))
            elif not ok and page.get("reviewed"):
                page["reviewed"] = False
                refusals.append({"path": page.get("path", ""), "purpose": page.get("purpose", ""), "reason": reason})
        return session, refusals, promoted

    def enforce(self, session: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Reconcile, keeping the older two-value shape for callers that only care about refusals."""
        session, refusals, _ = self.reconcile(session)
        return session, refusals

    def coverage(self, session: Dict[str, Any]) -> Dict[str, Any]:
        pages = session.get("pages", [])
        rows = []
        for page in pages:
            ok, reason = self.page_is_attested(page)
            rows.append({"path": page.get("path", ""), "purpose": page.get("purpose", ""),
                         "reviewed": bool(page.get("reviewed")), "attested": ok, "reason": reason})
        sops_total = sum(len(s.get("sops", {})) for study in session.get("studies", []) for s in study.get("series", []))
        attested = self.attested_sops()
        seen = sum(1 for study in session.get("studies", []) for s in study.get("series", [])
                   for uid in s.get("sops", {}) if uid in attested)
        return {"pages": rows,
                "pages_total": len(pages),
                "pages_attested": sum(1 for r in rows if r["attested"]),
                "slices_total": sops_total,
                "slices_attested": seen,
                "events": self.total(),
                "policy": self.policy.as_dict()}
