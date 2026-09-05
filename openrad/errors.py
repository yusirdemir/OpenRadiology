"""Exception hierarchy and process exit codes for OpenRadiology.

Library code raises :class:`OpenRadError` subclasses instead of calling
``sys.exit``. The CLI maps them to stable, script-friendly exit codes and
prints the message to ``stderr`` so that ``stdout`` stays machine-readable.

Exit codes
----------
0   success
1   unexpected internal error (traceback on stderr)
2   usage / argument error
3   input error (missing folder, no DICOM, ambiguous series, unreadable pixels)
4   unsupported or invalid geometry (tilt, gaps, multiframe, non-axial)
5   session / evidence validation failed (``openrad check``)
6   quantitation unavailable (SUV factor, HU calibration)
7   integrity / immutability violation (lock replaced, hash mismatch)
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_GEOMETRY = 4
EXIT_VALIDATION = 5
EXIT_QUANTITATION = 6
EXIT_INTEGRITY = 7


class OpenRadError(Exception):
    """Base class for every expected, user-facing failure."""

    exit_code: int = EXIT_INTERNAL

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else self.__class__.__name__


class UsageError(OpenRadError):
    """Bad command-line arguments or parameter combinations."""

    exit_code = EXIT_USAGE


class InputError(OpenRadError):
    """Study folder, DICOM files or series could not be found or decoded."""

    exit_code = EXIT_INPUT


class GeometryError(OpenRadError):
    """The stack is not a regular volume that the current algorithms support."""

    exit_code = EXIT_GEOMETRY


class ValidationError(OpenRadError):
    """Session or evidence ledger failed structural validation."""

    exit_code = EXIT_VALIDATION


class QuantitationError(OpenRadError):
    """Calibrated numbers (HU, SUV) cannot be produced from the headers."""

    exit_code = EXIT_QUANTITATION


class IntegrityError(OpenRadError):
    """Evidence immutability or hash verification was violated."""

    exit_code = EXIT_INTEGRITY
