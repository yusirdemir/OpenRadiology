/**
 * The front door.
 *
 * There are two things this application does, so there are two things on this
 * screen: the reviews already on the machine, and a way to start a new one. A
 * new one is either a single examination or a pair -- the picker enforces that
 * by refusing to hold more than two studies, rather than by explaining it.
 *
 * The cards are reductions of the saved ledgers, assembled by the library
 * store; the counts on them are counts of words already written into
 * `claims[].comparison.status`, never a re-reading of the images.
 */
import { useEffect, useMemo, useState } from "react";
import type { SessionCard, StudyCard } from "../api/types";
import { longDate, shortDate } from "../lib/findings";
import { pickFolder } from "../lib/platform";
import { useConnection } from "../state/connection";
import { isReadable, reviewName, setReviewName, useLibrary } from "../state/library";
import { MAX_SELECTION, useShelf } from "../state/shelf";
import { ArrowRight, Check, Pencil, Plus, X } from "./icons";

interface Props {
  onOpenSession: (path: string) => void;
  onPrepare: (paths: string[]) => void;
  opening: string | null;
  preparing: boolean;
}

export function Home({ onOpenSession, onPrepare, opening, preparing }: Props) {
  const { cards, loading, loaded, error, refresh } = useLibrary();
  const version = useConnection((s) => s.version);
  const [picker, setPicker] = useState(false);

  useEffect(() => {
    if (!loaded) void refresh();
  }, [loaded, refresh]);

  const ready = useMemo(() => cards.filter(isReadable), [cards]);

  return (
    <div className="scroll-y h-full bg-ink-950">
      <div className="mx-auto w-full max-w-[980px] px-8 pb-24 pt-12">
        <header className="flex items-start justify-between gap-6">
          <div>
            <p className="eyebrow">OpenRadiology</p>
            <h1 className="display mt-2.5 text-[34px]">İncelemeler</h1>
            <p className="prose-human mt-3 text-[14.5px]">
              Bir tetkiki tek başına inceleyin, ya da iki tetkiki yan yana koyup neyin değiştiğini
              görün. Aşağıdakiler bu bilgisayarda kayıtlı okumalar.
            </p>
          </div>
          <span className="mt-1 flex shrink-0 items-center gap-2 rounded-full border border-[var(--hairline)] bg-ink-900 px-3 py-1 text-[11px] text-chalk-600">
            <span className="h-1.5 w-1.5 rounded-full bg-good-400" />
            Motor {version || "…"}
          </span>
        </header>

        <div className="mt-7 flex flex-wrap items-center gap-2.5">
          <button type="button" className="btn btn-primary btn-lg" onClick={() => setPicker(true)}>
            <Plus size={15} aria-hidden />
            Yeni inceleme başlat
          </button>
          <button type="button" className="btn btn-lg" disabled={loading} onClick={() => void refresh()}>
            {loading ? "Aranıyor…" : "Listeyi yenile"}
          </button>
        </div>

        {error && (
          <p className="mt-6 rounded-[8px] border border-alert-400/30 bg-alert-900/50 px-3.5 py-2.5 text-[12px] text-alert-400">
            {error}
          </p>
        )}

        <section className="mt-11">
          <h2 className="eyebrow mb-3.5">Kayıtlı incelemeler</h2>
          {!loaded && <CardSkeletons />}
          {loaded && ready.length === 0 && (
            <p className="rounded-[10px] border border-dashed border-[var(--hairline-strong)] px-5 py-8 text-center text-[12.5px] leading-relaxed text-chalk-600">
              Henüz kayıtlı bir okuma yok. “Yeni inceleme başlat” ile bir DICOM klasörü seçin.
            </p>
          )}
          <ul className="space-y-2.5">
            {ready.map((card, i) => (
              <li key={card.path} className="rise" style={{ animationDelay: `${Math.min(i * 55, 340)}ms` }}>
                <ReviewCard
                  card={card}
                  busy={opening === card.path}
                  disabled={opening !== null}
                  onOpen={() => onOpenSession(card.path)}
                />
              </li>
            ))}
          </ul>
        </section>

      </div>

      {picker && (
        <StudyPicker
          preparing={preparing}
          onClose={() => setPicker(false)}
          onStart={(paths) => onPrepare(paths)}
        />
      )}
    </div>
  );
}

