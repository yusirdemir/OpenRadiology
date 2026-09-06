/**
 * Position in the stack, and a way to travel through it.
 *
 * The bare range input this replaces did one thing badly: it told you where you
 * were and gave you no way to get anywhere. A reader moving through four
 * hundred slices wants what a film viewer has always had -- a run through the
 * stack that plays by itself, a step, a jump to the thing being discussed --
 * and, on a machine where slices arrive slowly, an honest picture of which part
 * of the stack is actually in hand.
 *
 * So the track carries three layers: what is buffered, where the selected
 * finding lives, and where you are. Cine playback is capped by what has
 * arrived; it will not run ahead into slices that are not there and pretend
 * the study is empty.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronsLeft, ChevronsRight, Crosshair, Pause, Play, SkipBack, SkipForward } from "./icons";

interface Props {
  index: number;
  count: number;
  /** 0-255 per bucket: how much of each part of the stack is cached. */
  coverage: Uint8Array | null;
  /** Slice the selected finding is cited on, if any. */
  marker: number | null;
  /** First and last slice the finding actually occupies, once it is grown. */
  span: [number, number] | null;
  onSeek: (index: number) => void;
  onJumpToMarker: () => void;
  /** Is the next slice already in hand? Cine waits rather than stalling on it. */
  isReady?: (index: number) => boolean;
  /** True while this pane is the one the keyboard talks to. */
  active: boolean;
}

const SPEEDS = [8, 15, 24] as const;

