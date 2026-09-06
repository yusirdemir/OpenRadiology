import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SliceImage } from "../api/types";
import { SliceSource } from "./sliceCache";

/**
 * Every request the source makes, held open until the test lets it finish.
 *
 * Nothing here is timing-dependent: the point of these tests is the *order*
 * requests are started in and how many are allowed to run at once, which is
 * what decides whether a slow link is spent on the slices under the reader's
 * cursor or on ones they have already scrolled past.
 */
const open = new Map<number, { resolve: () => void; reject: (e: Error) => void }>();
const started: number[] = [];

vi.mock("../api/client", () => ({
  fetchSlice: (query: { index: number }, signal?: AbortSignal) => {
    started.push(query.index);
    return new Promise<SliceImage>((resolve, reject) => {
      open.set(query.index, {
        resolve: () => resolve(image(query.index)),
        reject: (error: Error) => reject(error),
      });
      signal?.addEventListener("abort", () => {
        // `open` means "still on the wire": an aborted transfer leaves it, the
        // same way it frees the browser's connection slot.
        open.delete(query.index);
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    });
  },
}));

function image(index: number): SliceImage {
  return {
    pixels: new Int16Array(4),
    rows: 2,
    cols: 2,
    mmPerPx: [1, 1],
    plane: "ax",
    index,
    count: 200,
    sopUid: `sop-${index}`,
    instance: String(index),
    seriesUid: "series",
    studyUid: "study",
    zMm: index,
    mipMm: 0,
  };
}

/*
 * The scheduler is a module singleton, on purpose: the connection budget it
 * hands out is a property of the browser, not of one pane. That makes test
 * isolation the test's job -- every source a case creates is disposed
 * afterwards, which aborts its transfers and hands the slots back.
 */
const made: SliceSource[] = [];

function source(capacity?: number): SliceSource {
  const created = new SliceSource(
    { study: `/s/${made.length}/${Math.random()}`, series: "series", plane: "ax", mipMm: 0, allowTilt: false },
    capacity,
  );
  made.push(created);
  return created;
}

/**
 * A source that fetches only what it is explicitly asked for.
 *
 * `load` always goes to the network; the rolling prefetch does not run while
 * the pane is off screen. Pausing is therefore how a test examines the cache
 * without the lookahead filling it with slices the test never mentioned.
 */
function quietSource(capacity?: number): SliceSource {
  const created = source(capacity);
  created.setPaused(true);
  return created;
}

/** Complete one request and let the source's continuations run. */
async function deliver(index: number): Promise<void> {
  open.get(index)?.resolve();
  open.delete(index);
  await Promise.resolve();
  await Promise.resolve();
}

/** Ask for one slice and let it arrive. */
async function fetchOne(s: SliceSource, index: number): Promise<void> {
  const wanted = s.load(index);
  await Promise.resolve();
  await deliver(index);
  await wanted;
}

beforeEach(() => {
  open.clear();
  started.length = 0;
});

afterEach(async () => {
  for (const created of made) created.dispose();
  made.length = 0;
  open.clear();
  started.length = 0;
  await Promise.resolve();
});

describe("the shared scheduler", () => {
  /*
   * A slice is fetched as many small ranged requests, so the scheduler keeps
   * only a few slices in flight and lets those ranges fill the browser's
   * connections. Two of the four slots are held for slices somebody is
   * actually looking at, which is what makes a jump respond at once instead of
   * queueing behind the lookahead.
   */
  it("runs the visible slice plus lookahead, and keeps the reserve free", async () => {
    const s = source();
    s.focus(50, 1, 200);
    await Promise.resolve();
    // One visible plus two speculative; the rest of the budget is held for
    // whichever pane needs the image it is showing next.
    expect(open.size).toBe(3);
    expect(open.has(50)).toBe(true);
  });

  it("spends the lookahead on the slices the reader is scrolling towards", async () => {
    const s = source();
    s.focus(50, 1, 200);
    await Promise.resolve();
    expect([...open.keys()].sort((a, b) => a - b)).toEqual([50, 51, 52]);
  });

  it("follows the reader when they reverse", async () => {
    const s = source();
    s.focus(50, -1, 200);
    await Promise.resolve();
    expect([...open.keys()].sort((a, b) => a - b)).toEqual([48, 49, 50]);
  });

  /**
   * The guarantee that matters when two studies are open: lookahead for the
   * pane that asked first must never make the other pane's *visible* slice
   * wait. Without the reserve the second pane sits blank for the length of
   * several speculative transfers.
   */
  it("never lets lookahead push a visible slice out of the queue", async () => {
    const filling = source();
    filling.focus(10, 1, 200);
    await Promise.resolve();

    const other = source();
    void other.load(800).catch(() => undefined);
    await Promise.resolve();
    expect([...open.keys()]).toContain(800);
  });

  it("abandons a jumped-away-from request and re-aims, without exceeding the budget", async () => {
    const s = source();
    s.focus(10, 1, 200);
    await Promise.resolve();
    expect(open.has(10)).toBe(true);

    s.focus(120, 1, 200);
    await Promise.resolve();
    expect(open.size).toBeLessThanOrEqual(6);
    // Far enough that the old transfers were genuinely abandoned rather than
    // left to finish into a cache nobody will read.
    expect(open.has(10)).toBe(false);
    expect(open.has(120)).toBe(true);
  });

  it("never asks for a slice outside the stack", async () => {
    const s = source();
    s.focus(1, -1, 4);
    await Promise.resolve();
    for (const index of open.keys()) {
      expect(index).toBeGreaterThanOrEqual(0);
      expect(index).toBeLessThan(4);
    }
  });
});

describe("the cache", () => {
  it("answers instantly for a slice it holds", async () => {
    const s = quietSource();
    await fetchOne(s, 7);
    expect(s.peek(7)?.index).toBe(7);
    expect(s.has(7)).toBe(true);
  });

  it("offers the nearest slice it holds while the asked-for one is still coming", async () => {
    const s = quietSource();
    await fetchOne(s, 20);
    expect(s.nearest(23, 8)?.index).toBe(20);
    // Beyond the search radius it admits it has nothing rather than reaching
    // for an image from somewhere else in the patient.
    expect(s.nearest(40, 8)).toBeUndefined();
  });

  /**
   * The eviction rule that stops a slow link from thrashing. Prefetching marks
   * every arriving slice as the most recent thing in the cache, so a
   * least-recently-used policy throws away exactly the slices under the
   * reader's cursor. Distance from the reader is what predicts reuse.
   */
  it("evicts the slice furthest from the reader, not the oldest", async () => {
    const s = quietSource(4);
    for (const index of [100, 101, 102, 103]) await fetchOne(s, index);
    s.focus(103, 1, 200);
    await fetchOne(s, 104);

    expect(s.has(100)).toBe(false); // furthest from 103
    expect(s.has(103)).toBe(true);
    expect(s.has(104)).toBe(true);
  });

  it("reports which parts of the stack are ready", async () => {
    const s = quietSource();
    await fetchOne(s, 0);
    await fetchOne(s, 1);
    const bar = s.coverage(8, 4);
    expect(bar).toHaveLength(4);
    expect(bar[0]).toBeGreaterThan(0); // slices 0-1 live in the first bucket
    expect(bar[3]).toBe(0);
  });

  it("stops spending the link once its pane is off screen", async () => {
    const s = quietSource();
    s.focus(50, 1, 200);
    await Promise.resolve();
    expect(open.size).toBe(0);
  });
});
