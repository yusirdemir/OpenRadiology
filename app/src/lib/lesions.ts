import { api } from "../api/client";
import type { LesionProfile, LesionSegmentation, PlaneName, Voxel } from "../api/types";
export interface LesionSeed {
  studyPath: string; seriesUid: string; index: number; row: number; col: number;
  sizeMm?: number; profile?: LesionProfile; positive?: Voxel[]; negative?: Voxel[]; brushMm?: number;
}
export function seedKey(seed: LesionSeed): string {
  return JSON.stringify([seed.studyPath, seed.seriesUid, seed.index, seed.row, seed.col,
    seed.profile ?? "auto", seed.positive ?? [], seed.negative ?? [], seed.brushMm ?? 1.5]);
}
const inflight = new Map<string, Promise<LesionSegmentation>>();
function prepare(seg: LesionSegmentation): Promise<LesionSegmentation> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./lesion.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event) => { worker.terminate(); event.data.error ? reject(new Error(event.data.error)) : resolve(event.data.result); };
    worker.onerror = (event) => { worker.terminate(); reject(new Error(event.message)); };
    worker.postMessage(seg);
  });
}
export function segmentLesion(seed: LesionSeed): Promise<LesionSegmentation> {
  const key = seedKey(seed);
  const existing = inflight.get(key);
  if (existing) return existing;
  const result = api.lesion({ study: seed.studyPath, series: seed.seriesUid,
    index: seed.index, row: Math.round(seed.row), col: Math.round(seed.col), profile: seed.profile,
    positive: seed.positive, negative: seed.negative, brush_mm: seed.brushMm,
  }).then(prepare).catch((error) => { inflight.delete(key); throw error; });
  inflight.set(key, result);
  if (inflight.size > 8) inflight.delete(inflight.keys().next().value!);
  return result;
}
const indices = new WeakMap<LesionSegmentation, Record<PlaneName, Map<number, LesionSegmentation["slices"][number]>>>();
export function sliceOf(seg: LesionSegmentation | null, index: number, plane: PlaneName = "ax") {
  if (!seg) return null;
  let maps = indices.get(seg);
  if (!maps) {
    maps = Object.fromEntries(Object.entries(seg.planes).map(([p, slices]) => [p, new Map(slices.map((s) => [s.index, s]))])) as NonNullable<typeof maps>;
    indices.set(seg, maps);
  }
  return maps[plane].get(index) ?? null;
}
export function outlineIsEstablished(seg: LesionSegmentation | null) { return Boolean(seg?.established); }
export function profileFor(text: string): LesionProfile {
  const t = text.toLocaleLowerCase("tr");
  if (/efüzyon|effusion|plevral sıvı/.test(t)) return "effusion";
  if (/(?:^|[\s,(])bül(?:[\s,.)]|$)|bulla/.test(t)) return "bulla";
  if (/kist|cyst/.test(t)) return "cyst";
  if (/kitle|nodül|nodule|mass|solid/.test(t)) return "solid";
  return "auto";
}

export function lesionMarker(seg: LesionSegmentation, index: number, plane: PlaneName) {
  const slice = sliceOf(seg, index, plane);
  if (!slice) return null;
  // Fluid is a layer: its number is a thickness at the focus, not a chord across the crescent.
  const layered = seg.tissue === "effusion" && slice.thickness_mm !== undefined;
  return { kind: "focus" as const, row: slice.row, col: slice.col, sizeMm: slice.diameter_mm,
    caliper: layered ? null : slice.caliper,
    label: layered ? `${slice.thickness_mm!.toFixed(1)} mm kalınlık · otomatik` : `${slice.diameter_mm.toFixed(1)} mm · otomatik`,
    lesion: { id: seg.id, slice, heatRange: seg.heat_range, plane, seriesUid: seg.series_uid } };
}
