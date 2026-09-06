import { beforeEach, describe, expect, it } from "vitest";
import type { PlaneName, SeriesMeta } from "../api/types";
import { IDENTITY_VIEW } from "../gl/viewport";
import { CT_WINDOWS, spacingMm, useViewer, type ViewerPane, type WindowSetting } from "./viewer";

function meta(count: number, sliceSpacing: number, pixelSpacing: [number, number] = [0.7, 0.9]): SeriesMeta {
  return {
    geometry: {
      modality: "CT",
      series_number: "1",
      series_uid: "s",
      study_uid: "st",
      shape_zyx: [count, 512, 512],
      pixel_spacing_mm: pixelSpacing,
      slice_spacing_mm: sliceSpacing,
      slice_thickness_mm: sliceSpacing,
      plane: "ax",
      iop: [1, 0, 0, 0, 1, 0],
      z_range_mm: [0, count * sliceSpacing],
      manufacturer: "",
    },
    planes: {
      ax: { count, mm_per_px: pixelSpacing },
      cor: { count: 512, mm_per_px: [sliceSpacing, pixelSpacing[1]] },
      sag: { count: 512, mm_per_px: [sliceSpacing, pixelSpacing[0]] },
    },
    value_range: [-1024, 3071],
    wire_dtype: "int16",
    windows: [],
    z_mm: Array.from({ length: count }, (_, i) => i * sliceSpacing),
    instances: [],
    sop_uids: [],
    bytes: 0,
  };
}

function pane(index: number, count: number, sliceSpacing: number, plane: PlaneName = "ax"): ViewerPane {
  return {
    studyPath: `/dicom/${sliceSpacing}`,
    studyUid: `study.${sliceSpacing}`,
    seriesUid: `series.${sliceSpacing}`,
    meta: meta(count, sliceSpacing),
    plane,
    index,
    mipMm: 0,
    window: { ...(CT_WINDOWS[1] as WindowSetting) },
    view: { ...IDENTITY_VIEW },
    loading: false,
    error: null,
  };
}

const indices = () => useViewer.getState().panes.map((p) => p.index);

function mount(panes: ViewerPane[], anchors: number[]) {
  useViewer.setState({ panes, anchors, activePane: 0, linked: true });
}

describe("slice spacing", () => {
  it("steps an axial stack by its slice spacing", () => {
    expect(spacingMm(pane(0, 100, 5))).toBe(5);
  });

  it("steps a reformat by the acquisition pixel it advances through", () => {
    expect(spacingMm(pane(0, 100, 5, "cor"))).toBe(0.7);
    expect(spacingMm(pane(0, 100, 5, "sag"))).toBe(0.9);
  });
});

/**
 * The requirement these tests exist for: two examinations of the same person
 * do not share a table origin. The panes must move together in millimetres of
 * patient, measured from wherever the reader last landed deliberately, and
 * never by jumping to an absolute z that the scanner happened to write.
 */
describe("relative lockstep", () => {
  beforeEach(() => {
    useViewer.setState({ panes: [], anchors: [], activePane: 0, linked: true });
  });

  it("moves a 1 mm series five slices for every slice of a 5 mm series", () => {
    mount([pane(10, 80, 5), pane(100, 400, 1)], [10, 100]);
    useViewer.getState().setIndex(0, 20); // +10 slices, +50 mm
    expect(indices()).toEqual([20, 150]);
  });

  it("holds a ten-centimetre table offset instead of collapsing it", () => {
    // The second study started 100 mm higher up the patient. Anchored at the
    // slices the reader actually matched, a two-slice move stays a two-slice
    // move -- it does not snap the panes onto a shared coordinate.
    mount([pane(10, 80, 5), pane(120, 400, 1)], [10, 120]);
    useViewer.getState().setIndex(0, 12); // +10 mm
    expect(indices()).toEqual([12, 130]);
  });

  it("does not drift over a long scroll out and back", () => {
    mount([pane(40, 200, 2.5), pane(100, 500, 1)], [40, 100]);
    const viewer = useViewer.getState();
    for (let i = 0; i < 60; i += 1) viewer.stepIndex(0, 1);
    expect(indices()).toEqual([100, 250]);
    for (let i = 0; i < 60; i += 1) viewer.stepIndex(0, -1);
    expect(indices()).toEqual([40, 100]);
  });

  it("lets a pane clamp at the end of its stack without dragging the other", () => {
    mount([pane(10, 80, 5), pane(395, 400, 1)], [10, 395]);
    useViewer.getState().setIndex(0, 20); // would put the partner at 445
    expect(indices()).toEqual([20, 399]);
    // And coming back is still measured from the anchor, so the partner
    // recovers exactly rather than staying stuck at the end.
    useViewer.getState().setIndex(0, 10);
    expect(indices()).toEqual([10, 395]);
  });

  it("leaves the other pane alone when the link is off", () => {
    mount([pane(10, 80, 5), pane(100, 400, 1)], [10, 100]);
    useViewer.setState({ linked: false });
    useViewer.getState().setIndex(0, 20);
    expect(indices()).toEqual([20, 100]);
  });

  it("does not drag a pane cut on a different plane", () => {
    mount([pane(10, 80, 5), pane(100, 400, 1, "cor")], [10, 100]);
    useViewer.getState().setIndex(0, 20);
    expect(indices()).toEqual([20, 100]);
  });

  it("takes the current positions as the new anchor when the link is switched on", () => {
    mount([pane(10, 80, 5), pane(100, 400, 1)], [0, 0]);
    useViewer.setState({ linked: false });
    useViewer.getState().setLinked(true);
    expect(useViewer.getState().anchors).toEqual([10, 100]);
    // Turning it on moved nothing.
    expect(indices()).toEqual([10, 100]);
  });

  it("re-anchors where a finding put the panes", () => {
    mount([pane(10, 80, 5), pane(100, 400, 1)], [10, 100]);
    useViewer.getState().anchorAt([
      { paneIndex: 0, index: 30 },
      { paneIndex: 1, index: 42 },
    ]);
    expect(useViewer.getState().anchors).toEqual([30, 42]);
    useViewer.getState().setIndex(0, 31); // +5 mm from the new anchor
    expect(indices()).toEqual([31, 47]);
  });
});
