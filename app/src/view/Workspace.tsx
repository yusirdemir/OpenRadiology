/**
 * The reading workspace.
 *
 * Three columns, one job: turn looking into a ledger entry without letting
 * anything slip through unaddressed. The anatomical sweep on the left is the
 * remaining work, the viewer in the middle is where looking actually happens
 * (and is what earns attestations), and the evidence panel on the right is
 * where a finding, its address, its measured number and its plain-language
 * sentence become one object.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { Claim, LocaleBundle, MeasureResult, Ref, SessionPage, SessionStudy, SliceImage } from "../api/types";
import { AttestationTracker } from "../lib/attestation";
import { SliceSourcePool } from "../lib/sliceCache";
import { useConnection } from "../state/connection";
import { useSession } from "../state/session";
import { useViewer, type PendingPoint, type ToolName } from "../state/viewer";
import { AgentPanel } from "./AgentPanel";
import { CompareBar } from "./CompareBar";
import { EvidencePanel } from "./EvidencePanel";
import { Filmstrip } from "./Filmstrip";
import { PageViewer } from "./PageViewer";
import { RenderDialog } from "./RenderDialog";
import { RegionRail } from "./RegionRail";
import { SliceCanvas, type CanvasMarker } from "./SliceCanvas";
import { Toolbar } from "./Toolbar";
import { ToolRail } from "./ToolRail";

interface Props {
  locale: LocaleBundle | null;
  onBack: () => void;
  onReport: () => void;
}

export function Workspace({ locale, onBack, onReport }: Props) {
  const { session, path: sessionPath, setRegion, upsertClaim, removeClaim, save, dirty, saving, runCheck, check } =
    useSession();
  const policy = useConnection((s) => s.policy);
  const viewer = useViewer();
  const pool = useRef(new SliceSourcePool());

  const [activeRegionKey, setActiveRegionKey] = useState<string | null>(null);
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null);
  const [measurement, setMeasurement] = useState<MeasureResult | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const [measureError, setMeasureError] = useState<string | null>(null);
  const [lastPoint, setLastPoint] = useState<PendingPoint | null>(null);
  const [lastGeometry, setLastGeometry] = useState<{ tool: ToolName; points: PendingPoint[]; sopUid: string; label: string } | null>(null);
  const [images, setImages] = useState<Record<number, SliceImage | null>>({});
  const [agentOpen, setAgentOpen] = useState(false);
  const [renderOpen, setRenderOpen] = useState(false);
  const [openPage, setOpenPage] = useState<SessionPage | null>(null);

  const pane = viewer.panes[viewer.activePane];
  const image = pane ? images[viewer.activePane] ?? null : null;
  const study = useMemo(
    () => session?.studies.find((s) => s.path === pane?.studyPath) ?? session?.studies[0],
    [session, pane?.studyPath],
  );

  // -- attestation -------------------------------------------------------
  const tracker = useRef<AttestationTracker | null>(null);
  if (!tracker.current) {
    tracker.current = new AttestationTracker(
      async (events) => {
        const current = useSession.getState();
        if (!current.session?.work_dir) return;
        // The session path travels with the batch so the server can promote
        // any page whose source slices have now all been displayed.
        await api.attest(current.session.work_dir, events, current.path ?? undefined);
        if (current.path) await useSession.getState().open(current.path);
      },
      policy,
    );
  }
  useEffect(() => {
    tracker.current?.setPolicy(policy);
  }, [policy]);
  useEffect(() => {
    const instance = tracker.current;
    const flush = () => void instance?.flush();
    globalThis.addEventListener("blur", flush);
    return () => {
      globalThis.removeEventListener("blur", flush);
      void instance?.dispose();
    };
  }, []);

  // -- open the first series automatically -------------------------------
  /**
   * Open one pane per study, oldest on the left.
   *
   * The order is the session's, which came from DICOM StudyDate and StudyTime.
   * For each study the largest renderable series is chosen, because that is
   * almost always the thin-slice reconstruction the reading is done on.
   */
  useEffect(() => {
    if (!session || viewer.panes.length) return;
    const studies = [...session.studies].sort((a, b) => `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`));
    if (!studies.length) return;
    void (async () => {
      const chosen: { study: SessionStudy; seriesUid: string }[] = [];
      for (const study of studies) {
        const list = await viewer.loadSeriesList(study.path);
        const candidate = list.filter((s) => s.renderable).sort((a, b) => b.instances - a.instances)[0] ?? list[0];
        if (candidate) chosen.push({ study, seriesUid: candidate.series_uid });
      }
      if (!chosen.length) return;
      useViewer.setState({
        panes: chosen.map(({ study, seriesUid }) => ({
          studyPath: study.path,
          studyUid: study.uid,
          seriesUid,
          meta: null,
          plane: "ax" as const,
          index: 0,
          mipMm: 0,
          window: { name: "soft", center: 40, width: 400, invert: false },
          view: { zoom: 1, panX: 0, panY: 0 },
          image: null,
          loading: true,
          error: null,
        })),
        activePane: chosen.length - 1,
      });
      for (const [index, entry] of chosen.entries()) {
        await viewer.openSeries(index, entry.study.path, entry.seriesUid);
      }
      // The newest examination is the one being read; the priors support it.
      useViewer.setState({ activePane: chosen.length - 1 });
    })();
  }, [session, viewer]);

  /**
   * Keep the priors on the same anatomy, debounced.
   *
   * Scrolling emits an index per wheel notch; asking the engine to locate a
   * patient point for each one would queue far more work than the reader can
   * see. One trailing call per pause is enough to keep the panes together.
   */
  const activeIndex = pane?.index ?? 0;
  useEffect(() => {
    if (!viewer.linked || viewer.panes.length < 2) return;
    const timer = window.setTimeout(() => void viewer.syncFromActive(), 140);
    return () => window.clearTimeout(timer);
  }, [activeIndex, viewer.activePane, viewer.linked, viewer.panes.length, viewer]);

  // -- keyboard ----------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
      const index = viewer.activePane;
      const map: Record<string, () => void> = {
        ArrowDown: () => viewer.stepIndex(index, 1),
        ArrowUp: () => viewer.stepIndex(index, -1),
        PageDown: () => viewer.stepIndex(index, 10),
        PageUp: () => viewer.stepIndex(index, -10),
        Escape: () => viewer.clearPoints(),
        g: () => viewer.setShowGrid(!viewer.showGrid),
        "1": () => viewer.setWindow(index, { name: "lung", center: -600, width: 1500 }),
        "2": () => viewer.setWindow(index, { name: "soft", center: 40, width: 400 }),
        "3": () => viewer.setWindow(index, { name: "bone", center: 450, width: 1800 }),
      };
      const action = map[event.key];
      if (action) {
        event.preventDefault();
        action();
      }
    };
    globalThis.addEventListener("keydown", onKey);
    return () => globalThis.removeEventListener("keydown", onKey);
  }, [viewer]);

  useEffect(() => () => pool.current.disposeAll(), []);

  // -- addresses ---------------------------------------------------------
  const currentRef = useCallback((): Ref | null => {
    if (!image || !image.sopUid) return null;
    const point = lastPoint;
    return {
      study_uid: image.studyUid,
      series_uid: image.seriesUid,
      sop_uid: image.sopUid,
      ...(point ? { row: point.row, col: point.col } : {}),
    };
  }, [image, lastPoint]);

  const seriesNumberOf = useCallback(
    (seriesUid: string) => {
      for (const list of Object.values(viewer.seriesByStudy)) {
        const hit = list.find((s) => s.series_uid === seriesUid);
        if (hit) return hit.number;
      }
      return undefined;
    },
    [viewer.seriesByStudy],
  );

  const jump = useCallback(
    (reference: Ref) => {
      const target = session?.studies.find((s) => s.uid === reference.study_uid) ?? session?.studies[0];
      if (target) void viewer.jumpToRef(reference, target.path);
    },
    [session, viewer],
  );

  // -- measurement -------------------------------------------------------
  const runMeasurement = useCallback(
    async (points: PendingPoint[]) => {
      if (!pane || !image || !session) return;
      const [first, second] = points;
      if (!first) return;
      const stamp = new Date().toISOString().slice(11, 19).replace(/:/g, "");
      const seriesNumber = seriesNumberOf(pane.seriesUid) ?? pane.seriesUid.slice(-6);
      const payload: Record<string, unknown> = {
        study: pane.studyPath,
        series: pane.seriesUid,
        instance: image.instance,
        output: `${session.work_dir}/meas/${viewer.tool}_S${seriesNumber}_i${image.instance}_${stamp}.txt`,
      };
      if (viewer.tool === "distance" && second) {
        payload.points = [`${first.row},${first.col}`, `${second.row},${second.col}`];
      } else if (viewer.tool === "roi" && second) {
        const radius = Math.max(1, Math.round(Math.hypot(second.row - first.row, second.col - first.col)));
        payload.roi = `${first.row},${first.col},${radius}`;
      } else if (viewer.tool === "seed") {
        payload.auto3d = `${first.row},${first.col}`;
      } else if (viewer.tool === "extent" && second) {
        payload.extent = [
          Math.min(first.row, second.row), Math.max(first.row, second.row),
          Math.min(first.col, second.col), Math.max(first.col, second.col),
        ].join(",");
      } else {
        return;
      }

      setMeasuring(true);
      setMeasureError(null);
      try {
        const result = await api.measure(payload);
        setMeasurement(result);
        // Leave the geometry on screen: a number in the panel that no longer
        // has a shape on the image is a number the reader cannot check.
        setLastGeometry({ tool: viewer.tool, points, sopUid: image.sopUid, label: headlineOf(result) });
      } catch (error) {
        setMeasureError(error instanceof Error ? error.message : String(error));
      } finally {
        setMeasuring(false);
        viewer.clearPoints();
      }
    },
    [pane, image, session, viewer, seriesNumberOf],
  );

  const onPick = useCallback(
    (point: PendingPoint) => {
      setLastPoint(point);
      if (viewer.tool === "browse" || viewer.tool === "window") return;
      const points = [...viewer.pending, point];
      const needed = viewer.tool === "seed" ? 1 : 2;
      if (points.length >= needed) {
        viewer.clearPoints();
        void runMeasurement(points);
      } else {
        viewer.addPoint(point);
      }
    },
    [viewer, runMeasurement],
  );

  // -- claims ------------------------------------------------------------
  const claimFromMeasurement = useCallback(() => {
    const reference = currentRef();
    if (!measurement || !reference) return;
    const id = `C${(session?.claims.length ?? 0) + 1}`;
    const measurements = toMeasurements(measurement, reference);
    const claim: Claim = {
      id,
      text: "",
      confidence: "moderate",
      priority: "routine",
      refs: [reference],
      pages: [],
      patient: { meaning: "", importance: "", uncertainty: "", discuss_with_doctor: "" },
      ...(measurements.length ? { measurements } : {}),
    };
    upsertClaim(claim);
    setActiveClaimId(id);
  }, [measurement, currentRef, session, upsertClaim]);

  const attachMeasurement = useCallback(
    (claimId: string) => {
      const reference = currentRef();
      const claim = session?.claims.find((c) => c.id === claimId);
      if (!measurement || !reference || !claim) return;
      upsertClaim({
        ...claim,
        measurements: [...(claim.measurements ?? []), ...toMeasurements(measurement, reference)],
      });
    },
    [measurement, currentRef, session, upsertClaim],
  );

  // -- markers -----------------------------------------------------------
  const markers = useMemo<CanvasMarker[]>(() => {
    if (!image) return [];
    const out: CanvasMarker[] = [];
    for (const claim of session?.claims ?? []) {
      for (const reference of claim.refs) {
        if (reference.sop_uid === image.sopUid && reference.row !== undefined && reference.col !== undefined) {
          out.push({
            kind: "point",
            points: [{ row: reference.row, col: reference.col }],
            label: claim.id,
            tone: claim.id === activeClaimId ? "amber" : "attest",
          });
        }
      }
    }
    if (lastGeometry && lastGeometry.sopUid === image.sopUid) {
      const { tool, points, label } = lastGeometry;
      const kind = tool === "distance" ? "line" : tool === "roi" ? "circle" : tool === "extent" ? "box" : "point";
      const radiusPx =
        tool === "roi" && points[0] && points[1]
          ? Math.round(Math.hypot(points[1].row - points[0].row, points[1].col - points[0].col))
          : undefined;
      out.push({ kind, points: tool === "roi" ? points.slice(0, 1) : points, radiusPx, label, tone: "amber" });
    }
    const region = activeRegionKey ? study?.regions[activeRegionKey] : undefined;
    for (const reference of region?.refs ?? []) {
      if (reference.sop_uid === image.sopUid && reference.row !== undefined && reference.col !== undefined) {
        out.push({ kind: "point", points: [{ row: reference.row, col: reference.col }], tone: "probe" });
      }
    }
    return out;
  }, [session?.claims, activeClaimId, activeRegionKey, study, image, lastGeometry]);

  if (!session || !sessionPath) {
    return <div className="grid h-full place-items-center text-xs text-chalk-600">Oturum yükleniyor…</div>;
  }

  const blocking = check?.blocking.length ?? 0;

  return (
    <div className="relative grid h-full grid-cols-[248px_1fr_320px] grid-rows-[36px_1fr_28px] overflow-hidden">
      {/* ---- top bar ---- */}
      <header className="chrome-grain col-span-3 flex items-center gap-2 border-b border-[var(--hairline)] bg-ink-900 px-2.5">
        <button type="button" className="btn" onClick={onBack}>
          ← Raf
        </button>
        <span className="readout truncate text-[11px] text-chalk-300">
          {session.studies.map((s) => s.folder).join("  →  ")}
        </span>
        <span className="chip">{session.mode === "comparison" ? "karşılaştırma" : "tek çekim"}</span>
        <div className="flex-1" />
        {dirty && <span className="text-[11px] text-caution-400">kaydedilmemiş değişiklik</span>}
        <button type="button" className="btn" disabled={saving || !dirty} onClick={() => void save()}>
          {saving ? "kaydediliyor…" : "Kaydet"}
        </button>
        <button type="button" className="btn" onClick={() => void runCheck()}>
          Denetle{blocking ? ` (${blocking})` : ""}
        </button>
        <button
          type="button"
          className="btn"
          title="Bu seriden sistematik kontakt sayfaları üret"
          onClick={() => setRenderOpen(true)}
          disabled={!pane?.meta}
        >
          Sayfa üret
        </button>
        <button
          type="button"
          className={`btn ${agentOpen ? "btn-active" : ""}`}
          title="MCP köprüsüne bağlı ajanın adımları"
          onClick={() => setAgentOpen((open) => !open)}
        >
          Ajan
        </button>
        <button type="button" className="btn btn-primary" onClick={onReport}>
          Rapor
        </button>
      </header>

      {/* ---- left rail ---- */}
      <aside className="chrome-grain min-h-0 border-r border-[var(--hairline)] bg-ink-900">
        <RegionRail study={study} locale={locale} activeKey={activeRegionKey} onSelect={setActiveRegionKey} />
      </aside>

      {/* ---- centre ---- */}
      <main className="flex min-h-0 min-w-0 flex-col">
        {pane && (
          <Toolbar
            meta={pane.meta}
            seriesList={viewer.seriesByStudy[pane.studyPath] ?? []}
            seriesUid={pane.seriesUid}
            plane={pane.plane}
            mipMm={pane.mipMm}
            window={pane.window}
            showGrid={viewer.showGrid}
            onSeries={(uid) => void viewer.openSeries(viewer.activePane, pane.studyPath, uid)}
            onPlane={(plane) => viewer.setPlane(viewer.activePane, plane)}
            onMip={(mm) => viewer.setMip(viewer.activePane, mm)}
            onWindow={(patch) => viewer.setWindow(viewer.activePane, patch)}
            onGrid={viewer.setShowGrid}
          />
        )}

        <div className="flex min-h-0 flex-1">
          <ToolRail tool={viewer.tool} onTool={viewer.setTool} pendingCount={viewer.pending.length} />
          <div
            className="grid min-h-0 flex-1 gap-px bg-[var(--hairline)]"
            style={{ gridTemplateColumns: `repeat(${Math.max(1, viewer.panes.length)}, minmax(0, 1fr))` }}
          >
          {viewer.panes.map((entry, i) => (
            <div key={`${entry.seriesUid}-${i}`} className="relative min-h-0 bg-ink-950">
              {entry.meta ? (
                <SliceCanvas
                  source={pool.current.for({
                    study: entry.studyPath,
                    series: entry.seriesUid,
                    plane: entry.plane,
                    mipMm: entry.mipMm,
                    allowTilt: false,
                  })}
                  index={entry.index}
                  window={entry.window}
                  view={entry.view}
                  tool={viewer.tool}
                  pending={i === viewer.activePane ? viewer.pending : []}
                  markers={i === viewer.activePane ? markers : []}
                  iop={entry.meta.geometry.iop}
                  showGrid={viewer.showGrid}
                  attest={i === viewer.activePane ? tracker.current ?? undefined : undefined}
                  studyUid={entry.studyUid}
                  active={i === viewer.activePane}
                  onView={(view) => viewer.setView(i, view)}
                  onIndex={(index) => viewer.setIndex(i, index)}
                  onWindow={(patch) => viewer.setWindow(i, patch)}
                  onPick={onPick}
                  onImage={(img) => setImages((prev) => (prev[i] === img ? prev : { ...prev, [i]: img }))}
                  onActivate={() => viewer.setActivePane(i)}
                />
              ) : (
                <div className="grid h-full place-items-center text-xs text-chalk-600">
                  {entry.error ?? "seri çözülüyor…"}
                </div>
              )}
            </div>
          ))}
          </div>
        </div>

        {viewer.panes.length > 1 && (
          <div className="flex h-7 shrink-0 items-center gap-2 border-t border-[var(--hairline)] bg-ink-900 px-2.5">
            <button
              type="button"
              className={`btn ${viewer.linked ? "btn-active" : ""}`}
              title="Panelleri hasta koordinatına kilitle (kesit numarasına değil)"
              onClick={() => viewer.setLinked(!viewer.linked)}
            >
              {viewer.linked ? "Kilitli" : "Bağımsız"}
            </button>
            <span className="text-[10px] text-chalk-600">
              {viewer.linked
                ? "Bir paneli kaydırdığınızda diğerleri aynı anatomik seviyeye gelir."
                : "Paneller ayrı ayrı geziliyor."}
            </span>
            <div className="flex-1" />
            {viewer.panes.map((entry, i) => {
              const study = session.studies.find((st) => st.path === entry.studyPath);
              return (
                <button
                  key={i}
                  type="button"
                  className={`chip ${i === viewer.activePane ? "border-amber-400/60 text-amber-400" : ""}`}
                  onClick={() => viewer.setActivePane(i)}
                >
                  {study ? `${study.date.slice(6, 8)}.${study.date.slice(4, 6)}.${study.date.slice(0, 4)}` : `#${i + 1}`}
                </button>
              );
            })}
          </div>
        )}

        {pane?.meta && (
          <div className="shrink-0 border-t border-[var(--hairline)]">
            <Filmstrip
              study={pane.studyPath}
              series={pane.seriesUid}
              plane={pane.plane}
              index={pane.index}
              count={pane.meta.planes[pane.plane].count}
              window={{ center: pane.window.center, width: pane.window.width }}
              attestedIndices={tracker.current?.attestedIndices(pane.seriesUid, pane.plane)}
              onSeek={(index) => viewer.setIndex(viewer.activePane, index)}
            />
          </div>
        )}

        {session.mode === "comparison" && (
          <CompareBar
            studies={session.studies}
            claims={session.claims}
            activeClaimId={activeClaimId}
            onSelect={setActiveClaimId}
            onJump={jump}
          />
        )}
      </main>

      {/* ---- right rail ---- */}
      <aside className="chrome-grain min-h-0 border-l border-[var(--hairline)] bg-ink-900">
        <EvidencePanel
          study={study}
          locale={locale}
          activeRegionKey={activeRegionKey}
          claims={session.claims}
          activeClaimId={activeClaimId}
          lastMeasurement={measurement}
          measuring={measuring}
          measureError={measureError}
          canAddress={currentRef() !== null}
          seriesNumberOf={seriesNumberOf}
          onRegionPatch={(key, patch) => study && setRegion({ studyUid: study.uid, key }, patch)}
          onAddRegionRef={(key) => {
            const reference = currentRef();
            const region = study?.regions[key];
            if (reference && region && study) {
              setRegion({ studyUid: study.uid, key }, { refs: [...region.refs, reference] });
            }
          }}
          onDropRegionRef={(key, index) => {
            const region = study?.regions[key];
            if (region && study) {
              setRegion({ studyUid: study.uid, key }, { refs: region.refs.filter((_, i) => i !== index) });
            }
          }}
          onJump={jump}
          onSelectClaim={setActiveClaimId}
          onClaimPatch={upsertClaim}
          onClaimRemove={removeClaim}
          onNewClaimFromMeasurement={claimFromMeasurement}
          onAttachMeasurement={attachMeasurement}
          pages={session.pages}
          attestation={useSession.getState().coverage}
          onOpenPage={setOpenPage}
          onRender={() => setRenderOpen(true)}
        />
      </aside>

      {/* ---- status bar ---- */}
      <StatusBar planeCount={pane?.meta?.planes[pane.plane].count ?? 0} />

      {/* The agent panel and its display gate overlay the whole workspace: a
          page the agent asked for has to be looked at, not glanced past. */}
      <AgentPanel open={agentOpen} onClose={() => setAgentOpen(false)} />

      {renderOpen && pane && (
        <RenderDialog
          studyPath={pane.studyPath}
          seriesUid={pane.seriesUid}
          seriesNumber={seriesNumberOf(pane.seriesUid) ?? pane.seriesUid.slice(-6)}
          meta={pane.meta}
          onClose={() => setRenderOpen(false)}
        />
      )}

      {openPage && (
        <PageViewer
          pagePath={openPage.path}
          purpose={openPage.purpose}
          workDir={session.work_dir}
          sessionPath={sessionPath}
          onAttested={() => {
            setOpenPage(null);
            // The server decides what the attestation is worth; re-opening the
            // session is how the interface learns whether it counted.
            if (sessionPath) void useSession.getState().open(sessionPath);
          }}
          onDismiss={() => setOpenPage(null)}
        />
      )}
    </div>
  );
}

