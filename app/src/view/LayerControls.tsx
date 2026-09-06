import { useViewer } from "../state/viewer";
export function LayerControls() {
  const layers = useViewer((s) => s.layers);
  const set = useViewer((s) => s.setLayers);
  return <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Lezyon katmanları">
    {([['contour', 'Sınır'], ['heat', 'Isı'], ['caliper', 'Cetvel']] as const).map(([key, label]) =>
      <button key={key} type="button" className={`btn px-2 py-1 text-[11px] ${layers[key] ? 'btn-on' : ''}`}
        aria-pressed={layers[key]} onClick={() => set({ [key]: !layers[key] })}>{label}</button>)}
    <input aria-label="Isı opaklığı" title="Isı opaklığı" type="range" min="0.05" max="0.85" step="0.05"
      value={layers.opacity} onChange={(e) => set({ opacity: Number(e.target.value) })} className="w-16 accent-amber-400" />
  </div>;
}
