/**
 * Viewer state: which image, drawn how, and how two of them stay together.
 *
 * The interesting part is the lockstep. Two CT examinations of the same person
 * almost never share a slice index or a table zero: the technologist may have
 * started the scan ten centimetres higher, the reconstruction spacing may
 * differ, the patient lay differently. Linking by slice index would put a
 * lesion next to a rib. Linking by absolute z in patient coordinates is worse:
 * the table origin is a property of the scanner that day, so the panes jump the
 * moment the reader touches the wheel.
 *
 * So the panes are linked *relatively*. An anchor pair is set whenever the
 * reader lands somewhere deliberate -- opening a study, jumping to a finding,
 * switching the link on -- and from then on the other pane's index is
 * recomputed from the anchor as a physical offset:
 *
 *     mm      = (index - anchor[a]) * spacing[a]
 *     index_b = anchor[b] + round(mm / spacing[b])
 *
 * Recomputing from the anchor rather than accumulating steps means rounding
 * never drifts, and a pane that hits the end of its stack simply clamps
 * without dragging the other one with it.
 */
import { create } from "zustand";
import { api } from "../api/client";
import type { PlaneName, SeriesCard, SeriesMeta } from "../api/types";
import { IDENTITY_VIEW, type ViewState } from "../gl/viewport";

export interface WindowSetting {
  center: number;
  width: number;
  invert: boolean;
  name: string;
}

export const CT_WINDOWS: WindowSetting[] = [
  { name: "lung", center: -600, width: 1500, invert: false },
  { name: "soft", center: 40, width: 400, invert: false },
  { name: "bone", center: 450, width: 1800, invert: false },
  { name: "liver", center: 60, width: 160, invert: false },
  { name: "brain", center: 40, width: 80, invert: false },
];

export const WINDOW_LABELS: Record<string, string> = {
  lung: "Akciğer",
  soft: "Yumuşak doku",
  mediastinum: "Mediasten",
  bone: "Kemik",
  liver: "Karaciğer",
  abdomen: "Batın",
  angio: "Damar",
  brain: "Beyin",
  stroke: "İnme",
  subdural: "Subdural",
  auto: "Otomatik",
  custom: "Elle",
};

export interface ViewerPane {
  studyPath: string;
  studyUid: string;
  seriesUid: string;
  meta: SeriesMeta | null;
  plane: PlaneName;
  index: number;
  mipMm: number;
  focus?: [number, number, number];
  window: WindowSetting;
  view: ViewState;
  loading: boolean;
  error: string | null;
}

interface ViewerState {
  panes: ViewerPane[];
  activePane: number;
  /** Anchor index per pane; the lockstep offset is measured from these. */
  anchors: number[];
  linked: boolean;
  showHighlights: boolean;
  showGrid: boolean;
  layers: { contour: boolean; heat: boolean; caliper: boolean; opacity: number };
  setLayers: (patch: Partial<ViewerState["layers"]>) => void;
  seriesByStudy: Record<string, SeriesCard[]>;

  reset: () => void;
  setActivePane: (paneIndex: number) => void;
  setLinked: (linked: boolean) => void;
  toggleHighlights: () => void;
  setShowGrid: (show: boolean) => void;

  loadSeriesList: (studyPath: string) => Promise<SeriesCard[]>;
  mountPanes: (panes: { studyPath: string; studyUid: string; seriesUid: string }[]) => void;
  openSeries: (paneIndex: number, studyPath: string, seriesUid: string) => Promise<void>;

  patchPane: (paneIndex: number, patch: Partial<ViewerPane>) => void;
  setIndex: (paneIndex: number, index: number) => void;
  stepIndex: (paneIndex: number, delta: number) => void;
  setPlane: (plane: PlaneName) => void;
  setWindow: (paneIndex: number, window: Partial<WindowSetting>) => void;
  setView: (paneIndex: number, view: ViewState) => void;
  setMip: (mipMm: number) => void;
  resetView: () => void;
  /** Land every pane on a chosen slice and make that the lockstep anchor. */
  anchorAt: (targets: { paneIndex: number; index: number; window?: WindowSetting }[]) => void;
  /** Take the current positions as the anchor pair, without moving anything. */
  realign: () => void;
}

