# FDG PET/CT — review checklist

Standards applied (citations in `docs/references.md`): EANM procedure guideline v2.0 (Boellaard 2015)
and v3.0 (2025), RSNA QIBA FDG-PET/CT Profile SUV pseudo-code, PERCIST 1.0 (Wahl 2009; O 2016),
DICOM PS3.3 C.8.9.1 PET Image Module.

## Technique block (from headers: `openrad inventory`, `openrad pet-render` → `suv_info.json`)
- Radiopharmaceutical, injected dose (MBq), injection → scan start interval (uptake time; EANM target
  55–75 min, tolerance 45–90), `DecayCorrection` (START/ADMIN), `Units` (BQML; GML = vendor SUV;
  CNTS only with Philips private scale factors), scan-start source
  (`DecayCorrectionDateTime` > `SeriesTime` > earliest `AcquisitionTime`), reconstruction
  (e.g. PSF+TOF), filter, PET slice thickness, CT type (low-dose attenuation-correction CT vs diagnostic
  contrast CT), coverage (vertex/skull base to thigh).
- Weight source for SUV (header or documented value); state that SUV precision depends on it. Height and
  sex allow an auxiliary SUL factor (Janmahasatian formula); SUL is only reported alongside SUVbw.
- Blood glucose: if not available in inspected inputs, state unknown (do not assume < 11 mmol/L).
- `suv_info.json` warnings (uptake time out of window, acquisition before series time) go verbatim into
  the limitations section.

## Reference activity (measure with `openrad measure --roi` on the PET series)
- Liver: document ROI geometry and SUVbw mean/SD. PERCIST prescribes a 3 cm sphere in the right lobe;
  a 2D ROI is not that sphere and is not a formal PERCIST input.
- Mediastinal blood pool: descending aorta ROI, SUVmean (typical 1.5–2.5). PERCIST uses a 1 cm × 2 cm
  cylinder when the liver is diseased.
- Describe uptake relative to both; do not apply a disease-specific scoring system (Deauville, Hopkins)
  to an unsupported setting.

## Detection order
1. Rotating MIP (`mip_rot.png`) — list every focus that stands out from background; note laterality on the
   0° (anterior) view: image left = patient right.
2. Hotspot table (`hotspots.md`) — thresholded components are a detection aid, not a lesion list.
   For each: open `hot_XX_*.png` and decide **physiologic / benign / suspicious / equivocal** on the CT.
3. Every native PET slice (`pet_native_NN.png`, coverage pass `pet:native`) plus full fused axial
   coverage where CT overlaps (`pet:fused`). Inspect regions outside CT coverage separately; never
   silently substitute the nearest CT edge for an uncovered body region.
4. Then read the CT component with the CT checklist (nodules below PET resolution, ~< 8 mm, are
   frequently FDG-negative: report them from CT).

## Physiologic and benign uptake to recognise (never call these disease without a CT correlate)
- Brain (cortex, basal ganglia), Waldeyer ring/tonsils, salivary glands, vocal cords (symmetric),
  thyroid diffuse (thyroiditis), myocardium (variable, LV), thymus (chevron shape, young/rebound after
  chemotherapy), breast (glandular), liver/spleen (diffuse), stomach/bowel (segmental, colon),
  kidneys/ureters/bladder (excretion), testes/uterus/ovaries (cycle), bone marrow diffusely
  (after chemotherapy or G-CSF: ≥ liver, uniform), skeletal muscle (exercise/tension, symmetric),
  brown fat (neck, supraclavicular, paravertebral, perirenal — fat density on CT), injection site
  and axillary node on the injection side, laryngeal muscles (talking).
- Post-operative: healing thoracotomy, rib fractures, stump inflammation (weeks–months, mild,
  linear), talc pleurodesis (intense, persistent), sutures/granulomas, drain sites.
- Inflammatory: pneumonia, aspiration, radiation pneumonitis, sarcoid-like reaction, reactive
  hilar nodes (symmetric, mild), degenerative joints, vertebral end plates, atherosclerotic plaque.
- Artefacts: attenuation-correction misregistration at the diaphragm (breathing) → check
  non-AC or fused; metal (dental, hip) → apparent uptake; truncation at arms.

## Malignant pattern support
- Focal, more intense than liver, corresponds to a CT abnormality (nodule/mass/node), asymmetric,
  not along a physiologic structure. SUVmax ≥ 2.5 for lung nodules ≥ 1 cm is a classic (not
  absolute) cut-off; sub-centimetre nodules and adenocarcinoma in situ / lepidic tumours can be
  false-negative; infection/inflammation false-positive.
- Nodes: FDG-avid node > blood pool with CT correlate → suspicious; compare with the primary's SUV.
- Bone: focal marrow uptake with/without CT change; distinguish from degenerative end plate.
- Adrenal: uptake > liver suspicious; adenoma ≤ 10 HU on non-contrast CT.

## Response comparison (when two PET studies were supplied)
- State both scan-specific SUVbw values, uptake time, weight source, reconstruction and scanner
  differences. PERCIST requires the same scanner/protocol and an uptake-time difference ≤ 15 min.
- Do not compute formal PERCIST, Deauville or Hopkins classifications in this general workflow. They need
  the appropriate disease setting, validated SULpeak measurements (1.2 cm sphere) and complete criteria.
  A 2D neighbourhood mean is not SUVpeak/SULpeak.
- After surgery, absence of the removed primary does not by itself measure response in residual disease.

## Output requirements specific to PET/CT
- Each reported focus: location, SUVmax (state weight source and uptake time), size on CT, CT character,
  interpretation category, and z / CT instance / PET SOP for traceability.
- Explicit negative statements per region: stump/hilum, mediastinum, contralateral lung, pleura,
  liver, adrenals, bones, brain (if covered), neck nodes.
- Limitations: uptake time, glucose unknown, motion, small-lesion sensitivity, CT dose/quality,
  coverage outside CT.
