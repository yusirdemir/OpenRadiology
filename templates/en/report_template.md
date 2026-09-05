# OpenRadiology report and evidence specification (English)

One ordinary invocation produces two Markdown documents (`<stem>__report.md` and
`<stem>__guide.md`) in the output directory (default `reports/`, override with
`paths.output_dir` or `openrad finish --output-dir`). Inventory, PNG sheets, render index, session JSON and measurement
logs stay in the ignored run cache (`.cache/create-report/run-*/`). The final documents are
generated from the session ledger only; never write interpretation into dated source records.

## 1. Professional report structure (RSNA/ESR structured reporting order)

`openrad finish` emits the sections below in this order. The reviewer fills the session; the
generator never invents text.

| Section | Session source | Content rules |
|---|---|---|
| Header banner | fixed | "AI preliminary review — not a physician-approved official report"; reader, blinding status, context disclosure |
| Examinations and clinical information | `studies[]`, `clinical_context` | Acquisition dates from DICOM headers, modalities, folder; clinical question if provided |
| Technique and comparison | regions `*:technique*`, `comparison.reason` | Scanner, kernel, slice thickness/spacing, contrast, coverage, SUV validity (PET), protocol adequacy (MR); comparison method or "no prior compared" |
| Findings | `studies[].regions` | One line per anatomical region in checklist order; scoped negatives ("Within the covered slices, no ... was identified on this preliminary review"); **bold** for finding/limited |
| Impression | `claims[]` | Numbered, most important first; measurement lines with method; comparison status per claim |
| Change over time | `claims[].timeline` | Only in comparison mode; one line per study date |
| Recommendations | `recommendations[]` | Optional; guideline-based, never a treatment schedule |
| Limitations | `limitations[]`, identity note | Coverage, sensitivity, technical and contextual limits |
| Image sources | `claims[].refs` | Study/Series/SOP UIDs with row/col for every claim; input SHA-256 fingerprint |

### Terminology
Use RadLex-conformant terms (radlex.org): "nodule" (< 30 mm), "mass" (≥ 30 mm), "ground-glass
opacity", "consolidation", "lymph node short axis", "enhancement". State laterality, lobe/segment or
IASLC station, and the measurement plane. Avoid hedges without content ("cannot be excluded")
unless paired with what would resolve them.

## 2. Session edits

Every region needs `text` (professional) and `explanation` (plain language): a complete account of
its finding, normal appearance or limitation, explaining terminology without adding new claims.
Each `studies[].regions` entry needs `status` (`finding`, `no_finding`, `limited`, `not_covered`).
`finding`/`no_finding` additionally require `refs` (actual DICOM Study/Series/SOP UIDs) and `pages`
(absolute paths of actually inspected registered PNGs). `limited`/`not_covered` need explicit reasons
and must also appear in `limitations`. No blanket normal statements.

Every claim needs `priority` (`important`/`routine`/`incidental`), `confidence`
(`low`/`moderate`/`high`) and `patient: {meaning, importance, uncertainty, discuss_with_doctor}`.
For comparisons, `timeline` has one entry per supplied StudyUID:
`{study_uid, status, text, explanation}` with status `present`/`not_seen`/`not_covered`/`indeterminate`;
`present`/`not_seen` also require `refs` on that timepoint. Cite per-date source refs in the claim; do
not infer an intermediate timepoint from its neighbours. Fill `patient_context` and
`patient_limitations`. Optional `glossary` contains `{term, explanation}`, optional `recommendations`
is a list of strings. Quantities are rendered from the same records in both documents.

### Evidence schema (example, **not patient data**)

