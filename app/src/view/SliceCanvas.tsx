/**
 * The viewer.
 *
 * Two stacked canvases: a WebGL2 one that draws the patient, and a 2D one that
 * draws everything the interface adds -- the focus ring, the scale bar, the
 * orientation letters. They are separate on purpose. Annotation is crisp at
 * device resolution, and nothing drawn here can ever be mistaken for something
 * the scanner recorded.
 *
 * Everything about this file is arranged around one number: sixty frames a
 * second while the wheel is turning. That rules out the obvious React shape.
 * A render loop whose effect depends on `view`, `window`, `markers` and a
 * cursor position is torn down and rebuilt several times per wheel notch, and
 * a cursor readout kept in component state re-renders the whole subtree on
 * every mouse move. So the loop is mounted once and reads live values out of a
 * ref, the cursor readout is written straight into the DOM, and the loop skips
 * its work entirely when nothing has changed. React is left to do what it is
 * good at -- deciding what exists -- and never asked to run the animation.
 */
import { useCallback, useEffect, useRef } from "react";
import type { SliceImage } from "../api/types";
import { PIXEL_INSPECT_SCALE, SliceRenderer } from "../gl/renderer";
import {
  canvasToImage,
  displayRect,
  imageToCanvas,
  screenPxPerImagePx,
  zoomAbout,
  type ImageGeometry,
  type ViewState,
} from "../gl/viewport";
import { edgeLabels, scaleBarLength } from "../lib/orientation";
import type { AttestationTracker } from "../lib/attestation";
import type { SliceSource } from "../lib/sliceCache";
import { useViewer, type WindowSetting } from "../state/viewer";
import type { LesionSlice, PlaneName, PleuralClosure } from "../api/types";

/**
 * What the interface draws on top of the patient.
 *
 *  - `focus`  the finding the reader has selected: a clean ring, nothing inside
 *             it, so the tissue it circles stays visible,
 *  - `clean`  the same anatomical place on a study where the finding is gone:
 *             a green check, and deliberately never a red ring,
 *  - `quiet`  another finding that happens to fall on this slice.
 */
export interface CanvasMarker {
  kind: "focus" | "clean" | "quiet";
  row: number;
  col: number;
  /** Diameter of the thing being circled. 0 means the outline is unknown. */
  sizeMm?: number;
  /** The measured segment, drawn so the number can be checked against it. */
  caliper?: [{ row: number; col: number }, { row: number; col: number }] | null;
  /**
   * The lesion's own pixels, as `row, first, last` triples.
   *
   * When the engine has grown the region this is its exact shape, and it is
   * what gets drawn -- outline and heat both. A lesion is lobulated and, where
   * it meets the pleura, flat; a circle at its diameter is a claim about
   * extent that the pixels do not support.
   */
  runs?: number[] | null;
  label?: string;
  /** "good" draws the caliper in the resolved colour: a measurement of something that is gone. */
  tone?: "good";
  lesion?: { id: string; slice: LesionSlice; heatRange: [number, number]; plane: PlaneName; seriesUid: string };
}

interface Props {
  source: SliceSource;
  index: number;
  count: number;
  window: WindowSetting;
  view: ViewState;
  markers: CanvasMarker[];
  showMarkers: boolean;
  iop?: number[];
  showGrid: boolean;
  attest?: AttestationTracker;
  studyUid: string;
  active: boolean;
  onView: (view: ViewState) => void;
  onIndex: (index: number) => void;
  onWindow: (patch: Partial<WindowSetting>) => void;
  onPick?: (row: number, col: number) => void;
  crosshair?: { row: number; col: number };
  onImage?: (image: SliceImage | null) => void;
  /**
   * Median Hounsfield value and air fraction in a small box at the focus mark,
   * measured on the very pixels being displayed. Reported once per slice so
   * the pane above can say whether the address matches its own claim.
   */
  onSample?: (sample: { medianHu: number; airFraction: number } | null) => void;
  onActivate?: () => void;
  onFailure?: (message: string | null) => void;
}

/** Wheel delta that advances one slice. Tuned for a trackpad, fine for a mouse. */
const WHEEL_NOTCH = 24;

const CLEAN = "#4fd6a0";
const QUIET = "rgba(255,255,255,0.42)";

