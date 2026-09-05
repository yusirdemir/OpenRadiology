# Blinded audit rubric

## Before reading images
This workflow is only for an explicitly requested audit, not ordinary `/openrad` use. Historical
lessons and old AI reviews disclose test findings. Hashes prove file integrity, not absence of prior exposure.
Declare exposure to those inputs; same-case post-fix rereads are regression tests, not fresh blinded tests.
1. Pick the study from the archive index (folder, date, modality only). Do **not** open the paired
   `.md` report, any derived clinical-status analysis, later reports that quote it, or prior report text.
2. Write a **leakage statement**: everything already known from mandatory context (reading guide
   patient summary, treatment plan quotes, pathology). Name the specific facts (e.g. "known RUL
   adenocarcinoma 5.5 cm, pT3N0"). Leakage lowers the evidential value of a match on those items —
   mark such matches `matched (leaked)`.
3. Decide whether prior DICOM may be used (it may, for a serial-comparison test; not for a
   first-read test) and record the decision.

## Reading
- Follow the modality checklist completely; write the review with the report template (`templates/<lang>/`).
- Copy the finalized professional review into the run cache; lock the snapshot with `blind_lock.py`.
  Record sha256 + time in the audit file, also in that cache. Keep the ordinary two-Markdown output standard.

## Unblinding
- Open the paired report only after the lock. Copy each report sentence that states a finding into
  the matching table (do not paraphrase away measurements).
- Status definitions:
  - `matched` — same finding, same side/lobe, size within ±2 mm (nodules) / ±20 % (masses, effusions).
  - `partial` — same finding but wrong segment, size outside tolerance, or character differs.
  - `missed` — in report, not in review (search the images afterwards and note whether visible in hindsight).
  - `overcalled` — in review, not in report; then verify on images: true positive not reported /
    equivocal / false positive.
- Clinical importance (RADPEER-like; Jackson 2009, see `docs/references.md`): **high** = would change staging/management (new nodule
  in cancer patient, node ≥1 cm, metastasis, PE, fracture, mass growth); **medium** = needs follow-up
  (indeterminate nodule, effusion, adrenal nodule); **low** = incidental/degenerative.
- Score separately: (a) impression/headline conclusions; (b) all reportable findings; (c) list of
  high-importance misses first. Report measurement deviations lesion by lesion.

## Interpretation of scores
- Official reports are a comparison reference, not infallible ground truth. Disagreement remains unresolved
  until independently adjudicated; self-confirmation on reread is not independent expert validation.
- Separate leaked findings, positive lesions, normal statements, misses and false positives. Nine matching
  report sentences is not 100% sensitivity. Do not claim clinical validation from a small same-patient set.

## Post-audit refactor (when skill updates are authorized)
- Every `missed` or `overcalled` item → a line in `checklists/lessons.md`: what was missed, why
  (not rendered / rendered but not seen / seen but misjudged), and the concrete fix (script default,
  checklist item, wording rule).
- Change the skill/scripts, then re-run the failing step on the same study to confirm the fix
  (e.g. a nodule now visible on the new MIP sheet) before moving to the next study.
