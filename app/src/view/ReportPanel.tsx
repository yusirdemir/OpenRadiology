/**
 * The two reports, over the reading rather than instead of it.
 *
 * This used to be a separate screen showing the engine's Markdown end to end,
 * which is thorough and completely unlike a radiology report: no reader wants
 * eight screens of prose when the form they know has six headings. So both
 * documents are composed here, from the ledger, in the shape each audience
 * expects.
 *
 * Composed, not written. Every sentence on both tabs is a sentence the engine
 * already put in the session -- a region's finding, a claim's wording, a
 * patient explanation -- and this file only decides the order and the
 * headings. Nothing is summarised, nothing is merged, and the sealed document
 * the engine produced is still one click away with its hash, because that is
 * the artifact and this is a view of it.
 */
import { useMemo, useState } from "react";
import type { LocaleBundle, Region, Session, SessionStudy } from "../api/types";
import { longDate, orderedStudies, type Finding } from "../lib/findings";
import { Check, FileText, Stethoscope, X } from "./icons";

type Tab = "clinical" | "patient";

interface Props {
  session: Session;
  findings: Finding[];
  locale: LocaleBundle | null;
  /** Digest of the document the engine sealed, shown as provenance. */
  reportSha: string;
  onClose: () => void;
}

const MODALITY: Record<string, string> = {
  CT: "Bilgisayarlı tomografi",
  MR: "Manyetik rezonans görüntüleme",
  PT: "PET/BT",
};

/** The region key without its modality prefix, which is how the locale keys it. */
function bareKey(key: string): string {
  return key.includes(":") ? key.slice(key.indexOf(":") + 1) : key;
}

function regionLabel(locale: LocaleBundle | null, key: string): string {
  const bare = bareKey(key);
  return locale?.regions?.[bare] ?? bare.replace(/_/g, " ");
}

const STATUS_NOTE: Record<Region["status"], string | null> = {
  finding: null,
  no_finding: null,
  limited: "Değerlendirme sınırlı.",
  not_covered: "Bu tetkikin kapsamı dışında.",
  pending: "Sonuçlandırılmadı.",
};

export function ReportPanel({ session, findings, locale, reportSha, onClose }: Props) {
  const [tab, setTab] = useState<Tab>("clinical");
  const studies = useMemo(() => orderedStudies(session), [session]);
  const current = studies[studies.length - 1];
  const prior = studies.length > 1 ? studies[0] : undefined;

  return (
    <div className="fade-in fixed inset-0 z-40 flex justify-end bg-ink-1000/70 backdrop-blur-[2px]" onClick={onClose}>
      <div
        className="report-sheet flex h-full w-full max-w-[860px] flex-col border-l border-[var(--hairline-strong)] bg-ink-950 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex shrink-0 items-center gap-3 border-b border-[var(--hairline)] px-5 py-3">
          <div className="flex rounded-[6px] border border-[var(--hairline)] bg-ink-900 p-0.5">
            {(
              [
                ["clinical", "Hekim raporu", FileText],
                ["patient", "Hasta raporu", Stethoscope],
              ] as [Tab, string, typeof FileText][]
            ).map(([id, label, Icon]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTab(id)}
                className={`flex items-center gap-1.5 rounded-[4px] px-3 py-1 text-[12px] font-medium transition-colors ${
                  tab === id ? "bg-ink-700 text-chalk-100" : "text-chalk-500 hover:text-chalk-200"
                }`}
              >
                <Icon size={13} aria-hidden />
                {label}
              </button>
            ))}
          </div>
          <div className="min-w-0 flex-1" />
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => window.print()}
            title="Yazdır veya PDF olarak kaydet"
          >
            Yazdır
          </button>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Kapat">
            <X size={15} aria-hidden />
          </button>
        </header>

        <div className="scroll-y min-h-0 flex-1 px-9 py-7">
          {tab === "clinical" ? (
            <ClinicalReport
              session={session}
              findings={findings}
              locale={locale}
              current={current}
              prior={prior}
            />
          ) : (
            <PatientReport session={session} findings={findings} current={current} prior={prior} />
          )}

          <footer className="mt-10 border-t border-[var(--hairline)] pt-4">
            <p className="text-[11px] leading-relaxed text-chalk-700">
              Bu iki belge oturum kaydından derlendi; her cümle motorun yazdığı cümledir.
              {reportSha && (
                <>
                  {" "}
                  Motorun mühürlediği metnin özeti:{" "}
                  <span className="readout">{reportSha.slice(0, 32)}…</span>
                </>
              )}
            </p>
          </footer>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ clinical
