/**
 * The agent bridge, from the reader's side.
 *
 * When an MCP client is driving this session, two things have to be visible.
 * The first is what it did: every tool call arrives on a live stream and lands
 * in the transcript, so the person can check the agent's work instead of
 * trusting it. The second is the display gate. When the agent asks to read a
 * rendered sheet, that request surfaces here as a page the person has to
 * actually look at -- at full resolution, for long enough to count -- before
 * the agent is allowed to receive it and mark it reviewed.
 *
 * The gate is the reason this panel is not a log viewer. Declining is a real
 * option, and a page nobody looks at never becomes evidence.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, fetchFile, followAgent } from "../api/client";
import type { DisplayRequest, TranscriptEntry } from "../api/types";
import { useConnection } from "../state/connection";
import { useSession } from "../state/session";

interface StreamState {
  connected: boolean;
  transcript: TranscriptEntry[];
  pending: DisplayRequest[];
}

function useAgentStream(): StreamState {
  const [state, setState] = useState<StreamState>({ connected: false, transcript: [], pending: [] });

  useEffect(() => {
    let alive = true;
    void api
      .agentState()
      .then((snapshot) => {
        if (alive) {
          setState({ connected: true, transcript: snapshot.transcript, pending: snapshot.pending_displays });
        }
      })
      .catch(() => undefined);

    const stop = followAgent((event, data) => {
      if (!alive) return;
      if (event === "hello") {
        const payload = data as { transcript: TranscriptEntry[]; pending_displays: DisplayRequest[] };
        setState({ connected: true, transcript: payload.transcript, pending: payload.pending_displays });
      } else if (event === "entry") {
        setState((prev) => ({ ...prev, transcript: [...prev.transcript, data as TranscriptEntry].slice(-400) }));
      } else if (event === "display") {
        setState((prev) => ({ ...prev, pending: [...prev.pending, data as DisplayRequest] }));
      }
    });
    return () => {
      alive = false;
      stop();
    };
  }, []);

  return state;
}

export function AgentPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { transcript, pending } = useAgentStream();
  const [dismissed, setDismissed] = useState<string[]>([]);
  const request = pending.find((r) => !dismissed.includes(r.id));
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [transcript.length]);

  return (
    <>
      {request && (
        <DisplayGateDialog
          request={request}
          onSettled={() => setDismissed((prev) => [...prev, request.id])}
        />
      )}
      {open && (
        <aside className="chrome-grain absolute inset-y-0 right-0 z-40 flex w-[380px] flex-col border-l border-[var(--hairline-strong)] bg-ink-900 shadow-[-16px_0_40px_rgba(0,0,0,0.45)]">
          <header className="flex h-9 shrink-0 items-center gap-2 border-b border-[var(--hairline)] px-2.5">
            <span className="rail-label">Ajan</span>
            <span className="chip text-attest-400">MCP köprüsü</span>
            <div className="flex-1" />
            <button type="button" className="btn" onClick={onClose}>
              Kapat
            </button>
          </header>

          <div ref={listRef} className="scroll-y min-h-0 flex-1 p-2">
            {transcript.length === 0 ? (
              <p className="p-2 text-[11px] leading-relaxed text-chalk-600">
                Henüz bir ajan bağlanmadı. Claude Code veya Claude Desktop’ı köprüye bağladığınızda
                yaptığı her adım burada canlı görünür; bir sayfayı okumak istediğinde önce size
                gösterilir.
              </p>
            ) : (
              <ol className="space-y-1">
                {transcript.map((entry) => (
                  <li key={entry.id} className="flex gap-2 rounded-[2px] px-1.5 py-1 hover:bg-ink-850">
                    <span className="readout mt-0.5 shrink-0 text-[10px] text-chalk-600">
                      {new Date(entry.ts * 1000).toLocaleTimeString("tr-TR", { hour12: false })}
                    </span>
                    <span className="min-w-0">
                      <span
                        className={`block text-[11px] leading-snug ${
                          entry.kind === "display_shown"
                            ? "text-attest-400"
                            : entry.kind === "display_request"
                              ? "text-amber-400"
                              : "text-chalk-300"
                        }`}
                      >
                        {entry.summary ?? entry.name ?? entry.kind}
                      </span>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <footer className="shrink-0 border-t border-[var(--hairline)] p-2.5">
            <p className="text-[10px] leading-relaxed text-chalk-600">
              Ajan bu pencerenin oturumunu ve çözülmüş hacimlerini paylaşır. Bir sayfayı “okundu”
              işaretleyebilmesi için o sayfanın burada gerçekten gösterilmiş olması gerekir.
            </p>
          </footer>
        </aside>
      )}
    </>
  );
}

/**
 * The gate itself.
 *
 * The sheet is shown at its natural resolution inside a scrollable frame,
 * never fitted to the window. Fitting a 1568-pixel contact sheet into a laptop
 * window is exactly the case the attestation policy exists to exclude: at that
 * magnification a small nodule may not have survived to the screen at all. So
 * the dwell only accumulates while the page is at full size and the window has
 * focus, and the reader scrolls through it the way they would a film.
 */