// --------------------------------------------------------------------- card
const MODALITY_SHORT: Record<string, string> = { CT: "Tomografi", MR: "MR", PT: "PET/BT" };

function summarise(card: SessionCard): { better: number; same: number; worse: number; attention: number } {
  const out = { better: 0, same: 0, worse: 0, attention: 0 };
  for (const [status, count] of Object.entries(card.verdicts)) {
    if (status === "resolved" || status === "decreased") out.better += count;
    else if (status === "increased") out.worse += count;
    else if (status === "new" || status === "indeterminate") out.attention += count;
    else out.same += count;
  }
  return out;
}

function ReviewCard({
  card,
  busy,
  disabled,
  onOpen,
}: {
  card: SessionCard;
  busy: boolean;
  disabled: boolean;
  onOpen: () => void;
}) {
  const counts = summarise(card);
  const comparison = card.mode === "comparison" && card.studies.length > 1;
  const modality = MODALITY_SHORT[card.studies[0]?.modalities?.[0] ?? ""] ?? "İnceleme";
  const first = card.studies[0];
  const last = card.studies[card.studies.length - 1];

  const [name, setName] = useState(() => reviewName(card.path));
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);

  const commit = () => {
    setReviewName(card.path, draft);
    setName(draft.trim());
    setEditing(false);
  };

  const dates =
    comparison && first && last
      ? `${longDate(first.date)} → ${longDate(last.date)}`
      : longDate(first?.date ?? "");

  return (
    <div className="group relative rounded-[10px] border border-[var(--hairline)] bg-ink-900 transition-colors hover:border-[var(--hairline-strong)] hover:bg-ink-850">
      <button
        type="button"
        onClick={onOpen}
        disabled={disabled || editing}
        className="flex w-full items-center gap-5 px-5 py-4 text-left disabled:opacity-60"
      >
        <div className="min-w-0 flex-1">
          <p className="eyebrow">
            {comparison ? "Karşılaştırma" : "Tek tetkik"} · {modality}
          </p>
          {editing ? (
            <div
              className="mt-1.5 flex items-center gap-1.5"
              onClick={(event) => event.stopPropagation()}
            >
              <input
                autoFocus
                value={draft}
                placeholder="Örn. Babam · kontrol"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") commit();
                  if (event.key === "Escape") {
                    setDraft(name);
                    setEditing(false);
                  }
                }}
                className="field w-[19rem]"
              />
              <button type="button" className="btn btn-ghost px-2" onClick={commit} aria-label="Kaydet">
                <Check size={14} aria-hidden />
              </button>
              <button
                type="button"
                className="btn btn-ghost px-2"
                aria-label="Vazgeç"
                onClick={() => {
                  setDraft(name);
                  setEditing(false);
                }}
              >
                <X size={14} aria-hidden />
              </button>
            </div>
          ) : (
            <>
              <h3 className="display mt-1.5 text-[17px]">{name || dates}</h3>
              {name && <p className="mt-0.5 text-[12px] text-chalk-600">{dates}</p>}
            </>
          )}
          <p className="mt-2 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[12px]">
            {counts.better > 0 && (
              <span className="text-good-400">
                <strong className="font-semibold">{counts.better}</strong> iyileşti
              </span>
            )}
            {counts.worse > 0 && (
              <span className="text-alert-400">
                <strong className="font-semibold">{counts.worse}</strong> büyüdü
              </span>
            )}
            {counts.attention > 0 && (
              <span className="text-warn-400">
                <strong className="font-semibold">{counts.attention}</strong> takip
              </span>
            )}
            {counts.same > 0 && (
              <span className="text-calm-400">
                <strong className="font-semibold">{counts.same}</strong>{" "}
                {comparison ? "değişmedi" : "bulgu"}
              </span>
            )}
          </p>
        </div>

        <span className="flex shrink-0 items-center gap-2 text-[12px] font-medium text-chalk-400 transition-colors group-hover:text-amber-400">
          {busy ? (
            <>
              <span className="h-3.5 w-3.5 rounded-full border-2 border-amber-400/30 border-t-amber-400 spin" />
              Açılıyor…
            </>
          ) : (
            <>
              Aç
              <ArrowRight size={14} aria-hidden />
            </>
          )}
        </span>
      </button>

      {!editing && (
        <button
          type="button"
          title="Bu incelemeye bir ad ver"
          aria-label="Ad ver"
          onClick={() => {
            setDraft(name);
            setEditing(true);
          }}
          className="absolute right-[5.5rem] top-4 rounded-[4px] p-1 text-chalk-700 opacity-0 transition-opacity hover:text-chalk-200 group-hover:opacity-100"
        >
          <Pencil size={13} aria-hidden />
        </button>
      )}
    </div>
  );
}

