/**
 * Producing the contact sheets a report is built on.
 *
 * The render itself is the ordinary `openrad ct-render` (or `mr-render`,
 * `pet-render`) run as a child process, so the sheets are byte-identical to
 * what a terminal would produce and carry the same `render_index.json` that
 * ties every tile back to its SOPInstanceUID. Progress is the engine's own
 * stderr, streamed and shown verbatim rather than replaced with a spinner and
 * a guess.
 *
 * When it finishes the pages are registered into the session -- registered
 * only. Nothing is marked reviewed here; that still takes looking at them.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, followJob } from "../api/client";
import type { JobSnapshot, SeriesMeta } from "../api/types";
import { useSession } from "../state/session";

interface Props {
  studyPath: string;
  seriesUid: string;
  seriesNumber: string;
  meta: SeriesMeta | null;
  onClose: () => void;
}

const WINDOW_CHOICES = [
  { id: "lung", label: "akciğer" },
  { id: "soft", label: "yumuşak doku" },
  { id: "bone", label: "kemik" },
];

export function RenderDialog({ studyPath, seriesUid, seriesNumber, meta, onClose }: Props) {
  const session = useSession((s) => s.session);
  const sessionPath = useSession((s) => s.path);
  const [windows, setWindows] = useState<string[]>(["lung", "soft"]);
  const [stepMm, setStepMm] = useState(5);
  const [mipMm, setMipMm] = useState(10);
  const [mpr, setMpr] = useState(true);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [registered, setRegistered] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [lines.length]);

  const modality = meta?.geometry.modality ?? "CT";
  const kind = modality === "MR" ? "mr" : modality === "PT" ? "pet" : "ct";
  const slices = meta?.planes.ax.count ?? 0;
  const spacing = Math.abs(meta?.geometry.slice_spacing_mm ?? 1);
  // The engine samples every `step` millimetres; native spacing is exhaustive.
  const sheets = stepMm > 0 ? Math.ceil((slices * spacing) / stepMm / 9) : 0;

  const start = useCallback(async () => {
    if (!session || !sessionPath) return;
    setError(null);
    setLines([]);
    setRegistered(null);
    const output = `${session.work_dir}/S${seriesNumber}_${kind}`;
    try {
      const snapshot = await api.render(kind, {
        study: studyPath,
        output,
        label: `S${seriesNumber} ${modality}`,
        args: {
          series: seriesUid,
          ...(kind === "ct" ? { windows: windows.join(","), step: stepMm, mip: mipMm } : {}),
          ...(mpr ? { mpr: true } : {}),
        },
      });
      setJob(snapshot);
      const stop = followJob(snapshot.id, (event, data) => {
        if (event === "log") {
          setLines((prev) => [...prev.slice(-300), String((data as { line: string }).line)]);
        } else if (event === "status") {
          setJob(data as JobSnapshot);
        } else if (event === "error" || event === "cancelled") {
          setError(String((data as { message?: string }).message ?? "render başarısız"));
          stop();
        } else if (event === "done") {
          stop();
          void api
            .register(sessionPath, output)
            .then((result) => {
              setRegistered(result.registered);
              void useSession.getState().open(sessionPath);
            })
            .catch((e) => setError(e instanceof Error ? e.message : String(e)));
        }
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [session, sessionPath, studyPath, seriesUid, seriesNumber, kind, modality, windows, stepMm, mipMm, mpr]);

  const running = job?.status === "running" || job?.status === "queued";

  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-ink-950/92 p-8 backdrop-blur-sm">
      <div className="panel flex max-h-full w-[660px] max-w-full flex-col">
        <header className="shrink-0 border-b border-[var(--hairline)] px-4 py-3">
          <h2 className="text-[14px] font-semibold text-chalk-100">
            Sayfa üret — seri {seriesNumber} · {modality} · {slices} kesit
          </h2>
          <p className="mt-1 text-[11px] leading-relaxed text-chalk-500">
            Terminaldeki <code className="readout text-probe-300">openrad {kind}-render</code> komutunun
            aynısı çalışır. Üretilen sayfalar oturuma kaydedilir; hiçbiri “okundu” işaretlenmez —
            bunun için bakmak gerekir.
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {kind === "ct" && (
            <>
              <p className="rail-label mb-1.5">Pencereler</p>
              <div className="mb-3 flex gap-1">
                {WINDOW_CHOICES.map((choice) => (
                  <button
                    key={choice.id}
                    type="button"
                    disabled={running}
                    className={`btn ${windows.includes(choice.id) ? "btn-active" : ""}`}
                    onClick={() =>
                      setWindows((prev) =>
                        prev.includes(choice.id) ? prev.filter((w) => w !== choice.id) : [...prev, choice.id],
                      )
                    }
                  >
                    {choice.label}
                  </button>
                ))}
              </div>

              <div className="mb-3 grid grid-cols-2 gap-4">
                <label className="block">
                  <span className="rail-label mb-1 block">
                    Kesit aralığı — {stepMm === 0 ? "her kesit (tam tarama)" : `${stepMm} mm`}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={10}
                    step={1}
                    value={stepMm}
                    disabled={running}
                    onChange={(e) => setStepMm(Number(e.target.value))}
                    className="w-full accent-[var(--color-amber-400)]"
                  />
                </label>
                <label className="block">
                  <span className="rail-label mb-1 block">
                    Slab MIP — {mipMm === 0 ? "kapalı" : `${mipMm} mm`}
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={20}
                    step={5}
                    value={mipMm}
                    disabled={running}
                    onChange={(e) => setMipMm(Number(e.target.value))}
                    className="w-full accent-[var(--color-amber-400)]"
                  />
                </label>
              </div>
            </>
          )}

          <label className="mb-3 flex items-center gap-2 text-[12px] text-chalk-300">
            <input type="checkbox" checked={mpr} disabled={running} onChange={(e) => setMpr(e.target.checked)} />
            Koronal ve sagital reformatlar da üret
          </label>

          {kind === "ct" && stepMm > 0 && (
            <p className="mb-3 text-[11px] leading-relaxed text-caution-400">
              {stepMm} mm aralık, kesitlerin bir bölümünü atlar. Kapsamı eksiksiz saymak için aralığı
              sıfıra çekin; oturum denetimi render edilmemiş kesitleri zaten bildirir.
              {sheets > 0 && <span className="text-chalk-600"> Yaklaşık {sheets * windows.length} sayfa.</span>}
            </p>
          )}

          {(lines.length > 0 || job) && (
            <div
              ref={logRef}
              className="readout max-h-[180px] overflow-y-auto rounded-[2px] border border-[var(--hairline)] bg-ink-950 p-2 text-[10px] leading-relaxed text-chalk-500"
            >
              {lines.map((line, i) => (
                <div key={i}>{line}</div>
              ))}
              {job && !lines.length && <div>iş sıraya alındı…</div>}
            </div>
          )}

          {registered !== null && (
            <p className="mt-3 rounded-[2px] border border-attest-400/40 bg-attest-900/40 p-2 text-[12px] text-attest-400">
              {registered} sayfa oturuma kaydedildi. Kanıt panelindeki “Sayfalar” sekmesinden açıp
              okuyabilirsiniz.
            </p>
          )}
          {error && <p className="mt-3 text-[11px] text-alarm-400">{error}</p>}
        </div>

        <footer className="flex shrink-0 items-center gap-2 border-t border-[var(--hairline)] px-4 py-3">
          <span className="readout flex-1 truncate text-[10px] text-chalk-600">
            {job ? `${job.command.join(" ")}` : ""}
          </span>
          {running && (
            <button type="button" className="btn" onClick={() => void api.cancelJob(job!.id)}>
              Durdur
            </button>
          )}
          <button type="button" className="btn" onClick={onClose}>
            {registered !== null ? "Kapat" : "Vazgeç"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={running || (kind === "ct" && windows.length === 0)}
            onClick={() => void start()}
          >
            {running ? "üretiliyor…" : "Üret"}
          </button>
        </footer>
      </div>
    </div>
  );
}
