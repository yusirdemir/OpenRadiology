# Brain MRI (metastasis screening / oncologic) — review checklist

Standards applied (citations in `docs/references.md`): consensus brain-metastasis imaging protocol
(Kaufmann 2020), RANO-BM response criteria (Lin 2015), RANO 2.0 (Wen 2023), Fazekas scale.

## Protocol adequacy (Kaufmann 2020 "minimum standard")
| Required | Parameters | Present? |
|---|---|---|
| 3D T1 IR-GRE (MPRAGE/BRAVO/TFE) **pre-contrast** | ≤ 1.5 mm, ideally 1 mm isotropic | |
| 3D T1 IR-GRE **post-contrast**, parameter-matched | same as pre; ≥ 4 min after 0.1 mmol/kg gadolinium | |
| Axial 2D T2 TSE | ≤ 4 mm | |
| Axial 2D or 3D T2 FLAIR | ≤ 4 mm (2D) / 1 mm (3D) | |
| DWI, 3 directions, b0 + b1000, ADC | ≤ 4 mm | |
| Post-contrast 2D T1 SE (optional, conspicuity) | ≤ 4 mm | |
| SWI (recommended for haemorrhage) | thin | |

Record each missing element in `technique_sequences_contrast` and in limitations.

## Sequence identification (inventory `guess` column; verify visually)
| Appearance | Typical parameters | Role |
|---|---|---|
| T1 MPRAGE / 3D GRE | TI 900–1100, TE < 5, thin 1 mm, SAG acquisition + AX/COR reformats | anatomy; **pre vs post contrast** by acquisition time and vessel/choroid brightness |
| T1 post-contrast | same as above acquired later (after contrast series), bright dural sinuses, choroid plexus, pituitary, nasal mucosa | metastasis detection (mandatory) |
| T2 TSE | TR > 3000, TE 80–120 | edema, cysts, mass effect |
| T2 FLAIR (3D SPACE) | TI ~2000–2500, TE > 300 (3D) | edema, white matter disease, leptomeningeal FLAIR hyperintensity |
| DWI trace b1000 + ADC | EPI, DIFFUSION in ImageType | infarct, abscess, cellular tumour, epidermoid |
| SWI + phase + minIP | GRE TE ~40 (3T ~20), thin | microbleeds, haemorrhagic metastasis, calcification (phase) |
| T1 GRE localizer | few thick slices | ignore |

- A later MPRAGE is only a candidate post-contrast sequence. Confirm available administration metadata
  (`ContrastBolusAgent`, series times), acquisition ordering and paired anatomical enhancement;
  brightness alone is confounded by window/scale. Pre/post side-by-side rendering
  (`openrad mr-render --pair`) matches positions in patient coordinates only; it does not register motion.
- Without confirmed post-contrast T1, say "metastasis screening sensitivity substantially reduced".

## Systematic read (post-contrast T1 thin AX or reformats, then FLAIR, DWI, SWI, T2)
Read every native slice of each diagnostically distinct sequence; the renderer's overview mode is not a
negative screen. Record unsupported mixed echo/b-value/multiframe series rather than silently skipping them.
1. Supratentorial parenchyma lobe by lobe (frontal, parietal, temporal, occipital, insula),
   grey-white junction especially (metastases favour it); basal ganglia/thalami.
2. Infratentorial: cerebellar hemispheres, vermis, brainstem (midbrain, pons, medulla).
3. Ventricles: size, symmetry, ependymal enhancement, choroid plexus, septum.
4. Extra-axial: dura (thickening/nodules), leptomeninges (sulcal enhancement — compare FLAIR),
   subdural/epidural collections, falx/tentorium.
5. Sella/pituitary, cavernous sinuses, pineal region, optic pathways.
6. Skull base, calvarium and marrow (T1 hypointense replacement, enhancement), scalp.
7. Orbits (globes, optic nerves, extraocular muscles, lacrimal glands), paranasal sinuses,
   mastoids, nasopharynx, upper cervical spine and cord if included.
8. Vessels: flow voids in ICA/basilar/MCA; venous sinuses on post-contrast (thrombus = filling defect).
9. Incidental: small-vessel ischaemic change (Fazekas 0–3, periventricular and deep separately),
   lacunes, atrophy (global/hippocampal), old haemorrhage, developmental venous anomaly, cavernoma,
   arachnoid cyst, pineal cyst, enlarged perivascular spaces, mega cisterna magna, empty sella,
   mastoid fluid, sinus disease.

## Lesion ledger (per lesion; RANO-BM conventions)
- Series/instance/SOP; location (lobe, gyrus, depth); size in mm: **longest diameter** on post-contrast
  T1 plus perpendicular axis in the same plane; measurable if ≥ 10 mm (slice ≤ 1.5 mm) or ≥ 5 mm on
  1 mm isotropic 3D; up to 5 target lesions in a trial context.
- Enhancement pattern (solid/ring/nodular), T2/FLAIR signal and edema extent, DWI/ADC, SWI blooming,
  mass effect/midline shift (mm), hydrocephalus.
- Number of lesions (1, 2–4, 5–10, > 10) matters for management: count explicitly.
- Comparison wording: state prior and current longest diameters; RANO-BM progression = ≥ 20 % increase in
  the sum of longest diameters **and** ≥ 5 mm absolute, or a new lesion; do not label response formally
  without corticosteroid and clinical status information.

## Pitfalls
- Enhancing vessels in sulci on 3D T1 look like small nodules → follow on consecutive slices and both
  reformats; a true metastasis is round on all planes and often has FLAIR edema (not for tiny ones).
- Motion/pulsation artefacts in the posterior fossa; susceptibility at the skull base on SWI/DWI.
- Choroid plexus, pituitary/infundibulum, pineal, dural sinuses, nasal mucosa enhance normally.
- Cortical laminar necrosis / old haemorrhage bright on T1 pre-contrast: compare pre and post.
- Per-slice normalisation can mimic enhancement; the renderer windows once per series for this reason.