function CardSkeletons() {
  return (
    <ul className="space-y-2.5" aria-hidden>
      {[0, 1, 2].map((i) => (
        <li key={i} className="rounded-[10px] border border-[var(--hairline)] bg-ink-900 px-5 py-4">
          <span className="skeleton block h-2.5 w-28" />
          <span className="skeleton mt-3 block h-4 w-72" />
          <span className="skeleton mt-3 block h-2.5 w-44" />
        </li>
      ))}
    </ul>
  );
}

// ------------------------------------------------------------------- picker
/**
 * Starting a new review.
 *
 * The whole mode question is answered by how many studies are ticked: one is a
 * single read, two is a comparison. There is no mode switch to get wrong, and
 * a third tick quietly replaces the older of the two rather than failing.
 */
function StudyPicker({
  preparing,
  onClose,
  onStart,
}: {
  preparing: boolean;
  onClose: () => void;
  onStart: (paths: string[]) => void;
}) {
  const { root, studies, scanning, error, selection, scan, toggle, clearSelection } = useShelf();
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

  const chosen = studies.filter((s) => selection.includes(s.path));
  const comparison = selection.length === MAX_SELECTION;

  return (
    <div className="fade-in fixed inset-0 z-40 grid place-items-center bg-ink-1000/85 p-6 backdrop-blur-sm">
      <div className="surface flex max-h-[86vh] w-full max-w-[760px] flex-col overflow-hidden">
        <header className="shrink-0 border-b border-[var(--hairline)] px-6 py-5">
          <h2 className="display text-[21px]">Yeni inceleme</h2>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-chalk-400">
            Bir tetkik seçerseniz tek inceleme, iki tetkik seçerseniz karşılaştırma açılır. En fazla
            iki tetkik.
          </p>
        </header>

        <div className="flex shrink-0 items-center gap-2 border-b border-[var(--hairline)] px-6 py-3">
          <button type="button" className="btn" onClick={choose} disabled={scanning}>
            Klasör seç…
          </button>
          {root && (
            <button type="button" className="btn btn-ghost" onClick={() => void scan(root)} disabled={scanning}>
              Yeniden tara
            </button>
          )}
          {root && <span className="readout truncate text-[10.5px] text-chalk-700">{root}</span>}
          {scanning && (
            <span className="ml-auto flex items-center gap-2 text-[11.5px] text-chalk-500">
              <span className="h-3 w-3 rounded-full border-2 border-amber-400/30 border-t-amber-400 spin" />
              taranıyor…
            </span>
          )}
        </div>

        <div className="scroll-y min-h-0 flex-1 px-6 py-4">
          {error && <p className="mb-3 text-[12px] text-alert-400">{error}</p>}
          {scanning && !studies.length && <RowSkeletons />}
          {!scanning && !studies.length && (
            <div
              className={`grid h-40 place-items-center rounded-[10px] border border-dashed px-6 text-center transition-colors ${
                dragging ? "border-amber-400 bg-amber-900/25" : "border-[var(--hairline-strong)]"
              }`}
            >
              <div>
                <p className="text-[13px] text-chalk-200">Bir DICOM klasörü seçin</p>
                <p className="mt-1.5 text-[11.5px] leading-relaxed text-chalk-600">
                  Alt klasörler de taranır; çekimler DICOM serilerine göre gruplanır.
                </p>
              </div>
            </div>
          )}
          <ul className="space-y-1.5">
            {studies.map((study, i) => (
              <StudyRow
                key={study.study_uid || study.path}
                study={study}
                index={i}
                rank={selection.indexOf(study.path)}
                onToggle={() => toggle(study.path)}
              />
            ))}
          </ul>
        </div>

        <footer className="flex shrink-0 items-center gap-3 border-t border-[var(--hairline)] px-6 py-4">
          <div className="min-w-0 flex-1">
            <p className="text-[12.5px] font-medium text-chalk-100">
              {selection.length === 0
                ? "Hiç tetkik seçilmedi"
                : comparison
                  ? "Karşılaştırma: önceki ve son tetkik"
                  : "Tek tetkik incelemesi"}
            </p>
            <p className="readout truncate text-[10.5px] text-chalk-600">
              {chosen
                .map((s) => `${shortDate(s.date)} ${s.description || s.folder}`)
                .join("   →   ") || "—"}
            </p>
          </div>
          {selection.length > 0 && (
            <button type="button" className="btn btn-ghost" onClick={clearSelection}>
              Temizle
            </button>
          )}
          <button type="button" className="btn" onClick={onClose}>
            Vazgeç
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={selection.length === 0 || preparing}
            onClick={() => onStart(selection)}
          >
            {preparing ? (
              <>
                <span className="h-3.5 w-3.5 rounded-full border-2 border-ink-1000/40 border-t-ink-1000 spin" />
                Hazırlanıyor…
              </>
            ) : comparison ? (
              "Karşılaştır"
            ) : (
              "İncelemeyi aç"
            )}
          </button>
        </footer>
      </div>
    </div>
  );
}

