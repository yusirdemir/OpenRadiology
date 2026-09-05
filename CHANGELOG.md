# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`openrad ct-render --auto-z lung`**: bounds the lung-window and slab-MIP passes to the longest run of
  slices that contain enclosed air (padded 8 mm). The skipped SOPs and the rule are written to
  `render_index.json` under `_scope`; `register` stores them as `series.pass_scope`, `check` no longer
  demands those slices for the scoped pass (and insists on a stated basis), and `finish` prints the scope
  in the technique block. On a 385-slice study this removed 82 lung sheets' worth of neck and abdomen.
- `Volume.lung_z_bounds`, `Volume.lung_slice_fraction`, `Volume.mpr_positions`.

### Changed
- Coronal/sagittal reformat positions are spread over the body bounding box instead of the full field of
  view, so no reformat tile shows empty air beside the patient.
- `mark_source` accepts the list of slices a tile is evidence for; `save_page` flattens them.
- Skill and checklists: thick (3–5 mm) soft reconstruction for mediastinum/abdomen/bone, thin series for
  lungs and calipers; calcium threshold for calcified nodules; box + calipers for pleural-based masses;
  ≤ 20 sheets per vision request; coronal zoom before calling a cardiophrenic "nodule".

### Fixed
- Slab-MIP sheets recorded only the slab centre as their source, so a `lung:mip` pass could never be
  completed (`check` reported hundreds of "unread" slices). Every slice inside the slab is now a source.
- Reformat (MPR) sheets carried no provenance; `register` left their study/series empty and `check`
  rejected them. They now carry the volume's study/series with purpose `<window>:mpr`.

- **Desktop application** (`app/`): a Tauri v2 window for macOS and Windows with a WebGL2 slice
  viewer, the anatomical sweep as a working checklist, measurement tools wired to the engine, a
  comparison mode and a report screen. Turkish and English, with region names read from the engine's
  own locale files so the screen and the report cannot drift apart.
- **`openrad.server`**: a loopback sidecar that keeps decoded volumes resident under an LRU byte
  budget, streams raw calibrated pixels for GPU windowing, runs engine commands as cancellable child
  processes with server-sent progress, and serves the session ledger. Bearer token on an
  OS-assigned port, no outbound connection, file access confined to the folders opened in the
  session.
- **View attestation**: `SKILL.md` concedes that a page can be marked `reviewed` without being
  opened and that `openrad check` cannot detect it. The window draws the pixels, so it can. A slice
  counts as seen only after an uninterrupted dwell (400 ms), at full resolution (at least one screen
  pixel per image pixel), in a focused window. Attestations are appended to `attestations.jsonl`
  beside the session; `session.schema.json` stays at version 2 and `openrad check` is unchanged, so
  a session can be worked on from the terminal and the window interchangeably.
- **MCP bridge** (`openrad-server --bridge`): a stdio shim that proxies an MCP client into the
  running window, sharing its volume cache and open session, and falls back to the in-process server
  when no window is running. Through the bridge, `page_view` passes the display gate: the sheet is
  shown at natural resolution and the tool runs only once an attestation covers it.
- **Same-patient gate in the interface**: when exports carry different patient identifiers, the
  sidecar finds the documents in the archive that already name every selected study so the reader
  makes a recorded assertion instead of reading a raw error.
- `packaging/openrad-server.spec`: PyInstaller specification producing one binary that is also the
  CLI (`--cli`) and the bridge (`--bridge`), with the compressed-DICOM decoders bundled.

### Changed
- `measure.run` accepts an optional pre-decoded `Volume`, so a long-lived caller can measure without
  re-reading several hundred files per click. The mathematics is untouched.
- `ruff` ignores `UP045` alongside `UP006` and `UP007`: the same Python 3.9 compatibility rule, split
  out by newer ruff versions.

## [0.4.0] - 2026-09-05

### Added
- `openrad mcp`: dependency-free Model Context Protocol server (JSON-RPC 2.0 over stdio, protocol
  2025-06-18 with 2025-03-26 / 2024-11-05 accepted) exposing 21 tools, `openrad://` resources
  (checklists, references, skill, templates, schema, config, sessions, pages as PNG blobs, evidence)
  and four prompts (`review_study`, `compare_studies`, `explain_for_patient`, `blinded_audit`).
- Protocol-level review discipline: `page_view` is the only way to mark a page reviewed and returns
  the image in the same step; region/claim setters return object-scoped validation errors;
  `session_status` reports technical alerts (tilt, unsupported series, SUV warnings, unrendered or
  unviewed slices, pending comparison) and coverage per required pass; `session_plan` returns the
  reading plan as data.
- `measure` over MCP writes write-once evidence into the session and returns the SHA-256 with a
  ready `measurement_template`; renders return inline PNGs within a byte budget plus resource links;
  engine progress is forwarded as MCP logging notifications.
- `openrad mcp --install claude-desktop|cursor|windsurf [--write]`, `--print --client claude-code|generic`,
  `--list-tools`; client entries use the absolute interpreter so GUI clients need no PATH.
- `docs/mcp.md`; `tests/test_mcp.py` (10 synthetic end-to-end tests driving the wire with dicts).

### Changed
- `openrad finish` now records finalization time, document paths and hashes in `session.json`.

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
