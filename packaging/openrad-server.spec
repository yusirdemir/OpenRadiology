# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the desktop sidecar.

One binary, two entry points. ``openrad-server`` runs the sidecar; the same
executable re-entered with ``--cli`` is the ordinary OpenRadiology command line
and with ``--bridge`` is the MCP stdio shim. Background jobs re-enter it that
way, so a packaged install needs no Python interpreter on the user's machine
and the desktop application runs byte-identical commands to a terminal.

Compressed transfer syntaxes are bundled deliberately: a scanner that writes
JPEG 2000 is common, and a viewer that cannot open half an archive is not a
viewer. The excludes drop the plotting, testing and interactive stacks that
SciPy and friends drag in but the engine never touches.
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

hidden = [
    # pydicom looks its pixel handlers up by name at run time.
    "pydicom.encoders.gdcm",
    "pydicom.encoders.pylibjpeg",
    "pydicom.pixel_data_handlers.pylibjpeg_handler",
    "pydicom.pixel_data_handlers.numpy_handler",
    "pydicom.pixel_data_handlers.rle_handler",
    "pylibjpeg",
    "libjpeg",
    "openjpeg",
    # scipy submodules the engine reaches through lazy attributes.
    "scipy.ndimage",
    "scipy.spatial",
    "scipy.spatial.qhull",
]

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "setuptools",
    "scipy.io.matlab", "numpy.f2py", "PIL.ImageQt", "PyQt5", "PyQt6", "PySide2", "PySide6",
]

analysis = Analysis(
    [str(ROOT / "packaging" / "server_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "openrad" / "locales"), "openrad/locales"),
        (str(ROOT / "openrad" / "schema"), "openrad/schema"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="openrad-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="openrad-server",
)