/**
 * The form a radiologist expects, in the order they expect it: what was done,
 * why, what it was compared with, what was seen region by region, and then the
 * impression that a referring physician actually reads.
 */
function ClinicalReport({
  session,
  findings,
  locale,
  current,
  prior,
}: {
  session: Session;
  findings: Finding[];
  locale: LocaleBundle | null;
  current: SessionStudy | undefined;
  prior: SessionStudy | undefined;
}) {
  const regions = Object.entries(current?.regions ?? {});
  const technique = regions.find(([key]) => bareKey(key) === "technique")?.[1];
  const body = regions.filter(([key]) => bareKey(key) !== "technique");
  const modality = MODALITY[current?.modalities?.[0] ?? ""] ?? "Görüntüleme";

  return (
    <article className="report">
      <header className="mb-6 border-b border-[var(--hairline)] pb-4">
        <h1 className="display text-[22px]">{modality} raporu</h1>
        <dl className="mt-3 grid grid-cols-[9rem_1fr] gap-x-4 gap-y-1 text-[12.5px]">
          <Field label="Tetkik tarihi">{current ? longDate(current.date) : "—"}</Field>
          <Field label="Karşılaştırma">
            {prior ? `${longDate(prior.date)} tarihli tetkik ile` : "Karşılaştırma yapılmadı"}
          </Field>
          <Field label="Değerlendiren">{session.reader || "—"}</Field>
        </dl>
      </header>

      {session.clinical_context && (
        <Section title="Klinik bilgi">
          <p>{session.clinical_context}</p>
        </Section>
      )}

      {technique?.text && (
        <Section title="Teknik">
          <p>{technique.text}</p>
        </Section>
      )}

      {session.comparison?.reason && prior && (
        <Section title="Karşılaştırma yöntemi">
          <p>{session.comparison.reason}</p>
        </Section>
      )}

      <Section title="Bulgular">
        <dl className="space-y-2.5">
          {body.map(([key, region]) => (
            <div key={key} className="grid grid-cols-[11rem_1fr] gap-x-4">
              <dt className="text-[12px] font-semibold leading-[1.6] text-chalk-200">
                {regionLabel(locale, key)}
              </dt>
              <dd className="text-[13px] leading-[1.65] text-chalk-300">
                {region.text || STATUS_NOTE[region.status] || "—"}
                {region.status !== "finding" && region.text && STATUS_NOTE[region.status] && (
                  <span className="text-chalk-600"> {STATUS_NOTE[region.status]}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Sonuç">
        <ol className="space-y-2.5">
          {findings.map((finding, i) => (
            <li key={finding.id} className="grid grid-cols-[1.4rem_1fr] gap-x-2">
              <span className="readout text-[12px] text-chalk-600">{i + 1}.</span>
              <span className="text-[13px] leading-[1.65] text-chalk-200">
                {finding.technical}
                {finding.change && (
                  <span className="readout ml-1.5 text-[11.5px] text-chalk-500">
                    ({finding.change.from} → {finding.change.to} {finding.change.unit})
                  </span>
                )}
              </span>
            </li>
          ))}
          {findings.length === 0 && <li className="text-[13px] text-chalk-500">Kayda geçmiş bulgu yok.</li>}
        </ol>
      </Section>

      {(session.recommendations ?? []).length > 0 && (
        <Section title="Öneriler">
          <ul className="space-y-1.5">
            {(session.recommendations ?? []).map((line, i) => (
              <li key={i} className="text-[13px] leading-[1.65] text-chalk-300">
                {line}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {session.limitations.length > 0 && (
        <Section title="Kısıtlar">
          <ul className="space-y-1.5">
            {session.limitations.map((line, i) => (
              <li key={i} className="text-[12.5px] leading-[1.6] text-chalk-500">
                {line}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <p className="mt-7 border-t border-[var(--hairline)] pt-3 text-[11px] leading-relaxed text-chalk-700">
        {session.validation_note ||
          "Yapısal kanıt denetimi görüntü yorumunu veya tanısal duyarlılığı doğrulamaz."}{" "}
        Bu bir ön değerlendirmedir; nihai rapor görüntüleri inceleyen hekime aittir.
      </p>
    </article>
  );
}

// ------------------------------------------------------------------- patient
function PatientReport({
  session,
  findings,
  current,
  prior,
}: {
  session: Session;
  findings: Finding[];
  current: SessionStudy | undefined;
  prior: SessionStudy | undefined;
}) {
  return (
    <article className="report">
      <header className="mb-6 border-b border-[var(--hairline)] pb-4">
        <h1 className="display text-[22px]">Tetkikinizin anlaşılır özeti</h1>
        <p className="mt-2 text-[12.5px] text-chalk-500">
          {current ? longDate(current.date) : "—"}
          {prior && ` · ${longDate(prior.date)} tarihli tetkik ile karşılaştırıldı`}
        </p>
      </header>

      {session.patient_context && (
        <Section title="Bu inceleme neydi">
          <p className="prose-human">{session.patient_context}</p>
        </Section>
      )}

      {prior && session.comparison?.explanation && (
        <Section title="Özetle ne değişti">
          <p className="prose-human">{session.comparison.explanation}</p>
        </Section>
      )}

      <Section title="Bulunanlar">
        <ol className="space-y-4">
          {findings.map((finding) => (
            <li key={finding.id}>
              <p className="text-[13.5px] font-semibold leading-[1.5] text-chalk-100">
                {finding.headline}
              </p>
              {finding.importance && (
                <p className="prose-human mt-1 text-[13px]">{finding.importance}</p>
              )}
              {finding.withDoctor && (
                <p className="mt-1.5 flex gap-2 text-[12.5px] leading-[1.6] text-chalk-500">
                  <Stethoscope size={13} className="mt-[3px] shrink-0" aria-hidden />
                  <span>{finding.withDoctor}</span>
                </p>
              )}
            </li>
          ))}
          {findings.length === 0 && (
            <li className="text-[13px] text-chalk-500">Kayda geçmiş bir bulgu yok.</li>
          )}
        </ol>
      </Section>

      {session.patient_limitations && (
        <Section title="Bu inceleme neyi söyleyemez">
          <p className="prose-human">{session.patient_limitations}</p>
        </Section>
      )}

      {(session.glossary ?? []).length > 0 && (
        <Section title="Geçen terimler">
          <dl className="space-y-2">
            {(session.glossary ?? []).map((entry) => (
              <div key={entry.term}>
                <dt className="text-[12.5px] font-semibold text-chalk-200">{entry.term}</dt>
                <dd className="text-[12.5px] leading-[1.6] text-chalk-500">{entry.explanation}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}

      <p className="mt-7 flex items-start gap-2 border-t border-[var(--hairline)] pt-3 text-[11.5px] leading-relaxed text-chalk-600">
        <Check size={13} className="mt-[3px] shrink-0 text-good-400" aria-hidden />
        <span>
          Buradaki her cümle, tomografi görüntüleri üzerinden ölçülerek yazıldı. Yine de bu bir ön
          değerlendirmedir; tedaviyle ilgili her kararı doktorunuz verir.
        </span>
      </p>
    </article>
  );
}

// --------------------------------------------------------------------- parts
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="eyebrow mb-2 border-b border-[var(--hairline)] pb-1.5">{title}</h2>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <>
      <dt className="text-chalk-600">{label}</dt>
      <dd className="text-chalk-200">{children}</dd>
    </>
  );
}
