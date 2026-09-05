/**
 * Position in the stack, as a strip of thumbnails.
 *
 * The thumbnails arrive as one PNG atlas rather than as forty-eight requests,
 * which keeps opening a series cheap. Under each thumbnail a tick shows whether
 * the slices it stands for have actually been displayed, so the reader can see
 * at a glance which part of the stack they have really been through -- not
 * which part they scrolled past.
 */
import { useEffect, useRef, useState } from "react";
import { fetchAtlas } from "../api/client";
import type { PlaneName } from "../api/types";

interface Props {
  study: string;
  series: string;
  plane: PlaneName;
  index: number;
  count: number;
  window: { center: number; width: number };
  attestedIndices?: Set<number>;
  onSeek: (index: number) => void;
}

const TILE = 46;
const SLOTS = 48;

export function Filmstrip({ study, series, plane, index, count, window: win, attestedIndices, onSeek }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const [indices, setIndices] = useState<number[]>([]);
  const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => entry && setWidth(entry.contentRect.width));
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let disposed: ImageBitmap | null = null;
    void fetchAtlas(study, series, plane, SLOTS, 64, win, controller.signal)
      .then(async (atlas) => {
        const image = await createImageBitmap(atlas.blob);
        disposed = image;
        setBitmap(image);
        setIndices(atlas.indices);
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      disposed?.close();
    };
    // The window is deliberately part of the key: a lung-window filmstrip is
    // useless for reading the mediastinum.
  }, [study, series, plane, win.center, win.width]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !bitmap || !indices.length || width <= 0) return;
    const dpr = globalThis.devicePixelRatio || 1;
    const slotWidth = width / indices.length;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round((TILE + 5) * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, TILE + 5);
    ctx.imageSmoothingQuality = "high";

    let nearest = 0;
    indices.forEach((value, slot) => {
      if (Math.abs(value - index) < Math.abs((indices[nearest] ?? 0) - index)) nearest = slot;
      ctx.drawImage(bitmap, slot * 64, 0, 64, 64, slot * slotWidth, 0, slotWidth, TILE);
      if (attestedIndices?.has(value)) {
        ctx.fillStyle = "#4ade80";
        ctx.fillRect(slot * slotWidth + 1, TILE + 1, slotWidth - 2, 2);
      }
    });

    ctx.strokeStyle = "#ffb454";
    ctx.lineWidth = 1.5;
    ctx.strokeRect(nearest * slotWidth + 0.75, 0.75, slotWidth - 1.5, TILE - 1.5);
  }, [bitmap, indices, index, width, attestedIndices]);

  const seekFromEvent = (clientX: number) => {
    const host = hostRef.current;
    if (!host || !indices.length) return;
    const rect = host.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    onSeek(Math.round(ratio * (count - 1)));
  };

  return (
    <div ref={hostRef} className="relative h-[52px] w-full cursor-pointer select-none bg-ink-950">
      <canvas
        ref={canvasRef}
        className="h-[51px] w-full"
        onPointerDown={(e) => {
          try {
            (e.target as Element).setPointerCapture?.(e.pointerId);
          } catch {
            /* scrubbing still works, it just stops at the strip edge */
          }
          seekFromEvent(e.clientX);
        }}
        onPointerMove={(e) => {
          if (e.buttons === 1) seekFromEvent(e.clientX);
        }}
      />
      {!bitmap && (
        <div className="absolute inset-0 grid place-items-center text-[10px] text-chalk-600">
          önizleme hazırlanıyor…
        </div>
      )}
    </div>
  );
}
