/**
 * The report screen.
 *
 * Two documents side by side -- the professional report and the patient
 * companion -- and, next to them, the exact list of things stopping `openrad
 * finish` from running. Each blocking item is a task, not a message: an
 * unaddressed region, a page marked reviewed that no attestation covers, a
 * hash that no longer matches. Nothing here can be dismissed; it can only be
 * fixed.
 */
import { useCallback, useEffect, useState } from "react";
import { api, fetchText } from "../api/client";
import type { LocaleBundle } from "../api/types";
import { GROUP_LABEL, groupBlockers, translateBlockers } from "../lib/blockers";
import { useSession } from "../state/session";
import { Markdown } from "./Markdown";
import { Spinner } from "./primitives";

interface Props {
  locale: LocaleBundle | null;
  onBack: () => void;
}

export function ReportScreen({ locale, onBack }: Props) {
  const { path, session, check, runCheck, coverage } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [guide, setGuide] = useState<string | null>(null);
  const [tab, setTab] = useState<"report" | "guide">("report");

  useEffect(() => {
    void runCheck();
  }, [runCheck]);

  const loadDocuments = useCallback(async (reportPath: string, guidePath: string) => {
    const [reportText, guideText] = await Promise.all([fetchText(reportPath), fetchText(guidePath)]);
    setReport(reportText);
    setGuide(guideText);
  }, []);

  useEffect(() => {
    if (session?.report_file && session?.guide_file) {
      void loadDocuments(session.report_file, session.guide_file).catch(() => undefined);
    }
  }, [session?.report_file, session?.guide_file, loadDocuments]);

  const finish = async () => {
    if (!path) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.finish(path, session?.language ?? "tr");
      await loadDocuments(result.report, result.guide);
      await useSession.getState().open(path);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const blocking = check?.blocking ?? [];
  const ready = check?.ok === true;

  return (
    <div className="grid h-full grid-rows-[36px_1fr] overflow-hidden">
      <header className="flex items-center gap-2 border-b border-[var(--hairline)] bg-ink-900 px-2.5">
        <button type="button" className="btn" onClick={onBack}>
          ← Çalışma alanı
        </button>
        <span className="readout truncate text-[11px] text-chalk-500">{path}</span>
        <div className="flex-1" />
        {coverage && (
          <span className="readout text-[10px] text-chalk-600">
            {coverage.pages_attested}/{coverage.pages_total} sayfa tasdikli ·{" "}
            {coverage.slices_attested}/{coverage.slices_total} kesit görüntülendi
          </span>
        )}
        <button type="button" className="btn" onClick={() => void runCheck()}>
          Yeniden denetle
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || !ready} onClick={() => void finish()}>
          {busy ? "üretiliyor…" : "Raporu üret"}
        </button>
      </header>

      <div className="grid min-h-0 grid-cols-[340px_1fr]">
        <aside className="scroll-y min-h-0 border-r border-[var(--hairline)] bg-ink-900 p-2.5">
          <h2 className="rail-label mb-2">Raporu engelleyenler</h2>
          {!check && <Spinner label="denetleniyor…" />}
          {check && ready && (
            <p className="rounded-[2px] border border-attest-400/40 bg-attest-900/40 p-2 text-[12px] text-attest-400">
              Yapısal denetim geçti. Bu, yorumun doğru olduğunu değil, hiçbir cümlenin bakılmadan
              yazılmadığını gösterir.
            </p>
          )}
          {blocking.length > 0 && (
            <div className="space-y-3">
              {groupBlockers(translateBlockers(blocking)).map((group) => (
                <section key={String(group.where)}>
                  <h3 className="rail-label mb-1 flex items-baseline gap-1.5">
                    {GROUP_LABEL[String(group.where)]}
                    <span className="text-chalk-600">{group.items.length}</span>
                  </h3>
                  <ol className="space-y-1">
                    {group.items.map((blocker, i) => (
                      <li
                        key={i}
                        // The engine's own wording is kept as the title: the
                        // translation is for reading, the original is what a
                        // terminal or an MCP client would show.
                        title={blocker.translated ? blocker.raw : undefined}
                        className="rounded-[2px] border border-alarm-400/25 bg-alarm-900/30 px-2 py-1.5 text-[11px] leading-relaxed text-chalk-300"
                      >
                        {blocker.text}
                      </li>
                    ))}
                  </ol>
                </section>
              ))}
            </div>
          )}
          {error && <p className="mt-2 text-[11px] text-alarm-400">{error}</p>}

          {session && (
            <div className="mt-4 space-y-1 border-t border-[var(--hairline)] pt-3">
              <Meta label="Okuyucu" value={session.reader || "—"} />
              <Meta label="Körlük" value={session.blind_status} />
              <Meta label="Bağlam beyanı" value={session.context_disclosure || "—"} />
              <Meta label="Karşılaştırma" value={session.comparison.status} />
              {session.report_sha256 && <Meta label="Rapor sha256" value={session.report_sha256} mono />}
            </div>
          )}
        </aside>

        <main className="flex min-h-0 flex-col">
          <div className="flex h-7 shrink-0 border-b border-[var(--hairline)]">
            {(
              [
                ["report", locale?.text?.title_single ?? "Hekim raporu"],
                ["guide", "Hasta rehberi"],
              ] as ["report" | "guide", string][]
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`px-4 text-[11px] tracking-wide transition-colors ${
                  tab === id ? "bg-ink-800 text-amber-400" : "text-chalk-500 hover:text-chalk-300"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="scroll-y min-h-0 flex-1 px-8 py-6">
            {tab === "report" ? (
              report ? (
                <Markdown source={report} />
              ) : (
                <Placeholder />
              )
            ) : guide ? (
              <Markdown source={guide} />
            ) : (
              <Placeholder />
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function Placeholder() {
  return (
    <p className="max-w-md text-[12px] leading-relaxed text-chalk-600">
      Belge henüz üretilmedi. Soldaki engelleyen maddeler temizlendiğinde “Raporu üret” çalışır ve
      hekim raporu ile hasta rehberi burada görünür.
    </p>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[92px_1fr] gap-2 text-[11px]">
      <span className="text-chalk-600">{label}</span>
      <span className={`${mono ? "readout break-all text-[10px]" : ""} text-chalk-300`}>{value}</span>
    </div>
  );
}
