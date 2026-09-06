/**
 * The same-patient gate.
 *
 * When two exports carry different patient identifiers the engine refuses to
 * compare them. That refusal is correct and worth keeping: comparing two
 * people produces a confident, wrong report, and anonymised exports of
 * different patients look identical from the outside.
 *
 * It is also, usually, resolvable. The archive normally contains a document
 * that maps the folders to one person. This dialog surfaces the documents that
 * already name every selected study, and records the reader's choice: the file
 * is hashed into the session as identity evidence, and the header mismatch
 * stays in the report's limitations. The reader is not clicking past a warning,
 * they are signing an assertion.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { IdentityCandidate } from "../api/types";
import { pickFolder } from "../lib/platform";

interface Props {
  studiesRoot: string;
  folders: string[];
  message: string;
  onConfirm: (identitySource: string) => void;
  onCancel: () => void;
}

export function IdentityGate({ studiesRoot, folders, message, onConfirm, onCancel }: Props) {
  const [candidates, setCandidates] = useState<IdentityCandidate[] | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api
      .identityCandidates(studiesRoot, folders)
      .then((result) => {
        setCandidates(result.candidates);
        setChosen(result.candidates.find((c) => c.covers_all)?.path ?? null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [studiesRoot, folders]);

  const usable = candidates?.filter((c) => c.covers_all) ?? [];

  return (
    <div className="absolute inset-0 z-50 grid place-items-center bg-ink-1000/88 p-8 backdrop-blur-sm">
      <div className="surface w-[620px] max-w-full">
        <header className="border-b border-[var(--hairline)] px-4 py-3">
          <h2 className="text-[14px] font-semibold text-chalk-100">Bu çekimler aynı kişiye mi ait?</h2>
          <p className="mt-1 text-[11px] leading-relaxed text-chalk-600">
            Seçtiğiniz çekimlerin DICOM başlıklarındaki hasta kimlikleri birbirini tutmuyor. Motor,
            aynı kişi olduğu belgelenmeden karşılaştırma yapmayı reddediyor — iki farklı hastayı
            kıyaslamak, kendinden emin ve yanlış bir rapor üretir.
          </p>
          <p className="readout mt-2 text-[10px] text-chalk-700">{message}</p>
        </header>

        <div className="max-h-[280px] overflow-y-auto p-4">
          <p className="eyebrow mb-2">Aynı kişi olduğunu belgeleyen dosya</p>
          {candidates === null && <p className="text-[11px] text-chalk-700">arşiv taranıyor…</p>}
          {candidates !== null && usable.length === 0 && (
            <p className="text-[11px] leading-relaxed text-warn-400">
              Arşivde seçtiğiniz klasörlerin hepsini adıyla anan bir belge bulunamadı. Böyle bir
              belge oluşturup her klasör adını içine yazın, ya da çekimleri tek tek inceleyin.
            </p>
          )}
          <ul className="space-y-1">
            {usable.map((candidate) => (
              <li key={candidate.path}>
                <button
                  type="button"
                  onClick={() => setChosen(candidate.path)}
                  className={`flex w-full items-center gap-2 rounded-[6px] border px-2.5 py-2 text-left ${
                    chosen === candidate.path
                      ? "border-amber-400/60 bg-amber-900/25"
                      : "border-[var(--hairline)] hover:bg-ink-850"
                  }`}
                >
                  <span className="text-amber-400">{chosen === candidate.path ? "◉" : "○"}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] text-chalk-100">{candidate.name}</span>
                    <span className="readout block truncate text-[10px] text-chalk-700">
                      seçilen {folders.length} klasörün hepsini adıyla anıyor
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {candidates !== null && (
            <button
              type="button"
              className="btn mt-2"
              onClick={() => {
                void pickFolder("Belgenin tam yolunu yazın").then((path) => path && setChosen(path));
              }}
            >
              Başka bir dosya seç…
            </button>
          )}
          {error && <p className="mt-2 text-[11px] text-alert-400">{error}</p>}
        </div>

        <footer className="flex items-center gap-2 border-t border-[var(--hairline)] px-4 py-3">
          <p className="flex-1 text-[10px] leading-relaxed text-chalk-700">
            Seçtiğiniz dosyanın SHA-256 özeti oturuma kimlik kanıtı olarak yazılır ve başlık
            uyuşmazlığı raporun kısıtlar bölümünde kalır.
          </p>
          <button type="button" className="btn" onClick={onCancel}>
            Vazgeç
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!chosen}
            onClick={() => chosen && onConfirm(chosen)}
          >
            Aynı kişi, devam et
          </button>
        </footer>
      </div>
    </div>
  );
}
