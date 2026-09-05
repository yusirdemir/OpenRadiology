# Scientific background and standards

Every threshold, protocol default and reporting convention in OpenRadiology traces to one of the
sources below. The engine never applies a management category automatically; the tables here
tell the reviewing agent or clinician *which* rule applies and *where* it is implemented.

## How the references map to the code

| Topic | Source | Where it is used |
|---|---|---|
| Image plane geometry, IPP/IOP, slice sorting | DICOM PS3.3 §C.7.6.2 (Image Plane Module); PS3.3 §C.8.9.1 (PET Image Module); PS3.3 §C.11.2 (VOI LUT) | `openrad/dcmlib.py` (`Volume`, `z_of`, `window`) |
| Gantry tilt as in-plane origin shear | DICOM PS3.3 §C.8.2.1.1.3 (`GantryDetectorTilt`, informational only); dcm2niix issue #253 | `Volume._validate`, `Volume.rectilinear` |
| Sliding-slab MIP for nodule detection | Kawel N et al. *AJR* 2009;192:1324-9, doi:10.2214/AJR.08.1689 (8 mm optimal); Jankowski A et al. *AJR* 2019;213:1237-42, doi:10.2214/AJR.19.21325 (10 mm best for solid nodules) | `openrad ct-render --mip 10` |
| Incidental nodule management (non-cancer patients) | MacMahon H et al. *Radiology* 2017;284:228-43, doi:10.1148/radiol.2017161659 | `checklists/ct_thorax_checklist.md` |
| Nodule measurement technique | Bankier AA et al. *Radiology* 2017;285:584-600, doi:10.1148/radiol.2017162894 | `openrad measure --points/--auto`, checklist |
| Lung cancer screening categories | ACR Lung-RADS v2022 (Christensen J et al. *J Am Coll Radiol* 2024;21:473-88, doi:10.1016/j.jacr.2023.09.009) | checklist (screening context only) |
| Solid-tumour response | Eisenhauer EA et al. *Eur J Cancer* 2009;45:228-47, doi:10.1016/j.ejca.2008.10.026 (RECIST 1.1); Schwartz LH et al. *Eur J Cancer* 2016;62:132-7, doi:10.1016/j.ejca.2016.03.081 (update) | measurement conventions; `templates/*` |
| Thoracic lymph node stations | Rusch VW et al. *J Thorac Oncol* 2009;4:568-77, doi:10.1097/JTO.0b013e3181a0d82e (IASLC map); El-Sherief AH et al. *RadioGraphics* 2014;34:1680-91, doi:10.1148/rg.346130097 (CT atlas) | checklist station table |
| TNM 9th edition (2025) | Rami-Porta R et al. *J Thorac Oncol* 2024;19:1007-27, doi:10.1016/j.jtho.2024.02.011 (stage groups); Huang J et al. *J Thorac Oncol* 2024;19:766-85, doi:10.1016/j.jtho.2023.10.012 (N2a/N2b) | checklist "staging limits" |
| FDG PET/CT acquisition and SUV | Boellaard R et al. *Eur J Nucl Med Mol Imaging* 2015;42:328-54, doi:10.1007/s00259-014-2961-x (EANM v2.0); EANM v3.0 2025 (Boellaard R et al. *Eur J Nucl Med Mol Imaging* 2025, doi:10.1007/s00259-025-07105-x) | `checklists/pet_ct_checklist.md`, `suv.py` warnings |
| SUV computation pseudo-code | RSNA QIBA FDG-PET/CT Profile, Appendix G "Vendor-neutral pseudo-code for SUV calculation" (rev. 2018-06-26) | `openrad/suv.py` |
| Metabolic response | Wahl RL et al. *J Nucl Med* 2009;50 Suppl 1:122S-50S, doi:10.2967/jnumed.108.057307 (PERCIST 1.0); O JH et al. *Radiology* 2016;280:576-84, doi:10.1148/radiol.2016142043 (practical PERCIST) | checklist; SUL factor in `suv.py` |
| Lean body mass (SUL) | Janmahasatian S et al. *Clin Pharmacokinet* 2005;44:1051-65, doi:10.2165/00003088-200544100-00004 | `suv.lean_body_mass_kg` |
| Brain metastasis MRI protocol | Kaufmann TJ et al. *Neuro Oncol* 2020;22:757-72, doi:10.1093/neuonc/noaa030 | `checklists/brain_mri_checklist.md` |
| Brain metastasis response | Lin NU et al. *Lancet Oncol* 2015;16:e270-8, doi:10.1016/S1470-2045(15)70057-4 (RANO-BM) | checklist lesion ledger |
| Glioma response (context) | Wen PY et al. *J Clin Oncol* 2010;28:1963-72, doi:10.1200/JCO.2009.26.3541 (RANO); Wen PY et al. *J Clin Oncol* 2023;41:5187-99, doi:10.1200/JCO.23.01059 (RANO 2.0) | checklist |
| White-matter grading | Fazekas F et al. *AJR* 1987;149:351-6, doi:10.2214/ajr.149.2.351 | checklist incidental items |
| Structured reporting and lexicon | RSNA RadReport templates (radreport.org); RadLex ontology (radlex.org); ESR structured reporting statement, *Insights Imaging* 2018;9:1-7, doi:10.1007/s13244-017-0588-8 | `templates/en`, `templates/tr`, `create_report.py` section order |
| Peer review taxonomy for audits | ACR RADPEER scoring (Jackson VP et al. *J Am Coll Radiol* 2009;6:21-5, doi:10.1016/j.jacr.2008.09.012) | `checklists/blind_audit_rubric.md` |
| De-identification | DICOM PS3.15 Annex E (Basic Application Confidentiality Profile, Table E.1-1) with options 113105/113107/113108/113111 (PS3.16 CID 7050); Attribute Confidentiality Profile bookkeeping PS3.3 C.7.1.1 (`PatientIdentityRemoved`, `DeidentificationMethodCodeSequence`) | `openrad anonymize` (`openrad/anonymize.py`) |
| Vision-model image budgets | Vendor documentation for longest accepted edge without resampling (values in `config.VISION_PROFILES`; verify for your deployment) | `openrad/grid.py`, `render.max_side` |
| Display windows | Standard CT presets (lung -600/1500, mediastinum 40/400, bone 450/1800, brain 40/80, stroke 32/8, subdural 75/215) as taught in ACR/RSNA curricula | `dcmlib.WINDOWS` |

