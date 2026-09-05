# Contributing to OpenRadiology

Thank you for helping build a reference-grade, deterministic imaging workbench for AI review.
This project sits at the intersection of medical imaging, cryptographic traceability and
agentic tooling, so a few rules are stricter than in a typical Python repository.

## Ground rules

1. **No patient data, ever.** Do not commit DICOM files, screenshots, rendered sheets, session
   JSON, reports or test fixtures derived from real examinations, even if "anonymized". Tests use
   synthetic phantoms generated in code (`tests/test_pipeline.py::make_study`). Pull requests that
   contain real headers (institution names, accession numbers, patient identifiers) are closed.
2. **Nothing interprets images.** The engine renders, measures and audits. Detection or
   classification models, "auto-diagnosis" flags and severity scores are out of scope. If you want
   to add an algorithm that decides something clinical, open a discussion first.
3. **Determinism over cleverness.** Same input, same bytes out. No random seeds without a fixed
   value, no network calls, no GPU-only paths. Pure `pydicom` + `numpy` + `Pillow`.
4. **Reject instead of guess.** When geometry or calibration is ambiguous (tilt, gaps,
   multi-frame, missing weight, non-BQML units) the correct behaviour is a typed error with a
   clear message and exit code (`openrad/errors.py`), not a silent fallback.
5. **Every claim needs a source.** New checklist items or thresholds must cite a guideline or a
   peer-reviewed paper with DOI in `docs/references.md`.

## Development setup

```bash
git clone https://github.com/yusirdemir/OpenRadiology.git
cd OpenRadiology
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest            # synthetic tests, < 1 s
ruff check openrad tests
```

## Code style

* Python 3.9+, `from __future__ import annotations`, type hints on every public function.
* Library modules raise `OpenRadError` subclasses; only `cli.py` translates them to exit codes.
* `stdout` is for data (paths, tables, JSON); `stderr` is for progress and diagnostics.
* Command modules expose `build_parser()` and `main(argv=None) -> int`.
* Keep functions small and side-effect free where possible; renderers return the list of files
  they wrote.
* Line length 140 (`ruff`).

## Adding a modality or a checklist item

1. Add the region keys to `REGIONS` in `openrad/create_report.py` and translations to
   `REGION_NAMES` for **both** `tr` and `en`.
2. Add the checklist section under `checklists/` with citations.
3. Add a synthetic test that proves the validator refuses an unsupported claim.
4. Update `docs/references.md`, `skills/openrad/SKILL.md` and the README table.

## Adding an MCP tool

1. Implement the behaviour as an `openrad` command first; the MCP tool must only build an argv and
   parse `--json` output (`openrad/mcp/tools.py`). No algorithm lives in the MCP layer.
2. Give the tool a JSON schema, annotations (`readOnlyHint` etc.) and a one-paragraph description that
   tells a model *when* to call it.
3. Never add a second way to mark a page reviewed; `page_view` is the only one by design.
4. Add an end-to-end test in `tests/test_mcp.py` that drives the wire with dicts.

## Adding a setting

1. Add one `Setting(...)` entry in `openrad/config.py` with section, default, coercer and help.
   The environment variable name is derived (`OPENRAD_<KEY>`); do not invent ad-hoc `os.environ` reads.
2. Expose it as a flag only in the commands where it matters, with the default taken from `Settings`.
3. Document it in `openrad config --init`'s template, `SKILL.md` and the README table.
4. Add a precedence test in `ConfigTests`.

## Testing philosophy

Tests cover engineering invariants (geometry, hashing, validation, exit codes). They do
**not** and cannot prove clinical sensitivity. When you fix a failure discovered on real images,
describe the failure mode generically in `checklists/lessons.md` and add a phantom test that
reproduces the geometry, never the case.

## Pull requests

* One topic per PR, with a short rationale and, for clinical logic, the citation.
* Run `pytest` and `ruff` locally; CI runs on Linux, macOS and Windows.
* Do not bump the version; maintainers do that with the changelog.

## Reporting security or privacy issues

If you find a way for patient data to leak into a report, cache or log, or a way to alter
evidence after hashing, please report it privately to the maintainer via the GitHub security
advisory feature instead of a public issue.