```json
{
  "id": "L1",
  "text": "Solid, smoothly marginated 6 mm nodule in the right lower lobe, posterior basal segment, subpleural; no calcification or fat.",
  "confidence": "high",
  "priority": "important",
  "refs": [{"study_uid": "ACTUAL", "series_uid": "ACTUAL", "sop_uid": "ACTUAL", "row": 245, "col": 312}],
  "pages": ["ABSOLUTE_INSPECTED_PNG_PATH"],
  "patient": {
    "meaning": "A nodule is a small round spot in the lung, here about the size of a pea.",
    "importance": "Small solid nodules are usually benign, but in someone with a cancer history a new one is watched closely.",
    "uncertainty": "The scan cannot tell what the nodule is made of; only change over time or tissue sampling can.",
    "discuss_with_doctor": "Ask whether a follow-up CT in a few months or a PET/CT is the right next step."
  },
  "measurements": [{
    "value": 6, "unit": "mm",
    "method": "Average of long (7 mm) and perpendicular short axis (5 mm) on the 1 mm lung-window slice of maximal size; native PixelSpacing Euclidean distance",
    "evidence_file": "ABSOLUTE_LOG_PATH", "sha256": "ACTUAL_SHA256",
    "ref": {"study_uid": "ACTUAL", "series_uid": "ACTUAL", "sop_uid": "ACTUAL", "row": 245, "col": 312}
  }],
  "comparison": {"status": "new", "from_study_uid": "PRIOR_STUDY_UID", "to_study_uid": "CURRENT_STUDY_UID",
                 "reason": "Not present at the corresponding location on the prior thin-section series."}
}
```

`comparison` and a `timeline` entry for every supplied study are required for multiple studies.
Allowed statuses: `new`, `increased`, `decreased`, `stable`, `resolved`, `indeterminate`,
`not_comparable`. Change claims declare `from_study_uid` and `to_study_uid` and need both endpoints'
source evidence, including the old location for `new` and the current location for `resolved`.
Every measurement requires actual tool output (`openrad measure --output`), method, unit, source
date and coordinates. HU is calibrated CT, SUVbw is body-weight PET, MR signal is not HU. Tool decimals
do not prove measurement accuracy. Do not substitute 2D neighbourhood means for SUVpeak/SULpeak.

Set read CT/MR/PET series `required_passes` exactly as routed in `SKILL.md` (for example
`["lung:native", "lung:mip", "soft:native", "bone:native"]`, `["mr:native"]`, `["pet:native", "pet:fused"]`).
The renderer records covered SOPs; all source slices in each required pass must be on reviewed pages.
Auxiliary MPR/zoom page entries need valid study/series/purpose; never invent coverage.
`geometry_checked` means the loader succeeded on that series.

## 3. Patient and family companion guide

The guide is generated from the same ledger and must satisfy all of the following:
1. **Accuracy first.** Every date, side, size and negation matches the professional report; no new
   claims, no softened findings.
2. **Calm, concrete language.** Short sentences, everyday words, one idea each. Compare sizes to
   familiar objects (a grain of rice ≈ 5 mm, a pea ≈ 8 mm, a grape ≈ 15 mm) after the millimetre value.
3. **Explain the term, then the finding.** "A nodule is ... In this scan, one nodule of 6 mm was seen ..."
4. **Separate certain from uncertain.** State what the image shows, what it cannot show, and what
   would settle the question (follow-up interval, biopsy, another modality).
5. **Reassure without promising.** "Most nodules of this size are harmless" is acceptable; "this is
   nothing" is not. Never state prognosis or treatment.
6. **Questions for the physician.** Each claim contributes one specific, answerable question; the
   guide also prints a consolidated list to take to the appointment.
7. **Every region is explained**, including normal ones, so silence is never mistaken for omission.
8. **Closing statement** that not seeing something does not exclude it, and that the physician who
   reviews the images and the official report make the final assessment.

## 4. Final check

Read the generated report: correct acquisition dates/order, side, source IDs, units, confidence and
negatives. Resolve contradictions such as fluid in one section and "effusion absent" in another.
Attributions to an official report require actually reading it under an authorized non-blind/audit
task. No unsupported treatment schedules or pathological stages inferred solely from images.
Structural validation cannot detect every semantic error; reread images when uncertain.

Audit snapshots and findings remain in the run cache. They are not clinical ground truth or clean
inputs to another same-case blinded test.