function DisplayGateDialog({ request, onSettled }: { request: DisplayRequest; onSettled: () => void }) {
  const policy = useConnection((s) => s.policy);
  const workDir = useSession((s) => s.session?.work_dir ?? "");
  const [source, setSource] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dwell, setDwell] = useState(0);
  const [busy, setBusy] = useState(false);
  const accumulated = useRef(0);

  useEffect(() => {
    let url: string | null = null;
    void fetchFile(request.page_path)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setSource(url);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [request.page_path]);

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

  const confirm = useCallback(async () => {
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
          page_path: request.page_path,
        },
      ]);
      await api.displayShown(request.id);
      void useSession.getState().refreshCoverage();
      onSettled();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }, [workDir, request, onSettled]);

  const decline = useCallback(async () => {
    setBusy(true);
    try {
      await api.displayDeclined(request.id, "Okuyucu bu sayfayı görüntülemedi");
    } finally {
      onSettled();
    }
  }, [request, onSettled]);

  const ready = dwell >= 1;

  return (
    <div className="absolute inset-0 z-50 grid grid-rows-[auto_1fr_auto] bg-ink-950/96 backdrop-blur-sm">
      <header className="flex items-center gap-3 border-b border-[var(--hairline-strong)] px-4 py-2.5">
        <span className="chip border-amber-400/50 text-amber-400">ajan istedi</span>
        <div className="min-w-0">
          <p className="truncate text-[12px] text-chalk-100">
            {request.page_path.split("/").pop()}
            {request.purpose && <span className="ml-2 text-chalk-600">{request.purpose}</span>}
          </p>
          <p className="text-[10px] text-chalk-600">
            Sayfa doğal çözünürlükte gösteriliyor. Ajanın bunu “okundu” sayabilmesi için burada
            gerçekten görüntülenmesi gerekir.
          </p>
        </div>
        <div className="flex-1" />
        <DwellMeter progress={dwell} />
      </header>

      <div className="scroll-y min-h-0 bg-black">
        {error && <p className="p-4 text-xs text-alarm-400">{error}</p>}
        {source ? (
          // Natural size, never fitted: a shrunken sheet attests nothing.
          <img src={source} alt="" className="max-w-none" style={{ imageRendering: "pixelated" }} />
        ) : (
          <p className="p-4 text-xs text-chalk-600">sayfa yükleniyor…</p>
        )}
      </div>

      <footer className="flex items-center gap-2 border-t border-[var(--hairline-strong)] px-4 py-2.5">
        <p className="flex-1 text-[11px] text-chalk-500">
          {ready
            ? "Bu sayfa tasdik edilebilir."
            : `Tasdik için ${Math.ceil(((1 - dwell) * policy.minDwellMs) / 100) / 10} sn daha görüntülenmeli.`}
        </p>
        <button type="button" className="btn" disabled={busy} onClick={() => void decline()}>
          Reddet
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => void confirm()}>
          Okudum, ajana ver
        </button>
      </footer>
    </div>
  );
}

function DwellMeter({ progress }: { progress: number }) {
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
        stroke={progress >= 1 ? "#4ade80" : "#ffb454"}
        strokeWidth="2"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - Math.min(1, progress))}
        transform="rotate(-90 12 12)"
      />
    </svg>
  );
}
