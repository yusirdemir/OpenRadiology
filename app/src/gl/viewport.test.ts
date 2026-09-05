import { describe, expect, it } from "vitest";
import {
  canvasToImage,
  displayRect,
  distanceMm,
  fitScale,
  imageToCanvas,
  screenPxPerImagePx,
  zoomAbout,
  type ImageGeometry,
} from "./viewport";

const axial: ImageGeometry = { rows: 512, cols: 512, mmPerPx: [0.76, 0.76] };
// A coronal reformat: 1 mm slice spacing vertically, 0.76 mm columns horizontally.
const coronal: ImageGeometry = { rows: 387, cols: 512, mmPerPx: [1.0, 0.76] };
const canvas = { width: 1000, height: 800 };

describe("layout is driven by millimetres, not pixel counts", () => {
  it("fits an isotropic image to the shorter canvas axis", () => {
    const rect = displayRect(axial, canvas, { zoom: 1, panX: 0, panY: 0 });
    expect(rect.width).toBeCloseTo(800, 6);
    expect(rect.height).toBeCloseTo(800, 6);
    expect(rect.x).toBeCloseTo(100, 6);
  });

  it("keeps a reformat's true proportions instead of stretching the patient", () => {
    const rect = displayRect(coronal, canvas, { zoom: 1, panX: 0, panY: 0 });
    // 512 * 0.76 = 389.12 mm wide, 387 * 1.0 = 387 mm tall: nearly square on screen.
    expect(rect.width / rect.height).toBeCloseTo(389.12 / 387, 6);
    // Pixel-count layout would have produced 512/387 = 1.32, a 25 % distortion.
    expect(rect.width / rect.height).not.toBeCloseTo(512 / 387, 2);
  });

  it("reports millimetres per screen pixel consistently", () => {
    expect(fitScale(axial, canvas)).toBeCloseTo(800 / (512 * 0.76), 6);
  });
});

describe("coordinate round trip", () => {
  it("maps canvas to image and back", () => {
    const view = { zoom: 2.5, panX: -40, panY: 17 };
    const point = imageToCanvas(axial, canvas, view, 128, 301);
    const back = canvasToImage(axial, canvas, view, point.x, point.y);
    expect(back.row).toBeCloseTo(128, 6);
    expect(back.col).toBeCloseTo(301, 6);
    expect(back.inside).toBe(true);
  });

  it("knows when the cursor is off the image", () => {
    const outside = canvasToImage(axial, canvas, { zoom: 1, panX: 0, panY: 0 }, 5, 5);
    expect(outside.inside).toBe(false);
  });
});

describe("attestation input", () => {
  it("is below one at fit-to-window for a large matrix", () => {
    expect(screenPxPerImagePx(axial, canvas, { zoom: 1, panX: 0, panY: 0 })).toBeCloseTo(800 / 512, 6);
  });

  it("crosses one when the reader zooms in", () => {
    const zoomed = screenPxPerImagePx(axial, canvas, { zoom: 1.5, panX: 0, panY: 0 });
    expect(zoomed).toBeGreaterThan(1);
  });
});

describe("zoom", () => {
  it("keeps the pixel under the cursor under the cursor", () => {
    const view = { zoom: 1, panX: 0, panY: 0 };
    const anchor = { x: 640, y: 300 };
    const before = canvasToImage(axial, canvas, view, anchor.x, anchor.y);
    const next = zoomAbout(axial, canvas, view, 1.8, anchor.x, anchor.y);
    const after = canvasToImage(axial, canvas, next, anchor.x, anchor.y);
    expect(after.row).toBeCloseTo(before.row, 4);
    expect(after.col).toBeCloseTo(before.col, 4);
  });

  it("clamps to the configured limits", () => {
    const view = { zoom: 1, panX: 0, panY: 0 };
    expect(zoomAbout(axial, canvas, view, 1000, 0, 0).zoom).toBe(40);
    expect(zoomAbout(axial, canvas, view, 0.0001, 0, 0).zoom).toBe(0.25);
  });
});

describe("distance uses the image's own calibration", () => {
  it("measures an axial diagonal in millimetres", () => {
    expect(distanceMm(axial, { row: 0, col: 0 }, { row: 3, col: 4 })).toBeCloseTo(5 * 0.76, 6);
  });

  it("uses different spacing per axis on a reformat", () => {
    expect(distanceMm(coronal, { row: 10, col: 0 }, { row: 0, col: 0 })).toBeCloseTo(10, 6);
    expect(distanceMm(coronal, { row: 0, col: 10 }, { row: 0, col: 0 })).toBeCloseTo(7.6, 6);
  });
});