/**
 * Millimetres between neighbouring slices of a plane.
 *
 * For the acquired axial stack that is the slice spacing. For a reformat the
 * step is a pixel of the acquisition grid: coronal slices advance by the row
 * spacing, sagittal ones by the column spacing.
 */
export function spacingMm(pane: ViewerPane): number {
  const meta = pane.meta;
  if (!meta) return 1;
  if (pane.plane === "ax") {
    const spacing = meta.geometry.slice_spacing_mm;
    if (spacing && Number.isFinite(spacing) && spacing > 0) return spacing;
    const [first, second] = meta.z_mm;
    if (first !== undefined && second !== undefined) return Math.abs(second - first) || 1;
    return 1;
  }
  const [row = 1, col = 1] = meta.geometry.pixel_spacing_mm ?? [1, 1];
  return (pane.plane === "cor" ? row : col) || 1;
}

function countOf(pane: ViewerPane): number {
  return pane.meta?.planes?.[pane.plane]?.count ?? 0;
}

function clampIndex(pane: ViewerPane, index: number): number {
  const count = countOf(pane);
  if (count <= 0) return 0;
  return Math.max(0, Math.min(count - 1, Math.round(index)));
}

function defaultWindow(meta: SeriesMeta | null): WindowSetting {
  if (!meta) return { ...(CT_WINDOWS[1] as WindowSetting) };
  if (meta.geometry.modality === "CT") return { ...(CT_WINDOWS[1] as WindowSetting) };
  const auto = meta.windows.find((w) => w.name === "auto") ?? meta.windows[0];
  return auto
    ? { name: auto.name, center: auto.center, width: auto.width, invert: false }
    : { name: "auto", center: 0, width: 1, invert: false };
}

function blankPane(studyPath: string, studyUid: string, seriesUid: string): ViewerPane {
  return {
    studyPath,
    studyUid,
    seriesUid,
    meta: null,
    plane: "ax",
    index: 0,
    mipMm: 0,
    window: { ...(CT_WINDOWS[1] as WindowSetting) },
    view: { ...IDENTITY_VIEW },
    loading: true,
    error: null,
  };
}

