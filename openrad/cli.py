"""OpenRadiology unified command-line interface.

    openrad inventory  STUDY_DIR --output OUT_DIR
    openrad prepare    FOLDER [FOLDER ...] [--repo PATH] [--identity-source FILE]
    openrad ct-render  STUDY_DIR --series NUM --output OUT_DIR [options]
    openrad mr-render  STUDY_DIR --output OUT_DIR [--series NUM ...] [options]
    openrad pet-render STUDY_DIR --pt NUM --ct NUM --output OUT_DIR [options]
    openrad zoom       STUDY_DIR --series NUM --center R,C --output PNG [options]
    openrad measure    STUDY_DIR --series NUM [options] [--json] [--output FILE]
    openrad register   SESSION --directory DIR
    openrad check      SESSION [--json]
    openrad finish     SESSION [--lang en|tr]
    openrad lock       REVIEW.md [--verify]
    openrad anonymize  IN_DIR OUT_DIR --salt SECRET
    openrad config     [--json | --init [.openrad.toml]]
    openrad doctor     [--json]
    openrad mcp        [--repo DIR] | --print | --install CLIENT [--write] | --list-tools

Global options (before the command): ``--config FILE`` selects a TOML file
(same as ``OPENRAD_CONFIG``). Settings precedence: flags > ``OPENRAD_*`` env >
project ``.openrad.toml`` / ``[tool.openrad]`` > user config > defaults.

Conventions
-----------
* ``stdout`` carries data (tables, JSON, written file paths); ``stderr`` carries
  progress, warnings and error messages.
* Exit codes are stable (see ``openrad.errors``): 0 ok, 2 usage, 3 input,
  4 geometry, 5 validation, 6 quantitation, 7 integrity, 1 internal.
* Every subcommand accepts ``--help``.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Callable, Dict, List, Optional, Sequence

from . import __version__
from .errors import EXIT_INTERNAL, EXIT_USAGE, OpenRadError

Runner = Callable[[Optional[Sequence[str]]], int]

COMMANDS: Dict[str, str] = {
    "inventory": "Inspect every DICOM series in a study folder (matrix, spacing, kernel, timing)",
    "prepare": "Create a review session for one study or a chronological comparison",
    "ct-render": "Render systematic CT contact sheets (lung/soft/bone, slab MIP, coronal/sagittal)",
    "mr-render": "Render MR contact sheets per sequence (T1, T2, FLAIR, DWI, SWI) and pre/post pairs",
    "pet-render": "Render PET/CT: SUVbw conversion, rotating MIP, hotspot table, fused axial tiles",
    "zoom": "Magnified multi-slice view with pixel grid and scale bar for lesion-vs-vessel decisions",
    "measure": "Calibrated distances, ROI statistics (HU/SUVbw), extents; write-once SHA-256 evidence",
    "register": "Register rendered PNG sheets into the session (nothing is marked reviewed)",
    "check": "Validate the evidence ledger: coverage, references, hashes, comparison logic",
    "finish": "Generate the professional report and the patient companion guide",
    "lock": "Seal a preliminary review with a SHA-256 lock before unblinding",
    "anonymize": "De-identify a DICOM tree (PS3.15 basic profile, deterministic pseudonyms, shifted dates)",
    "config": "Show the effective configuration and its sources, or write a template .openrad.toml",
    "doctor": "Check interpreter, dependencies, decoders, configuration and writable paths",
    "mcp": "Run the Model Context Protocol server (stdio) or configure Claude Desktop / Cursor / Windsurf",
}
ALIASES: Dict[str, str] = {"render": "ct-render", "render-ct": "ct-render", "render-mr": "mr-render", "render-pet": "pet-render"}


def _runner(command: str) -> Runner:
    """Import lazily so that ``openrad --help`` stays fast and dependency errors are local."""
    if command == "inventory":
        from .dicom_inventory import main
    elif command in ("prepare", "register", "check", "finish"):
        from .create_report import main as cr_main
        return lambda argv: cr_main([command, *(argv or [])])
    elif command == "ct-render":
        from .ct_render import main
    elif command == "mr-render":
        from .mr_render import main
    elif command == "pet-render":
        from .pet_render import main
    elif command == "zoom":
        from .ct_zoom import main
    elif command == "measure":
        from .measure import main
    elif command == "lock":
        from .blind_lock import main
    elif command == "anonymize":
        from .anonymize import main
    elif command == "config":
        from .config import main
    elif command == "doctor":
        from .doctor import main
    elif command == "mcp":
        from .mcp.cli import main
    else:  # pragma: no cover - guarded by the parser
        raise KeyError(command)
    return main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openrad",
        description="OpenRadiology: deterministic DICOM workbench for traceable AI radiology review (CT, PET/CT, MRI).",
        epilog="Run 'openrad <command> --help' for command options.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", metavar="FILE", help="TOML configuration file (overrides OPENRAD_CONFIG)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name, help_text in COMMANDS.items():
        # Each command owns its argparse; we only reserve the name and forward the remainder.
        sub.add_parser(name, help=help_text, add_help=False)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args: List[str] = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args or args[0] in ("-h", "--help"):
        parser.print_help(sys.stdout)
        return 0
    if args[0] in ("--version", "-V"):
        print(f"openrad {__version__}")
        return 0
    if args[0] == "--config":
        if len(args) < 3:
            print("openrad: error: --config needs a file and a command", file=sys.stderr)
            return EXIT_USAGE
        os.environ["OPENRAD_CONFIG"] = args[1]
        args = args[2:]
    elif args[0].startswith("--config="):
        os.environ["OPENRAD_CONFIG"] = args[0].split("=", 1)[1]
        args = args[1:]
    command = ALIASES.get(args[0], args[0])
    if command not in COMMANDS:
        parser.print_usage(sys.stderr)
        print(f"openrad: error: unknown command '{args[0]}'. Commands: {', '.join(COMMANDS)}", file=sys.stderr)
        return EXIT_USAGE
    try:
        # Settings that must act before numpy is imported (thread caps) and logging level.
        from .config import apply_compute_settings, load_settings
        from .log import configure
        settings = load_settings()
        apply_compute_settings(settings)
        configure(settings.log_level)
        return int(_runner(command)(args[1:]) or 0)
    except OpenRadError as e:
        print(f"openrad {command}: error: {e.message}", file=sys.stderr)
        return e.exit_code
    except SystemExit as e:  # argparse --help / usage errors
        code = e.code
        return 0 if code in (None, 0) else (code if isinstance(code, int) else EXIT_USAGE)
    except KeyboardInterrupt:  # pragma: no cover
        print("openrad: interrupted", file=sys.stderr)
        return 130
    except Exception as e:  # pragma: no cover - unexpected bug, keep traceback visible
        import traceback
        traceback.print_exc()
        print(f"openrad {command}: internal error: {e}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
