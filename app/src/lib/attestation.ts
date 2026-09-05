/**
 * Client half of view attestation.
 *
 * The renderer knows exactly which image is on screen, how large it is drawn,
 * and whether the window has focus. This tracker turns that into segments of
 * uninterrupted viewing and reports the ones that satisfy the policy. The
 * server decides what to accept; nothing here can grant an attestation the
 * server would refuse, and the thresholds are duplicated only so the interface
 * can show a live progress ring rather than guess.
 *
 * A segment ends when the displayed image changes, the window loses focus, or
 * the document is hidden. Losing focus does not pause a segment, it ends it:
 * "I had it on screen for four seconds, two of them while reading mail" is not
 * looking at it.
 */
import type { PlaneName, ViewEventPayload } from "../api/types";

export interface DwellPolicy {
  minDwellMs: number;
  minScale: number;
  requireFocus: boolean;
}

export const DEFAULT_POLICY: DwellPolicy = { minDwellMs: 400, minScale: 1, requireFocus: true };

export interface DisplayState {
  studyUid: string;
  seriesUid: string;
  sopUid: string;
  plane: PlaneName;
  index: number;
  scale: number;
  windowCenter: number;
  windowWidth: number;
  /** Set for a rendered contact sheet rather than a native slice. */
  pagePath?: string;
}

interface Segment {
  state: DisplayState;
  startedAt: number;
  minScale: number;
}

function identity(state: DisplayState): string {
  return state.pagePath ? `page:${state.pagePath}` : `${state.seriesUid}|${state.plane}|${state.index}`;
}

export class AttestationTracker {
  private segment: Segment | null = null;
  private queue: ViewEventPayload[] = [];
  private reported = new Set<string>();
  private timer: number | null = null;

  constructor(
    private send: (events: ViewEventPayload[]) => Promise<unknown>,
    private policy: DwellPolicy = DEFAULT_POLICY,
    private flushIntervalMs = 2500,
  ) {}

  setPolicy(policy: DwellPolicy): void {
    this.policy = policy;
  }

  /** Fraction of the dwell requirement the current segment has completed. */
  progress(now = performance.now()): number {
    if (!this.segment) return 0;
    if (this.segment.minScale < this.policy.minScale) return 0;
    return Math.min(1, (now - this.segment.startedAt) / this.policy.minDwellMs);
  }

  /** True once the image on screen has been looked at long enough to count. */
  get satisfied(): boolean {
    return this.progress() >= 1;
  }

  get currentKey(): string | null {
    return this.segment ? identity(this.segment.state) : null;
  }

  /**
   * Report what is on screen right now. Safe to call every frame: it only does
   * work when the displayed image or the viewing conditions actually change.
   */
  observe(state: DisplayState | null, now = performance.now()): void {
    const focused = !this.policy.requireFocus || (document.hasFocus() && document.visibilityState === "visible");
    if (!state || !focused) {
      this.close(now);
      return;
    }
    const key = identity(state);
    if (!this.segment || identity(this.segment.state) !== key) {
      this.close(now);
      this.segment = { state, startedAt: now, minScale: state.scale };
      return;
    }
    this.segment.state = state;
    this.segment.minScale = Math.min(this.segment.minScale, state.scale);
  }

  /** End the current segment, queueing it if it satisfies the policy. */
  close(now = performance.now()): void {
    const segment = this.segment;
    this.segment = null;
    if (!segment) return;
    const dwell = now - segment.startedAt;
    if (dwell < this.policy.minDwellMs || segment.minScale < this.policy.minScale) return;
    const key = identity(segment.state);
    if (this.reported.has(key)) return;
    this.reported.add(key);
    const { state } = segment;
    this.queue.push({
      study_uid: state.studyUid,
      series_uid: state.seriesUid,
      sop_uid: state.sopUid,
      plane: state.plane,
      index: state.index,
      dwell_ms: dwell,
      scale: segment.minScale,
      focused: true,
      visible: true,
      window_center: state.windowCenter,
      window_width: state.windowWidth,
      ...(state.pagePath ? { source: "page" as const, page_path: state.pagePath } : { source: "viewer" as const }),
    });
    this.schedule();
  }

  private schedule(): void {
    if (this.timer !== null) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.flushIntervalMs);
  }

  async flush(): Promise<void> {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (!this.queue.length) return;
    const batch = this.queue;
    this.queue = [];
    try {
      await this.send(batch);
    } catch {
      // Keep the evidence rather than lose it: a sidecar hiccup should not
      // quietly erase the fact that these images were read.
      this.queue.unshift(...batch);
      for (const event of batch) {
        this.reported.delete(event.page_path ? `page:${event.page_path}` : `${event.series_uid}|${event.plane}|${event.index}`);
      }
      this.schedule();
    }
  }

  /**
   * Indices of this series and plane already attested in this session.
   *
   * Used only to shade the filmstrip. The server remains the authority on what
   * counts; this is the reader's own progress made visible while they work.
   */
  attestedIndices(seriesUid: string, plane: PlaneName): Set<number> {
    const prefix = `${seriesUid}|${plane}|`;
    const out = new Set<number>();
    for (const key of this.reported) {
      if (key.startsWith(prefix)) {
        const index = Number(key.slice(prefix.length));
        if (Number.isFinite(index)) out.add(index);
      }
    }
    return out;
  }

  /** Reset per-session memory when the reader opens a different study. */
  reset(): void {
    this.segment = null;
    this.reported.clear();
  }

  async dispose(): Promise<void> {
    this.close();
    await this.flush();
  }
}
