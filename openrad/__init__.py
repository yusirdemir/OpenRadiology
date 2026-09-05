"""OpenRadiology: deterministic DICOM workbench for traceable AI radiology review.

The package has two layers:

* ``openrad.dcmlib`` / ``openrad.suv`` / ``openrad.measure`` - deterministic
  geometry, windowing, calibrated measurement (HU, SUVbw) and evidence hashing.
* ``openrad.create_report`` - the evidence ledger (session JSON) that forces
  every finding to cite a DICOM ``SOPInstanceUID`` and native pixel
  coordinates before a report can be generated.

Nothing here interprets images. Interpretation is delegated to a reviewing
agent or clinician who must look at the rendered sheets.
"""
from __future__ import annotations

__version__ = "0.4.1"
__author__ = "Yusir Demir"
__license__ = "MIT"

from .errors import (  # noqa: E402
    GeometryError,
    InputError,
    IntegrityError,
    OpenRadError,
    QuantitationError,
    UsageError,
    ValidationError,
)

__all__ = [
    "__version__",
    "OpenRadError",
    "UsageError",
    "InputError",
    "GeometryError",
    "ValidationError",
    "QuantitationError",
    "IntegrityError",
]