function StatusBar({ planeCount }: { planeCount: number }) {
  const coverage = useSession((s) => s.coverage);
  const refused = useSession((s) => s.refusedReviews);
  const policy = useConnection((s) => s.policy);
  return (
    <footer className="chrome-grain col-span-3 flex items-center gap-3 border-t border-[var(--hairline)] bg-ink-900 px-2.5 text-[10px] text-chalk-600">
      <span className="readout">
        {coverage ? `${coverage.slices_attested}/${coverage.slices_total} kesit görüntülendi` : "tasdik defteri okunuyor…"}
      </span>
      <span className="readout">
        {coverage ? `${coverage.pages_attested}/${coverage.pages_total} sayfa tasdikli` : ""}
      </span>
      <span className="h-3 w-px bg-[var(--hairline-strong)]" />
      <span>
        tasdik eşiği {policy.minDwellMs} ms · ≥{policy.minScale} px/px · odaklı pencere
      </span>
      {refused.length > 0 && (
        <span className="text-alarm-400">{refused.length} “okundu” işareti tasdiksiz olduğu için geri alındı</span>
      )}
      <div className="flex-1" />
      <span className="readout">{planeCount} kesit</span>
    </footer>
  );
}

/** The one number worth printing next to the shape that produced it. */
function headlineOf(result: MeasureResult): string {
  const unit = result.data.unit === "SUVbw" ? "SUV" : "HU";
  for (const entry of result.data.results) {
    if (entry.kind === "distance" && typeof entry.value_mm === "number") return `${entry.value_mm.toFixed(1)} mm`;
    if (typeof entry.reported_mm === "number") return `${entry.reported_mm.toFixed(1)} mm`;
    if (entry.kind === "roi" && typeof entry.mean === "number") return `${entry.mean.toFixed(0)} ${unit}`;
    if (entry.kind === "extent" && typeof entry.craniocaudal_extent_mm === "number") {
      return `${entry.craniocaudal_extent_mm.toFixed(1)} mm`;
    }
  }
  return "";
}

