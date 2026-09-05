"""MCP resources: clinical checklists, references, templates, schema, configuration and session artefacts.

URI scheme ``openrad://``:

    openrad://checklist/{name}            ct_thorax | pet_ct | brain_mri | lessons | blind_audit
    openrad://references                  guidelines and DOIs mapped to code
    openrad://skill                       the agent protocol (SKILL.md)
    openrad://template/{lang}             report and evidence specification per locale
    openrad://schema/session              JSON Schema of the ledger
    openrad://config                      effective configuration with sources
    openrad://sessions                    sessions in the cache
    openrad://session/{id}                session.json
    openrad://session/{id}/status         progress, alerts, validation summary
    openrad://session/{id}/page/{name}    rendered PNG (blob)
    openrad://session/{id}/evidence/{name} measurement evidence file
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import load_settings
from ..errors import InputError
from .sessions import SessionStore, status

CHECKLISTS = {"ct_thorax": "ct_thorax_checklist.md", "pet_ct": "pet_ct_checklist.md", "brain_mri": "brain_mri_checklist.md",
              "lessons": "lessons.md", "blind_audit": "blind_audit_rubric.md"}


def docs_root() -> Optional[Path]:
    """Source checkout (or OPENRAD_DOCS_ROOT) that carries checklists/, docs/, templates/, skills/."""
    candidates = [Path(os.environ["OPENRAD_DOCS_ROOT"]).expanduser()] if os.environ.get("OPENRAD_DOCS_ROOT") else []
    candidates += [Path(__file__).resolve().parents[2], Path.cwd()]
    for c in candidates:
        if (c / "checklists").is_dir():
            return c
    return None


class Resources:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def list(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        root = docs_root()
        if root:
            for name, fname in CHECKLISTS.items():
                if (root / "checklists" / fname).is_file():
                    items.append({"uri": f"openrad://checklist/{name}", "name": f"checklist:{name}", "mimeType": "text/markdown",
                                  "description": f"Systematic review checklist ({name})"})
            if (root / "docs" / "references.md").is_file():
                items.append({"uri": "openrad://references", "name": "references", "mimeType": "text/markdown",
                              "description": "Guidelines and papers (with DOIs) behind every default"})
            if (root / "skills" / "openrad" / "SKILL.md").is_file():
                items.append({"uri": "openrad://skill", "name": "skill", "mimeType": "text/markdown", "description": "The image-first review protocol"})
            for lang_dir in sorted((root / "templates").glob("*/report_template.md")):
                items.append({"uri": f"openrad://template/{lang_dir.parent.name}", "name": f"template:{lang_dir.parent.name}",
                              "mimeType": "text/markdown", "description": "Report and evidence specification"})
        items.append({"uri": "openrad://schema/session", "name": "session-schema", "mimeType": "application/schema+json",
                      "description": "JSON Schema of the evidence ledger"})
        items.append({"uri": "openrad://config", "name": "config", "mimeType": "application/json", "description": "Effective configuration with sources"})
        items.append({"uri": "openrad://sessions", "name": "sessions", "mimeType": "application/json", "description": "Sessions in the cache directory"})
        for s in self.store.list():
            items.append({"uri": f"openrad://session/{s['id']}", "name": f"session:{s['id']}", "mimeType": "application/json",
                          "description": f"{s['mode']} session, {', '.join(s['studies'])}"})
        return items

    @staticmethod
    def templates() -> List[Dict[str, Any]]:
        return [
            {"uriTemplate": "openrad://checklist/{name}", "name": "checklist", "mimeType": "text/markdown",
             "description": "name: ct_thorax | pet_ct | brain_mri | lessons | blind_audit"},
            {"uriTemplate": "openrad://template/{lang}", "name": "template", "mimeType": "text/markdown", "description": "Report specification per locale"},
            {"uriTemplate": "openrad://session/{id}", "name": "session", "mimeType": "application/json", "description": "Session ledger"},
            {"uriTemplate": "openrad://session/{id}/status", "name": "session-status", "mimeType": "application/json", "description": "Progress and alerts"},
            {"uriTemplate": "openrad://session/{id}/page/{name}", "name": "page", "mimeType": "image/png", "description": "Rendered sheet (blob)"},
            {"uriTemplate": "openrad://session/{id}/evidence/{name}", "name": "evidence", "mimeType": "application/json",
             "description": "Write-once measurement evidence"},
        ]

    def read(self, uri: str) -> Dict[str, Any]:
        if not uri.startswith("openrad://"):
            raise InputError(f"unsupported URI scheme: {uri}")
        parts = uri[len("openrad://"):].split("/")
        kind = parts[0]
        root = docs_root()
        if kind == "checklist" and len(parts) == 2:
            fname = CHECKLISTS.get(parts[1])
            if not fname or not root:
                raise InputError(f"unknown checklist '{parts[1]}'")
            return _text(uri, (root / "checklists" / fname).read_text(encoding="utf-8"), "text/markdown")
        if kind == "references" and root:
            return _text(uri, (root / "docs" / "references.md").read_text(encoding="utf-8"), "text/markdown")
        if kind == "skill" and root:
            return _text(uri, (root / "skills" / "openrad" / "SKILL.md").read_text(encoding="utf-8"), "text/markdown")
        if kind == "template" and len(parts) == 2 and root:
            p = root / "templates" / parts[1] / "report_template.md"
            if not p.is_file():
                raise InputError(f"no template for locale '{parts[1]}'")
            return _text(uri, p.read_text(encoding="utf-8"), "text/markdown")
        if kind == "schema" and parts[1:] == ["session"]:
            p = Path(__file__).resolve().parents[1] / "schema" / "session.schema.json"
            return _text(uri, p.read_text(encoding="utf-8"), "application/schema+json")
        if kind == "config":
            st = load_settings()
            return _text(uri, json.dumps({"settings": st.as_dict(), "sources": st.sources, "files": [str(p) for p in st.files]}, indent=2), "application/json")
        if kind == "sessions":
            return _text(uri, json.dumps(self.store.list(), indent=2), "application/json")
        if kind == "session" and len(parts) >= 2:
            path = self.store.resolve(parts[1])
            s = self.store.load(path)
            if len(parts) == 2:
                return _text(uri, json.dumps(s, indent=2, ensure_ascii=False), "application/json")
            if parts[2] == "status":
                return _text(uri, json.dumps(status(s, self.store.session_id(path)), indent=2, ensure_ascii=False), "application/json")
            if parts[2] == "page" and len(parts) == 4:
                match = [p for p in s["pages"] if Path(p["path"]).name == parts[3]]
                png = Path(match[0]["path"]) if match else None
                if png is None or not png.is_file():
                    raise InputError(f"page '{parts[3]}' not registered in session {parts[1]}")
                return {"contents": [{"uri": uri, "mimeType": "image/png", "blob": base64.b64encode(png.read_bytes()).decode("ascii")}]}
            if parts[2] == "evidence" and len(parts) == 4:
                p = Path(s["work_dir"]) / "evidence" / parts[3]
                if not p.is_file() or ".." in parts[3]:
                    raise InputError(f"evidence '{parts[3]}' not found")
                return _text(uri, p.read_text(encoding="utf-8"), "application/json" if p.suffix == ".json" else "text/plain")
        raise InputError(f"unknown resource: {uri}")


def _text(uri: str, body: str, mime: str) -> Dict[str, Any]:
    return {"contents": [{"uri": uri, "mimeType": mime, "text": body}]}
