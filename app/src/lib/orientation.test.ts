import { describe, expect, it } from "vitest";
import { directionLetter, edgeLabels, scaleBarLength } from "./orientation";

const HFS = [1, 0, 0, 0, 1, 0]; // the canonical head-first-supine axial
const FEET_FIRST = [-1, 0, 0, 0, -1, 0];

describe("direction letters follow the cosines, not the common case", () => {
  it("names the cardinal directions in LPS", () => {
    expect(directionLetter([1, 0, 0])).toBe("L");
    expect(directionLetter([-1, 0, 0])).toBe("R");
    expect(directionLetter([0, 1, 0])).toBe("P");
    expect(directionLetter([0, -1, 0])).toBe("A");
    expect(directionLetter([0, 0, 1])).toBe("H");
    expect(directionLetter([0, 0, -1])).toBe("F");
  });

  it("refuses to name an oblique direction", () => {
    expect(directionLetter([0.6, 0.6, 0.53])).toBe("");
  });
});

describe("edge labels", () => {
  it("puts the patient's left on the right of a head-first axial", () => {
    expect(edgeLabels(HFS, "ax")).toEqual({ right: "L", left: "R", bottom: "P", top: "A" });
  });

  it("flips with the acquisition instead of assuming", () => {
    expect(edgeLabels(FEET_FIRST, "ax")).toEqual({ right: "R", left: "L", bottom: "A", top: "P" });
  });

  it("puts superior at the top of a coronal reformat", () => {
    const labels = edgeLabels(HFS, "cor");
    expect(labels.top).toBe("H");
    expect(labels.bottom).toBe("F");
    expect(labels.right).toBe("L");
  });

  it("labels a sagittal reformat by the row direction", () => {
    const labels = edgeLabels(HFS, "sag");
    expect(labels.top).toBe("H");
    expect(labels.right).toBe("P");
    expect(labels.left).toBe("A");
  });

  it("shows nothing rather than a wrong letter when geometry is missing", () => {
    expect(edgeLabels(undefined, "ax")).toEqual({});
  });
});

describe("scale bar", () => {
  it("picks a round millimetre length that fits", () => {
    expect(scaleBarLength(120, 0.76)).toEqual({ mm: 50, px: 50 / 0.76 });
    expect(scaleBarLength(20, 0.76)).toEqual({ mm: 10, px: 10 / 0.76 });
  });
});
