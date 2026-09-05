/**
 * The study shelf: the first thing the application shows.
 *
 * Cards are grouped by StudyInstanceUID, not by folder name, and ordered by
 * StudyDate and StudyTime. That matters more than it sounds: a comparison read
 * whose chronology came from folder names would happily call a shrinking lesion
 * a growing one. Folder names appear on the card only as a human label.
 */
import { useEffect, useState } from "react";
import type { StudyCard } from "../api/types";
import { formatStudyDate, formatStudyTime, pickFolder } from "../lib/platform";
import { useConnection } from "../state/connection";
import { useShelf } from "../state/shelf";
import { Spinner } from "./primitives";

interface Props {
  onOpen: (paths: string[]) => void;
  busy: boolean;
}

export function Shelf({ onOpen, busy }: Props) {
  const { root, studies, scanning, error, selection, scan, toggle } = useShelf();
  const { version, cacheBudgetBytes } = useConnection();
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const stop = (event: DragEvent) => {
      event.preventDefault();
      setDragging(event.type === "dragover");
    };
    for (const type of ["dragover", "dragleave", "drop"]) {
      window.addEventListener(type, stop as EventListener);
    }
    return () => {
      for (const type of ["dragover", "dragleave", "drop"]) {
        window.removeEventListener(type, stop as EventListener);
      }
    };
  }, []);

  const choose = async () => {
    const folder = await pickFolder("DICOM arşiv klasörünü seçin");
    if (folder) void scan(folder);
  };

  const comparison = selection.length > 1;

  return (
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-end justify-between px-6 pb-4 pt-8">
        <div>
          <h1 className="text-[26px] font-semibold leading-none tracking-[-0.02em] text-chalk-100">
            OpenRadiology
          </h1>
          <p className="mt-1.5 max-w-lg text-[12px] leading-relaxed text-chalk-500">
            Yorumlayan bir model değil, kanıt disiplini dayatan bir tezgâh. Baktığınız kesiti adresiyle
            göstermeyen hiçbir cümle rapora giremez.
          </p>
        </div>
        <span className="readout text-[10px] text-chalk-600">motor {version || "…"}</span>
      </header>

      <div className="flex shrink-0 items-center gap-2 px-6 pb-3">
        <button type="button" className="btn btn-primary" onClick={choose} disabled={scanning}>
          Klasör aç
        </button>
        {root && (
          <button type="button" className="btn" onClick={() => void scan(root)} disabled={scanning}>
            Yeniden tara
          </button>
        )}
        {root && <span className="readout truncate text-[11px] text-chalk-600">{root}</span>}
        {scanning && <Spinner label="taranıyor…" />}
      </div>

      {error && <p className="mx-6 mb-3 shrink-0 text-xs text-alarm-400">{error}</p>}

      <div className="scroll-y min-h-0 flex-1 px-6 pb-24">
        {!studies.length && !scanning ? (
          <div
            className={`grid h-64 place-items-center rounded-[3px] border border-dashed p-8 text-center transition-colors ${
              dragging ? "border-amber-400 bg-amber-900/20" : "border-[var(--hairline-strong)]"
            }`}
          >
            <div>
              <p className="text-[13px] text-chalk-300">Bir DICOM klasörü seçin</p>
              <p className="mt-1 text-[11px] text-chalk-600">
                Alt klasörler taranır; çalışmalar klasör adına göre değil, DICOM kimliğine göre gruplanır.
              </p>
            </div>
          </div>
        ) : (
          <ul className="grid gap-1.5">
            {studies.map((study, i) => (
              <StudyRow
                key={study.study_uid || study.path}
                study={study}
                index={i}
                selected={selection.indexOf(study.path)}
                onToggle={() => toggle(study.path)}
              />
            ))}
          </ul>
        )}
      </div>

      {selection.length > 0 && (
        <footer className="rise absolute inset-x-0 bottom-0 flex items-center justify-between gap-4 border-t border-[var(--hairline-strong)] bg-ink-900/95 px-6 py-3 backdrop-blur">
          <div className="min-w-0">
            <p className="text-[12px] text-chalk-100">
              {comparison ? `${selection.length} çekim kronolojik karşılaştırma` : "Tek çekim incelemesi"}
            </p>
            <p className="readout truncate text-[10px] text-chalk-600">
              {selection.map((path) => path.split("/").pop()).join("  →  ")}
            </p>
          </div>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => onOpen(selection)}>
            {busy ? "Oturum hazırlanıyor…" : comparison ? "Karşılaştır" : "İncelemeyi başlat"}
          </button>
        </footer>
      )}

      <p className="pointer-events-none absolute bottom-1 right-3 text-[10px] text-chalk-600">
        hacim önbelleği {Math.round(cacheBudgetBytes / 1024 ** 3)} GB
      </p>
    </div>
  );
}

function StudyRow({
  study,
  index,
  selected,
  onToggle,
}: {
  study: StudyCard;
  index: number;
  selected: number;
  onToggle: () => void;
}) {
  const chosen = selected >= 0;
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        disabled={!study.supported}
        style={{ animationDelay: `${Math.min(index * 26, 300)}ms` }}
        className={`rise grid w-full grid-cols-[auto_7.5rem_1fr_auto] items-center gap-3 rounded-[3px] border px-3 py-2.5 text-left transition-colors ${
          chosen
            ? "border-amber-400/60 bg-amber-900/25"
            : "border-[var(--hairline)] bg-ink-900 hover:border-[var(--hairline-strong)] hover:bg-ink-850"
        } ${study.supported ? "" : "opacity-45"}`}
      >
        <span
          className={`readout grid h-5 w-5 place-items-center rounded-[2px] border text-[10px] ${
            chosen ? "border-amber-400 bg-amber-400 text-ink-950" : "border-[var(--hairline-strong)] text-transparent"
          }`}
        >
          {chosen ? selected + 1 : "0"}
        </span>

        <span className="readout leading-tight">
          <span className="block text-[12px] text-chalk-100">{formatStudyDate(study.date)}</span>
          <span className="block text-[10px] text-chalk-600">{formatStudyTime(study.time)}</span>
        </span>

        <span className="min-w-0">
          <span className="block truncate text-[12px] text-chalk-100">
            {study.description || study.folder}
          </span>
          <span className="readout block truncate text-[10px] text-chalk-600">
            {study.description ? `${study.folder} · ` : ""}
            {study.series_count} seri · {study.instances} kesit
            {study.shares_folder && " · klasörde birden fazla çalışma"}
            {study.inconsistent_date && " · tutarsız tarih"}
          </span>
        </span>

        <span className="flex items-center gap-1.5">
          {study.identity_removed && (
            <span className="chip text-attest-400" title="PatientIdentityRemoved = YES">
              anonim
            </span>
          )}
          {!study.supported && (
            <span className="chip text-chalk-600" title="Desteklenen protokoller: BT, PET/BT, beyin MR">
              desteklenmiyor
            </span>
          )}
          {study.modalities.map((modality) => (
            <span
              key={modality}
              className="chip border-amber-400/35 text-amber-400"
              title={modality}
            >
              {modality}
            </span>
          ))}
        </span>
      </button>
    </li>
  );
}
