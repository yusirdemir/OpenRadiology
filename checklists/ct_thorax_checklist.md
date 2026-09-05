# CT thorax — systematic review checklist

Standards applied (full citations in `docs/references.md`): Fleischner 2017 nodule guideline and
measurement statement (MacMahon 2017; Bankier 2017), ACR Lung-RADS v2022, RECIST 1.1, IASLC lymph
node map (Rusch 2009) and TNM 9th edition descriptors, slab-MIP detection literature (Kawel 2009,
Jankowski 2019).

Use thin series (≤ 1.5 mm, ideally 1.0 mm) for lungs: native lung window, overlapping regional views,
plus an 8–10 mm sliding-slab MIP (`openrad ct-render --mip 10`), bounded to the slices that contain lung
(`--auto-z lung`; the scope is recorded in the ledger). Use a thick (3–5 mm) soft reconstruction for
mediastinum, hila, upper abdomen and bone; inspect thin soft-tissue sections only for targeted zoom and
calipers. Read every native slice of each pass, then coronal/sagittal views. Overview sampling alone is
not full coverage. Every positive item goes into the lesion ledger with series/instance/SOP/z and a
measurement.

## Technique block (from inventory, not from the report)
- Scanner, kVp/mAs, kernel, `PixelPaddingValue`, `GantryDetectorTilt`, contrast agent tag (absence of
  the tag ≠ non-contrast: judge from vessel density; a single HU value cannot establish the phase).
- Series used for each window; slice thickness vs measured spacing; coverage z-range (apices to adrenals?).
- Inspiration depth (tracheal shape, posterior dependent density), motion, beam hardening, noise.
- Tilted or sheared stacks are flagged by the loader; state whether reformats were de-sheared.

## Pass 1 — detection (contiguous, one anatomical system at a time)
1. **Airways**: trachea (calibre, wall, saber-sheath), carina, main/lobar/segmental bronchi;
   post-lobectomy: bronchial stump, stump staple line, stump air/fluid, stump thickening.
2. **Lungs** (each lobe/segment on lung window + MIP): nodules, masses, ground-glass, consolidation,
   atelectasis, emphysema (centrilobular/paraseptal/bullous — grade extent), bronchiectasis,
   bronchial wall thickening, tree-in-bud, septal lines, honeycombing/fibrosis, mosaic pattern,
   scarring, air trapping, cysts. Check the apices and the retrocardiac / paravertebral lung.
3. **Pleura & fissures**: effusion (measure max depth, free vs loculated), thickening, plaques,
   calcification, nodularity, pneumothorax, fissural nodules/fluid.
4. **Hila**: vessel vs node (soft window); hilar mass; post-lobectomy hilar clips/staples.
5. **Mediastinum & nodes**: IASLC stations 1–14 (table below); short axis; calcified vs soft;
   fat/necrosis. Report each node ≥ 10 mm short axis and any suspicious node of any size (round, loss of
   fatty hilum, clustering, growth). Thymus, oesophagus (wall, dilation, hiatal hernia), thyroid (lower
   poles), thoracic duct region.
6. **Heart & pericardium**: size (cardiothoracic ratio approximation), coronary calcification
   (per vessel: LM/LAD/LCx/RCA), valve calcification, pericardial effusion/thickening/calcification.
7. **Vessels**: aorta calibre (ascending > 40 mm = dilated), atherosclerosis, dissection flap,
   pulmonary artery calibre (main PA > 30 mm suggests hypertension), central filling defects if
   contrast, SVC, azygos.
8. **Chest wall & soft tissues**: breast tissue/gynecomastia, axillary nodes, supraclavicular nodes,
   subcutaneous lesions, port catheters, surgical drains, muscle atrophy, thoracotomy defects.
9. **Bones**: every vertebra (sclerotic/lytic lesion, compression, degenerative), sternum,
   every rib (fracture/resection/lesion), scapulae, clavicles, humeral heads. Use bone window
   AND soft window (marrow replacement).
10. **Upper abdomen included**: liver (lesions, steatosis), spleen, adrenals (nodule, thickening;
    ≤ 10 HU unenhanced = lipid-rich adenoma), kidneys (upper poles), pancreas tail, stomach, bowel;
    free air/fluid.
11. **Lower neck** if included: thyroid nodules, nodes, vessels.

### IASLC lymph node stations (Rusch 2009; CT landmarks after El-Sherief 2014)
| Station | Name | Key CT borders |
|---|---|---|
| 1R / 1L | Low cervical, supraclavicular, sternal notch | Upper: cricoid; lower: clavicles and manubrium top; midline of trachea separates R/L |
| 2R / 2L | Upper paratracheal | From manubrium top to the intersection of the left brachiocephalic vein with the trachea (2R) or the top of the aortic arch (2L); **left lateral tracheal wall is the R/L border** |
| 3a / 3p | Prevascular / retrotracheal | 3a anterior to the vessels; 3p posterior to the trachea |
| 4R / 4L | Lower paratracheal | 4R: from 2R lower border to the lower border of the azygos vein; 4L: from arch top to the upper rim of the left main pulmonary artery, medial to the ligamentum arteriosum |
| 5 | Subaortic (AP window) | Lateral to ligamentum arteriosum, below arch to upper rim of left PA |
| 6 | Para-aortic | Anterior and lateral to the ascending aorta and arch |
| 7 | Subcarinal | Carina to the upper border of the lower lobe bronchus (left) / bronchus intermedius lower border (right) |
| 8 | Paraoesophageal | Below station 7, adjacent to the oesophageal wall |
| 9 | Pulmonary ligament | Within the pulmonary ligament |
| 10 | Hilar | Adjacent to main bronchus and hilar vessels, below azygos (R) / upper rim of PA (L) |
| 11 | Interlobar | Between lobar bronchi (11s upper/intermedius, 11i intermedius/lower on the right) |
| 12–14 | Lobar, segmental, subsegmental | Adjacent to the respective bronchi |

