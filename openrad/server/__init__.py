"""Local sidecar that backs the OpenRadiology desktop application.

The engine stays the single source of truth for anything numeric; this package
only keeps decoded volumes resident, streams pixels to a GPU-side viewer, runs
engine commands as cancellable child processes, and records what the
application actually put on screen so that ``reviewed`` flags mean something.

See ``docs/superpowers/specs/2026-09-06-openradiology-desktop-design.md``.
"""
from __future__ import annotations

__all__ = ["AppState", "build_router", "create_app", "serve"]


def __getattr__(name: str):  # lazy: importing the package must not pull in numpy
    if name in ("AppState", "build_router"):
        from .api import AppState, build_router

        return {"AppState": AppState, "build_router": build_router}[name]
    if name in ("create_app", "serve"):
        from .app import create_app, serve

        return {"create_app": create_app, "serve": serve}[name]
    raise AttributeError(name)