export function SliceCanvas(props: Props) {
  const layers = useViewer((s) => s.layers);
  const layersRef = useRef(layers);
  layersRef.current = layers;
  const glRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const dwellRef = useRef<SVGCircleElement>(null);
  const dwellWrapRef = useRef<HTMLDivElement>(null);
  const readoutRef = useRef<HTMLDivElement>(null);
  const positionRef = useRef<HTMLSpanElement>(null);
  const veilRef = useRef<HTMLDivElement>(null);
  const hintRef = useRef<HTMLParagraphElement>(null);
  const pendingRef = useRef<HTMLParagraphElement>(null);
  const hintTimer = useRef<number | null>(null);

  const rendererRef = useRef<SliceRenderer | null>(null);
  const imageRef = useRef<SliceImage | null>(null);
  const sizeRef = useRef({ width: 0, height: 0 });
  const cursorRef = useRef<{ row: number; col: number; value: number | null } | null>(null);
  const dirtyRef = useRef(true);
  const directionRef = useRef(0);
  const wheelRef = useRef(0);
  const frameRef = useRef(0);

  // Every render refreshes the box the loop reads from. The loop itself never
  // restarts, so a wheel gesture costs one uniform write, not a teardown.
  const live = useRef(props);
  live.current = props;
  useEffect(() => {
    dirtyRef.current = true;
  });

  // -- renderer ------------------------------------------------------------
  useEffect(() => {
    const canvas = glRef.current;
    if (!canvas) return;
    try {
      rendererRef.current = new SliceRenderer(canvas);
    } catch (error) {
      live.current.onFailure?.(error instanceof Error ? error.message : String(error));
    }
    return () => {
      rendererRef.current?.dispose();
      rendererRef.current = null;
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      sizeRef.current = { width: entry.contentRect.width, height: entry.contentRect.height };
      dirtyRef.current = true;
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // -- pixels --------------------------------------------------------------
  /**
   * Put an image on screen and tell the readout which one it is.
   *
   * The number under the dwell ring is the slice *being displayed*, never the
   * slice that was requested. On a link too slow to keep up the two differ for
   * a second or two, and a viewer that shows one image while captioning it
   * with another slice's number is the worst thing this application could do.
   */
  const adopt = useCallback((image: SliceImage) => {
    imageRef.current = image;
    dirtyRef.current = true;
    live.current.onImage?.(image);
    if (positionRef.current) positionRef.current.textContent = `${image.index + 1}`;
    live.current.onSample?.(sampleAt(image, live.current.markers));
  }, []);

  const { source, index, count } = props;
  useEffect(() => {
    let cancelled = false;
    const settle = () => {
      if (hintTimer.current !== null) {
        window.clearTimeout(hintTimer.current);
        hintTimer.current = null;
      }
      if (veilRef.current) veilRef.current.style.opacity = "0";
      if (hintRef.current) hintRef.current.style.opacity = "0";
    };

    const cached = source.peek(index);
    if (cached) {
      adopt(cached);
      settle();
    } else {
      /*
       * The requested slice is still on the wire.
       *
       * Rather than freeze, show the nearest slice that *is* in hand and let
       * the readout name it: scrolling through a stack that is arriving slowly
       * then moves, roughly, instead of stopping dead. This is only honest
       * because the number on screen is the number of the image on screen --
       * it goes amber the moment it differs from the slice being asked for,
       * and the real one replaces it as soon as it lands.
       */
      const near = source.nearest(index, 8);
      if (near && near !== imageRef.current) adopt(near);
      if (veilRef.current) veilRef.current.style.opacity = near ? "0.55" : "1";
      if (pendingRef.current) pendingRef.current.textContent = `${index + 1}. kesit bekleniyor`;
      if (hintTimer.current === null) {
        hintTimer.current = window.setTimeout(() => {
          hintTimer.current = null;
          if (hintRef.current) hintRef.current.style.opacity = "1";
        }, 1200);
      }
    }

    // One statement of intent: here, going this way, in a stack this long. The
    // source decides what to fetch, the shared scheduler decides when.
    source.focus(index, directionRef.current, count);

    void source
      .load(index)
      .then((loaded) => {
        if (cancelled) return;
        adopt(loaded);
        live.current.onFailure?.(null);
        settle();
      })
      .catch((error: unknown) => {
        if (cancelled || (error as Error)?.name === "AbortError") return;
        live.current.onFailure?.(error instanceof Error ? error.message : String(error));
        settle();
      });

    return () => {
      cancelled = true;
      if (hintTimer.current !== null) {
        window.clearTimeout(hintTimer.current);
        hintTimer.current = null;
      }
    };
  }, [source, index, count, adopt]);

  // A source only spends the shared connection budget while its pane is on
  // screen: a reconstruction the reader switched away from must not compete
  // with the one they are looking at.
  useEffect(() => {
    source.setPaused(false);
    return () => source.setPaused(true);
  }, [source]);

  // -- the loop ------------------------------------------------------------
  useEffect(() => {
    const gl = glRef.current;
    const overlay = overlayRef.current;
    if (!gl || !overlay) return;
    const loop = (now: number) => {
      frameRef.current = requestAnimationFrame(loop);
      const p = live.current;
      const image = imageRef.current;
      const size = sizeRef.current;
      const renderer = rendererRef.current;
      if (!image || size.width <= 0 || size.height <= 0) return;

      // The attestation ledger is fed every frame: the loop that just drew the
      // image is the only place that truthfully knows what is on screen, how
      // large, and whether the window has focus.
      if (p.attest) {
        const scale = screenPxPerImagePx(
          { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx },
          size,
          p.view,
        );
        p.attest.observe(
          p.active && image.sopUid
            ? {
                studyUid: p.studyUid,
                seriesUid: image.seriesUid,
                sopUid: image.sopUid,
                plane: image.plane,
                index: image.index,
                scale,
                windowCenter: p.window.center,
                windowWidth: p.window.width,
              }
            : null,
          now,
        );
        paintDwell(dwellRef.current, dwellWrapRef.current, p.attest.progress(now), p.attest.satisfied);
      }

      // The one place that can tell the truth about lag: it knows both the
      // slice on screen and the slice being asked for. When they differ the
      // number goes amber, so a stale frame can never pass for a fresh one.
      const behind = image.index !== p.index || image.plane !== p.source.key.plane || image.seriesUid !== p.source.key.series;
      if (positionRef.current && positionRef.current.dataset.behind !== String(behind)) {
        positionRef.current.dataset.behind = String(behind);
        positionRef.current.style.color = behind ? "var(--color-amber-400)" : "var(--color-chalk-200)";
      }

      if (!dirtyRef.current) return;
      dirtyRef.current = false;

      const dpr = globalThis.devicePixelRatio || 1;
      const validMarkers = p.showMarkers && !behind && image.mipMm === 0 ? p.markers : [];
      const layer = layersRef.current;
      const selected = validMarkers.find((m) => m.lesion?.slice.index === image.index && m.lesion.plane === image.plane && m.lesion.seriesUid === image.seriesUid)?.lesion;
      if (renderer) {
        renderer.resize(size.width, size.height, dpr);
        renderer.draw(image, p.view, {
          center: p.window.center,
          width: p.window.width,
          invert: p.window.invert,
        }, undefined, selected ? { ...selected, heat: layer.heat, contour: layer.contour, alpha: layer.opacity } : undefined);
      }
      drawOverlay(overlay, image, size, p.view, dpr, {
        markers: validMarkers,
        calipers: layer.caliper,
        crosshair: !behind ? p.crosshair : undefined,
        showGrid: p.showGrid,
        iop: p.iop,
        cursor: cursorRef.current,
      });
    };
    frameRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frameRef.current);
  }, []);

  // -- interaction ---------------------------------------------------------
  const pointAt = useCallback((clientX: number, clientY: number) => {
    const host = hostRef.current;
    const image = imageRef.current;
    if (!host || !image) return null;
    const rect = host.getBoundingClientRect();
    const geometry: ImageGeometry = { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx };
    return canvasToImage(geometry, sizeRef.current, live.current.view, clientX - rect.left, clientY - rect.top);
  }, []);

  /*
   * Wheel handling is a native, non-passive listener on purpose. React attaches
   * wheel listeners passively, so `preventDefault` inside an `onWheel` prop is
   * ignored and the page scrolls underneath the study while the reader tries to
   * move through slices.
   */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const handler = (event: WheelEvent) => {
      const image = imageRef.current;
      if (!image) return;
      event.preventDefault();
      const p = live.current;
      p.onActivate?.();
      const rect = host.getBoundingClientRect();
      if (event.shiftKey || event.ctrlKey || event.metaKey) {
        const geometry: ImageGeometry = { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx };
        p.onView(
          zoomAbout(
            geometry,
            sizeRef.current,
            p.view,
            event.deltaY < 0 ? 1.12 : 1 / 1.12,
            event.clientX - rect.left,
            event.clientY - rect.top,
          ),
        );
        return;
      }
      // A trackpad emits many small deltas per notch; accumulate so one
      // physical gesture does not fly through twenty slices.
      wheelRef.current += event.deltaY;
      const step = Math.trunc(wheelRef.current / WHEEL_NOTCH);
      if (step !== 0) {
        wheelRef.current -= step * WHEEL_NOTCH;
        directionRef.current = Math.sign(step);
        const next = p.index + step;
        // A slice already in hand goes up on the next frame rather than after
        // the next render. State still travels the normal path; this only
        // removes React from between the wheel and the pixels.
        const ready = next >= 0 && next < p.count ? p.source.peek(next) : undefined;
        if (ready) adopt(ready);
        p.onIndex(next);
      }
    };
    host.addEventListener("wheel", handler, { passive: false });
    return () => host.removeEventListener("wheel", handler);
  }, [adopt]);

  const drag = useRef<{ mode: "pan" | "window"; x: number; y: number; view: ViewState; win: WindowSetting } | null>(
    null,
  );

  const onPointerDown = useCallback((event: React.PointerEvent) => {
    const p = live.current;
    p.onActivate?.();
    try {
      (event.target as Element).setPointerCapture?.(event.pointerId);
    } catch {
      /* the drag simply ends at the element boundary */
    }
    if (p.onPick && event.button === 0 && !event.altKey && !event.shiftKey) {
      const point = pointAt(event.clientX, event.clientY);
      const image = imageRef.current;
      if (point?.inside && image?.index === p.index && image.plane === p.source.key.plane && image.seriesUid === p.source.key.series) {
        p.onPick(Math.floor(point.row), Math.floor(point.col));
      }
      return;
    }
    const windowing = event.button === 2 || event.altKey;
    drag.current = {
      mode: windowing ? "window" : "pan",
      x: event.clientX,
      y: event.clientY,
      view: p.view,
      win: p.window,
    };
  }, []);

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      const image = imageRef.current;
      const point = pointAt(event.clientX, event.clientY);
      if (point?.inside && image) {
        const row = Math.floor(point.row);
        const col = Math.floor(point.col);
        const raw = image.pixels[row * image.cols + col];
        const value = raw === undefined ? null : Number(raw);
        cursorRef.current = { row, col, value };
        // Written straight into the DOM: a readout that went through component
        // state would re-render this subtree on every mouse move.
        if (readoutRef.current) {
          readoutRef.current.textContent = value === null ? "" : `${Math.round(value)} HU`;
        }
      } else {
        cursorRef.current = null;
        if (readoutRef.current) readoutRef.current.textContent = "";
      }

      const state = drag.current;
      if (!state) return;
      const dx = event.clientX - state.x;
      const dy = event.clientY - state.y;
      const p = live.current;
      if (state.mode === "pan") {
        p.onView({ ...state.view, panX: state.view.panX + dx, panY: state.view.panY + dy });
      } else {
        // Horizontal widens, vertical brightens: the convention every reader
        // already has in their hands from other workstations.
        p.onWindow({
          width: Math.max(1, state.win.width + dx * Math.max(2, state.win.width / 200)),
          center: state.win.center - dy * Math.max(1, state.win.width / 300),
          name: "custom",
        });
      }
    },
    [pointAt],
  );

  const endDrag = useCallback(() => {
    drag.current = null;
  }, []);

  return (
    <div
      ref={hostRef}
      className="relative h-full w-full overflow-hidden bg-ink-1000"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onPointerLeave={() => {
        endDrag();
        cursorRef.current = null;
        if (readoutRef.current) readoutRef.current.textContent = "";
      }}
      onContextMenu={(event) => event.preventDefault()}
      style={{ cursor: "grab" }}
    >
      <canvas ref={glRef} className="absolute inset-0 h-full w-full" />
      <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />

      {/* A slice that is not in hand yet dims rather than blanks: a black flash
          between two images reads as a fault, and it happens ten times a second
          during a fast scroll. */}
      <div
        ref={veilRef}
        className="pointer-events-none absolute inset-0 grid place-items-center bg-ink-1000/45 opacity-0 transition-opacity duration-150"
      >
        <div className="flex flex-col items-center gap-2.5">
          <span className="h-6 w-6 rounded-full border-2 border-amber-400/25 border-t-amber-400 spin" />
          <p ref={pendingRef} className="readout text-[11px] text-chalk-400" />
          <p
            ref={hintRef}
            className="max-w-[17rem] text-center text-[11.5px] leading-relaxed text-chalk-600 opacity-0 transition-opacity duration-300"
          >
            Kesitler bu makinede yavaş geliyor. Ekranda duran görüntü gerçek; sol üstteki sayı hangi
            kesite baktığınızı söyler.
          </p>
        </div>
      </div>

      <div className="pointer-events-none absolute left-3 top-2.5 flex items-center gap-2">
        <div ref={dwellWrapRef} title="Bu kesit tasdik defterine yazılacak kadar görüntülendiğinde dolar.">
          <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden>
            <circle cx="9" cy="9" r="7" fill="none" stroke="rgba(255,255,255,0.14)" strokeWidth="1.5" />
            <circle
              ref={dwellRef}
              cx="9"
              cy="9"
              r="7"
              fill="none"
              stroke="#ffb454"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 7}
              strokeDashoffset={2 * Math.PI * 7}
              transform="rotate(-90 9 9)"
            />
          </svg>
        </div>
        <span className="readout text-[11px] text-chalk-400">
          <span ref={positionRef} className="text-chalk-200">
            {props.index + 1}
          </span>
          <span className="text-chalk-700">/{props.count || "—"}</span>
        </span>
      </div>

      <div
        ref={readoutRef}
        className="readout pointer-events-none absolute bottom-2.5 right-3 text-[11px] text-chalk-400"
      />
    </div>
  );
}

