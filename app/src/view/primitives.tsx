/** Small shared pieces. Kept together so the visual language stays consistent. */
import type { ReactNode } from "react";
import type { Ref, RegionStatus } from "../api/types";

export const STATUS_LABEL_TR: Record<RegionStatus, string> = {
  pending: "bakılmadı",
  finding: "bulgu",
  no_finding: "temiz",
  limited: "sınırlı",
  not_covered: "kapsam dışı",
};

export const STATUS_ORDER: RegionStatus[] = ["finding", "limited", "not_covered", "no_finding", "pending"];

export function StatusTick({ status, tall = false }: { status: RegionStatus; tall?: boolean }) {
  return <span className={`tick tick-${status}`} style={{ height: tall ? 18 : 12 }} aria-hidden />;
}

/**
 * A citation, rendered as the address it is.
 *
 * `S8 · #219 · r248 c301` is the whole point of the ledger made clickable: the
 * series, the instance, and the native pixel. Clicking flies the viewer there,
 * so a reader never has to take a claim's word for it.
 */
export function AddressChip({
  reference,
  seriesNumber,
  onJump,
  title,
}: {
  reference: Ref;
  seriesNumber?: string;
  onJump?: (reference: Ref) => void;
  title?: string;
}) {
  const parts = [
    seriesNumber ? `S${seriesNumber}` : null,
    reference.row !== undefined && reference.col !== undefined ? `r${reference.row} c${reference.col}` : null,
  ].filter(Boolean);
  return (
    <button
      type="button"
      className="chip chip-address"
      title={title ?? `${reference.sop_uid}`}
      onClick={() => onJump?.(reference)}
      disabled={!onJump}
    >
      <span className="text-amber-400">◎</span>
      {parts.length ? parts.join(" · ") : reference.sop_uid.slice(-12)}
    </button>
  );
}

/** A number that came from the engine, shown with the evidence that backs it. */
export function MeasurementChip({
  value,
  unit,
  sha256,
  method,
}: {
  value: number;
  unit: string;
  sha256?: string;
  method?: string;
}) {
  return (
    <span className="chip" title={[method, sha256 ? `sha256 ${sha256}` : null].filter(Boolean).join("\n")}>
      <span className="font-semibold text-chalk-100">
        {Number.isInteger(value) ? value : value.toFixed(1)}
      </span>
      <span className="text-chalk-500">{unit}</span>
      {sha256 && <span className="text-attest-400" aria-label="kanıt dosyası mühürlü">✓</span>}
    </span>
  );
}

export function Section({
  title,
  right,
  children,
  className = "",
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`flex min-h-0 flex-col ${className}`}>
      <header className="flex h-7 shrink-0 items-center justify-between border-b border-[var(--hairline)] px-2.5">
        <h2 className="rail-label">{title}</h2>
        {right}
      </header>
      <div className="min-h-0 flex-1">{children}</div>
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="grid h-full place-items-center p-6 text-center text-xs text-chalk-600">{children}</div>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-chalk-500">
      <span className="pulse-ring inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />
      {label}
    </div>
  );
}

/** Coverage as a bar of ticks: eighteen regions readable without reading. */
export function CoverageBar({ statuses }: { statuses: RegionStatus[] }) {
  return (
    <div className="flex h-3 items-end gap-[2px]" aria-hidden>
      {statuses.map((status, i) => (
        <span key={i} className={`tick tick-${status}`} style={{ height: status === "pending" ? 6 : 12 }} />
      ))}
    </div>
  );
}