## Pass 2 — characterisation (lesion ledger, one row per finding)
- Location: side, lobe, segment; central/peripheral; relation to pleura/fissure/vessel/bronchus.
- Series + instance + SOP + z of the largest cross-section; confirm on a second window.
- Size (Bankier 2017): long axis and perpendicular short axis in the same plane on the thin section
  where the lesion is largest (lung window for nodules, soft window for nodes/masses), rounded to
  whole mm. **Average diameter** for nodules < 10 mm; long axis for larger lesions and RECIST targets;
  **short axis** for lymph nodes. Craniocaudal extent from inspected boundaries; `openrad measure
  --auto3d` is exploratory. Verify that region growing does not leak into vessels/chest wall or touch the
  imposed box. Do not call threshold output an exact volume.
- Attenuation: solid / part-solid (state solid component size) / pure ground-glass / calcified
  (pattern: diffuse, central, laminated, popcorn = benign; stippled/eccentric = indeterminate) /
  fat (−40 to −120 HU = hamartoma) / cavitation (wall thickness) / air bronchogram / cystic.
- Margin: smooth, lobulated, spiculated, pleural tag, halo, satellite nodules.
- Vessel relationship: feeding vessel, vessel convergence, encasement/abutment (state the
  vessel), bronchus cut-off or narrowing.
- Chest wall/mediastinal contact: length of contact, fat plane preserved or not, rib destruction.
- Calcified nodule: grow at `--thr 150` (calcium only); the −300 HU default merges the adjacent vessel.
  Pleural-based mass: `--box` plus caliper axes; region growing leaks into the chest wall.
- Distinguish: end-on vessel (follow on consecutive slices/MIP), scar (linear, pleural-based),
  diaphragm/liver dome at the cardiophrenic angle (round on 3–6 axial slices; coronal zoom shows the dome),
  atelectasis (volume loss, bronchovascular crowding), mucus plug (branching, low density),
  granuloma (calcified), intrapulmonary node (perifissural, triangular/oval, < 1 cm), postoperative
  staple line artefact, suture granuloma.

## Staging limits
- Describe measured anatomy, suspicious contact/invasion and nodal location/short axis. Do not infer
  pathological stage from CT or equate pleural contact/effusion with invasion/malignant effusion.
- Formal staging is outside routine review. If explicitly requested, name the edition (TNM 9th from
  January 2025: N2a single station vs N2b multiple stations; M1c1/M1c2) and distinguish clinical
  descriptors from pathological findings.

## Mandatory evidence checks before finishing
- Right and left pleural space/costophrenic recess **separately**, including fissures: soft window,
  native slices and MPR. If a postoperative band might be fluid, sample its non-metal component with a
  HU ROI; use morphology and extent, not HU alone. Record pages and SOP references in each regional entry.
- Right kidney and left kidney separately over the included extent; do not let normal renal sinus fat
  distract from a cortical lesion. Incomplete poles get limited/not_covered, not "kidneys normal".
- Gastro-oesophageal junction and both retroareolar regions get targeted images and separate observations.
- Both lungs get exhaustive regional detection, including cardiac-border, juxtavascular and subpleural
  locations. Prior report knowledge never substitutes for searching other lobes.
- Recheck every negative that affects the impression; a completed checklist is not evidence of sensitivity.

## Postoperative (lobectomy) surveillance — mandatory items
- Which lobe is absent; compensatory hyperinflation/shift of remaining lobes; fissure position;
  mediastinal shift; residual pleural space (air/fluid), pleural thickening.
- Bronchial stump: staple line, any soft tissue mass > 1 cm at the stump, dehiscence (air leak).
- Staple/suture line in lung parenchyma: linear metallic density; any nodular soft tissue beside it.
- Ipsilateral hilum and mediastinal nodes: compare short axis with the prior study.
- Remaining ipsilateral lobes and contralateral lung: new nodules (any size is reportable after cancer
  surgery — do not apply incidental-nodule thresholds to a cancer patient).
- Pleura: nodularity, effusion; chest wall: thoracotomy/port sites, rib resection.
- Adrenals, liver, bones (sclerotic and lytic) for metastasis.
- Postoperative changes that mimic disease: stump granuloma, rounded atelectasis, fat pad,
  pericardial fat necrosis, seroma, thymic rebound after chemotherapy.

## Nodule follow-up wording
- Give exact prior/current sizes in mm and the interval in months. Growth = increase ≥ 2 mm in
  average diameter (Fleischner measurement statement) or ≥ 25 % volume; Lung-RADS uses > 1.5 mm in
  12 months for screening. Below that say "no measurable change".
- After chemotherapy: nodule shrinkage supports metastasis; stability is non-diagnostic.
- Never use the incidental-nodule (Fleischner 2017) management table or Lung-RADS categories for a
  patient with known lung cancer; cite them only for prior-to-diagnosis incidental or screening findings.
- RECIST 1.1 language (target/non-target, sum of diameters, PR/PD thresholds) only when the reader has
  the baseline, the same measurement plane and the trial context; otherwise report raw sizes.
