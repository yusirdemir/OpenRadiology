/** Viewer controls: window presets, plane, slab thickness and the measuring tools. */
import type { PlaneName, SeriesCard, SeriesMeta } from "../api/types";
import { WINDOW_PRESETS, type WindowSetting } from "../state/viewer";

const PLANE_LABEL: Record<PlaneName, string> = { ax: "Aksiyel", cor: "Koronal", sag: "Sagital" };

interface Props {
  meta: SeriesMeta | null;
  seriesList: SeriesCard[];
  seriesUid: string;
  plane: PlaneName;
  mipMm: number;
  window: WindowSetting;
  showGrid: boolean;
  onSeries: (uid: string) => void;
  onPlane: (plane: PlaneName) => void;
  onMip: (mm: number) => void;
  onWindow: (patch: Partial<WindowSetting>) => void;
  onGrid: (show: boolean) => void;
}

export function Toolbar(props: Props) {
  const { meta, seriesList, seriesUid, plane, mipMm, window: win, showGrid } = props;
  const modality = meta?.geometry.modality ?? "";
  const presets = modality === "CT" ? WINDOW_PRESETS : (meta?.windows ?? []).map((w) => ({
    name: w.name, center: w.center, width: w.width, invert: false,
  }));
  const tilted = meta?.geometry.tilted === true;

  return (
    <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-[var(--hairline)] bg-ink-900 px-2">
      <select
        className="field h-[26px] w-[220px] shrink-0 py-0"
        value={seriesUid}
        onChange={(e) => props.onSeries(e.target.value)}
      >
        {seriesList.map((series) => (
          <option key={series.series_uid} value={series.series_uid} disabled={!series.renderable}>
            S{series.number} · {series.modality} · {series.instances} kesit
            {series.description ? ` · ${series.description.slice(0, 20)}` : ""}
            {/* A scanner usually writes several reconstructions; the reformats
                among them are not regular volumes. Saying so in the picker
                beats making the reader open each one to find out. */}
            {series.geometry_note ? ` — ${series.geometry_note}` : ""}
          </option>
        ))}
      </select>

      <Divider />

      <div className="flex shrink-0 gap-0.5">
        {(["ax", "cor", "sag"] as PlaneName[]).map((p) => (
          <button
            key={p}
            type="button"
            className={`btn ${plane === p ? "btn-active" : ""}`}
            onClick={() => props.onPlane(p)}
            disabled={p !== "ax" && tilted}
            title={p !== "ax" && tilted ? "Gantry eğimli seride reformat güvenilir değil" : PLANE_LABEL[p]}
          >
            {PLANE_LABEL[p].slice(0, 3)}
          </button>
        ))}
      </div>

      <Divider />

      <div className="flex shrink-0 gap-0.5">
        {presets.slice(0, 5).map((preset) => (
          <button
            key={preset.name}
            type="button"
            className={`btn ${win.name === preset.name ? "btn-active" : ""}`}
            onClick={() => props.onWindow({ ...preset })}
          >
            {preset.name}
          </button>
        ))}
      </div>

      {plane === "ax" && (
        <>
          <Divider />
          <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-chalk-500">
            <span>MIP</span>
            <input
              type="range"
              min={0}
              max={30}
              step={5}
              value={mipMm}
              onChange={(e) => props.onMip(Number(e.target.value))}
              className="h-1 w-[70px] accent-[var(--color-amber-400)]"
            />
            <span className="readout w-9 text-chalk-300">{mipMm ? `${mipMm}mm` : "kapalı"}</span>
          </label>
        </>
      )}

      <div className="min-w-0 flex-1" />

      <button
        type="button"
        className={`btn shrink-0 ${showGrid ? "btn-active" : ""}`}
        title="Piksel ızgarası (yeterince yakınlaştırıldığında görünür)"
        onClick={() => props.onGrid(!showGrid)}
      >
        Izgara
      </button>
    </div>
  );
}

function Divider() {
  return <span className="h-4 w-px shrink-0 bg-[var(--hairline-strong)]" />;
}