## Key numbers used in the checklists

### Nodules and masses (CT)
* Thin sections (≤ 1.5 mm, ideally 1.0 mm) in lung window on the slice of maximal cross-section;
  long axis and perpendicular short axis in the same plane; **average diameter** for nodules < 10 mm,
  long axis for larger lesions; round to whole millimetres (Bankier 2017).
* Growth: ≥ 2 mm change in average diameter is the smallest reliable difference between two CTs;
  Lung-RADS calls > 1.5 mm within 12 months "growth" (screening).
* Lung-RADS v2022 (screening only): solid < 6 mm → 2; 6 to < 8 mm → 3; 8 to < 15 mm → 4A;
  ≥ 15 mm or new ≥ 8 mm → 4B; part-solid uses solid component; juxtapleural ≤ 10 mm mean diameter
  with smooth margins → 2.
* Fleischner 2017 applies to incidental nodules in patients ≥ 35 years **without** known cancer.
  Never apply it to a patient under oncologic surveillance.

### RECIST 1.1
* Measurable non-nodal lesion: ≥ 10 mm longest diameter on CT with slice thickness ≤ 5 mm (≥ 2 × slice
  thickness otherwise).
* Lymph node: **short axis** ≥ 15 mm = target; 10 to < 15 mm = non-target pathologic; < 10 mm = normal.
* Response: CR, PR (≥ 30 % decrease in sum of diameters), PD (≥ 20 % increase and ≥ 5 mm absolute,
  or new lesion), SD.

### IASLC lymph node map (stations 1 to 14)
| Zone | Stations | Landmark summary |
|---|---|---|
| Supraclavicular | 1 | Above the clavicles / manubrium; midline of trachea splits 1R/1L |
| Upper mediastinal | 2R, 2L, 3a, 3p, 4R, 4L | 2/4 border = lower margin of left brachiocephalic vein crossing (2R) or top of aortic arch (2L); **the left lateral tracheal wall, not the midline, separates R from L**; 3a prevascular, 3p retrotracheal |
| Aortopulmonary | 5, 6 | 5 subaortic (lateral to ligamentum arteriosum), 6 para-aortic (anterior/lateral to ascending aorta and arch) |
| Subcarinal | 7 | Below the carina to the upper border of the lower lobe bronchus (left) / bronchus intermedius (right) |
| Lower mediastinal | 8, 9 | 8 paraoesophageal, 9 pulmonary ligament |
| Hilar/interlobar | 10, 11 | 10 adjacent to main bronchus and hilar vessels, 11 between lobar bronchi |
| Peripheral | 12, 13, 14 | Lobar, segmental, subsegmental |

TNM 9th edition (in force from January 2025) splits N2 into **N2a** (single ipsilateral mediastinal or
subcarinal station) and **N2b** (multiple stations) and M1c into M1c1/M1c2. The engine reports station,
short axis and morphology only; stage assignment stays with the clinician.

### FDG PET/CT
* Uptake time 55 to 75 min (EANM), tolerance 45 to 90; blood glucose < 11 mmol/L (200 mg/dL)
  documented; weight measured on the day.
* SUVbw = C [Bq/mL] × weight [g] / decay-corrected dose [Bq]. `DecayCorrection` START vs ADMIN decides
  whether the dose is decayed to scan start (QIBA pseudo-code).
* PERCIST: SULpeak in a 1.2 cm diameter sphere; liver reference = 3 cm sphere in the right lobe
  (mean + 2 SD); blood pool = 1 cm × 2 cm cylinder in descending aorta when the liver is diseased;
  measurable target = SULpeak ≥ 1.5 × liver SULmean + 2 SD. A 2D ROI or 3 × 3 mean is **not** SUVpeak.
* Physiologic uptake and inflammation are listed in the checklist; SUV 2.5 is a detection aid, not a
  malignancy threshold.

### Brain MRI (metastasis surveillance)
* Minimum protocol (Kaufmann 2020): parameter-matched pre- and post-contrast 3D T1 IR-GRE (≤ 1.5 mm,
  ideally 1 mm isotropic), axial T2, axial FLAIR (2D or 3D), DWI in three directions with ADC, and
  post-contrast 2D T1 SE for conspicuity; contrast dose 0.1 mmol/kg with ≥ 4 min delay.
* RANO-BM: measurable = ≥ 10 mm longest diameter on contrast T1 (slice ≤ 1.5 mm) or ≥ 5 mm on 1 mm
  isotropic 3D; up to 5 target lesions; PD = ≥ 20 % increase in sum of longest diameters plus ≥ 5 mm
  absolute, or new lesion.
* Fazekas 0 to 3 for periventricular and deep white-matter hyperintensity.

## What is deliberately not implemented
* Automatic staging, Lung-RADS/Fleischner category assignment, PERCIST/RECIST response labels.
* Deformable or rigid registration between studies (pairs are compared by patient coordinates only).
* Any detection or segmentation model. Region growing in `measure.py` is an exploratory caliper aid
  with documented leak warnings, not a segmentation.
