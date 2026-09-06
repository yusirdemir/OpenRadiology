/**
 * The findings, as a person would want them.
 *
 * The first version of this rail put every plain-language field on screen the
 * moment a finding was selected -- what it means, how certain it is, what to
 * ask a doctor -- as three stacked paragraphs of serif prose. Each sentence was
 * worth reading and the pile of them was not: it pushed the rest of the list
 * off screen and made a calm answer look like a wall.
 *
 * So the rail is now three depths, and only the first is ever forced on
 * anybody. A row is a verdict, a measured change, and one sentence. Selecting
 * it adds the one paragraph that says why it matters. Everything else --
 * certainty, questions for the doctor, the radiologist's own wording, the
 * addresses and the hashes -- is one more click, in a panel that scrolls on its
 * own so the list above it never moves.
 */
import { useState } from "react";
import type { Ref } from "../api/types";
import { changeLabel, findingBadge, format, shortDate, tally, type Finding } from "../lib/findings";
import {
  ChevronDown,
  ChevronRight,
  Crosshair,
  ShieldCheck,
  Stethoscope,
  VERDICT_ICONS,
} from "./icons";

interface Props {
  findings: Finding[];
  activeId: string | null;
  comparison: boolean;
  /** Oldest first, to label the columns of the technical detail. */
  studyDates: { uid: string; date: string }[];
  onSelect: (id: string | null) => void;
  onJump: (reference: Ref) => void;
}

