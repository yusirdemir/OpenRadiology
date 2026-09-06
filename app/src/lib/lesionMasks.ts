import type { LesionSegmentation, LesionSlice, PlaneName, Voxel } from "../api/types";

/** Pixel-centre coordinates; reformats reverse the native slice axis. */
export function projectVoxel(v: Voxel, plane: PlaneName, depth: number) {
  const [z, r, c] = v;
  return plane === "ax" ? { index: z, row: r, col: c }
    : plane === "cor" ? { index: r, row: depth - 1 - z, col: c }
    : { index: c, row: depth - 1 - z, col: r };
}
export function unprojectVoxel(plane: PlaneName, index: number, row: number, col: number, depth: number): Voxel {
  return plane === "ax" ? [index, row, col]
    : plane === "cor" ? [depth - 1 - row, index, col] : [depth - 1 - row, col, index];
}
export function unpackSlice(slice: LesionSlice): Uint8Array {
  const cols = slice.col_max - slice.col_min + 1;
  const mask = new Uint8Array((slice.row_max - slice.row_min + 1) * cols);
  for (let i = 0; i < slice.runs.length; i += 3) {
    const row = slice.runs[i]! - slice.row_min;
    const first = row * cols + slice.runs[i + 1]! - slice.col_min;
    mask.fill(255, first, row * cols + slice.runs[i + 2]! - slice.col_min + 1);
  }
  return mask;
}
export function prepareMasks(seg: LesionSegmentation): LesionSegmentation {
  for (const plane of Object.values(seg.planes)) for (const slice of plane) slice.mask = unpackSlice(slice);
  const [nz, nr, nc] = seg.mask_shape_zyx;
  const [z0, r0, c0] = seg.mask_origin_zyx;
  const mask = new Uint8Array(nz * nr * nc);
  for (const slice of seg.planes.ax) {
    const z = slice.index - z0;
    for (let i = 0; i < slice.runs.length; i += 3) {
      const row = slice.runs[i]! - r0;
      const begin = (z * nr + row) * nc + slice.runs[i + 1]! - c0;
      mask.fill(255, begin, (z * nr + row) * nc + slice.runs[i + 2]! - c0 + 1);
    }
  }
  seg.mask3d = mask;
  const raw = Uint8Array.from(atob(seg.hu_data), (c) => c.charCodeAt(0));
  const view = new DataView(raw.buffer);
  seg.hu3d = new Float32Array(raw.length / 2);
  for (let i = 0; i < seg.hu3d.length; i++) seg.hu3d[i] = view.getInt16(i * 2, true);
  seg.hu_data = "";
  const packed = new Uint8Array(mask.length * 2);
  const [lo,hi] = seg.heat_range;
  for (let i=0;i<mask.length;i++) { packed[i*2]=mask[i]!; packed[i*2+1]=Math.round(Math.max(0,Math.min(1,(seg.hu3d[i]!-lo)/Math.max(1,hi-lo)))*255); }
  seg.rgba3d = packed;
  seg.slices = seg.planes.ax;
  return seg;
}
