/** Wire types of the sidecar. Kept in one file so a server change breaks the build loudly. */

export interface StudyCard {
  path: string;
  folder: string;
  study_uid: string;
  shares_folder: boolean;
  date: string;
  inconsistent_date: boolean;
  time: string;
  description: string;
  modalities: string[];
  series_count: number;
  instances: number;
  identity_removed: boolean;
  supported: boolean;
}

export interface SeriesCard {
  series_uid: string;
  number: string;
  modality: string;
  description: string;
  instances: number;
  rows: number;
  cols: number;
  pixel_spacing_mm: [number, number] | null;
  slice_thickness_mm: number | null;
  plane: string;
  kernel: string;
  body_part: string;
  renderable: boolean;
  /** Header-only geometry probe; null when it could not be assessed. */
  regular_volume: boolean | null;
  geometry_note: string;
}

export interface WindowPreset {
  name: string;
  center: number;
  width: number;
}

export type PlaneName = "ax" | "cor" | "sag";

export interface PlaneInfo {
  count: number;
  mm_per_px: [number, number];
}

export interface SeriesMeta {
  geometry: {
    modality: string;
    series_number: string;
    series_uid: string;
    study_uid: string;
    frame_uid?: string;
    origin_lps?: [number,number,number];
    shape_zyx: [number, number, number];
    pixel_spacing_mm: [number, number];
    slice_spacing_mm: number;
    slice_thickness_mm: number | null;
    plane: string;
    iop: number[];
    z_range_mm: [number, number];
    manufacturer: string;
    tilted?: boolean;
    gantry_tilt_deg?: number;
  };
  planes: Record<PlaneName, PlaneInfo>;
  value_range: [number, number];
  wire_dtype: "int16" | "float32";
  windows: WindowPreset[];
  z_mm: number[];
  instances: string[];
  sop_uids: string[];
  bytes: number;
}

/** One image plus the calibration needed to display and address it honestly. */
export interface SliceImage {
  pixels: Int16Array | Float32Array;
  rows: number;
  cols: number;
  /** [vertical, horizontal] millimetres per pixel of this image. */
  mmPerPx: [number, number];
  plane: PlaneName;
  index: number;
  count: number;
  sopUid: string;
  instance: string;
  seriesUid: string;
  studyUid: string;
  zMm: number | null;
  mipMm: number;
}

export interface Ref {
  study_uid: string;
  series_uid: string;
  sop_uid: string;
  row?: number;
  col?: number;
}

export interface Measurement {
  value: number;
  unit: string;
  method: string;
  evidence_file: string;
  sha256: string;
  ref: Ref;
  tool_output?: string;
  points?: { row: number; col: number }[];
}

export interface PatientExplanation {
  meaning: string;
  importance: string;
  uncertainty: string;
  discuss_with_doctor: string;
}

export interface Claim {
  id: string;
  text: string;
  confidence: "low" | "moderate" | "high";
  priority: "important" | "routine" | "incidental";
  refs: Ref[];
  pages: string[];
  patient: PatientExplanation;
  measurements?: Measurement[];
  comparison?: { status: string; reason: string; from_study_uid?: string; to_study_uid?: string };
  timeline?: { study_uid: string; status: string; text: string; explanation: string; refs?: Ref[] }[];
}

export type RegionStatus = "pending" | "finding" | "no_finding" | "limited" | "not_covered";

export interface Region {
  status: RegionStatus;
  text: string;
  explanation: string;
  refs: Ref[];
  pages: string[];
}

export interface SessionSeries {
  uid: string;
  number: string;
  modality: string;
  description: string;
  sops: Record<string, { instance: string; rows: number; cols: number }>;
  disposition: "pending" | "read" | "excluded" | "unsupported";
  reason: string;
  geometry_checked: boolean;
  required_passes: string[];
}

export interface SessionStudy {
  folder: string;
  path: string;
  uid: string;
  date: string;
  time: string;
  modalities: string[];
  series: SessionSeries[];
  regions: Record<string, Region>;
  source_files: { path: string; sha256: string }[];
}

export interface SessionPage {
  path: string;
  sha256: string;
  reviewed: boolean;
  study_uid: string;
  series_uid: string;
  purpose: string;
  sources: { study_uid: string; series_uid: string; sop_uid: string; instance?: string; purpose: string }[];
}

export interface Session {
  schema: 2;
  created_utc: string;
  finalized_utc?: string;
  repo: string;
  work_dir: string;
  studies_root?: string;
  language?: "tr" | "en";
  mode: "single" | "comparison";
  reader: string;
  context_disclosure: string;
  blind_status: "not_claimed" | "partial" | "blinded";
  clinical_context?: string;
  limitations: string[];
  recommendations?: string[];
  patient_context: string;
  patient_limitations: string;
  glossary?: { term: string; explanation: string }[];
  reading_complete: boolean;
  validation_note?: string;
  toolchain: { path: string; sha256: string }[];
  comparison: { status: string; reason: string; explanation: string };
  studies: SessionStudy[];
  pages: SessionPage[];
  claims: Claim[];
  report_file?: string;
  guide_file?: string;
  report_sha256?: string;
  guide_sha256?: string;
}

export interface AttestationRow {
  path: string;
  purpose: string;
  reviewed: boolean;
  attested: boolean;
  reason: string;
}

export interface AttestationCoverage {
  pages: AttestationRow[];
  pages_total: number;
  pages_attested: number;
  slices_total: number;
  slices_attested: number;
  events: number;
  policy: { min_dwell_ms: number; min_scale: number; require_focus: boolean };
}