export function FindingRail({ findings, activeId, comparison, studyDates, onSelect, onJump }: Props) {
  const counts = tally(findings);

  return (
    <div className="flex h-full min-h-0 flex-col bg-ink-950">
      <header className="shrink-0 border-b border-[var(--hairline)] px-4 pb-3 pt-4">
        <h2 className="display text-[18px]">
          {counts.total === 0
            ? "Kaydedilmiş bulgu yok"
            : `${counts.total} bulgu incelendi`}
        </h2>
        {comparison && counts.total > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            {counts.worse > 0 && <Tally n={counts.worse} label="büyüdü" tone="alert" />}
            {counts.attention > 0 && <Tally n={counts.attention} label="takip" tone="warn" />}
            {counts.better > 0 && <Tally n={counts.better} label="iyileşti" tone="good" />}
            {counts.same > 0 && <Tally n={counts.same} label="değişmedi" tone="calm" />}
          </div>
        )}
      </header>

      <div className="scroll-y min-h-0 flex-1 px-2 py-2">
        {findings.length === 0 && (
          <p className="px-2 py-6 text-[12.5px] leading-relaxed text-chalk-600">
            Bu incelemede kayda geçmiş bir bulgu yok. Kesitleri yine de gezebilir, raporu
            açabilirsiniz.
          </p>
        )}
        <ul className="space-y-1">
          {findings.map((finding, i) => (
            <li key={finding.id} className="rise" style={{ animationDelay: `${Math.min(i * 40, 240)}ms` }}>
              <FindingRow
                finding={finding}
                active={finding.id === activeId}
                comparison={comparison}
                studyDates={studyDates}
                onSelect={() => onSelect(finding.id === activeId ? null : finding.id)}
                onJump={onJump}
              />
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Tally({ n, label, tone }: { n: number; label: string; tone: string }) {
  return (
    <span className={`verdict verdict-${tone}`}>
      <span className="readout">{n}</span>
      {label}
    </span>
  );
}

function FindingRow({
  finding,
  active,
  comparison,
  studyDates,
  onSelect,
  onJump,
}: {
  finding: Finding;
  active: boolean;
  comparison: boolean;
  studyDates: { uid: string; date: string }[];
  onSelect: () => void;
  onJump: (reference: Ref) => void;
}) {
  const [deep, setDeep] = useState(false);
  const style = findingBadge(finding, comparison);
  const Icon = VERDICT_ICONS[style.icon] ?? VERDICT_ICONS.dot;
  const change = changeLabel(finding);

  return (
    <div
      className={`overflow-hidden rounded-[8px] border transition-colors ${
        active ? "border-[var(--hairline-strong)] bg-ink-900" : "border-transparent hover:bg-ink-900/60"
      }`}
    >
      <button type="button" className="finding-row" data-active={active} onClick={onSelect}>
        <span className="flex items-center gap-2">
          <span className={`verdict verdict-${style.tone}`}>
            {Icon && <Icon size={11} strokeWidth={2.6} aria-hidden />}
            {style.label}
          </span>
          {change && <span className="readout ml-auto shrink-0 text-[11px] text-chalk-400">{change}</span>}
        </span>
        <span
          className={`mt-1.5 block text-[13px] leading-[1.5] ${active ? "text-chalk-100" : "text-chalk-300"}`}
          style={active ? undefined : { display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}
        >
          {finding.headline}
        </span>
      </button>

      {active && (
        <div className="fade-in px-4 pb-3.5">
          {finding.importance && (
            <p className="text-[12.5px] leading-[1.62] text-chalk-400">{finding.importance}</p>
          )}

          <button
            type="button"
            onClick={() => setDeep((open) => !open)}
            className="mt-3 flex items-center gap-1.5 text-[11.5px] font-medium text-chalk-600 transition-colors hover:text-chalk-200"
          >
            {deep ? <ChevronDown size={13} aria-hidden /> : <ChevronRight size={13} aria-hidden />}
            Ayrıntı, kesinlik ve kanıt
          </button>

          {deep && (
            <div className="fade-in mt-2.5 max-h-[19rem] space-y-3 overflow-y-auto border-l border-[var(--hairline-strong)] pl-3">
              {finding.uncertainty && (
                <Detail icon={ShieldCheck} label="Ne kadar kesin">
                  {finding.uncertainty}
                </Detail>
              )}
              {finding.withDoctor && (
                <Detail icon={Stethoscope} label="Doktorunuza sorun">
                  {finding.withDoctor}
                </Detail>
              )}
              <Detail icon={Crosshair} label="Radyolog ifadesi">
                {finding.technical}
              </Detail>
              {comparison && finding.reason && (
                <Detail icon={Crosshair} label="Değişim gerekçesi">
                  {finding.reason}
                </Detail>
              )}

              <div>
                <p className="eyebrow mb-1.5">Kanıt</p>
                <ul className="space-y-1.5">
                  {finding.series.map((at) => {
                    const date = studyDates.find((s) => s.uid === at.studyUid)?.date ?? "";
                    return (
                      <li key={at.studyUid} className="flex flex-wrap items-center gap-1.5">
                        <span className="readout w-[4.6rem] shrink-0 text-[10.5px] text-chalk-700">
                          {shortDate(date)}
                        </span>
                        {at.measurement ? (
                          <span
                            className="chip"
                            title={[
                              at.measurement.method,
                              `sha256 ${at.measurement.sha256}`,
                              at.measurement.evidence_file,
                            ].join("\n")}
                          >
                            <span className="font-semibold text-chalk-200">
                              {format(at.measurement.value)}
                            </span>
                            <span className="text-chalk-600">{at.measurement.unit}</span>
                            <ShieldCheck size={10} className="text-good-400" aria-label="mühürlü ölçüm" />
                          </span>
                        ) : (
                          <span className="chip text-chalk-700">ölçüm yok</span>
                        )}
                        {at.ref && (
                          <button
                            type="button"
                            className="chip chip-link"
                            title={`Kesit adresi\nSOP ${at.ref.sop_uid}`}
                            onClick={() => at.ref && onJump(at.ref)}
                          >
                            <Crosshair size={10} className="text-amber-400" aria-hidden />
                            {at.ref.row !== undefined && at.ref.col !== undefined
                              ? `r${at.ref.row} c${at.ref.col}`
                              : "kesite git"}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
                <p className="mt-2 text-[10.5px] leading-relaxed text-chalk-700">
                  İşaretin yeri bu satırdaki satır/sütun adresinden gelir; arayüz hiçbir koordinatı
                  kendisi hesaplamaz.
                </p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Detail({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof ShieldCheck;
  label: string;
  children: string;
}) {
  return (
    <div>
      <p className="eyebrow mb-1 flex items-center gap-1.5">
        <Icon size={11} aria-hidden />
        {label}
      </p>
      <p className="text-[12px] leading-[1.6] text-chalk-400">{children}</p>
    </div>
  );
}