function StudyRow({
  study,
  index,
  rank,
  onToggle,
}: {
  study: StudyCard;
  index: number;
  rank: number;
  onToggle: () => void;
}) {
  const chosen = rank >= 0;
  return (
    <li>
      <button
        type="button"
        onClick={onToggle}
        disabled={!study.supported}
        style={{ animationDelay: `${Math.min(index * 22, 260)}ms` }}
        className={`rise grid w-full grid-cols-[22px_9rem_1fr_auto] items-center gap-3 rounded-[7px] border px-3 py-2.5 text-left transition-colors ${
          chosen
            ? "border-amber-400/55 bg-amber-900/25"
            : "border-[var(--hairline)] hover:border-[var(--hairline-strong)] hover:bg-ink-850"
        } ${study.supported ? "" : "opacity-45"}`}
      >
        <span
          className={`readout grid h-5 w-5 place-items-center rounded-full border text-[10px] ${
            chosen
              ? "border-amber-400 bg-amber-400 font-semibold text-ink-1000"
              : "border-[var(--hairline-strong)] text-transparent"
          }`}
        >
          {chosen ? rank + 1 : "0"}
        </span>

        <span className="leading-tight">
          <span className="block text-[12.5px] text-chalk-100">{shortDate(study.date)}</span>
          <span className="readout block text-[10px] text-chalk-700">
            {study.time ? `${study.time.slice(0, 2)}:${study.time.slice(2, 4)}` : ""}
          </span>
        </span>

        <span className="min-w-0">
          <span className="block truncate text-[12.5px] text-chalk-100">
            {study.description || study.folder}
          </span>
          <span className="readout block truncate text-[10px] text-chalk-700">
            {study.series_count} seri · {study.instances} kesit
            {study.shares_folder && " · klasörde birden fazla çekim"}
            {study.inconsistent_date && " · tutarsız tarih"}
          </span>
        </span>

        <span className="flex items-center gap-1.5">
          {study.identity_removed && (
            <span className="chip text-good-400" title="PatientIdentityRemoved = YES">
              anonim
            </span>
          )}
          {!study.supported && (
            <span className="chip text-chalk-700" title="Desteklenen protokoller: BT, PET/BT, beyin MR">
              desteklenmiyor
            </span>
          )}
          {study.modalities.map((modality) => (
            <span key={modality} className="chip border-amber-400/35 text-amber-400">
              {modality}
            </span>
          ))}
        </span>
      </button>
    </li>
  );
}

function RowSkeletons() {
  return (
    <ul className="space-y-1.5" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <li key={i} className="skeleton h-[52px] rounded-[7px]" />
      ))}
    </ul>
  );
}