export function SliceScrubber({
  index,
  count,
  coverage,
  marker,
  span,
  onSeek,
  onJumpToMarker,
  isReady,
  active,
}: Props) {
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(15);
  const trackRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [waiting, setWaiting] = useState(false);
  const live = useRef({ index, count, onSeek, isReady });
  live.current = { index, count, onSeek, isReady };

  // -- cine ----------------------------------------------------------------
  useEffect(() => {
    if (!playing || count < 2) return;
    const timer = window.setInterval(() => {
      const { index: at, count: total, onSeek: seek, isReady: ready } = live.current;
      const next = at + 1;
      if (next >= total) {
        setPlaying(false);
        setWaiting(false);
        return;
      }
      // Playback never runs ahead of the images. On a link that cannot keep up,
      // holding on the last real frame is honest; advancing the counter over
      // slices nobody has seen is not.
      if (ready && !ready(next)) {
        setWaiting(true);
        return;
      }
      setWaiting(false);
      seek(next);
    }, 1000 / speed);
    return () => window.clearInterval(timer);
  }, [playing, speed, count]);

  // Playing through a pane the reader has left is a distraction, not a feature.
  useEffect(() => {
    if (!active) setPlaying(false);
  }, [active]);
  useEffect(() => {
    if (!playing) setWaiting(false);
  }, [playing]);

  const seekFromPointer = useCallback(
    (clientX: number) => {
      const track = trackRef.current;
      if (!track || count < 2) return;
      const rect = track.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      onSeek(Math.round(ratio * (count - 1)));
    },
    [count, onSeek],
  );

  const position = count > 1 ? (index / (count - 1)) * 100 : 0;
  const buckets = coverage?.length ?? 0;

  return (
    <div className="flex h-12 shrink-0 items-center gap-2.5 border-t border-[var(--hairline)] bg-ink-950 px-3">
      <div className="flex shrink-0 items-center gap-0.5">
        <Step title="10 kesit geri" onClick={() => onSeek(index - 10)} disabled={index <= 0}>
          <ChevronsLeft size={15} aria-hidden />
        </Step>
        <Step title="Bir kesit geri" onClick={() => onSeek(index - 1)} disabled={index <= 0}>
          <SkipBack size={13} aria-hidden />
        </Step>
        <button
          type="button"
          title={playing ? "Durdur" : "Kesitleri sırayla oynat"}
          aria-label={playing ? "Durdur" : "Oynat"}
          disabled={count < 2}
          onClick={() => setPlaying((on) => !on)}
          className={`grid h-7 w-7 place-items-center rounded-full border transition-colors ${
            playing
              ? "border-amber-400/60 bg-amber-400/15 text-amber-400"
              : "border-[var(--hairline-strong)] text-chalk-300 hover:bg-ink-800 hover:text-chalk-100"
          } disabled:opacity-40`}
        >
          {playing ? <Pause size={13} aria-hidden /> : <Play size={13} className="ml-[1px]" aria-hidden />}
        </button>
        <Step title="Bir kesit ileri" onClick={() => onSeek(index + 1)} disabled={index >= count - 1}>
          <SkipForward size={13} aria-hidden />
        </Step>
        <Step title="10 kesit ileri" onClick={() => onSeek(index + 10)} disabled={index >= count - 1}>
          <ChevronsRight size={15} aria-hidden />
        </Step>
      </div>

      {playing && (
        <button
          type="button"
          className="readout shrink-0 rounded-[4px] border border-[var(--hairline)] px-1.5 py-0.5 text-[10px] text-chalk-500 transition-colors hover:text-chalk-200"
          title="Oynatma hızı"
          onClick={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length] as (typeof SPEEDS)[number])}
        >
          {speed}/sn
        </button>
      )}
      {waiting && (
        <span className="shrink-0 text-[10.5px] text-warn-400" title="Sonraki kesit henüz gelmedi">
          kesit bekleniyor
        </span>
      )}

      {/* ---- the track ---- */}
      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label="Kesit"
        aria-valuemin={0}
        aria-valuemax={Math.max(0, count - 1)}
        aria-valuenow={index}
        onPointerDown={(event) => {
          dragging.current = true;
          try {
            (event.target as Element).setPointerCapture?.(event.pointerId);
          } catch {
            // Capture keeps a drag alive outside the track, but it throws for a
            // pointer id the element does not own. That must never cost the
            // seek itself; the drag simply ends at the track's edge.
          }
          setPlaying(false);
          seekFromPointer(event.clientX);
        }}
        onPointerMove={(event) => dragging.current && seekFromPointer(event.clientX)}
        onPointerUp={() => {
          dragging.current = false;
        }}
        onPointerCancel={() => {
          dragging.current = false;
        }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft") onSeek(index - 1);
          else if (event.key === "ArrowRight") onSeek(index + 1);
          else return;
          event.preventDefault();
        }}
        className="relative min-w-0 flex-1 cursor-pointer py-3"
      >
        {/* the stack */}
        <div className="relative h-[6px] w-full overflow-hidden rounded-full bg-ink-800">
          {/* what has arrived */}
          {buckets > 0 && (
            <div className="absolute inset-0 flex" aria-hidden>
              {Array.from(coverage as Uint8Array, (value, i) => (
                <span
                  key={i}
                  className="h-full flex-1 bg-chalk-600"
                  style={{ opacity: (value / 255) * 0.5 }}
                />
              ))}
            </div>
          )}
          {/* the slices the finding actually occupies */}
          {span && count > 1 && (
            <div
              className="absolute inset-y-0 rounded-full bg-focus-500/45"
              style={{
                left: `${(span[0] / (count - 1)) * 100}%`,
                width: `${Math.max(0.6, ((span[1] - span[0]) / (count - 1)) * 100)}%`,
              }}
              aria-hidden
            />
          )}
          {/* how far through the stack you are */}
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-amber-400/35"
            style={{ width: `${position}%` }}
            aria-hidden
          />
        </div>

        {/* where the selected finding lives */}
        {marker !== null && count > 1 && (
          <button
            type="button"
            title="Seçili bulgunun kesiti"
            aria-label="Seçili bulgunun kesitine git"
            onClick={(event) => {
              event.stopPropagation();
              onJumpToMarker();
            }}
            onPointerDown={(event) => event.stopPropagation()}
            className="absolute top-[6px] grid h-4 w-4 -translate-x-1/2 place-items-center"
            style={{ left: `${(marker / (count - 1)) * 100}%` }}
          >
            <Crosshair size={13} className="text-focus-500" aria-hidden />
          </button>
        )}

        {/* where you are */}
        <span
          className="pointer-events-none absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-ink-950 bg-amber-400 shadow"
          style={{ left: `${position}%` }}
          aria-hidden
        />
      </div>

      <span className="readout w-[5.5rem] shrink-0 text-right text-[11px] text-chalk-400">
        {count ? (
          <>
            <span className="text-chalk-100">{index + 1}</span>
            <span className="text-chalk-700"> / {count}</span>
          </>
        ) : (
          "—"
        )}
      </span>
    </div>
  );
}

function Step({
  title,
  onClick,
  disabled,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="grid h-6 w-6 place-items-center rounded-[4px] text-chalk-500 transition-colors hover:bg-ink-800 hover:text-chalk-100 disabled:opacity-30 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}
