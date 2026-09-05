# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-09-05

### Added
- `openrad/config.py`: layered configuration (flags > `OPENRAD_*` env > `.openrad.toml` /
  `[tool.openrad]` > `~/.config/openrad/config.toml` > defaults) with typed coercion, source
  tracking, `openrad config [--json | --init]` and a global `--config FILE` option.
- `openrad/grid.py`: vision-budget-aware sheet grids (`--grid 2x2|3x3|auto`, `--max-side`,
  `--vision claude|gpt|gemini|local`) that never down-sample a native slice; adaptive fonts,
  scale bars and a millimetre depth ruler on reformats.
- `openrad anonymize`: DICOM PS3.15 basic confidentiality profile with deterministic HMAC
  pseudonyms, shifted dates (times kept), retained patient characteristics, safe private tags,
  descriptor cleaning, pixel-hash verification, pseudonymous output layout and optional map file.
- `openrad doctor`: environment, decoder, configuration and writable-path self-check.
- `openrad measure --convention fleischner|recist --lesion-type nodule|node|mass`: the reported
  diameter now follows the configured guideline; raw axes are still printed.
- Inventories redact PatientID/Accession/Institution by default (`privacy.redact_identifiers`,
  `--no-redact`).
- `openrad/schema/session.schema.json` (JSON Schema 2020-12) documenting the ledger contract.
- `openrad/log.py`: stderr diagnostics with `general.log_level` / `OPENRAD_LOG_LEVEL`.
- `tests/conftest.py` and in-file path bootstrap: `python3 tests/test_pipeline.py` and `pytest`
  both run without installation or `PYTHONPATH`. 42 tests.

### Changed
- **English is the default report language**; other languages are selected once via configuration.
  All report strings live in `openrad/locales/<lang>.json`; the engine contains no natural-language
  report text. Default output directory is now `<repo>/reports`; set `paths.output_dir` to change it.
- The same-patient index file for anonymized exports is configurable (`paths.identity_index`,
  default `PATIENT_INDEX.md`).
- Renderers, `pet-render` and `measure` read their defaults (MIP thickness, step, thresholds,
  uptake window, region radius) from configuration.
- `compute.threads` caps BLAS/OpenMP threads before numpy is imported.
- `tomli` added as a dependency on Python < 3.11.

## [0.2.0] - 2026-09-05

### Added
- `openrad/errors.py`: typed exception hierarchy with stable exit codes (2 usage, 3 input,
  4 geometry, 5 validation, 6 quantitation, 7 integrity).
- Gantry-tilt detection from slice origins (compared with `GantryDetectorTilt`), opt-in
  `--allow-tilt` with per-slice integer de-shear for reformats and explicit refusal of 3D
  operations on sheared stacks.
- QIBA-conformant SUV logic: `DecayCorrectionDateTime` preferred, earliest-acquisition rule,
  Philips `CNTS` private scale factors, `GML` passthrough, plausibility warnings for uptake time,
  optional lean-body-mass (SUL) factor.
- `openrad measure --json` and `--output` handled inside the command; write-once evidence file
  with `.sha256` sidecar and structured results (distance, ROI, region 2D/3D, extent, profile).
- Native PET slice sheets (`pet_native_NN.png`) so the "every native PET slice" pass can be
  audited like CT/MR passes; fused tiles now carry provenance.
- `openrad finish --lang en|tr` and `--output-dir`; session stores `language` and
  `studies_root`; `openrad prepare --studies-root` removes the hard-coded `DCIM/` layout.
- `openrad check --json`, `openrad inventory --json`, `openrad lock` subcommand,
  `python -m openrad`.
- Patient guide: "How to read this document" preamble and a consolidated question list.
- `docs/references.md` with DOIs for every guideline used; CI on three operating systems;
  `CONTRIBUTING.md`, `CITATION.cff`, `py.typed`.

### Changed
- All modules use package-relative imports; `sys.path` hacks removed. Scripts are run through
  the `openrad` CLI or `python -m openrad.<module>`.
- Progress messages go to `stderr`; `stdout` carries only data.
- Coronal slab thickness now uses row spacing (was column spacing).
- Session schema bumped to 2 (adds `language`, `studies_root`, `recommendations`).

### Changed (packaging)
- `setup.py` reduced to a metadata-free shim; all metadata lives in `pyproject.toml`.

## [0.1.0] - 2026-09-05
- Initial public release.
