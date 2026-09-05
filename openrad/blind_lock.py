"""Seal a preliminary review before opening the reference report.

    openrad lock REVIEW.md            # writes REVIEW.md.lock.json (sha256 + UTC time)
    openrad lock REVIEW.md --verify   # exit 7 if the file changed since locking

Never edit or append to a locked review. Write the audit in a separate file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .dcmlib import sha256_file
from .errors import InputError, IntegrityError


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="openrad lock", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path)
    ap.add_argument("--verify", action="store_true")
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if not a.file.is_file():
        raise InputError(f"File not found: {a.file}")
    lock = a.file.with_suffix(a.file.suffix + ".lock.json")
    if a.verify:
        if not lock.exists():
            raise InputError("no lock file")
        rec = json.loads(lock.read_text())
        ok = rec["sha256"] == sha256_file(a.file)
        print(("UNCHANGED since lock " if ok else "MODIFIED after lock! ") + rec["locked_utc"])
        if not ok:
            raise IntegrityError("review changed after lock")
        return 0
    rec = {"file": str(a.file), "sha256": sha256_file(a.file), "bytes": a.file.stat().st_size,
           "locked_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        with lock.open("x") as stream:
            stream.write(json.dumps(rec, indent=2))
    except FileExistsError as e:
        raise IntegrityError("Lock already exists; verify it, never replace it") from e
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
