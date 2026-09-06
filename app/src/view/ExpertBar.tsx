/**
 * Everything a radiologist wants and a family member does not.
 *
 * Kept behind one switch, and behind it kept dense: plane, window, slab,
 * reconstruction, the pixel grid, and the state of the lockstep. None of it is
 * needed to understand the report, all of it is needed to check it, and mixing
 * the two in one screen is what made the previous interface unreadable.
 */
import type { PlaneName, SeriesCard } from "../api/types";
import { CT_WINDOWS, WINDOW_LABELS, useViewer, type WindowSetting } from "../state/viewer";
import { LayerControls } from "./LayerControls";

const PLANES: [PlaneName, string][] = [
  ["ax", "Aksiyel"],
  ["cor", "Koronal"],
  ["sag", "Sagital"],
];

export function ExpertBar({ seriesByStudy }: { seriesByStudy: Record<string, SeriesCard[]> }) {
  const panes = useViewer((s) => s.panes);
  const activePane = useViewer((s) => s.activePane);
  const linked = useViewer((s) => s.linked);
  const showGrid = useViewer((s) => s.showGrid);
  const setPlane = useViewer((s) => s.setPlane);
  const setWindow = useViewer((s) => s.setWindow);
  const setMip = useViewer((s) => s.setMip);
  const setLinked = useViewer((s) => s.setLinked);
  const setShowGrid = useViewer((s) => s.setShowGrid);
  const setView = useViewer((s) => s.setView);
  const resetView = useViewer((s) => s.resetView);
  const openSeries = useViewer((s) => s.openSeries);

  const pane = panes[activePane] ?? panes[0];
  if (!pane) return null;

  const modality = pane.meta?.geometry.modality ?? "";
  const presets: WindowSetting[] =
    modality === "CT"
      ? CT_WINDOWS
      : (pane.meta?.windows ?? []).map((w) => ({ ...w, invert: false }));
  const tilted = pane.meta?.geometry.tilted === true;

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--hairline)] bg-ink-900 px-3.5 py-2">
      <Group label="Düzlem">
        {PLANES.map(([plane, label]) => (
          <button
            key={plane}
            type="button"
            className={`btn px-2.5 py-1 text-[11.5px] ${pane.plane === plane ? "btn-on" : ""}`}
            disabled={plane !== "ax" && tilted}
            title={plane !== "ax" && tilted ? "Gantry eğimli seride reformat güvenilir değil" : label}
            onClick={() => setPlane(plane)}
          >
            {label}
          </button>
        ))}
      </Group>

      <Group label="Pencere">
        {presets.slice(0, 5).map((preset) => (
          <button
            key={preset.name}
            type="button"
            className={`btn px-2.5 py-1 text-[11.5px] ${pane.window.name === preset.name ? "btn-on" : ""}`}
            title={`merkez ${preset.center} · genişlik ${preset.width}`}
            onClick={() => setWindow(activePane, { ...preset })}
          >
            {WINDOW_LABELS[preset.name] ?? preset.name}
          </button>
        ))}
        <span className="readout ml-1 text-[10.5px] text-chalk-700">
          W{Math.round(pane.window.width)} / L{Math.round(pane.window.center)}
        </span>
      </Group>

      {pane.plane === "ax" && (
        <Group label="Kalın kesit">
          <input
            type="range"
            min={0}
            max={30}
            step={5}
            value={pane.mipMm}
            aria-label="MIP kalınlığı"
            onChange={(event) => setMip(Number(event.target.value))}
            className="h-1 w-[72px] cursor-pointer accent-[var(--color-amber-400)]"
          />
          <span className="readout w-12 text-[10.5px] text-chalk-600">
            {pane.mipMm ? `${pane.mipMm} mm` : "kapalı"}
          </span>
        </Group>
      )}

      <Group label="Görünüm">
        <button
          type="button"
          className={`btn px-2.5 py-1 text-[11.5px] ${linked ? "btn-on" : ""}`}
          title="İki tetkiği oransal olarak birlikte kaydır. Mutlak z değil, bağıl ilerleme kullanılır."
          disabled={panes.length < 2}
          onClick={() => setLinked(!linked)}
        >
          {linked ? "Birlikte kaydır" : "Ayrı kaydır"}
        </button>
        <button
          type="button"
          className="btn px-2 py-1 text-[11.5px]"
          aria-label="Uzaklaştır"
          onClick={() => setView(activePane, { ...pane.view, zoom: Math.max(0.25, pane.view.zoom / 1.25) })}
        >
          −
        </button>
        <span className="readout w-11 text-center text-[10.5px] text-chalk-600">
          {`${Math.round(pane.view.zoom * 100)}%`}
        </span>
        <button
          type="button"
          className="btn px-2 py-1 text-[11.5px]"
          aria-label="Yakınlaştır"
          title="Küçültülmüş bir görüntü tasdik etmez: yakınlaştırdıkça tasdik halkası dolar."
          onClick={() => setView(activePane, { ...pane.view, zoom: Math.min(40, pane.view.zoom * 1.25) })}
        >
          +
        </button>
        <button type="button" className="btn px-2.5 py-1 text-[11.5px]" onClick={resetView}>
          Sığdır
        </button>
        <button
          type="button"
          className={`btn px-2.5 py-1 text-[11.5px] ${showGrid ? "btn-on" : ""}`}
          title="Piksel ızgarası — yeterince yakınlaştırıldığında görünür"
          onClick={() => setShowGrid(!showGrid)}
        >
          Izgara
        </button>
      </Group>

      <Group label="Lezyon katmanı">
        <LayerControls />
      </Group>

      <div className="min-w-0 flex-1" />

      <Group label="Rekonstrüksiyon">
        {panes.map((entry, i) => (
          <select
            key={i}
            className="field h-[26px] w-[190px] py-0 text-[11.5px]"
            value={entry.seriesUid}
            aria-label={panes.length > 1 ? (i === 0 ? "Önceki tetkikin serisi" : "Son tetkikin serisi") : "Seri"}
            title={panes.length > 1 ? (i === 0 ? "Önceki tetkik" : "Son tetkik") : "Seri"}
            onChange={(event) => void openSeries(i, entry.studyPath, event.target.value)}
          >
            {(seriesByStudy[entry.studyPath] ?? []).map((series) => (
              <option key={series.series_uid} value={series.series_uid} disabled={!series.renderable}>
                S{series.number} · {series.instances} kesit
                {series.geometry_note ? ` — ${series.geometry_note}` : ""}
              </option>
            ))}
          </select>
        ))}
      </Group>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="eyebrow mr-0.5">{label}</span>
      {children}
    </div>
  );
}