// ------------------------------------------------------------------ overlay
interface OverlayContext {
  calipers: boolean;
  crosshair?: { row: number; col: number };
  markers: CanvasMarker[];
  showGrid: boolean;
  iop?: number[];
  cursor: { row: number; col: number; value: number | null } | null;
}

function drawOverlay(
  canvas: HTMLCanvasElement,
  image: SliceImage,
  size: { width: number; height: number },
  view: ViewState,
  dpr: number,
  context: OverlayContext,
): void {
  const width = Math.round(size.width * dpr);
  const height = Math.round(size.height * dpr);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size.width, size.height);

  const geometry: ImageGeometry = { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx };
  const rect = displayRect(geometry, size, view);
  const scale = screenPxPerImagePx(geometry, size, view);
  const at = (row: number, col: number) => imageToCanvas(geometry, size, view, row, col);

  // -- pixel grid: only when a sample is unambiguously visible -------------
  if (context.showGrid && scale >= PIXEL_INSPECT_SCALE) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x, rect.y, rect.width, rect.height);
    ctx.clip();
    ctx.strokeStyle = "rgba(255,255,255,0.15)";
    ctx.lineWidth = 0.5;
    const stepX = rect.width / image.cols;
    const stepY = rect.height / image.rows;
    for (let col = Math.max(0, Math.floor(-rect.x / stepX)); col <= Math.min(image.cols, Math.ceil((size.width - rect.x) / stepX)); col += 1) {
      const x = rect.x + col * stepX;
      ctx.beginPath();
      ctx.moveTo(x, rect.y);
      ctx.lineTo(x, rect.y + rect.height);
      ctx.stroke();
    }
    for (let row = Math.max(0, Math.floor(-rect.y / stepY)); row <= Math.min(image.rows, Math.ceil((size.height - rect.y) / stepY)); row += 1) {
      const y = rect.y + row * stepY;
      ctx.beginPath();
      ctx.moveTo(rect.x, y);
      ctx.lineTo(rect.x + rect.width, y);
      ctx.stroke();
    }
    ctx.restore();
  }

  // -- markers -------------------------------------------------------------
  // Quiet first, so the focus ring is never drawn under another mark.
  const ordered = [...context.markers].sort((a, b) => weight(a.kind) - weight(b.kind));
  for (const marker of ordered) {
    const point = at(marker.row, marker.col);
    if (marker.kind === "focus") {
      drawFocus(ctx, point, marker, context.calipers, at);
      if (marker.lesion?.plane === "ax" && marker.lesion.slice.closure?.length) drawClosures(ctx, marker.lesion.slice.closure, at, point);
    }
    else if (marker.kind === "clean") drawClean(ctx, point, marker);
    else drawQuiet(ctx, point, marker);
  }

  if (context.crosshair) {
    const p = at(context.crosshair.row + 0.5, context.crosshair.col + 0.5);
    ctx.save(); ctx.strokeStyle = "rgba(98,229,234,0.65)"; ctx.lineWidth = 0.75;
    ctx.setLineDash([5, 4]); ctx.beginPath();
    ctx.moveTo(rect.x, p.y); ctx.lineTo(p.x - 8, p.y);
    ctx.moveTo(p.x + 8, p.y); ctx.lineTo(rect.x + rect.width, p.y);
    ctx.moveTo(p.x, rect.y); ctx.lineTo(p.x, p.y - 8);
    ctx.moveTo(p.x, p.y + 8); ctx.lineTo(p.x, rect.y + rect.height);
    ctx.stroke(); ctx.restore();
  }
  // -- orientation letters --------------------------------------------------
  const labels = edgeLabels(context.iop, image.plane);
  ctx.fillStyle = "rgba(236,235,231,0.42)";
  ctx.font = "600 11px 'IBM Plex Sans Variable', sans-serif";
  ctx.textAlign = "center";
  if (labels.top) ctx.fillText(labels.top, size.width / 2, 16);
  if (labels.bottom) ctx.fillText(labels.bottom, size.width / 2, size.height - 30);
  ctx.textAlign = "left";
  if (labels.left) ctx.fillText(labels.left, 9, size.height / 2);
  ctx.textAlign = "right";
  if (labels.right) ctx.fillText(labels.right, size.width - 9, size.height / 2);
  ctx.textAlign = "left";

  // -- scale bar ------------------------------------------------------------
  const mmPerScreenPx = (image.mmPerPx[1] * image.cols) / Math.max(rect.width, 1);
  const bar = scaleBarLength(Math.min(130, size.width * 0.24), mmPerScreenPx);
  const barY = size.height - 18;
  ctx.strokeStyle = "rgba(236,235,231,0.5)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(13, barY);
  ctx.lineTo(13 + bar.px, barY);
  ctx.moveTo(13, barY - 3.5);
  ctx.lineTo(13, barY + 3.5);
  ctx.moveTo(13 + bar.px, barY - 3.5);
  ctx.lineTo(13 + bar.px, barY + 3.5);
  ctx.stroke();
  ctx.fillStyle = "rgba(236,235,231,0.5)";
  ctx.font = "500 10px 'IBM Plex Mono', monospace";
  ctx.fillText(`${bar.mm} mm`, 13, barY - 7);
}

