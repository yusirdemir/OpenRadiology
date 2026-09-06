/**
 * A rendered contact sheet, shown so that looking at it counts.
 *
 * The sheet is displayed at natural resolution inside a scrollable frame and
 * never fitted to the window. Fitting a 1568-pixel sheet into a laptop window
 * is exactly the case the attestation policy exists to exclude: at that
 * magnification a small nodule may not have survived to the screen at all. So
 * the dwell only accumulates at full size with the window focused, and the
 * reader scrolls through the sheet the way they would a film.
 *
 * The same component serves both callers: a reader opening a page from the
 * ledger, and an agent's `page_view` waiting on the display gate.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fetchFile } from "../api/client";
import { useConnection } from "../state/connection";

interface Props {
  pagePath: string;
  purpose?: string;
  workDir: string;
  sessionPath?: string;
  /** Shown when an agent is waiting on this page rather than the reader. */
  requestedByAgent?: boolean;
  onAttested: () => void;
  onDismiss: (declined: boolean) => void;
}

export function PageViewer({ pagePath, purpose, workDir, sessionPath, requestedByAgent, onAttested, onDismiss }: Props) {
  const policy = useConnection((s) => s.policy);
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dwell, setDwell] = useState(0);
  const [busy, setBusy] = useState(false);
  const accumulated = useRef(0);

  useEffect(() => {
    let url: string | null = null;
    accumulated.current = 0;
    setDwell(0);
    void fetchFile(pagePath)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setSource(url);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [pagePath]);

  useEffect(() => {
    if (!source) return;
    let last = performance.now();
    const tick = window.setInterval(() => {
      const now = performance.now();
      const delta = now - last;
      last = now;
      if (document.hasFocus() && document.visibilityState === "visible") {
        accumulated.current += delta;
        setDwell(Math.min(1, accumulated.current / policy.minDwellMs));
      }
    }, 100);
    return () => window.clearInterval(tick);
  }, [source, policy.minDwellMs]);

  const attest = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await api.attest(workDir, [
        {
          study_uid: "",
          series_uid: "",
          sop_uid: "",
          plane: "ax",
          index: -1,
          dwell_ms: accumulated.current,
          scale: 1,
          focused: true,
          visible: true,
          window_center: 0,
          window_width: 0,
          source: "page",
          page_path: pagePath,
        },
      ], sessionPath);
      onAttested();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [workDir, pagePath, sessionPath, onAttested]);

  const ready = dwell >= 1;
  const remaining = Math.max(0, Math.ceil(((1 - dwell) * policy.minDwellMs) / 100) / 10);

  return (
    <div className="absolute inset-0 z-50 grid grid-rows-[auto_1fr_auto] bg-ink-1000/92 backdrop-blur-sm">
      <header className="flex items-center gap-3 border-b border-[var(--hairline-strong)] px-4 py-2.5">
        {requestedByAgent && <span className="chip border-amber-400/45 text-amber-400">ajan istedi</span>}
        <div className="min-w-0">
          <p className="truncate text-[12px] text-chalk-100">
            {pagePath.split("/").pop()}
            {purpose && <span className="ml-2 text-chalk-700">{purpose}</span>}
          </p>
          <p className="text-[10px] text-chalk-700">
            Doğal çözünürlükte gösteriliyor; küçültülmüş bir kontakt sayfası hiçbir şeyi tasdik etmez.
          </p>
        </div>
        <div className="flex-1" />
        <DwellMeter progress={dwell} />
      </header>

      <div className="scroll-y min-h-0 bg-black">
        {error && <p className="p-4 text-xs text-alert-400">{error}</p>}
        {source ? (
          <img src={source} alt="" className="max-w-none" style={{ imageRendering: "pixelated" }} />
        ) : (
          !error && <p className="p-4 text-xs text-chalk-700">sayfa yükleniyor…</p>
        )}
      </div>

      <footer className="flex items-center gap-2 border-t border-[var(--hairline-strong)] px-4 py-2.5">
        <p className="flex-1 text-[11px] text-chalk-600">
          {ready ? "Bu sayfa tasdik edilebilir." : `Tasdik için ${remaining} sn daha görüntülenmeli.`}
        </p>
        <button type="button" className="btn" disabled={busy} onClick={() => onDismiss(true)}>
          {requestedByAgent ? "Reddet" : "Kapat"}
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => void attest()}>
          {requestedByAgent ? "Okudum, ajana ver" : "Okudum"}
        </button>
      </footer>
    </div>
  );
}

export function DwellMeter({ progress }: { progress: number }) {
  const radius = 9;
  const circumference = 2 * Math.PI * radius;
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" aria-label="görüntüleme süresi">
      <circle cx="12" cy="12" r={radius} fill="none" stroke="rgba(255,255,255,0.14)" strokeWidth="2" />
      <circle
        cx="12"
        cy="12"
        r={radius}
        fill="none"
        stroke={progress >= 1 ? "#4fd6a0" : "#ffb454"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.min(1, progress))}
        transform="rotate(-90 12 12)"
      />
    </svg>
  );
}
