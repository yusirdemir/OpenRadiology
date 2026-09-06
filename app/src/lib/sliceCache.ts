/**
 * Slice delivery: one scheduler, a cache per series, and a window that follows
 * the reader.
 *
 * The shape of this file is dictated by a measurement. A slice is 512 KiB of
 * calibrated samples, the engine produces one in 0.9 ms, and everything after
 * that is transport. On a healthy machine that transport is free; on a machine
 * whose loopback is being inspected it is not, and the same request takes
 * seconds. Two facts from measuring it decide the design:
 *
 *  - the stall is *per connection*, and concurrency scales linearly, so the
 *    six connections a browser allows per origin must be kept busy at all
 *    times rather than used one at a time,
 *  - a browser hands those six out first-come-first-served, so the moment more
 *    than six requests are outstanding the browser's queue decides the order
 *    and ours is ignored.
 *
 * Hence a single scheduler shared by every pane, holding the concurrency limit
 * itself and always starting the most useful slice next: the one on screen
 * first, then the ones just ahead of where the reader is travelling. And hence
 * the reluctance to abort: throwing away a half-received slice on a slow link
 * discards seconds of the only resource that is scarce.
 */
import { fetchSlice, type SliceQuery } from "../api/client";
import type { PlaneName, SliceImage } from "../api/types";

const DEFAULT_CAPACITY = 192;

/**
 * Requests in flight at once, across every pane.
 *
 * Deliberately small, because a slice is no longer one request: it is fetched
 * as ranges that themselves fill the browser's six connections. Letting six
 * whole slices go at once would put two hundred ranged requests in a queue this
 * scheduler cannot reorder, and the slice on screen would land behind the
 * lookahead.
 */
const MAX_IN_FLIGHT = 4;

/**
 * Slots that lookahead may never occupy.
 *
 * Two, because a comparison has two panes and each may be waiting on the slice
 * it is showing. Without the reserve the first pane to ask fills the budget
 * with its own lookahead and the other pane's visible slice queues behind it.
 * Reading ahead is worth a lot; it is never worth more than the image someone
 * is looking at right now.
 */
const RESERVED_FOR_VISIBLE = 2;

/** A request this far from the reader is genuinely abandoned, and worth aborting. */
const ABANDON_DISTANCE = 48;

/**
 * Slices queued but not yet started, per series.
 *
 * Short on purpose. The queue is refilled from the reader's *current*
 * position every time a request finishes, so a shallow queue that re-aims
 * constantly beats a deep one that is still working through where the reader
 * used to be. Over a minute of reading this still fills the whole series --
 * nearest first, from wherever they happen to be standing.
 */
const QUEUE_DEPTH = MAX_IN_FLIGHT * 3;

/** Priority penalty for a slice behind the direction of travel. */
const BACKWARD_PENALTY = 8;

interface Job {
  key: string;
  priority: number;
  start: () => void;
}

/**
 * The one queue.
 *
 * Priority is a small number: 0 is the slice being looked at right now, and it
 * grows with distance from the reader. A job already running is never
 * preempted -- on a slow link the bytes already transferred are worth more
 * than the ordering.
 */
class SliceScheduler {
  private queue: Job[] = [];
  /** key -> priority, so the reserve can tell lookahead from a visible slice. */
  private active = new Map<string, number>();

  submit(job: Job): void {
    const existing = this.queue.findIndex((entry) => entry.key === job.key);
    if (existing >= 0) {
      // Already queued: keep the more urgent of the two claims on it.
      const current = this.queue[existing] as Job;
      if (job.priority < current.priority) current.priority = job.priority;
      this.pump();
      return;
    }
    this.queue.push(job);
    this.pump();
  }

  cancel(key: string): void {
    this.queue = this.queue.filter((entry) => entry.key !== key);
  }

  done(key: string): void {
    this.active.delete(key);
    this.pump();
  }