function weight(kind: CanvasMarker["kind"]): number {
  return kind === "quiet" ? 0 : kind === "clean" ? 1 : 2;
}

/**
 * The tissue under the focus mark, straight out of the samples on screen.
 *
 * Nine by nine around the mark, median rather than mean so one bright rib edge
 * cannot carry the answer, plus the fraction of the box that is air. This is
 * the number that decides whether a cited address is on the thing its sentence
 * describes.
 */
function sampleAt(
  image: SliceImage,
  markers: CanvasMarker[],
): { medianHu: number; airFraction: number } | null {
  const focus = markers.find((m) => m.kind === "focus");
  if (!focus) return null;
  const values: number[] = [];
  let air = 0;
  for (let dr = -4; dr <= 4; dr += 1) {
    const row = focus.row + dr;
    if (row < 0 || row >= image.rows) continue;
    for (let dc = -4; dc <= 4; dc += 1) {
      const col = focus.col + dc;
      if (col < 0 || col >= image.cols) continue;
      const value = Number(image.pixels[row * image.cols + col]);
      if (!Number.isFinite(value)) continue;
      values.push(value);
      if (value < -400) air += 1;
    }
  }
  if (!values.length) return null;
  values.sort((a, b) => a - b);
  return {
    medianHu: values[Math.floor(values.length / 2)] as number,
    airFraction: air / values.length,
  };
}

