/**
 * Viewer state: which image, drawn how.
 *
 * Window and level live here rather than in the renderer because several
 * panels read them -- the evidence panel records them with an attestation, the
 * comparison view mirrors them across two studies. The renderer stays a pure
 * function of this state.
 */
import { create } from "zustand";
import { api } from "../api/client";
import type { PlaneName, Ref, SeriesCard, SeriesMeta, SliceImage } from "../api/types";
import { IDENTITY_VIEW, type ViewState } from "../gl/viewport";

export type ToolName = "browse" | "window" | "distance" | "roi" | "seed" | "extent";

export interface PendingPoint {
  row: number;
  col: number;
}

export interface WindowSetting {
  center: number;
  width: number;
  invert: boolean;
  name: string;
}

export const WINDOW_PRESETS: WindowSetting[] = [
  { name: "lung", center: -600, width: 1500, invert: false },
  { name: "soft", center: 40, width: 400, invert: false },
  { name: "bone", center: 450, width: 1800, invert: false },
  { name: "liver", center: 60, width: 160, invert: false },
  { name: "brain", center: 40, width: 80, invert: false },
];

export interface ViewerPane {
  studyPath: string;
  studyUid: string;
  seriesUid: string;
  meta: SeriesMeta | null;
  plane: PlaneName;
  index: number;
  mipMm: number;
  window: WindowSetting;
  view: ViewState;
  image: SliceImage | null;
  loading: boolean;
  error: string | null;
}

interface ViewerState {
  panes: ViewerPane[];
  activePane: number;
  tool: ToolName;
  /** Points collected for the tool in progress, in native row and column. */
  pending: PendingPoint[];
  seriesByStudy: Record<string, SeriesCard[]>;
  /** Lock two panes to the same patient coordinate rather than the same index. */
  linked: boolean;
  showGrid: boolean;

  setTool: (tool: ToolName) => void;
  addPoint: (point: PendingPoint) => void;
  clearPoints: () => void;
  setLinked: (linked: boolean) => void;
  setShowGrid: (show: boolean) => void;

  loadSeriesList: (studyPath: string) => Promise<SeriesCard[]>;
  openSeries: (paneIndex: number, studyPath: string, seriesUid: string) => Promise<void>;
  closePane: (paneIndex: number) => void;
  setActivePane: (paneIndex: number) => void;

  patchPane: (paneIndex: number, patch: Partial<ViewerPane>) => void;
  setIndex: (paneIndex: number, index: number) => void;
  stepIndex: (paneIndex: number, delta: number) => void;
  setPlane: (paneIndex: number, plane: PlaneName) => void;
  setWindow: (paneIndex: number, window: Partial<WindowSetting>) => void;
  setView: (paneIndex: number, view: ViewState) => void;
  setMip: (paneIndex: number, mipMm: number) => void;
  jumpToRef: (ref: Ref, studyPath: string) => Promise<void>;
}

function defaultWindow(meta: SeriesMeta | null): WindowSetting {
  if (!meta) return { ...(WINDOW_PRESETS[1] as WindowSetting) };
  if (meta.geometry.modality === "CT") return { ...(WINDOW_PRESETS[1] as WindowSetting) };
  const auto = meta.windows.find((w) => w.name === "auto") ?? meta.windows[0];
  return auto
    ? { name: auto.name, center: auto.center, width: auto.width, invert: false }
    : { name: "auto", center: 0, width: 1, invert: false };
}

function emptyPane(studyPath: string, studyUid: string, seriesUid: string): ViewerPane {
  return {
    studyPath,
    studyUid,
    seriesUid,
    meta: null,
    plane: "ax",
    index: 0,
    mipMm: 0,
    window: { ...(WINDOW_PRESETS[1] as WindowSetting) },
    view: { ...IDENTITY_VIEW },
    image: null,
    loading: true,
    error: null,
  };
}

