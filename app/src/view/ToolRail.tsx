/**
 * Vertical tool strip down the left edge of the viewer.
 *
 * The tools moved off the top bar because they were competing for width with
 * the series picker and the window presets and losing at every window size. A
 * vertical strip is also what a reader's hand expects from a workstation: the
 * tools stay put while the study above them changes.
 */
import type { ReactElement } from "react";
import type { ToolName } from "../state/viewer";

interface Props {
  tool: ToolName;
  onTool: (tool: ToolName) => void;
  pendingCount: number;
}

const STROKE = { fill: "none", stroke: "currentColor", strokeWidth: 1.3, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

const TOOLS: { id: ToolName; label: string; hint: string; needs: number; icon: ReactElement }[] = [
  {
    id: "browse",
    label: "Gez",
    hint: "Gezinme — sürükle kaydır, tekerlek kesit değiştir, shift+tekerlek yakınlaştır",
    needs: 0,
    icon: (
      <g {...STROKE}>
        <path d="M9 3v12M3 9h12" />
        <path d="M6.5 5.5 9 3l2.5 2.5M6.5 12.5 9 15l2.5-2.5M5.5 6.5 3 9l2.5 2.5M12.5 6.5 15 9l-2.5 2.5" />
      </g>
    ),
  },
  {
    id: "window",
    label: "Pencere",
    hint: "Pencere genişliği ve seviyesi — sürükle (sağ tuş her araçta çalışır)",
    needs: 0,
    icon: (
      <g {...STROKE}>
        <circle cx="9" cy="9" r="6" />
        <path d="M9 3a6 6 0 0 1 0 12z" fill="currentColor" stroke="none" />
      </g>
    ),
  },
  {
    id: "distance",
    label: "Mesafe",
    hint: "İki uç nokta seçin — mesafeyi motor ölçer ve kanıt dosyasını imzalar",
    needs: 2,
    icon: (
      <g {...STROKE}>
        <path d="M4 14 14 4" />
        <path d="M2.5 12.5 5.5 15.5M12.5 2.5 15.5 5.5" />
      </g>
    ),
  },
  {
    id: "roi",
    label: "ROI",
    hint: "Merkez, sonra kenar seçin — dairesel bölge istatistiği (HU / SUVbw)",
    needs: 2,
    icon: (
      <g {...STROKE}>
        <circle cx="9" cy="9" r="5.5" />
        <path d="M9 9h5.5" strokeDasharray="1.6 1.6" />
        <circle cx="9" cy="9" r="0.9" fill="currentColor" stroke="none" />
      </g>
    ),
  },
  {
    id: "seed",
    label: "Lezyon",
    hint: "Lezyon içine bir tohum nokta — 3B bölge büyütme ile uzun ve kısa eksen",
    needs: 1,
    icon: (
      <g {...STROKE}>
        <path d="M9 2v3.5M9 12.5V16M2 9h3.5M12.5 9H16" />
        <circle cx="9" cy="9" r="2.6" />
      </g>
    ),
  },
  {
    id: "extent",
    label: "Kutu",
    hint: "İki köşe seçin — kutu içindeki kraniyokaudal uzanım",
    needs: 2,
    icon: (
      <g {...STROKE}>
        <rect x="3.5" y="3.5" width="11" height="11" strokeDasharray="2.2 1.8" />
      </g>
    ),
  },
];

export function ToolRail({ tool, onTool, pendingCount }: Props) {
  const active = TOOLS.find((entry) => entry.id === tool);
  return (
    <div className="flex w-9 shrink-0 flex-col items-center gap-0.5 border-r border-[var(--hairline)] bg-ink-900 py-1.5">
      {TOOLS.map((entry) => (
        <button
          key={entry.id}
          type="button"
          title={entry.hint}
          aria-label={entry.label}
          aria-pressed={tool === entry.id}
          onClick={() => onTool(entry.id)}
          className={`grid h-7 w-7 place-items-center rounded-[2px] border transition-colors ${
            tool === entry.id
              ? "border-amber-400/55 bg-amber-400/15 text-amber-400"
              : "border-transparent text-chalk-500 hover:border-[var(--hairline-strong)] hover:bg-ink-800 hover:text-chalk-100"
          }`}
        >
          <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
            {entry.icon}
          </svg>
        </button>
      ))}

      {active && active.needs > 0 && (
        <div className="mt-1 flex flex-col items-center gap-1" title={`${active.label}: ${active.needs} nokta gerekir`}>
          {Array.from({ length: active.needs }, (_, i) => (
            <span
              key={i}
              className={`h-1.5 w-1.5 rounded-full ${i < pendingCount ? "bg-amber-400" : "bg-ink-600"}`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
