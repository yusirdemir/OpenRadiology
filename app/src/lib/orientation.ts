/**
 * Which way the patient is facing, derived from the direction cosines.
 *
 * Getting this wrong flips left and right, which in radiology is not a cosmetic
 * error. So the labels are computed from `ImageOrientationPatient` rather than
 * assumed from the usual head-first-supine case, and when the geometry is
 * oblique enough that a single letter would be misleading, no letter is shown.
 *
 * DICOM patient coordinates are LPS: +x to the patient's left, +y posterior,
 * +z superior.
 */
import type { PlaneName } from "../api/types";

export type Edge = "top" | "bottom" | "left" | "right";
export type Labels = Partial<Record<Edge, string>>;

const AXIS_LETTERS: [string, string][] = [
  ["R", "L"], // -x, +x
  ["A", "P"], // -y, +y
  ["F", "H"], // -z, +z
];

/** Letter for a unit direction, or "" when it is too oblique to name. */
export function directionLetter(vector: readonly number[], obliqueTolerance = 0.75): string {
  let best = 0;
  let bestValue = 0;
  for (let axis = 0; axis < 3; axis += 1) {
    const value = Math.abs(vector[axis] ?? 0);
    if (value > bestValue) {
      bestValue = value;
      best = axis;
    }
  }
  if (bestValue < obliqueTolerance) return "";
  const pair = AXIS_LETTERS[best];
  if (!pair) return "";
  return (vector[best] ?? 0) >= 0 ? pair[1] : pair[0];
}

function opposite(letter: string): string {
  for (const [negative, positive] of AXIS_LETTERS) {
    if (letter === positive) return negative;
    if (letter === negative) return positive;
  }
  return "";
}

/**
 * Edge labels for a displayed image.
 *
 * `iop` is the six-element ImageOrientationPatient: the first triple is the
 * direction of increasing column, the second the direction of increasing row.
 * Reformats are rendered superior-at-top by the engine (`Volume.coronal` and
 * `Volume.sagittal` flip vertically), which is why their vertical axis is
 * labelled from the slice normal rather than from a row direction.
 */
export function edgeLabels(iop: readonly number[] | undefined, plane: PlaneName): Labels {
  if (!iop || iop.length < 6) return {};
  const colDir = iop.slice(0, 3);
  const rowDir = iop.slice(3, 6);
  const normal = [
    (colDir[1] ?? 0) * (rowDir[2] ?? 0) - (colDir[2] ?? 0) * (rowDir[1] ?? 0),
    (colDir[2] ?? 0) * (rowDir[0] ?? 0) - (colDir[0] ?? 0) * (rowDir[2] ?? 0),
    (colDir[0] ?? 0) * (rowDir[1] ?? 0) - (colDir[1] ?? 0) * (rowDir[0] ?? 0),
  ];

  if (plane === "ax") {
    const right = directionLetter(colDir);
    const bottom = directionLetter(rowDir);
    return { right, left: opposite(right), bottom, top: opposite(bottom) };
  }

  // Reformats put superior at the top; the horizontal axis is whichever
  // in-plane direction survives the reformat.
  const up = directionLetter(normal);
  const horizontal = plane === "cor" ? directionLetter(colDir) : directionLetter(rowDir);
  return { top: up, bottom: opposite(up), right: horizontal, left: opposite(horizontal) };
}

/** A round number of millimetres that fits comfortably in the given width. */
export function scaleBarLength(maxPx: number, mmPerPx: number): { mm: number; px: number } {
  const candidates = [1, 2, 5, 10, 20, 50, 100, 200];
  const maxMm = maxPx * mmPerPx;
  let chosen = candidates[0] as number;
  for (const candidate of candidates) {
    if (candidate <= maxMm) chosen = candidate;
  }
  return { mm: chosen, px: chosen / mmPerPx };
}
