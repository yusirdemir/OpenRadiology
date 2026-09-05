/**
 * The viewer.
 *
 * Two stacked canvases: a WebGL2 one that draws the patient, and a 2D one that
 * draws everything the reader adds -- measurement geometry, the pixel grid, the
 * scale bar, orientation letters. They are separate on purpose. Annotation is
 * crisp at device resolution, and nothing the interface draws can ever be
 * mistaken for something the scanner recorded.
 *
 * The render loop also feeds the attestation tracker, because the only place
 * that truthfully knows what is on screen, how large, and whether the window
 * has focus is the loop that just drew it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import type { PendingPoint, ToolName, WindowSetting } from "../state/viewer";

export interface CanvasMarker {
  kind: "point" | "line" | "circle" | "box";
  points: PendingPoint[];
  radiusPx?: number;
  label?: string;
  tone?: "probe" | "amber" | "attest";
}

interface Props {
  source: SliceSource;
  index: number;
  window: WindowSetting;
  view: ViewState;
  tool: ToolName;
  pending: PendingPoint[];
  markers: CanvasMarker[];
  iop?: number[];
  showGrid: boolean;
  attest?: AttestationTracker;
  studyUid: string;
  active: boolean;
  onView: (view: ViewState) => void;
  onIndex: (index: number) => void;
  onWindow: (patch: Partial<WindowSetting>) => void;
  onPick: (point: PendingPoint) => void;
  onImage?: (image: SliceImage | null) => void;
  onActivate?: () => void;
}

/** Wheel delta that advances one slice. Tuned for a trackpad, fine for a mouse. */
const WHEEL_NOTCH = 28;

const TONE_COLOURS: Record<NonNullable<CanvasMarker["tone"]>, string> = {
  probe: "#7dd3fc",
  amber: "#ffb454",
  attest: "#4ade80",
};

