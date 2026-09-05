"""Diagnostics on stderr, controlled by ``general.log_level`` / ``OPENRAD_LOG_LEVEL``.

``stdout`` is reserved for data everywhere in the CLI; use :func:`progress`
for anything a human should see while a command runs.
"""
from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger("openrad")


def configure(level: str = "info") -> logging.Logger:
    if not _LOGGER.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("[openrad %(levelname).1s] %(message)s"))
        _LOGGER.addHandler(handler)
        _LOGGER.propagate = False
    _LOGGER.setLevel(getattr(logging, level.upper(), logging.INFO))
    return _LOGGER


def progress(message: str) -> None:
    (_LOGGER if _LOGGER.handlers else configure()).info(message)


def warn(message: str) -> None:
    (_LOGGER if _LOGGER.handlers else configure()).warning(message)


def debug(message: str) -> None:
    (_LOGGER if _LOGGER.handlers else configure()).debug(message)