/** Turn an engine measurement report into ledger measurements, hash included. */
function toMeasurements(result: MeasureResult, reference: Ref) {
  if (!result.evidence) return [];
  const unit = result.data.unit === "SUVbw" ? "SUVbw" : "HU";
  const out: NonNullable<Claim["measurements"]> = [];
  const add = (value: number, valueUnit: string, method: string) =>
    out.push({
      value,
      unit: valueUnit,
      method,
      evidence_file: result.evidence!.path,
      sha256: result.evidence!.sha256,
      ref: reference,
    });
  for (const entry of result.data.results) {
    if (entry.kind === "distance" && typeof entry.value_mm === "number") {
      add(entry.value_mm, "mm", String(entry.method ?? "mesafe"));
    } else if (entry.kind === "roi" && typeof entry.mean === "number") {
      add(entry.mean, unit, `dairesel ROI ortalaması (n=${entry.n})`);
    } else if (typeof entry.reported_mm === "number") {
      add(entry.reported_mm, "mm", String(entry.reported_rule ?? "raporlanan çap"));
    } else if (entry.kind === "extent" && typeof entry.craniocaudal_extent_mm === "number") {
      add(entry.craniocaudal_extent_mm, "mm", "kraniyokaudal uzanım");
    }
  }
  return out;
}