/** Calipers share voxel-centre geometry with the mask; no radial fallback. */
function drawFocus(
  ctx: CanvasRenderingContext2D,
  point: { x: number; y: number },
  marker: CanvasMarker,
  showCaliper: boolean,
  at: (row: number, col: number) => { x: number; y: number },
): void {
  ctx.save();
  if (showCaliper && marker.caliper) {
    const [a, b] = marker.caliper.map((p) => at(p.row + 0.5, p.col + 0.5));
    if (a && b) {
      const angle = Math.atan2(b.y - a.y, b.x - a.x);
      const ink = marker.tone === "good" ? CLEAN : "#fff6d8";
      ctx.strokeStyle = ink; ctx.lineWidth = 1.3;
      ctx.shadowColor = "#000"; ctx.shadowBlur = 2;
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      for (const [p, direction] of [[a, 1], [b, -1]] as const) {
        const nx = -Math.sin(angle) * 4, ny = Math.cos(angle) * 4;
        ctx.moveTo(p.x - nx, p.y - ny); ctx.lineTo(p.x + nx, p.y + ny);
        ctx.moveTo(p.x + direction * Math.cos(angle) * 6 + nx, p.y + direction * Math.sin(angle) * 6 + ny);
        ctx.lineTo(p.x, p.y);
        ctx.lineTo(p.x + direction * Math.cos(angle) * 6 - nx, p.y + direction * Math.sin(angle) * 6 - ny);
      }
      ctx.stroke(); ctx.shadowBlur = 0;
      if (marker.label) pill(ctx, marker.label, (a.x + b.x) / 2 + 6, (a.y + b.y) / 2 - 18, ink, "#12191d");
    }
  } else if (marker.lesion && marker.label) {
    // A grown shape with no chord to draw (a fluid layer) still says its number.
    pill(ctx, marker.label, point.x + 10, point.y - 24, "#fff6d8", "#12191d");
  } else if (!marker.lesion) {
    // A location marker carries no extent claim.
    ctx.strokeStyle = "#70dfe5"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(point.x - 4, point.y); ctx.lineTo(point.x + 4, point.y);
    ctx.moveTo(point.x, point.y - 4); ctx.lineTo(point.x, point.y + 4); ctx.stroke();
  }
  ctx.restore();
}

