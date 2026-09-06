---
name: openrad
description: Traceable, image-first preliminary radiology review of DICOM studies (thoracic CT, whole-body FDG PET/CT, brain MRI) for single reads or chronological multi-study comparison. Uses the OpenRadiology CLI to render, measure, de-identify and audit; produces a structured professional report and a patient companion guide. Use when a user points at DICOM folders and asks for a read, a comparison, a nodule/lesion measurement, de-identification, or a plain-language explanation of imaging.
---

# OpenRadiology agent skill

You are operating a deterministic imaging workbench, not a diagnostic model. The CLI renders
slices faithfully, measures with native calibration and refuses to finish unless every claim
cites a slice you actually looked at. Your job is to look, systematically, and to write what
you saw with its coordinates. This skill works identically in Claude Code, OpenAI Codex, Cursor,
Google Antigravity or any agent that can run shell commands and view PNG files.

## The three rules (non-negotiable)

1. **No look, no claim.** A finding, a negative ("no effusion") or a comparison verdict may only be
   written after you opened the rendered page that shows it. Marking a page `reviewed: true` without
   opening it is falsification; `openrad check` cannot detect it, so you must not do it.
2. **Every statement has an address.** Each region entry and each claim carries
   `StudyInstanceUID`, `SeriesInstanceUID`, `SOPInstanceUID` and native `row`, `col`, plus the absolute
   path of the inspected PNG. Coordinates come from the zoom grid or the measurement tool, never from
   estimation.
3. **Numbers come from the tool.** Sizes, HU and SUV values are pasted from `openrad measure --output`
   evidence files (write-once, SHA-256 hashed). Never type a measurement from visual impression.

If any rule cannot be satisfied for a region, the region is `limited` or `not_covered` with the reason
stated. Silence is never an option; an unexplained region blocks `finish`.

## Invocation

```text
/openrad <study_folder>
/openrad <study_folder_1> <study_folder_2> [<study_folder_3> ...]
/openrad <study_folder> --lang tr
```

- **Single study:** review only that examination without prior assumptions.
- **Multiple studies:** chronological order comes from DICOM `StudyDate`/`StudyTime` (never from folder
  names). Interval change verdicts (`new`, `increased`, `decreased`, `stable`, `resolved`,
  `indeterminate`, `not_comparable`) need evidence from **both** endpoints.
- Language defaults to English (`__report.md` / `__guide.md`). `--lang <locale>`, `OPENRAD_LANG=<locale>`
  or `general.lang` in `.openrad.toml` selects any locale shipped in `openrad/locales/` (file suffixes,
  section titles and region names come from that locale file).

## Two ways to drive the engine
- **Shell** (Claude Code, Codex, Cursor agent mode): the `openrad` commands below.
- **MCP** (Claude Desktop, Cursor, Windsurf, any MCP client): `openrad mcp --install <client> --write`; the
  tools mirror the commands one to one (`render_ct`, `measure`, `page_view`, `session_*`). Over MCP the
  only way to mark a page reviewed is `page_view`, which hands you the image. See `docs/mcp.md`.

## Before the first command: environment and configuration

```bash
openrad doctor            # interpreter, decoders, config sources, writable paths; exit 1 = fix first
openrad config            # effective settings with their source (default / file / env / cli)
openrad config --init     # writes a commented .openrad.toml when a project wants its own defaults
```

Precedence is flags > `OPENRAD_*` environment > `.openrad.toml` (or `[tool.openrad]` in
`pyproject.toml`) > `~/.config/openrad/config.toml` > defaults. Settings that change what you see:

