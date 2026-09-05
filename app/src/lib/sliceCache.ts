/**
 * Slice delivery: cache, in-flight de-duplication and idle prefetch.
 *
 * Scrolling a study issues requests far faster than they complete. Three rules
 * keep it smooth:
 *
 *  - the slice the reader is looking at always wins; overtaken requests are
 *    aborted rather than left to finish into a cache nobody will read,
 *  - a slice already in flight is never requested twice,
 *  - neighbours are fetched while the machine is idle, so a steady scroll runs
 *    entirely out of cache.
 */
import { fetchSlice, type SliceQuery } from "../api/client";
import type { PlaneName, SliceImage } from "../api/types";

const DEFAULT_CAPACITY = 96;

export interface SourceKey {
  study: string;
  series: string;
  plane: PlaneName;
  mipMm: number;
  allowTilt: boolean;
}

export class SliceSource {
  private cache = new Map<number, SliceImage>();
  private inflight = new Map<number, { promise: Promise<SliceImage>; controller: AbortController }>();
  private idleHandle: number | null = null;

  constructor(
    readonly key: SourceKey,
    private capacity = DEFAULT_CAPACITY,
  ) {}

  get id(): string {
    return `${this.key.series}|${this.key.plane}|${this.key.mipMm}`;
  }

  peek(index: number): SliceImage | undefined {
    const hit = this.cache.get(index);
    if (hit) {
      // Refresh recency: Map preserves insertion order, so re-inserting is the
      // cheapest possible LRU bump.
      this.cache.delete(index);
      this.cache.set(index, hit);
    }
    return hit;
  }

  has(index: number): boolean {
    return this.cache.has(index);
  }

  async load(index: number): Promise<SliceImage> {
    const cached = this.peek(index);
    if (cached) return cached;
    const pending = this.inflight.get(index);
    if (pending) return pending.promise;

    const controller = new AbortController();
    const query: SliceQuery = {
      study: this.key.study,
      series: this.key.series,
      plane: this.key.plane,
      index,
      mipMm: this.key.mipMm,
      allowTilt: this.key.allowTilt,
    };
    const promise = fetchSlice(query, controller.signal)
      .then((image) => {
        this.store(index, image);
        return image;
      })
      .finally(() => {
        this.inflight.delete(index);
      });
    this.inflight.set(index, { promise, controller });
    return promise;
  }

  private store(index: number, image: SliceImage): void {
    this.cache.set(index, image);
    while (this.cache.size > this.capacity) {
      const oldest = this.cache.keys().next();
      if (oldest.done) break;
      this.cache.delete(oldest.value);
    }
  }

  /** Abort in-flight requests that are no longer near the reader's position. */
  focus(index: number, keepRadius = 3): void {
    for (const [pending, entry] of this.inflight) {
      if (Math.abs(pending - index) > keepRadius) {
        entry.controller.abort();
        this.inflight.delete(pending);
      }
    }
  }

  /** Fetch neighbours while the machine is idle, nearest first. */
  prefetch(around: number, radius: number, count: number): void {
    if (this.idleHandle !== null) return;
    const schedule =
      typeof requestIdleCallback === "function"
        ? requestIdleCallback
        : (fn: () => void) => window.setTimeout(fn, 32);
    this.idleHandle = schedule(() => {
      this.idleHandle = null;
      const wanted: number[] = [];
      for (let offset = 1; offset <= radius && wanted.length < count; offset += 1) {
        for (const candidate of [around + offset, around - offset]) {
          if (candidate >= 0 && candidate < Number.MAX_SAFE_INTEGER && !this.has(candidate) && !this.inflight.has(candidate)) {
            wanted.push(candidate);
          }
        }
      }
      for (const index of wanted.slice(0, count)) void this.load(index).catch(() => undefined);
    }) as unknown as number;
  }

  dispose(): void {
    for (const entry of this.inflight.values()) entry.controller.abort();
    this.inflight.clear();
    this.cache.clear();
    if (this.idleHandle !== null && typeof cancelIdleCallback === "function") cancelIdleCallback(this.idleHandle);
    this.idleHandle = null;
  }
}

/** One source per (series, plane, slab) combination, so switching planes is free. */
export class SliceSourcePool {
  private sources = new Map<string, SliceSource>();

  for(key: SourceKey): SliceSource {
    const id = `${key.study}|${key.series}|${key.plane}|${key.mipMm}|${key.allowTilt}`;
    let source = this.sources.get(id);
    if (!source) {
      source = new SliceSource(key);
      this.sources.set(id, source);
    }
    return source;
  }

  release(seriesUid: string): void {
    for (const [id, source] of [...this.sources]) {
      if (id.includes(`|${seriesUid}|`)) {
        source.dispose();
        this.sources.delete(id);
      }
    }
  }

  disposeAll(): void {
    for (const source of this.sources.values()) source.dispose();
    this.sources.clear();
  }
}
