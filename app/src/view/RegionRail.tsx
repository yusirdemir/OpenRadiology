/**
 * The anatomical sweep.
 *
 * This is `session.studies[].regions` shown as it is: eighteen entries for a
 * CT, eleven for a brain MR, ten for a PET/CT, each of which must end up as a
 * finding, a clean read, a stated limitation, or an admission that it was not
 * covered. `openrad check` refuses to finish while any of them is still
 * pending, so this rail is the actual remaining work, not a summary of it.
 */
import { useMemo } from "react";
import type { LocaleBundle, Region, RegionStatus, SessionStudy } from "../api/types";
import { CoverageBar, STATUS_LABEL_TR, StatusTick } from "./primitives";

interface Props {
  study: SessionStudy | undefined;
  locale: LocaleBundle | null;
  activeKey: string | null;
  onSelect: (key: string) => void;
}

function regionLabel(locale: LocaleBundle | null, key: string): string {
  const bare = key.includes(":") ? key.slice(key.indexOf(":") + 1) : key;
  return locale?.regions?.[bare] ?? bare.replace(/_/g, " ");
}

export function RegionRail({ study, locale, activeKey, onSelect }: Props) {
  const entries = useMemo<[string, Region][]>(() => Object.entries(study?.regions ?? {}), [study]);
  const statuses = entries.map(([, region]) => region.status as RegionStatus);
  const done = statuses.filter((s) => s !== "pending").length;
  const findings = statuses.filter((s) => s === "finding").length;

  if (!study) {
    return <div className="grid h-full place-items-center text-xs text-chalk-600">Oturum açılmadı</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-[var(--hairline)] px-2.5 py-2">
        <div className="mb-1.5 flex items-baseline justify-between">
          <span className="readout text-[15px] font-semibold text-chalk-100">
            {done}
            <span className="text-chalk-600">/{entries.length}</span>
          </span>
          <span className="text-[11px] text-chalk-500">
            {findings > 0 ? <span className="text-amber-400">{findings} bulgu</span> : "bulgu yok"}
          </span>
        </div>
        <CoverageBar statuses={statuses} />
      </div>

      <div className="scroll-y min-h-0 flex-1 py-1">
        {entries.map(([key, region]) => (
          <button
            key={key}
            type="button"
            className="region-row w-full text-left"
            data-active={activeKey === key}
            onClick={() => onSelect(key)}
          >
            <StatusTick status={region.status as RegionStatus} tall />
            <span className="min-w-0">
              <span className="block truncate text-[12px] text-chalk-100">{regionLabel(locale, key)}</span>
              {region.text && (
                <span className="block truncate text-[11px] text-chalk-600">{region.text}</span>
              )}
            </span>
            <span className={`text-[10px] status-${region.status}`}>
              {region.status === "pending" ? "" : STATUS_LABEL_TR[region.status as RegionStatus]}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