/**
 * The pleural tangent closure: where the lesion meets the chest wall there is
 * no HU edge, so the boundary there is the wall's own arc continued between
 * the two tangent points. Drawn distinctly from the observed outline so the
 * reader sees which part of the contour is geometry rather than contrast.
 */
function drawClosures(
  ctx: CanvasRenderingContext2D,
  closures: PleuralClosure[],
  at: (row: number, col: number) => { x: number; y: number },
  centre: { x: number; y: number },
): void {
  ctx.save();
  const longest = closures.reduce((best, c) => (c.chord_mm > best.chord_mm ? c : best), closures[0]!);
  for (const closure of closures) {
    if (closure.chord_mm < 2) continue;
    const pts = closure.arc.map(([r, c]) => at(r + 0.5, c + 0.5));
    ctx.shadowColor = "#000"; ctx.shadowBlur = 3;
    ctx.strokeStyle = "#ffd166"; ctx.lineWidth = 1.6; ctx.setLineDash([5, 3]);
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    ctx.stroke();
    ctx.setLineDash([]); ctx.shadowBlur = 0;
    for (const [r, c] of [closure.p1, closure.p2]) {
      const p = at(r + 0.5, c + 0.5);
      ctx.fillStyle = "#ffd166"; ctx.strokeStyle = "#12191d"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    }
    // One label per slice, pushed outward from the lesion so it never sits on the caliper.
    const mid = pts[Math.floor(pts.length / 2)];
    if (mid && closure === longest) {
      const dx = mid.x - centre.x, dy = mid.y - centre.y, d = Math.hypot(dx, dy) || 1;
      const label = `plevral yay ${closure.chord_mm.toFixed(0)} mm`;
      ctx.font = "600 11.5px 'IBM Plex Sans Variable', sans-serif";
      const x = mid.x + (dx / d) * 22 - (dx < 0 ? ctx.measureText(label).width + 16 : 0);
      pill(ctx, label, x, mid.y + (dy / d) * 22 - 9, "#ffd166", "#12191d");
    }
  }
  ctx.restore();
}