export function SliceCanvas(props: Props) {
  const {
    source, index, window: win, view, tool, pending, markers, iop, showGrid,
    attest, studyUid, active, onView, onIndex, onWindow, onPick, onImage, onActivate,
  } = props;

  const glRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<SliceRenderer | null>(null);
  const imageRef = useRef<SliceImage | null>(null);
  const frameRef = useRef(0);
  const wheelAccumulator = useRef(0);

  const [image, setImage] = useState<SliceImage | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [cursor, setCursor] = useState<{ row: number; col: number; value: number | null } | null>(null);
  const [dwell, setDwell] = useState(0);
  const [failure, setFailure] = useState<string | null>(null);

  const geometry: ImageGeometry | null = useMemo(
    () => (image ? { rows: image.rows, cols: image.cols, mmPerPx: image.mmPerPx } : null),
    [image],
  );

  // -- renderer lifecycle ------------------------------------------------
  useEffect(() => {
    const canvas = glRef.current;
    if (!canvas) return;
    try {
      rendererRef.current = new SliceRenderer(canvas);
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
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
      const { width, height } = entry.contentRect;
      setSize({ width, height });
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  // -- pixels ------------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    const cached = source.peek(index);
    if (cached) {
      imageRef.current = cached;
      setImage(cached);
      onImage?.(cached);
    }
    source.focus(index);
    void source
      .load(index)
      .then((loaded) => {
        if (cancelled) return;
        imageRef.current = loaded;
        setImage(loaded);
        onImage?.(loaded);
        setFailure(null);
      })
      .catch((error: unknown) => {
        if (cancelled || (error as Error)?.name === "AbortError") return;
        setFailure(error instanceof Error ? error.message : String(error));
      });
    source.prefetch(index, 10, 6);
    return () => {
      cancelled = true;
    };
  }, [source, index, onImage]);

  // -- draw loop ---------------------------------------------------------
  useEffect(() => {
    const gl = glRef.current;
    const overlay = overlayRef.current;
    const renderer = rendererRef.current;
    if (!gl || !overlay || !renderer) return;

    const loop = () => {
      frameRef.current = requestAnimationFrame(loop);
      const current = imageRef.current;
      if (!current || size.width <= 0) return;
      const dpr = globalThis.devicePixelRatio || 1;
      renderer.resize(size.width, size.height, dpr);
      renderer.draw(current, view, { center: win.center, width: win.width, invert: win.invert });
      drawOverlay(overlay, current, size, view, dpr, {
        markers, pending, tool, showGrid, iop, cursor,
      });

      if (attest) {
        const scale = screenPxPerImagePx(
          { rows: current.rows, cols: current.cols, mmPerPx: current.mmPerPx },
          size,
          view,
        );
        attest.observe(
          active && current.sopUid
            ? {
                studyUid,
                seriesUid: current.seriesUid,
                sopUid: current.sopUid,
                plane: current.plane,
                index: current.index,
                scale,
                windowCenter: win.center,
                windowWidth: win.width,
              }
            : null,
        );
        setDwell(attest.progress());
      }
    };
    frameRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frameRef.current);
  }, [size, view, win, markers, pending, tool, showGrid, iop, cursor, attest, studyUid, active]);

  // -- interaction -------------------------------------------------------
  const positionOf = useCallback(
    (event: { clientX: number; clientY: number }) => {
      const host = hostRef.current;
      if (!host || !geometry) return null;
      const rect = host.getBoundingClientRect();
      return canvasToImage(geometry, size, view, event.clientX - rect.left, event.clientY - rect.top);
    },
    [geometry, size, view],
  );

  /**
   * Wheel handling is a native, non-passive listener on purpose.
   *
   * React attaches wheel listeners passively, so `preventDefault` inside a
   * `onWheel` prop is ignored and the page scrolls underneath the study while
   * the reader tries to move through slices. Registering it here with
   * `{ passive: false }` is the only way to own the gesture.
   */
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const handler = (event: WheelEvent) => {
      const current = imageRef.current;
      if (!current) return;
      event.preventDefault();
      onActivate?.();
      const rect = host.getBoundingClientRect();
      if (event.shiftKey || event.ctrlKey || event.metaKey) {
        const shape: ImageGeometry = { rows: current.rows, cols: current.cols, mmPerPx: current.mmPerPx };
        onView(
          zoomAbout(shape, size, view, event.deltaY < 0 ? 1.12 : 1 / 1.12,
                    event.clientX - rect.left, event.clientY - rect.top),
        );
        return;
      }
      // A trackpad emits many small deltas per notch; accumulate so one physical
      // gesture does not fly through twenty slices.
      wheelAccumulator.current += event.deltaY;
      const step = Math.trunc(wheelAccumulator.current / WHEEL_NOTCH);
      if (step !== 0) {
        wheelAccumulator.current -= step * WHEEL_NOTCH;
        onIndex(index + step);
      }
    };
    host.addEventListener("wheel", handler, { passive: false });
    return () => host.removeEventListener("wheel", handler);
  }, [size, view, index, onView, onIndex, onActivate]);

  const drag = useRef<{ mode: "pan" | "window" | null; x: number; y: number; view: ViewState; win: WindowSetting } | null>(null);

  const onPointerDown = useCallback(
    (event: React.PointerEvent) => {
      onActivate?.();
      const point = positionOf(event);
      // Pointer capture keeps a drag alive outside the canvas, but it throws
      // for a pointer id the element does not currently own. That must never
      // cost us the click itself.
      try {
        (event.target as Element).setPointerCapture?.(event.pointerId);
      } catch {
        /* the drag simply ends at the element boundary */
      }
      // Right button or alt+left sets window/level; middle or space-less drag pans.
      const windowing = event.button === 2 || event.altKey || tool === "window";
      const panning = event.button === 1 || (event.button === 0 && (event.shiftKey || tool === "browse"));
      if (windowing) {
        drag.current = { mode: "window", x: event.clientX, y: event.clientY, view, win };
        return;
      }
      if (event.button === 0 && tool !== "browse" && point?.inside) {
        onPick({ row: Math.round(point.row), col: Math.round(point.col) });
        return;
      }
      if (panning) drag.current = { mode: "pan", x: event.clientX, y: event.clientY, view, win };
    },
    [positionOf, tool, view, win, onPick, onActivate],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent) => {
      const point = positionOf(event);
      const current = imageRef.current;
      if (point?.inside && current) {
        const row = Math.floor(point.row);
        const col = Math.floor(point.col);
        // A readout of the very samples the engine sent, not a derived number.
        const value = current.pixels[row * current.cols + col];
        setCursor({ row, col, value: value === undefined ? null : Number(value) });
      } else {
        setCursor(null);
      }

      const state = drag.current;
      if (!state?.mode) return;
      const dx = event.clientX - state.x;
      const dy = event.clientY - state.y;
      if (state.mode === "pan") {
        onView({ ...state.view, panX: state.view.panX + dx, panY: state.view.panY + dy });
      } else {
        // Horizontal widens, vertical brightens: the convention every reader
        // already has in their hands from other workstations.
        onWindow({
          width: Math.max(1, state.win.width + dx * Math.max(2, state.win.width / 200)),
          center: state.win.center - dy * Math.max(1, state.win.width / 300),
          name: "custom",
        });
      }
    },
    [positionOf, onView, onWindow],
  );

  const endDrag = useCallback(() => {
    drag.current = null;
  }, []);

  const scale = geometry ? screenPxPerImagePx(geometry, size, view) : 0;

  return (
    <div
      ref={hostRef}
      className="relative h-full w-full overflow-hidden bg-ink-950"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onPointerLeave={() => {
        endDrag();
        setCursor(null);
      }}
      onContextMenu={(e) => e.preventDefault()}
      style={{ cursor: tool === "browse" ? "grab" : tool === "window" ? "ns-resize" : "crosshair" }}
    >
      <canvas ref={glRef} className="absolute inset-0 h-full w-full" />
      <canvas ref={overlayRef} className="pointer-events-none absolute inset-0 h-full w-full" />

      {failure && (
        <div className="absolute inset-0 grid place-items-center p-6 text-center">
          <p className="max-w-sm text-alarm-400">{failure}</p>
        </div>
      )}

      <ViewerHud
        image={image}
        cursor={cursor}
        scale={scale}
        dwell={dwell}
        win={win}
        attested={attest?.satisfied ?? false}
      />
    </div>
  );
}