| Setting | Env | Meaning |
|---|---|---|
| `general.lang` | `OPENRAD_LANG` | report language (`en` default; any `openrad/locales/<lang>.json`) |
| `paths.studies_root` / `cache_dir` / `output_dir` | `OPENRAD_STUDIES_ROOT` … | where studies, evidence and the two documents live (defaults `<repo>/DCIM`, `<repo>/.cache/create-report`, `<repo>/reports`) |
| `render.vision_profile` / `max_side` | `OPENRAD_VISION_PROFILE`, `OPENRAD_MAX_SIDE` | longest sheet edge your vision model accepts without down-sampling (claude 1568, gpt 2048, gemini 3072, local 1024) |
| `render.grid` | `OPENRAD_GRID` | tiles per sheet: `2x2` detail mode, `3x3` fast systematic scan (9 native 512 px slices in 1542 px), `auto` = largest that fits |
| `render.mip_mm`, `render.step_mm` | `OPENRAD_MIP_MM`, `OPENRAD_STEP_MM` | systematic-read defaults (10 mm slab MIP, every native slice) |
| `measure.convention` | `OPENRAD_CONVENTION` | which single diameter is *reported* by `--auto`: `fleischner` (average < 10 mm) or `recist` (long axis); raw axes are always printed |
| `pet.suv_threshold`, `pet.uptake_window_min` | `OPENRAD_SUV_THRESHOLD` … | hotspot detection aid and the interval that triggers an uptake warning |
| `privacy.redact_identifiers` | `OPENRAD_REDACT_IDENTIFIERS` | inventories never carry PatientID/Accession/Institution (default on) |
| `privacy.pseudonym_salt` | `OPENRAD_PSEUDONYM_SALT` | secret for `openrad anonymize`; set in the environment, never in a committed file |

Never edit a config file to make a check pass. Exit codes: 0 ok, 2 usage, 3 input, 4 geometry,
5 validation, 6 quantitation, 7 integrity. On a non-zero exit read `stderr`, fix the cause and re-run;
never work around a refusal by editing the session by hand.

## Workflow

### Step 0 — Read the checklist for the modality
- `checklists/ct_thorax_checklist.md` (IASLC stations, Fleischner/Bankier measurement, postoperative items)
- `checklists/pet_ct_checklist.md` (uptake time, decay correction, physiologic uptake, PERCIST limits)
- `checklists/brain_mri_checklist.md` (Kaufmann protocol adequacy, RANO-BM ledger, pre/post T1)
- `checklists/lessons.md` (known failure modes). `docs/references.md` holds the citations.

### Step 1 — Create the session
```bash
openrad prepare <folder> [<folder2> ...] --repo <repo_root> [--lang en|tr] [--studies-root DIR] [--identity-source FILE]
```
Validates one StudyInstanceUID per folder, consistent patient identity (anonymized exports need a
documented `--identity-source`), acquisition chronology, and hashes every source file and the toolchain.
Prints the session path; keep it. If the data are not yet de-identified and must leave the machine,
run `openrad anonymize <in> <out> --salt "$OPENRAD_PSEUDONYM_SALT"` first and review the output tree.

### Step 2 — Inventory and series selection
```bash
openrad inventory <folder> --output <work_dir>/inventory [--json]
```
Read matrix, spacing, kernel, contrast tag, multiframe flag, tilt, PET units/decay/timing, MR sequence
guesses. In the session set each series `disposition`:
- `read` for the diagnostic volumes (thin axial CT ≤ 1.5 mm; PET BQML AC series + its CT; each
  diagnostically distinct MR sequence), with `geometry_checked: true` after the renderer succeeded
  and `required_passes` exactly as rendered.
- `excluded` (with reason) for scanner reformats, dose sheets, localizers, duplicates.
- `unsupported` (with reason) for multiframe or irregular geometry the loader refused (exit 4).

### Step 3 — Render systematic sheets (all native slices)
Choose the grid for the pass: `--grid 3x3` for the first systematic scan, `--grid 2x2` for detail
passes and for any region you intend to characterise. The engine never down-samples a slice; if the
grid does not fit the vision budget it shrinks and says so on `stderr`.

