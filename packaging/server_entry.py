"""Frozen entry point.

Kept as a file rather than a module reference so PyInstaller has an
unambiguous script to analyse, and so the multiprocessing guard runs before
anything imports numpy.
"""
from __future__ import annotations

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from openrad.server.__main__ import main

    sys.exit(main())