export const useViewer = create<ViewerState>((set, get) => ({
  panes: [],
  activePane: 0,
  tool: "browse",
  pending: [],
  seriesByStudy: {},
  linked: true,
  showGrid: false,

  setTool: (tool) => set({ tool, pending: [] }),
  addPoint: (point) => set({ pending: [...get().pending, point] }),
  clearPoints: () => set({ pending: [] }),
  setLinked: (linked) => set({ linked }),
  setShowGrid: (showGrid) => set({ showGrid }),

  loadSeriesList: async (studyPath) => {
    const cached = get().seriesByStudy[studyPath];
    if (cached) return cached;
    const result = await api.listSeries(studyPath);
    set({ seriesByStudy: { ...get().seriesByStudy, [studyPath]: result.series } });
    return result.series;
  },

  openSeries: async (paneIndex, studyPath, seriesUid) => {
    const panes = [...get().panes];
    const existing = panes[paneIndex];
    panes[paneIndex] = { ...emptyPane(studyPath, existing?.studyUid ?? "", seriesUid) };
    set({ panes, activePane: paneIndex });
    try {
      const meta = await api.seriesMeta(studyPath, seriesUid);
      const next = [...get().panes];
      const pane = next[paneIndex];
      if (!pane || pane.seriesUid !== seriesUid) return;
      const count = meta.planes.ax.count;
      next[paneIndex] = {
        ...pane,
        meta,
        studyUid: meta.geometry.study_uid,
        index: Math.floor(count / 2),
        window: defaultWindow(meta),
        loading: false,
      };
      set({ panes: next });
    } catch (error) {
      const next = [...get().panes];
      const pane = next[paneIndex];
      if (pane) {
        next[paneIndex] = { ...pane, loading: false, error: error instanceof Error ? error.message : String(error) };
        set({ panes: next });
      }
    }
  },

  closePane: (paneIndex) => {
    const panes = get().panes.filter((_, i) => i !== paneIndex);
    set({ panes, activePane: Math.max(0, Math.min(get().activePane, panes.length - 1)) });
  },

  setActivePane: (activePane) => set({ activePane }),

  patchPane: (paneIndex, patch) => {
    const panes = [...get().panes];
    const pane = panes[paneIndex];
    if (!pane) return;
    panes[paneIndex] = { ...pane, ...patch };
    set({ panes });
  },

  setIndex: (paneIndex, index) => {
    const pane = get().panes[paneIndex];
    if (!pane?.meta) return;
    const count = pane.meta.planes[pane.plane].count;
    get().patchPane(paneIndex, { index: Math.max(0, Math.min(count - 1, Math.round(index))) });
  },

  stepIndex: (paneIndex, delta) => {
    const pane = get().panes[paneIndex];
    if (!pane) return;
    get().setIndex(paneIndex, pane.index + delta);
  },

  setPlane: (paneIndex, plane) => {
    const pane = get().panes[paneIndex];
    if (!pane?.meta) return;
    const count = pane.meta.planes[plane].count;
    get().patchPane(paneIndex, {
      plane,
      index: Math.min(count - 1, Math.floor(count / 2)),
      view: { ...IDENTITY_VIEW },
    });
  },

  setWindow: (paneIndex, patch) => {
    const pane = get().panes[paneIndex];
    if (!pane) return;
    get().patchPane(paneIndex, { window: { ...pane.window, ...patch } });
  },

  setView: (paneIndex, view) => get().patchPane(paneIndex, { view }),

  setMip: (paneIndex, mipMm) => get().patchPane(paneIndex, { mipMm }),

  /**
   * Fly to a cited address. The series is opened if needed, the slice is found
   * by SOPInstanceUID rather than by index, and the pane is left showing the
   * exact pixel the claim points at.
   */
  jumpToRef: async (ref, studyPath) => {
    const state = get();
    let paneIndex = state.panes.findIndex((p) => p.seriesUid === ref.series_uid);
    if (paneIndex < 0) {
      paneIndex = state.panes.length ? state.activePane : 0;
      if (!state.panes.length) set({ panes: [emptyPane(studyPath, ref.study_uid, ref.series_uid)] });
      await get().openSeries(paneIndex, studyPath, ref.series_uid);
    }
    const pane = get().panes[paneIndex];
    if (!pane?.meta) return;
    const index = pane.meta.sop_uids.indexOf(ref.sop_uid);
    if (index >= 0) {
      get().patchPane(paneIndex, { plane: "ax", index });
      set({ activePane: paneIndex });
    }
  },
}));