// ------------------------------------------------------------------ overlay
interface OverlayContext {
  markers: CanvasMarker[];
  pending: PendingPoint[];
  tool: ToolName;
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
  const toCanvas = (p: PendingPoint) => imageToCanvas(geometry, size, view, p.row, p.col);
  const scale = screenPxPerImagePx(geometry, size, view);

  // -- pixel grid: only when a pixel is unambiguously visible ------------
  if (context.showGrid && scale >= PIXEL_INSPECT_SCALE) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(rect.x, rect.y, rect.width, rect.height);
    ctx.clip();
    ctx.strokeStyle = "rgba(255,255,255,0.16)";
    ctx.lineWidth = 0.5;
    const stepX = rect.width / image.cols;
    const stepY = rect.height / image.rows;
    const firstCol = Math.max(0, Math.floor(-rect.x / stepX));
    const lastCol = Math.min(image.cols, Math.ceil((size.width - rect.x) / stepX));
    for (let col = firstCol; col <= lastCol; col += 1) {
      const x = rect.x + col * stepX;
      ctx.beginPath();
      ctx.moveTo(x, rect.y);
      ctx.lineTo(x, rect.y + rect.height);
      ctx.stroke();
    }
    const firstRow = Math.max(0, Math.floor(-rect.y / stepY));
    const lastRow = Math.min(image.rows, Math.ceil((size.height - rect.y) / stepY));
    for (let row = firstRow; row <= lastRow; row += 1) {
      const y = rect.y + row * stepY;
      ctx.beginPath();
      ctx.moveTo(rect.x, y);
      ctx.lineTo(rect.x + rect.width, y);
      ctx.stroke();
    }
    ctx.restore();
  }

  // -- markers -----------------------------------------------------------
  ctx.lineWidth = 1.25;
  ctx.font = "500 11px 'IBM Plex Mono', monospace";
  for (const marker of [...context.markers, ...(context.pending.length ? [pendingMarker(context)] : [])]) {
    if (!marker) continue;
    const colour = TONE_COLOURS[marker.tone ?? "probe"];
    ctx.strokeStyle = colour;
    ctx.fillStyle = colour;
    const points = marker.points.map(toCanvas);
    const first = points[0];
    if (!first) continue;

    if (marker.kind === "line" && points[1]) {
      const second = points[1];
      ctx.beginPath();
      ctx.moveTo(first.x, first.y);
      ctx.lineTo(second.x, second.y);
      ctx.stroke();
      for (const p of [first, second]) tick(ctx, p);
    } else if (marker.kind === "circle") {
      const radius = ((marker.radiusPx ?? 3) / image.cols) * rect.width;
      ctx.beginPath();
      ctx.arc(first.x, first.y, Math.max(radius, 2), 0, Math.PI * 2);
      ctx.stroke();
      tick(ctx, first);
    } else if (marker.kind === "box" && points[1]) {
      const second = points[1];
      ctx.strokeRect(
        Math.min(first.x, second.x), Math.min(first.y, second.y),
        Math.abs(second.x - first.x), Math.abs(second.y - first.y),
      );
    } else {
      tick(ctx, first);
    }

    if (marker.label) {
      const anchor = points[points.length - 1] ?? first;
      ctx.fillStyle = "rgba(8,9,11,0.82)";
      const metrics = ctx.measureText(marker.label);
      ctx.fillRect(anchor.x + 8, anchor.y - 16, metrics.width + 8, 15);
      ctx.fillStyle = colour;
      ctx.fillText(marker.label, anchor.x + 12, anchor.y - 5);
    }
  }

  // -- orientation letters ------------------------------------------------
  const labels = edgeLabels(context.iop, image.plane);
  ctx.fillStyle = "rgba(236,234,229,0.5)";
  ctx.font = "600 12px 'IBM Plex Sans Variable', sans-serif";
  ctx.textAlign = "center";
  if (labels.top) ctx.fillText(labels.top, size.width / 2, 18);
  if (labels.bottom) ctx.fillText(labels.bottom, size.width / 2, size.height - 8);
  ctx.textAlign = "left";
  if (labels.left) ctx.fillText(labels.left, 10, size.height / 2);
  ctx.textAlign = "right";
  if (labels.right) ctx.fillText(labels.right, size.width - 10, size.height / 2);
  ctx.textAlign = "left";

  // -- scale bar ----------------------------------------------------------
  const mmPerScreenPx = (image.mmPerPx[1] * image.cols) / Math.max(rect.width, 1);
  const bar = scaleBarLength(Math.min(140, size.width * 0.25), mmPerScreenPx);
  const barY = size.height - 22;
  ctx.strokeStyle = "rgba(236,234,229,0.6)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(14, barY);
  ctx.lineTo(14 + bar.px, barY);
  ctx.moveTo(14, barY - 4);
  ctx.lineTo(14, barY + 4);
  ctx.moveTo(14 + bar.px, barY - 4);
  ctx.lineTo(14 + bar.px, barY + 4);
  ctx.stroke();
  ctx.fillStyle = "rgba(236,234,229,0.6)";
  ctx.font = "500 10px 'IBM Plex Mono', monospace";
  ctx.fillText(`${bar.mm} mm`, 14, barY - 8);
}