Thoracic CT:
```bash
openrad ct-render <folder> --series <thin_lung> --windows lung --step 1 --mip 10 --mpr --auto-z lung --grid 3x3 --output <work_dir>/lung
openrad ct-render <folder> --series <thick_soft> --windows soft,bone --step 1 --mpr --grid 3x3 --output <work_dir>/soft
```
`required_passes` → `["lung:native", "lung:mip"]` for the thin lung series and `["soft:native", "bone:native"]`
for the soft-tissue series. Series choice (measured on a 385-slice, 1 mm study: 166 → ≈85 sheets, no
finding lost):
- **Lungs**: the thinnest axial series (≤ 1.5 mm). `--auto-z lung` limits the lung and MIP passes to the
  slices that actually contain aerated lung (neck and abdomen slices carry no lung); the renderer writes the
  skipped SOPs and the rule into `render_index.json`, `register` copies them to `series.pass_scope`, `check`
  accepts them and `finish` prints the scope in the technique block. The skipped slices stay covered by the
  soft-tissue pass.
- **Mediastinum, hila, upper abdomen, bones**: a thick axial reconstruction (3–5 mm, soft kernel) when the
  study has one. A 1 mm soft-tissue series at 512 px per tile is too noisy to judge nodes or renal lesions
  and costs 4–5× the pages; keep the thin soft series for `zoom`/`measure` only and mark it `excluded`
  with that reason (or `read` only if you actually open all its pages). If no thick series exists, render
  the thin soft series with `--step 3`.
- MPR positions are spread over the body bounding box (never the empty field of view).
- Vision budget: open at most ~20 sheets per request; a larger batch is silently truncated by the client
  ("media removed"), and a page that was never displayed must not be marked reviewed.

PET/CT:
```bash
openrad pet-render <folder> --pt <PT> --ct <CT> [--weight <kg>] --output <work_dir>/pet
```
`required_passes` for the PT series → `["pet:native", "pet:fused"]`; for the CT → as above.
Copy `suv_info.json` warnings into limitations. A missing weight is a hard stop (exit 6): ask for a
documented value; never guess.

Brain MRI:
```bash
openrad mr-render <folder> --series <T1post_ax> --step 1 --tile 478 --grid 3x3 --output <work_dir>/t1post
openrad mr-render <folder> --series <FLAIR_ax> --step 1 --grid 3x3 --output <work_dir>/flair
openrad mr-render <folder> --series <T2> <DWI> <ADC> <SWI> <SWI_minIP> <T1pre_ax> --step 1 --grid auto --output <work_dir>/mr
openrad mr-render <folder> --pair <T1pre> <T1post> --step 1 --output <work_dir>/mr_pair    # only for a candidate
```
`required_passes` → `["mr:native"]` per read series. Read 3D T1/FLAIR on their axial reformat series
(every slice) and mark the native sagittal volume `excluded` with that reason; upscale a small-matrix
post-contrast T1 (`--tile 478`) because 2–3 mm enhancing foci are invisible at 239 px. `zoom` works on
oblique MR axially (percentile window); reformat zooms need the canonical grid. Measured cost: a full
seven-sequence protocol is ~70 sheets.

Gantry-tilted head CT (exit 4 with "Gantry-tilted"): re-run with `--allow-tilt`; state in technique
that reformats were de-sheared and 3D extents were not measured.

### Step 4 — Register, then look
```bash
openrad register <session.json> --directory <work_dir>
```
Open every registered PNG. Read superior → inferior, one anatomical system at a time, following the
checklist. Reformat tiles carry a millimetre depth ruler on the left edge and every tile has a scale
bar; orientation letters are drawn from the direction cosines. Only after opening a page set
`"reviewed": true`. Write region entries as you go; do not batch "normal" at the end.

### Step 5 — Resolve every candidate with zoom and measurement
For each candidate lesion, node, stump, effusion, hotspot:
```bash
openrad zoom <folder> --series <S> --instance <N> --center <row,col> --context 2 --scale 4 --output <work_dir>/zoom/<id>_ax.png
openrad zoom <folder> --series <S> --instance <N> --center <row,col> --plane cor --output <work_dir>/zoom/<id>_cor.png
openrad measure <folder> --series <S> --instance <N> --points <r1,c1> <r2,c2> --output <work_dir>/meas/<id>_long.txt
openrad measure <folder> --series <S> --instance <N> --roi <r,c,radius> --output <work_dir>/meas/<id>_roi.txt
openrad measure <folder> --series <S> --instance <N> --auto <r,c> --lesion-type nodule|node|mass --output <work_dir>/meas/<id>_auto.txt
```
- Vessel vs nodule: follow on consecutive slices and in a second plane; a vessel elongates, a nodule stays round.
- `--auto` prints long axis, perpendicular short axis, average and a **reported diameter** chosen by the
  configured convention and `--lesion-type` (node → short axis). Quote the reported value and name
  the rule in the measurement `method`.