export interface CheckResult {
  ok: boolean;
  errors: string[];
  attestation: AttestationCoverage;
  unattested_reviews: AttestationRow[];
  blocking: string[];
}

export interface JobSnapshot {
  id: string;
  kind: string;
  label: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  created: number;
  started: number | null;
  finished: number | null;
  exit_code: number | null;
  error: string;
  meta: Record<string, unknown>;
  command: string[];
}

export interface MeasureResult {
  text: string;
  data: {
    tool: string;
    unit?: string;
    results: MeasureEntry[];
    warnings: string[];
    notes: string[];
    slice?: { study_uid: string; series_uid: string; sop_uid: string; instance: string; z_mm: number };
  };
  evidence: { path: string; sha256: string } | null;
  command: string[];
}

export interface MeasureEntry {
  kind: string;
  [key: string]: unknown;
}

export interface ViewEventPayload {
  study_uid: string;
  series_uid: string;
  sop_uid: string;
  plane: PlaneName;
  index: number;
  dwell_ms: number;
  scale: number;
  focused: boolean;
  visible: boolean;
  window_center: number;
  window_width: number;
  source?: "viewer" | "page";
  page_path?: string;
}

/** One saved review, as the library lists it. Summarised by the sidecar. */
export interface SessionCard {
  path: string;
  modified: number;
  mode: "single" | "comparison";
  language: string;
  reader: string;
  reading_complete: boolean;
  finalized: boolean;
  report_file: string;
  guide_file: string;
  studies: {
    uid: string;
    path: string;
    folder: string;
    date: string;
    time: string;
    modalities: string[];
    series: number;
  }[];
  claims: number;
  verdicts: Record<string, number>;
  comparison_status: string;
  patient_context: string;
}

export type LesionProfile = "auto" | "solid" | "effusion" | "bulla" | "cyst";
export type Voxel = [number, number, number];
export interface LesionSlice {
  index: number;
  row: number;
  col: number;
  row_min: number;
  row_max: number;
  col_min: number;
  col_max: number;
  pixels: number;
  /** Twice the deepest inscribed radius on this slice: the thickness of a layer. */
  thickness_mm?: number;
  diameter_mm: number;
  caliper: [{ row: number; col: number }, { row: number; col: number }] | null;
  measurement_method: string;
  runs: number[];
  /** Pleural tangent closures bounding this axial slice: P1-P2 wall arcs. */
  closure?: PleuralClosure[];
  /** Worker-prepared, cropped native-grid label image. Never linearly filtered. */
  mask?: Uint8Array;
}
/** One closed gap in the pleural line: tangent points and the fitted wall arc (native pixel centres). */
export interface PleuralClosure {
  index: number;
  p1: [number, number];
  p2: [number, number];
  arc: [number, number][];
  chord_mm: number;
  bulge_mm: number;
  flank_points: number;
}
export interface LesionSegmentation {
  version: string;
  id: string;
  series_uid: string;
  status: "draft" | "needs-review";
  reasons: string[];
  seed: { index: number; row: number; col: number };
  seed_hu: number;
  band: [number, number];
  tissue: LesionProfile;
  frame_uid: string;
  native_origin_lps: Voxel;
  mask_origin_lps: Voxel;
  focus_zyx: Voxel;
  centroid_lps: Voxel;
  shape_zyx: Voxel;
  spacing_zyx: Voxel;
  mask_origin_zyx: Voxel;
  mask_shape_zyx: Voxel;
  mask3d?: Uint8Array;
  hu_data: string;
  hu3d?: Float32Array;
  rgba3d?: Uint8Array;
  planes: Record<PlaneName, LesionSlice[]>;
  voxels: number;
  volume_ml: number;
  craniocaudal_mm: number;
  established: boolean;
  reason: string;
  extent: [number, number];
  slices: LesionSlice[];
  heat_range: [number, number];
  pleural_closure: { active: boolean; radius_mm: number | null; flank_mm: number | null; method: string; slices: PleuralClosure[] };
  quality: { converged: boolean; iterations: number; weak_boundary_fraction: number; crop_contact_voxels: number; automatic_partition: boolean; pruned_voxels: number; prune_calibre_mm: number };
  provenance: { positive_zyx: Voxel[]; negative_zyx: Voxel[]; brush_mm: number; native_grid: boolean; sigma_mm: number };
  elapsed_ms: number;
}
export interface VolumePreview {
  shape_zyx: Voxel;
  native_shape_zyx: Voxel;
  spacing_zyx: Voxel;
  data: string;
  dtype: "int16-le";
  series_uid: string;
  sampling: string;
  origin_lps: Voxel;
  frame_uid: string;
}

/** The engine's own locale bundle: region names and report headings. */
export interface LocaleBundle {
  language: string;
  name: string;
  text: Record<string, string>;
  regions: Record<string, string>;
}

/** A page the bridged agent has asked the window to display. */
export interface DisplayRequest {
  id: string;
  page_path: string;
  session_path: string;
  purpose: string;
  created: number;
  shown_at: number | null;
  declined: string;
}

/** One line of what the agent did, replayable from the panel. */
export interface TranscriptEntry {
  id: string;
  ts: number;
  kind: "tool" | "display_request" | "display_shown" | string;
  summary?: string;
  name?: string;
  arguments?: Record<string, unknown>;
  page?: string;
  purpose?: string;
}

/** A document in the archive that could establish a same-patient mapping. */
export interface IdentityCandidate {
  path: string;
  name: string;
  covers_all: boolean;
  missing: string[];
  size: number;
}