function drawClean(ctx: CanvasRenderingContext2D, point: { x: number; y: number }, marker: CanvasMarker): void {
  ctx.save();
  const radius = 11;
  ctx.fillStyle = "rgba(8,10,9,0.72)";
  ctx.beginPath();
  ctx.arc(point.x, point.y, radius, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = CLEAN;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.strokeStyle = CLEAN;
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(point.x - 4.4, point.y + 0.2);
  ctx.lineTo(point.x - 1.2, point.y + 3.6);
  ctx.lineTo(point.x + 4.8, point.y - 3.6);
  ctx.stroke();

  if (marker.label) {
    pill(ctx, marker.label, point.x + radius + 8, point.y - radius - 6, CLEAN, "#0a0b0e");
  }
  ctx.restore();
}

/** Another finding that happens to land on this slice. Present, not shouting. */
function drawQuiet(ctx: CanvasRenderingContext2D, point: { x: number; y: number }, marker: CanvasMarker): void {
  ctx.save();
  ctx.strokeStyle = "rgba(0,0,0,0.5)";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
  ctx.stroke();
  ctx.strokeStyle = QUIET;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  ctx.arc(point.x, point.y, 9, 0, Math.PI * 2);
  ctx.stroke();
  if (marker.label) {
    ctx.font = "500 10px 'IBM Plex Mono', monospace";
    ctx.fillStyle = "rgba(0,0,0,0.72)";
    const w = ctx.measureText(marker.label).width;
    ctx.fillRect(point.x + 12, point.y - 14, w + 8, 14);
    ctx.fillStyle = QUIET;
    ctx.fillText(marker.label, point.x + 16, point.y - 3.5);
  }
  ctx.restore();
}

function pill(
  ctx: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  border: string,
  ink: string,
): void {
  ctx.font = "600 11.5px 'IBM Plex Sans Variable', sans-serif";
  const width = ctx.measureText(text).width + 15;
  const height = 20;
  const radius = 5;
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + width, y, x + width, y + height, radius);
  ctx.arcTo(x + width, y + height, x, y + height, radius);
  ctx.arcTo(x, y + height, x, y, radius);
  ctx.arcTo(x, y, x + width, y, radius);
  ctx.closePath();
  ctx.fillStyle = ink === "#ffffff" ? "rgba(14,10,10,0.94)" : border;
  ctx.fill();
  if (ink === "#ffffff") {
    ctx.strokeStyle = border;
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.fillStyle = ink;
  ctx.fillText(text, x + 7.5, y + 14);
}

/**
 * The dwell ring, written straight into the SVG.
 *
 * It fills while an image stays on screen at full resolution in a focused
 * window, and turns green when the view has earned an attestation. It is the
 * one continuously animated thing in the interface, because it is the one
 * thing that is continuously happening -- and it costs two attribute writes a
 * frame rather than a React render.
 */
function paintDwell(
  circle: SVGCircleElement | null,
  wrap: HTMLDivElement | null,
  progress: number,
  done: boolean,
): void {
  if (!circle) return;
  const circumference = 2 * Math.PI * 7;
  const offset = circumference * (1 - Math.min(1, progress));
  const next = String(offset.toFixed(1));
  if (circle.getAttribute("stroke-dashoffset") !== next) circle.setAttribute("stroke-dashoffset", next);
  const colour = done ? "#4fd6a0" : "#ffb454";
  if (circle.getAttribute("stroke") !== colour) circle.setAttribute("stroke", colour);
  if (wrap) {
    const title = done
      ? "Bu kesit görüntülendi ve tasdik defterine yazıldı."
      : "Kesit yeterince görüntülendiğinde tasdik edilir.";
    if (wrap.title !== title) wrap.title = title;
  }
}