function tick(ctx: CanvasRenderingContext2D, point: { x: number; y: number }): void {
  ctx.beginPath();
  ctx.moveTo(point.x - 4, point.y);
  ctx.lineTo(point.x + 4, point.y);
  ctx.moveTo(point.x, point.y - 4);
  ctx.lineTo(point.x, point.y + 4);
  ctx.stroke();
}

function pendingMarker(context: OverlayContext): CanvasMarker | null {
  const points = context.pending;
  if (!points.length) return null;
  if (context.tool === "distance") return { kind: points.length >= 2 ? "line" : "point", points, tone: "amber" };
  if (context.tool === "roi") return { kind: "circle", points, radiusPx: 6, tone: "amber" };
  if (context.tool === "extent") return { kind: points.length >= 2 ? "box" : "point", points, tone: "amber" };
  return { kind: "point", points, tone: "amber" };
}

// ---------------------------------------------------------------------- HUD
interface HudProps {
  image: SliceImage | null;
  cursor: { row: number; col: number; value: number | null } | null;
  scale: number;
  dwell: number;
  win: WindowSetting;
  attested: boolean;
}

function ViewerHud({ image, cursor, scale, dwell, win, attested }: HudProps) {
  if (!image) return null;
  return (
    <>
      <div className="pointer-events-none absolute left-3 top-2.5 flex items-center gap-2">
        <DwellRing progress={dwell} done={attested} />
        <div className="readout text-[11px] leading-tight text-chalk-300">
          <div>
            {image.plane.toUpperCase()} {image.index + 1}
            <span className="text-chalk-600">/{image.count}</span>
            {image.mipMm > 0 && <span className="ml-1.5 text-amber-400">MIP {image.mipMm}mm</span>}
          </div>
          <div className="text-chalk-600">
            {image.instance ? `#${image.instance}` : ""} {image.zMm !== null ? `z ${image.zMm.toFixed(1)}` : ""}
          </div>
        </div>
      </div>

      <div className="readout pointer-events-none absolute right-3 top-2.5 text-right text-[11px] leading-tight text-chalk-300">
        <div>
          W {Math.round(win.width)} / L {Math.round(win.center)}
        </div>
        <div className="text-chalk-600">{scale.toFixed(2)} px/px</div>
      </div>

      {cursor && (
        <div className="readout pointer-events-none absolute bottom-2.5 right-3 text-right text-[11px] leading-tight">
          <div className="text-chalk-300">
            {cursor.value === null ? "—" : cursor.value.toFixed(cursor.value % 1 === 0 ? 0 : 2)}
            <span className="ml-1 text-chalk-600">okuma</span>
          </div>
          <div className="text-chalk-600">
            r{cursor.row} c{cursor.col}
          </div>
        </div>
      )}
    </>
  );
}

/**
 * The dwell ring: the attestation being earned, in real time.
 *
 * It fills while an image stays on screen at full resolution in a focused
 * window, and ticks over when the view counts. It is the one continuously
 * animated thing in the interface, because it is the one thing that is
 * continuously happening.
 */
function DwellRing({ progress, done }: { progress: number; done: boolean }) {
  const radius = 7;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
      <circle cx="9" cy="9" r={radius} fill="none" stroke="rgba(255,255,255,0.13)" strokeWidth="1.5" />
      <circle
        cx="9"
        cy="9"
        r={radius}
        fill="none"
        stroke={done ? "#4ade80" : "#ffb454"}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.min(1, progress))}
        transform="rotate(-90 9 9)"
      />
      {done && <path d="M6 9.2 8.1 11.3 12 7" fill="none" stroke="#4ade80" strokeWidth="1.4" strokeLinecap="round" />}
    </svg>
  );
}