export const useViewer = create<ViewerState>((set, get) => ({
  panes: [],
  activePane: 0,
  anchors: [],
  linked: true,
  showHighlights: true,
  showGrid: false,
  layers: { contour: true, heat: true, caliper: true, opacity: 0.4 },
  setLayers: (patch) => set({ layers: { ...get().layers, ...patch } }),
  seriesByStudy: {},

  reset: () => set({ panes: [], activePane: 0, anchors: [], showGrid: false }),

  setActivePane: (activePane) => set({ activePane }),

  // Turning the link on must never move the image the reader is looking at, so
  // the current positions become the anchor pair.
  setLinked: (linked) => {
    set({ linked });
    if (linked) get().realign();
  },

  toggleHighlights: () => set({ showHighlights: !get().showHighlights }),
  setShowGrid: (showGrid) => set({ showGrid }),

  loadSeriesList: async (studyPath) => {
    const cached = get().seriesByStudy[studyPath];
    if (cached) return cached;
    const result = await api.listSeries(studyPath);
    set({ seriesByStudy: { ...get().seriesByStudy, [studyPath]: result.series } });
    return result.series;
  },

  mountPanes: (entries) =>
    set({
      panes: entries.map((entry) => blankPane(entry.studyPath, entry.studyUid, entry.seriesUid)),
      anchors: entries.map(() => 0),
      activePane: Math.max(0, entries.length - 1),
    }),

  openSeries: async (paneIndex, studyPath, seriesUid) => {
    const panes = [...get().panes];
    const existing = panes[paneIndex];
    panes[paneIndex] = blankPane(studyPath, existing?.studyUid ?? "", seriesUid);
    set({ panes });
    try {
      const meta = await api.seriesMeta(studyPath, seriesUid);
      const next = [...get().panes];
      const pane = next[paneIndex];
      // A slow answer that arrives after the reader moved on must not be
      // stamped onto the series they switched to.
      if (!pane || pane.seriesUid !== seriesUid) return;
      const middle = Math.floor(meta.planes.ax.count / 2);
      next[paneIndex] = {
        ...pane,
        meta,
        studyUid: meta.geometry.study_uid,
        index: middle,
        window: defaultWindow(meta),
        loading: false,
      };
      const anchors = [...get().anchors];
      anchors[paneIndex] = middle;
      set({ panes: next, anchors });
    } catch (error) {
      const next = [...get().panes];
      const pane = next[paneIndex];
      if (!pane || pane.seriesUid !== seriesUid) return;
      next[paneIndex] = {
        ...pane,
        loading: false,
        error: error instanceof Error ? error.message : String(error),
      };
      set({ panes: next });
    }
  },

  patchPane: (paneIndex, patch) => {
    const panes = [...get().panes];
    const pane = panes[paneIndex];
    if (!pane) return;
    panes[paneIndex] = { ...pane, ...patch };
    set({ panes });
  },

  setIndex: (paneIndex, index) => {
    const { panes, linked, anchors } = get();
    const source = panes[paneIndex];
    if (!source?.meta) return;
    const wanted = clampIndex(source, index);
    if (wanted === source.index) return;

    if (!linked || panes.length < 2) {
      get().patchPane(paneIndex, { index: wanted });
      return;
    }

    const offsetMm = (wanted - (anchors[paneIndex] ?? 0)) * spacingMm(source);
    set({
      panes: panes.map((pane, i) => {
        if (i === paneIndex) return { ...pane, index: wanted };
        // Only a pane cut on the same plane can follow; a coronal reformat
        // beside an axial stack is a different journey through the patient.
        if (!pane.meta || pane.plane !== source.plane) return pane;
        const target = clampIndex(pane, (anchors[i] ?? 0) + offsetMm / spacingMm(pane));
        return target === pane.index ? pane : { ...pane, index: target };
      }),
    });
  },

  stepIndex: (paneIndex, delta) => {
    const pane = get().panes[paneIndex];
    if (!pane) return;
    get().setIndex(paneIndex, pane.index + delta);
  },

  // The plane is a property of the reading, not of one pane: comparing an axial
  // slice with a coronal reformat is not a comparison.
  setPlane: (plane) => {
    const panes = get().panes.map((pane) => {
      if (!pane.meta) return pane;
      const count = pane.meta.planes[plane]?.count ?? 0;
      const target = pane.focus ? pane.focus[plane === "ax" ? 0 : plane === "cor" ? 1 : 2] : Math.floor(count / 2);
      return { ...pane, plane, index: Math.max(0, Math.min(count - 1, Math.round(target))), view: { ...IDENTITY_VIEW } };
    });
    set({ panes, anchors: panes.map((pane) => pane.index) });
  },

  // Two studies windowed differently is not a comparison either, so while the
  // panes are linked the window travels with them.
  setWindow: (paneIndex, patch) => {
    const { panes, linked } = get();
    if (!panes[paneIndex]) return;
    set({
      panes: panes.map((pane, i) =>
        linked || i === paneIndex ? { ...pane, window: { ...pane.window, ...patch } } : pane,
      ),
    });
  },

  setView: (paneIndex, view) => {
    const { panes, linked } = get();
    if (!panes[paneIndex]) return;
    // Zoom and pan are shared while linked: side by side at different
    // magnifications, one lesion looks twice the size of the other.
    set({ panes: panes.map((pane, i) => (linked || i === paneIndex ? { ...pane, view } : pane)) });
  },

  setMip: (mipMm) => set({ panes: get().panes.map((pane) => ({ ...pane, mipMm })) }),

  resetView: () => set({ panes: get().panes.map((pane) => ({ ...pane, view: { ...IDENTITY_VIEW } })) }),

  anchorAt: (targets) => {
    const panes = [...get().panes];
    const anchors = [...get().anchors];
    for (const target of targets) {
      const pane = panes[target.paneIndex];
      if (!pane) continue;
      const index = clampIndex(pane, target.index);
      panes[target.paneIndex] = { ...pane, index, ...(target.window ? { window: target.window } : {}) };
      anchors[target.paneIndex] = index;
    }
    // A pane the jump said nothing about keeps its position, and its anchor is
    // refreshed so the next wheel gesture starts from where it actually is.
    panes.forEach((pane, i) => {
      if (!targets.some((t) => t.paneIndex === i)) anchors[i] = pane.index;
    });
    set({ panes, anchors });
  },

  realign: () => set({ anchors: get().panes.map((pane) => pane.index) }),
}));
