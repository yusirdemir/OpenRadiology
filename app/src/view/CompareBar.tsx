/**
 * Interval change, for a comparison read.
 *
 * One row per finding, one column per examination date. The verdict words are
 * the ledger's own -- new, increased, decreased, stable, resolved,
 * indeterminate, not_comparable -- and the engine will not accept any of them
 * without evidence from both endpoints, so a row that looks empty is telling
 * the truth about what was actually established rather than hiding a guess.
 *
 * Numbers come from the claim's measurements, which carry the hash of the
 * evidence file they were read out of. Nothing here is computed from anything
 * on screen.
 */
import type { Claim, Ref, SessionStudy } from "../api/types";
import { formatStudyDate } from "../lib/platform";

const VERDICT_TR: Record<string, string> = {
  new: "yeni",
  increased: "büyümüş",
  decreased: "küçülmüş",
  stable: "stabil",
  resolved: "gerilemiş",
  indeterminate: "belirsiz",
  not_comparable: "kıyaslanamaz",
};

const VERDICT_TONE: Record<string, string> = {
  new: "text-alarm-400",
  increased: "text-alarm-400",
  decreased: "text-attest-400",
  resolved: "text-attest-400",
  stable: "text-chalk-300",
  indeterminate: "text-caution-400",
  not_comparable: "text-chalk-600",
};

const TIMEPOINT_MARK: Record<string, string> = {
  present: "●",
  not_seen: "○",
  not_covered: "—",
  indeterminate: "?",
};

interface Props {
  studies: SessionStudy[];
  claims: Claim[];
  activeClaimId: string | null;
  onSelect: (id: string) => void;
  onJump: (reference: Ref) => void;
}

export function CompareBar({ studies, claims, activeClaimId, onSelect, onJump }: Props) {
  const ordered = [...studies].sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
  if (ordered.length < 2) return null;

  return (
    <div className="flex max-h-[184px] flex-col border-t border-[var(--hairline)] bg-ink-900">
      <header className="flex h-6 shrink-0 items-center gap-2 border-b border-[var(--hairline)] px-2.5">
        <span className="rail-label">Zaman içindeki değişim</span>
        <span className="text-[10px] text-chalk-600">
          her verdikt iki uçtan da kanıt ister; eksikse kayıt boş kalır
        </span>
      </header>

      <div className="scroll-y min-h-0">
        <table className="w-full border-collapse text-[11px]">
          <thead>
            <tr className="text-chalk-600">
              <th className="rail-label sticky top-0 bg-ink-900 px-2.5 py-1 text-left font-normal">Bulgu</th>
              {ordered.map((study) => (
                <th key={study.uid} className="readout sticky top-0 bg-ink-900 px-2 py-1 text-left font-normal">
                  {formatStudyDate(study.date)}
                </th>
              ))}
              <th className="rail-label sticky top-0 bg-ink-900 px-2 py-1 text-left font-normal">Değişim</th>
              <th className="rail-label sticky top-0 bg-ink-900 px-2 py-1 text-left font-normal">Ölçüm seyri</th>
            </tr>
          </thead>
          <tbody>
            {claims.length === 0 && (
              <tr>
                <td colSpan={ordered.length + 3} className="px-2.5 py-3 text-chalk-600">
                  Henüz bulgu yok. Bir lezyon ölçüp bulguya dönüştürdüğünüzde satırı burada belirir.
                </td>
              </tr>
            )}
            {claims.map((claim) => {
              const verdict = claim.comparison?.status ?? "";
              return (
                <tr
                  key={claim.id}
                  onClick={() => onSelect(claim.id)}
                  className={`cursor-pointer border-t border-[var(--hairline)] ${
                    claim.id === activeClaimId ? "bg-ink-800" : "hover:bg-ink-850"
                  }`}
                >
                  <td className="max-w-[260px] truncate px-2.5 py-1 text-chalk-100" title={claim.text}>
                    <span className="readout mr-1.5 text-chalk-600">{claim.id}</span>
                    {claim.text || "—"}
                  </td>
                  {ordered.map((study) => {
                    const point = claim.timeline?.find((t) => t.study_uid === study.uid);
                    const reference = point?.refs?.[0];
                    return (
                      <td key={study.uid} className="px-2 py-1">
                        <button
                          type="button"
                          className={`readout ${reference ? "chip-address cursor-pointer" : "cursor-default"} ${
                            point?.status === "present" ? "text-amber-400" : "text-chalk-600"
                          }`}
                          title={point?.text || "bu tetkikte kayıt yok"}
                          onClick={(event) => {
                            event.stopPropagation();
                            if (reference) onJump(reference);
                          }}
                        >
                          {point ? (TIMEPOINT_MARK[point.status] ?? "?") : "·"}
                        </button>
                      </td>
                    );
                  })}
                  <td className={`px-2 py-1 ${VERDICT_TONE[verdict] ?? "text-chalk-600"}`}>
                    {verdict ? (VERDICT_TR[verdict] ?? verdict) : "—"}
                  </td>
                  <td className="px-2 py-1">
                    <Trend claim={claim} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * Measured sizes over time, as bars.
 *
 * Only length measurements are plotted, and only ones that carry an evidence
 * hash. A trend drawn from mixed units, or from a number somebody typed, would
 * be worse than no trend.
 */
function Trend({ claim }: { claim: Claim }) {
  const values = (claim.measurements ?? []).filter((m) => m.unit === "mm" && m.sha256).map((m) => m.value);
  if (values.length < 2) {
    return (
      <span className="readout text-chalk-600">
        {values.length === 1 ? `${values[0]?.toFixed(1)} mm` : "—"}
      </span>
    );
  }
  const max = Math.max(...values);
  const first = values[0] ?? 0;
  const last = values[values.length - 1] ?? 0;
  const rising = last > first;
  return (
    <span className="flex items-end gap-2">
      <span className="flex h-4 items-end gap-[2px]">
        {values.map((value, i) => (
          <span
            key={i}
            className={`w-1.5 rounded-[1px] ${rising ? "bg-alarm-400" : "bg-attest-400"}`}
            style={{ height: `${Math.max(2, (value / max) * 16)}px`, opacity: 0.55 + (0.45 * (i + 1)) / values.length }}
            title={`${value.toFixed(1)} mm`}
          />
        ))}
      </span>
      <span className="readout text-chalk-300">
        {first.toFixed(1)} → {last.toFixed(1)} mm
      </span>
    </span>
  );
}