  private pump(): void {
    for (;;) {
      if (this.active.size >= MAX_IN_FLIGHT || !this.queue.length) return;
      let speculative = 0;
      for (const priority of this.active.values()) if (priority > 0) speculative += 1;
      const lookaheadFull = speculative >= MAX_IN_FLIGHT - RESERVED_FOR_VISIBLE;

      let best = -1;
      for (let i = 0; i < this.queue.length; i += 1) {
        const candidate = this.queue[i] as Job;
        if (lookaheadFull && candidate.priority > 0) continue;
        if (best < 0 || candidate.priority < (this.queue[best] as Job).priority) best = i;
      }
      if (best < 0) return;
      const [job] = this.queue.splice(best, 1);
      if (!job) return;
      this.active.set(job.key, job.priority);
      job.start();
    }
  }
}

const scheduler = new SliceScheduler();

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
  private target = 0;
  private direction = 1;
  private limit = Number.MAX_SAFE_INTEGER;
  private paused = false;

  constructor(
    readonly key: SourceKey,
    private capacity = DEFAULT_CAPACITY,
  ) {}

  get id(): string {
    return `${this.key.study}|${this.key.series}|${this.key.plane}|${this.key.mipMm}`;
  }

  peek(index: number): SliceImage | undefined {
    const hit = this.cache.get(index);
    if (hit) {
      // Map preserves insertion order, so re-inserting is the cheapest LRU bump.
      this.cache.delete(index);
      this.cache.set(index, hit);
    }
    return hit;
  }

  has(index: number): boolean {
    return this.cache.has(index);
  }

  /**
   * The nearest slice already in hand.
   *
   * Used to keep something truthful on screen while the requested slice is
   * still on the wire. The caller is told which slice it actually got, and is
   * responsible for never labelling it as the one that was asked for.
   */
  nearest(index: number, radius = 6): SliceImage | undefined {
    for (let offset = 1; offset <= radius; offset += 1) {
      const behind = this.cache.get(index - offset);
      if (behind) return behind;
      const ahead = this.cache.get(index + offset);
      if (ahead) return ahead;
    }
    return undefined;
  }

  /**
   * Which parts of the stack are in hand, as a coarse bar.
   *
   * On a link fast enough to keep up this is always full and nobody notices
   * it. On a slow one it is the difference between "the application is broken"
   * and "the middle of the study is ready, the ends are still arriving" -- the
   * same reason a video player shows what it has buffered.
   */
  coverage(count: number, buckets: number): Uint8Array {
    const out = new Uint8Array(buckets);
    if (count <= 0) return out;
    const per = count / buckets;
    const tally = new Uint16Array(buckets);
    for (const index of this.cache.keys()) {
      if (index < 0 || index >= count) continue;
      const bucket = Math.min(buckets - 1, Math.floor(index / per));
      tally[bucket] = (tally[bucket] ?? 0) + 1;
    }
    for (let i = 0; i < buckets; i += 1) {
      const size = Math.max(1, Math.round(per));
      out[i] = Math.min(255, Math.round(((tally[i] ?? 0) / size) * 255));
    }
    return out;
  }

  async load(index: number): Promise<SliceImage> {
    const cached = this.peek(index);
    if (cached) return cached;
    return this.request(index, 0);
  }

  private request(index: number, priority: number): Promise<SliceImage> {
    const pending = this.inflight.get(index);
    if (pending) return pending.promise;

    const controller = new AbortController();
    const key = `${this.id}#${index}`;
    let begin: () => void = () => undefined;
    const promise = new Promise<SliceImage>((resolve, reject) => {
      begin = () => {
        const query: SliceQuery = {
          study: this.key.study,
          series: this.key.series,
          plane: this.key.plane,
          index,
          mipMm: this.key.mipMm,
          allowTilt: this.key.allowTilt,
        };
        fetchSlice(query, controller.signal)
          .then((image) => {
            this.store(index, image);
            resolve(image);
          })
          .catch(reject)
          .finally(() => {
            this.inflight.delete(index);
            scheduler.done(key);
            this.pump();
          });
      };
    });

    this.inflight.set(index, { promise, controller });
    scheduler.submit({ key, priority, start: () => begin() });
    return promise;
  }

  /**
   * Keep a slice, and if the cache is full drop the one furthest from the
   * reader.
   *
   * Least-recently-used is the wrong policy for a stack of images. Filling the
   * cache ahead of the reader marks every prefetched slice as the most recent
   * thing in it, so an LRU evicts precisely the slices under the reader's
   * cursor and the viewer thrashes. Distance is what actually predicts reuse
   * here, so distance is what decides.
   */
  private store(index: number, image: SliceImage): void {
    this.cache.set(index, image);
    while (this.cache.size > this.capacity) {
      let furthest = -1;
      let worst = -1;
      for (const held of this.cache.keys()) {
        const distance = Math.abs(held - this.target);
        if (distance > worst) {
          worst = distance;
          furthest = held;
        }
      }
      if (furthest < 0 || furthest === index) break;
      this.cache.delete(furthest);
    }
  }

  /**
   * Say where the reader is and which way they are going.
   *
   * This replaces the old pair of `focus` and `prefetch` calls. There is one
   * statement of intent -- "the reader is here, moving this way, in a stack
   * this long" -- and the source works out what to ask for and in what order.
   */
  focus(index: number, direction: number, count: number): void {
    this.target = index;
    if (direction !== 0) this.direction = Math.sign(direction);
    if (count > 0) this.limit = count;

    // A request the reader has travelled a long way from is genuinely
    // abandoned; anything nearer is left alone, because the bytes already on
    // the wire are worth more than the connection slot.
    const abandoned = [...this.inflight.keys()].filter((pending) => Math.abs(pending - index) > ABANDON_DISTANCE);
    for (const pending of abandoned) scheduler.cancel(`${this.id}#${pending}`);
    for (const pending of abandoned) {
      this.inflight.get(pending)?.controller.abort();
      this.inflight.delete(pending);
    }
    for (const pending of abandoned) scheduler.done(`${this.id}#${pending}`);
    this.pump();
  }

  /**
   * Stop asking for anything: this pane is no longer the one being read.
   *
   * A pane that switches reconstruction leaves its old source behind, and
   * without this that source keeps spending the same six connections as the
   * series the reader is actually looking at.
   */
  setPaused(paused: boolean): void {
    this.paused = paused;
    if (!paused) this.pump();
  }

  /**
   * Ask for the most useful slices that are still missing.
   *
   * There is no lookahead limit, because the series is finite and every slice
   * of it will eventually be wanted: what matters is only the order. Candidates
   * are gathered outward from the reader and ranked by distance, with a
   * penalty for slices behind the direction of travel, and the best handful are
   * handed to the shared scheduler. Called again on every completion, so the
   * front keeps re-aiming at wherever the reader now is.
   */
  private pump(): void {
    if (this.paused) return;
    const candidates: number[] = [];
    const want = (candidate: number) => {
      if (candidate < 0 || candidate >= this.limit) return;
      if (this.cache.has(candidate) || this.inflight.has(candidate)) return;
      candidates.push(candidate);
    };
    want(this.target);
    const reach = Math.min(this.limit, this.capacity);
    for (let offset = 1; offset <= reach && candidates.length < QUEUE_DEPTH * 3; offset += 1) {
      want(this.target + offset * this.direction);
      want(this.target - offset * this.direction);
    }

    const priority = (index: number) => {
      const distance = Math.abs(index - this.target);
      const backwards = (index - this.target) * this.direction < 0;
      return distance + (backwards ? BACKWARD_PENALTY : 0);
    };
    candidates.sort((a, b) => priority(a) - priority(b));
    for (const index of candidates.slice(0, QUEUE_DEPTH)) {
      void this.request(index, priority(index)).catch(() => undefined);
    }
  }

  dispose(): void {
    // Order matters. Releasing a slot lets the scheduler start the next job,
    // so everything this source has queued must be withdrawn *before* any of
    // it is released -- otherwise tearing a source down starts the very
    // requests the teardown is meant to cancel.
    this.paused = true;
    const keys = [...this.inflight.keys()].map((index) => `${this.id}#${index}`);
    for (const key of keys) scheduler.cancel(key);
    for (const entry of this.inflight.values()) entry.controller.abort();
    this.inflight.clear();
    for (const key of keys) scheduler.done(key);
    this.cache.clear();
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