- Copy the printed `sha256=` into `measurements[].sha256` and the file path into `evidence_file`.
- Region growing (`--auto`, `--auto3d`) is exploratory; when it warns about leaks or box contact,
  say so and prefer caliper points. A pleural-based mass leaks into the chest wall at `--thr -300`: use
  `--box` and report caliper axes. A calcified nodule leaks into the adjacent vessel at `--thr -300`:
  grow with `--thr 150` (calcium only) and quote the threshold in `method`.
- Coordinates: read `row,col` off the `zoom` grid, never off a contact sheet by eye. If you must start
  from a 3x3 sheet, `native = sheet_px − 514 × tile_index` (512 px tile + 2 px gutter) per axis, then
  confirm on the zoom grid; a seed that lands outside the lesion aborts with "seed value … outside".
- Diaphragm dome, cardiophrenic fat and vessel turns mimic nodules on axial lung tiles: one coronal
  `zoom --plane cor` settles it before any measurement.
- HU statistics need a low-noise slice: sample ROI/profile on the thick series (`--series <thick>`), not
  on the 1 mm soft series; state the series in `method`.

### Step 6 — Fill the ledger
Follow `templates/<lang>/report_template.md`; the machine-readable contract is
`openrad/schema/session.schema.json`. For every region: `status`, `text`, `explanation`, `refs`,
`pages`. For every claim: `priority`, `confidence`, `refs`, `pages`, `patient` (four fields),
`measurements`, and in comparison mode `comparison` + `timeline` covering every study. Fill `reader`,
`context_disclosure`, `blind_status`, `limitations`, `patient_context`, `patient_limitations`,
optional `recommendations` and `glossary`, then `reading_complete: true`.

### Step 7 — Check and finish
```bash
openrad check <session.json> --json      # exit 5 lists every unmet requirement
openrad finish <session.json> [--lang en|tr] [--output-dir DIR]
```
Read both generated documents end to end. Dates, sides, sizes, negations and uncertainty must match
between the professional report and the patient guide.

## Writing standards
- Professional report: RadLex terms, laterality and lobe/segment or IASLC station, measurement plane,
  scoped negatives ("within the covered slices … not identified on this preliminary review").
- No staging, no Lung-RADS/Fleischner category, no PERCIST/RECIST/RANO response label unless the
  user explicitly requested it and the prerequisites in the checklist are met; then name the edition.
- Patient guide: calm, concrete, accurate; explain the term, then the finding; separate certain from
  uncertain; one answerable question per claim; never a prognosis or a treatment plan.

## De-identification (when data must leave the machine)
```bash
export OPENRAD_PSEUDONYM_SALT='<long secret kept outside the repo>'
openrad anonymize <study_or_archive> <empty_out_dir> [--json] [--map key.json]
```
PS3.15 basic profile with shifted dates (times kept, so SUV and chronology survive), retained patient
characteristics (weight/sex for SUV), safe private tags (Philips PET scale factors) and cleaned
descriptors. Pixel data are verified unchanged. The same salt maps the same patient and objects
identically across exports, so longitudinal comparison still works. Never commit the salt or a map.

## Blinded audits
Only when explicitly requested: follow `checklists/blind_audit_rubric.md`, seal the review with
`openrad lock <review.md>` before opening any reference report, and score with the RADPEER-like
table. A hash proves integrity, not absence of prior exposure; declare leakage.

## Safety notice
OpenRadiology is an AI decision-support and educational tool. It does not provide certified medical
diagnoses and does not replace evaluation by a board-certified radiologist or the treating physician.
A negative preliminary review never excludes disease.
