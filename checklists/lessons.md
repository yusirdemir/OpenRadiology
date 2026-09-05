# Technical lessons and validation limits

This file contains reusable failure modes, not patient findings or a test answer key.
Case-specific observations, source-report comparisons and images belong in the run cache.

## Rendering and reading
- A wide contact sheet can conceal small findings even when saved at native resolution. Default to at most
  four tiles; use overlapping regional crops and inspect all native slices. Rendering is not reading.
- Search both lungs and every included organ. A targeted successful search after receiving a location hint
  does not establish general detection sensitivity.
- Check each pleural recess/fissure, each kidney, gastro-oesophageal junction and retroareolar tissue.
  Postoperative bands can contain fluid; metallic staple density and non-metal components must be separated.
- Cardiac contours, vessel turns and diaphragm can resemble nodules. Follow consecutive slices and a
  second plane before deciding. There is no rule that a real lesion must be round in every plane.
- An 8–10 mm slab MIP raises nodule detection but hides ground-glass and merges adjacent vessels;
  always confirm on the native slice and never measure on the MIP.

## Geometry
- Draw orientation from direction cosines. CT/PET reformat geometry supports canonical LPS axial
  stacks; reject unsupported geometry instead of inventing correct-looking R/L markers.
- A gantry-tilted head CT shears the stack. The loader now measures the shear and compares it with the
  `GantryDetectorTilt` tag. In-plane measurements stay valid; slab MIP/MPR use a per-slice integer
  de-shear; 3D region growing and z-extent are refused because their neighbourhoods would be wrong.
- `SliceThickness` and `SpacingBetweenSlices` are vendor-filled and often disagree with the measured
  IPP increment. Geometry uses the measured increment only.
- SeriesNumber is not a unique identifier. Use SeriesInstanceUID; duplicate positions can represent
  multiple b-values/echoes. Split explicit acquisition dimensions before stacking, never drop duplicates.
- Anonymized exports can replace patient identity with a different accession per examination. Use an
  explicit same-patient mapping, record its source, and preserve chronology from acquisition headers.

## Measurement
- Region growing can leak into vessels/chest wall or be truncated by its box. Check boundaries and regard
  threshold-derived volumes/axes as exploratory. PixelSpacing calibrates distance, not lesion recognition.
- Distances are between pixel centres; add one pixel when an edge-to-edge caliper reading is needed
  (`--auto` already does this for Feret axes).
- Tool decimals do not prove accuracy; inter-reader variability of caliper measurements is about ±1.5 mm
  on thin sections (Bankier 2017), so a 1 mm "change" is noise.

## PET
- Keep RGB when composing PET/CT fusion pages. Check frame-of-reference, common coverage and respiratory
  alignment. Missing weight/timing/units invalidates quantitative SUV; do not substitute an arbitrary weight.
- `SeriesTime` can be later than the first `AcquisitionTime` (GE). The QIBA rule (earliest acquisition)
  is applied and flagged; a 10 min error in uptake time changes SUV by about 6 %.
- The PET native stack must be reviewed beyond thresholded hotspots and outside CT overlap. A 2D mean is
  not SUVpeak/SULpeak. Do not automatically assign formal response classifications.

## MR
- MR sequence names are hints. Per-slice normalization can mimic enhancement; use consistent series windows,
  compare anatomy, and check contrast metadata. Side-by-side images are not automatically registered.

## Evidence and validation
- A report hash only detects later edits. It does not establish a clean model context, independent
  adjudication, zero hallucinations or a clinical sensitivity percentage.
- Both output documents derive from one ledger. The patient explanation must preserve dates, measurements,
  negation and uncertainty, and explain every region, not just the main finding.
- Any new technical change needs engineering tests and then real-image regression before claiming it was
  validated. Existing synthetic tests cover selected invariants only; clinical sensitivity remains unknown.

Validation handoff: run `pytest`, then perform image-first reviews and requested report audits with
`checklists/blind_audit_rubric.md`. Learned changes may improve known failures; same-case rereading is
not an independent blind test.
