/**
 * The reading screen.
 *
 * Two shapes, one screen. A single examination gets one column of images; a
 * comparison gets exactly two, older on the left, current on the right, each
 * labelled with its date in words. There is no third shape, because there is no
 * third question: either you are looking at a scan, or you are looking at what
 * changed between two of them.
 *
 * Everything else follows from that. Selecting a finding in the rail sends
 * every column to the slice the ledger cites for *that* column -- which is
 * usually a different slice number in a different reconstruction, because the
 * two examinations do not share a coordinate system. From then on the wheel
 * moves both together, proportionally, anchored where the jump left them.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, followAgent } from "../api/client";
import type { DisplayRequest, LocaleBundle, Ref, SeriesCard, SessionStudy } from "../api/types";
import { AttestationTracker } from "../lib/attestation";
import { buildFindings, longDate, monthsBetween, orderedStudies } from "../lib/findings";
import { SliceSourcePool } from "../lib/sliceCache";
import { useConnection } from "../state/connection";
import { useSession } from "../state/session";
import { CT_WINDOWS, useViewer, type WindowSetting } from "../state/viewer";
import { ExpertBar } from "./ExpertBar";
import { Boxes, Eye, EyeOff, FileText, SlidersHorizontal } from "./icons";
import { FindingRail } from "./FindingRail";
import { PageViewer } from "./PageViewer";
import { ReportPanel } from "./ReportPanel";
import { LesionView, type VolumeOption } from "./LesionView";
import { StudyPane } from "./StudyPane";

interface Props {
  locale: LocaleBundle | null;
  onHome: () => void;
}

const MODALITY_LABEL: Record<string, string> = {
  CT: "Bilgisayarlı tomografi",
  MR: "Manyetik rezonans",
  PT: "PET/BT",
  PET: "PET/BT",
};

function modalityLabel(studies: SessionStudy[]): string {
  const set = new Set(studies.flatMap((s) => s.modalities));
  if (set.has("PT")) return MODALITY_LABEL.PT as string;
  const first = [...set][0] ?? "";
  return MODALITY_LABEL[first] ?? first ?? "İnceleme";
}

/**
 * Which window a finding should be read in, taken from the ledger.
 *
 * The engine already decided what each reconstruction is for and wrote it into
 * the series as a required pass -- `lung:native` for the thin lung series,
 * `soft:native` for the diagnostic soft-tissue one. Reading the window off that
 * is honest and works for any protocol; guessing it from the claim's wording,
 * as an earlier build did, worked only for the six findings it was written
 * against.
 */
function windowForSeries(series: { required_passes?: string[] } | undefined, modality: string): WindowSetting | undefined {
  if (modality !== "CT") return undefined;
  const family = (series?.required_passes ?? [])[0]?.split(":")[0] ?? "";
  const byFamily: Record<string, string> = {
    lung: "lung",
    bone: "bone",
    soft: "soft",
    mediastinum: "soft",
    abdomen: "liver",
    liver: "liver",
    brain: "brain",
  };
  const wanted = byFamily[family] ?? "soft";
  const preset = CT_WINDOWS.find((w) => w.name === wanted);
  return preset ? { ...preset } : undefined;
}

/**
 * The series a study should open on.
 *
 * A scanner writes half a dozen reconstructions of one acquisition, and several
 * of them cannot be displayed as a volume at all: secondary workstation
 * reformats with duplicated or unevenly spaced slices, a one-image dose report.
 * Opening one of those greets the reader with a geometry error instead of their
 * scan, which is what a first draft of this screen did on any review the agent
 * had not already dispositioned.
 *
 * So the choice is made against the header probe rather than guessed from the
 * ledger: renderable, a regular volume, axial, and a real stack. Among what
 * survives, the *coarsest* one wins -- the 5 mm diagnostic series opens in a
 * fraction of the time the 1 mm volume takes, and selecting a finding moves the
 * pane to whichever reconstruction that finding is actually addressed in.
 */
function primarySeries(study: SessionStudy, cards: SeriesCard[]): string | undefined {
  const byUid = new Map(cards.map((card) => [card.series_uid, card]));
  const usable = (uid: string): SeriesCard | null => {
    const card = byUid.get(uid);
    if (!card || !card.renderable || card.regular_volume === false) return null;
    if (card.instances < 10) return null;
    return card;
  };
  const coarsest = (uids: string[]): string | undefined =>
    uids
      .map(usable)
      .filter((card): card is SeriesCard => card !== null)
      .sort((a, b) => a.instances - b.instances || a.number.localeCompare(b.number, undefined, { numeric: true }))
      .map((card) => card.series_uid)[0];

  const read = study.series.filter((s) => s.disposition === "read").map((s) => s.uid);
  const every = study.series.map((s) => s.uid);
  // A fresh session has no dispositions yet, so the header probe is the only
  // thing that knows which of these can be shown at all.
  return (
    coarsest(read) ??
    coarsest(every) ??
    [...study.series]
      .sort((a, b) => Object.keys(b.sops ?? {}).length - Object.keys(a.sops ?? {}).length)[0]?.uid
  );
}

export function Review({ locale, onHome }: Props) {
  const session = useSession((s) => s.session);
  const sessionPath = useSession((s) => s.path);
  const coverage = useSession((s) => s.coverage);
  const policy = useConnection((s) => s.policy);

  // A plain `panes` subscription would re-render this screen on every wheel
  // notch. The pane roster only changes when a study is opened, so the study
  // identities are subscribed to as one string and compared by value.
  const paneRoster = useViewer((s) => s.panes.map((p) => p.studyUid).join("|"));
  const paneStudyUids = useMemo(() => (paneRoster ? paneRoster.split("|") : []), [paneRoster]);
  const showHighlights = useViewer((s) => s.showHighlights);
  const toggleHighlights = useViewer((s) => s.toggleHighlights);
  const seriesByStudy = useViewer((s) => s.seriesByStudy);

  const pool = useRef(new SliceSourcePool());
  const tracker = useRef<AttestationTracker | null>(null);
  const mounted = useRef<string | null>(null);

  const [activeId, setActiveId] = useState<string | null>(null);
  const [expert, setExpert] = useState(false);
  const [pending, setPending] = useState<DisplayRequest | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  // Which column the 3D scene opens on; null while it is closed.
  const [lesionPane, setLesionPane] = useState<number | null>(null);
  const lesionPaneRef = useRef<number | null>(null);
  lesionPaneRef.current = lesionPane;
  const activePaneIndex = useViewer((s) => s.activePane);
  const panesReady = useViewer((s) => s.panes.map((p) => (p.meta ? "1" : "0")).join(""));

  const studies = useMemo(() => orderedStudies(session), [session]);
  const findings = useMemo(() => buildFindings(session), [session]);
  const comparison = session?.mode === "comparison" && studies.length > 1;
  const active = findings.find((f) => f.id === activeId) ?? null;
  // Recomputed only when the selection changes: a new array every render would
  // defeat the memo on the pane and redraw both columns on every notch.
  const otherFindings = useMemo(() => findings.filter((f) => f.id !== activeId), [findings, activeId]);

  // -- attestation ---------------------------------------------------------
  if (!tracker.current) {
    tracker.current = new AttestationTracker(async (events) => {
      const current = useSession.getState();
      if (!current.session?.work_dir) return;
      await api.attest(current.session.work_dir, events, current.path ?? undefined);
      await current.refreshCoverage();
    }, policy);
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
  useEffect(() => {
    const sources = pool.current;
    return () => sources.disposeAll();
  }, []);

  // -- open the studies ----------------------------------------------------
  useEffect(() => {
    if (!session || !sessionPath || mounted.current === sessionPath) return;
    mounted.current = sessionPath;
    if (!studies.length) return;

    // Shells first, so the reader sees the layout and a skeleton immediately
    // rather than an empty window while the headers are read and the volumes
    // decoded.
    useViewer.getState().mountPanes(
      studies.map((study) => ({ studyPath: study.path, studyUid: study.uid, seriesUid: "" })),
    );

    void (async () => {
      // The series lists are header-only and cheap, and they carry the geometry
      // probe. Reading them before committing to a reconstruction is the
      // difference between opening on the study and opening on an error.
      const lists = await Promise.all(
        studies.map((study) => useViewer.getState().loadSeriesList(study.path).catch(() => [] as SeriesCard[])),
      );
      await Promise.all(
        studies.map((study, i) => {
          const uid = primarySeries(study, lists[i] ?? []);
          return uid ? useViewer.getState().openSeries(i, study.path, uid) : Promise.resolve();
        }),
      );
      const first = buildFindings(useSession.getState().session)[0];
      if (first) await jumpToFinding(first.id);
    })();
    // `jumpToFinding` is stable enough for this one-shot mount; re-running the
    // whole open sequence because a callback identity changed would reopen the
    // volumes underneath the reader.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, sessionPath, studies]);

  // -- navigation ----------------------------------------------------------
  const jumpToFinding = useCallback(
    async (id: string) => {
      setActiveId(id);
      const current = useSession.getState().session;
      const finding = buildFindings(current).find((f) => f.id === id);
      if (!current || !finding) return;

      const viewer = useViewer.getState();
      const openings = viewer.panes.map(async (pane, index) => {
        const study = current.studies.find((s) => s.uid === pane.studyUid);
        const at = study ? finding.atStudy[study.uid] : undefined;
        if (!study || !at?.ref) return null;

        // The cited slice lives in one particular reconstruction; a soft-tissue
        // finding is not addressable in the thin lung series and vice versa.
        const series =
          study.series.find((s) => s.sops?.[at.ref!.sop_uid]) ??
          study.series.find((s) => s.uid === at.ref!.series_uid);
        const seriesUid = series?.uid ?? at.ref.series_uid;
        if (pane.seriesUid !== seriesUid || !pane.meta) {
          await useViewer.getState().openSeries(index, pane.studyPath, seriesUid);
        }
        const meta = useViewer.getState().panes[index]?.meta;
        if (!meta) return null;

        let slice = meta.sop_uids.indexOf(at.ref.sop_uid);
        if (slice < 0 && series) {
          const instance = series.sops?.[at.ref.sop_uid]?.instance;
          if (instance) slice = meta.instances.indexOf(instance);
        }
        if (slice < 0) return null;
        return {
          paneIndex: index,
          index: slice,
          window: windowForSeries(series, meta.geometry.modality),
        };
      });

      const targets = (await Promise.all(openings)).filter(
        (t): t is { paneIndex: number; index: number; window: WindowSetting | undefined } => t !== null,
      );
      if (!targets.length) return;
      // The image is put back at fit before the ring is drawn: landing on a
      // finding while zoomed into somewhere else is disorienting.
      useViewer.getState().resetView();
      useViewer.getState().anchorAt(
        targets.map((t) => ({ paneIndex: t.paneIndex, index: t.index, ...(t.window ? { window: t.window } : {}) })),
      );
    },
    [],
  );

  const onSelect = useCallback(
    (id: string | null) => {
      if (!id) {
        setActiveId(null);
        return;
      }
      void jumpToFinding(id);
    },
    [jumpToFinding],
  );

  const onJump = useCallback((reference: Ref) => {
    const viewer = useViewer.getState();
    const index = viewer.panes.findIndex((p) => p.studyUid === reference.study_uid);
    const pane = viewer.panes[index];
    if (!pane?.meta) return;
    const slice = pane.meta.sop_uids.indexOf(reference.sop_uid);
    if (slice >= 0) {
      viewer.setActivePane(index);
      viewer.anchorAt([{ paneIndex: index, index: slice }]);
    }
  }, []);

  const stepFinding = useCallback(
    (delta: number) => {
      if (!findings.length) return;
      const current = findings.findIndex((f) => f.id === activeId);
      const next = findings[(current + delta + findings.length) % findings.length];
      if (next) void jumpToFinding(next.id);
    },
    [findings, activeId, jumpToFinding],
  );

  // -- keyboard ------------------------------------------------------------
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target;
      if (target instanceof HTMLElement && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      const viewer = useViewer.getState();
      const pane = viewer.activePane;
      const actions: Record<string, () => void> = {
        ArrowDown: () => viewer.stepIndex(pane, 1),
        ArrowUp: () => viewer.stepIndex(pane, -1),
        PageDown: () => viewer.stepIndex(pane, 10),
        PageUp: () => viewer.stepIndex(pane, -10),
        ArrowRight: () => stepFinding(1),
        ArrowLeft: () => stepFinding(-1),
        // The 3D scene owns Escape while it is open.
        Escape: () => { if (lesionPaneRef.current === null) setActiveId(null); },
        h: () => viewer.toggleHighlights(),
        H: () => viewer.toggleHighlights(),
        "1": () => viewer.setWindow(pane, { ...(CT_WINDOWS[0] as WindowSetting) }),
        "2": () => viewer.setWindow(pane, { ...(CT_WINDOWS[1] as WindowSetting) }),
        "3": () => viewer.setWindow(pane, { ...(CT_WINDOWS[2] as WindowSetting) }),
      };
      const action = actions[event.key];
      if (action) {
        event.preventDefault();
        action();
      }
    };
    globalThis.addEventListener("keydown", onKey);
    return () => globalThis.removeEventListener("keydown", onKey);
  }, [stepFinding]);

  // -- the agent's display gate --------------------------------------------
  // A bridged agent that asks for a page is blocked until this window shows it.
  // The panel that used to list the agent's transcript is gone; the gate is
  // not, because the server is waiting on it.
  useEffect(() => {
    let alive = true;
    const sync = () =>
      void api
        .agentState()
        .then((state) => {
          if (alive) setPending(state.pending_displays[0] ?? null);
        })
        .catch(() => undefined);
    sync();
    const stop = followAgent(() => sync());
    return () => {
      alive = false;
      stop();
    };
  }, []);

  if (!session || !sessionPath) {
    return (
      <div className="grid h-full place-items-center">
        <span className="text-[12px] text-chalk-600">İnceleme açılıyor…</span>
      </div>
    );
  }

  const span = comparison ? monthsBetween(studies[0]?.date ?? "", studies[1]?.date ?? "") : null;

  return (
    <div className="relative grid h-full grid-rows-[52px_auto_1fr_auto] overflow-hidden bg-ink-950">
      {/* ---- what am I looking at ---- */}
      <header className="flex shrink-0 items-center gap-3 border-b border-[var(--hairline)] px-3.5">
        <button type="button" className="btn btn-ghost" onClick={onHome}>
          ← İncelemeler
        </button>
        <span className="h-5 w-px bg-[var(--hairline-strong)]" />
        <div className="min-w-0">
          <p className="eyebrow leading-none">
            {comparison ? "Karşılaştırma" : "Tek tetkik"} · {modalityLabel(studies)}
          </p>
          <h1 className="mt-1 truncate text-[13.5px] font-semibold leading-none tracking-tight text-chalk-100">
            {comparison
              ? `${longDate(studies[0]?.date ?? "")} → ${longDate(studies[1]?.date ?? "")}`
              : longDate(studies[0]?.date ?? "")}
            {span !== null && (
              <span className="ml-2 font-normal text-chalk-600">
                {span < 1.5 ? `${Math.round(span * 30.44)} gün ara` : `${span.toFixed(1).replace(".", ",")} ay ara`}
              </span>
            )}
          </h1>
        </div>

        <div className="min-w-0 flex-1" />

        {/* The highlight switch is never more than one click away, by request:
            a reader has to be able to look at bare tissue at any moment. */}
        <button
          type="button"
          className={`btn ${showHighlights ? "btn-on" : ""}`}
          aria-pressed={showHighlights}
          title="Lezyon katmanlarını göster / gizle (H)"
          onClick={toggleHighlights}
        >
          {showHighlights ? <Eye size={15} aria-hidden /> : <EyeOff size={15} aria-hidden />}
          {showHighlights ? "Vurgular açık" : "Vurgular kapalı"}
        </button>
        <button
          type="button"
          className={`btn ${expert ? "btn-on" : ""}`}
          aria-pressed={expert}
          title="Düzlem, pencere, rekonstrüksiyon ve ölçek denetimleri"
          onClick={() => setExpert((open) => !open)}
        >
          <SlidersHorizontal size={15} aria-hidden />
          Uzman
        </button>
        <button
          type="button"
          className="btn"
          disabled={!panesReady.includes("1")}
          title={
            comparison
              ? "Tüm bulguları 3D hacimde gör; içeride önceki ve son tetkik arasında geçiş yapabilirsiniz"
              : "Tüm bulguları tam ekran 3D hacimde gör"
          }
          onClick={() => setLesionPane(Math.min(activePaneIndex, Math.max(0, paneStudyUids.length - 1)))}
        >
          <Boxes size={15} aria-hidden />
          3D hacim
          {comparison && (
            <span className="chip ml-0.5 text-[10px]">{activePaneIndex === 0 ? "Önceki" : "Son"}</span>
          )}
        </button>
        <button type="button" className="btn btn-primary" onClick={() => setReportOpen(true)}>
          <FileText size={15} aria-hidden />
          Rapor
        </button>
      </header>

      {expert ? <ExpertBar seriesByStudy={seriesByStudy} /> : <div />}

      {/* ---- the reading ---- */}
      <div className="grid min-h-0 grid-cols-[360px_1fr] overflow-hidden">
        <aside className="min-h-0 overflow-hidden border-r border-[var(--hairline)]">
          <FindingRail
            findings={findings}
            activeId={activeId}
            comparison={Boolean(comparison)}
            studyDates={studies.map((s) => ({ uid: s.uid, date: s.date }))}
            onSelect={onSelect}
            onJump={onJump}
          />
        </aside>

        <main
          className="grid min-h-0 min-w-0 gap-px bg-[var(--hairline)]"
          style={{ gridTemplateColumns: `repeat(${Math.max(1, paneStudyUids.length)}, minmax(0, 1fr))` }}
        >
          {paneStudyUids.length === 0 ? (
            <div className="grid place-items-center bg-ink-1000">
              <div className="flex flex-col items-center gap-3">
                <span className="h-7 w-7 rounded-full border-2 border-amber-400/25 border-t-amber-400 spin" />
                <p className="text-[12px] text-chalk-600">Tomografi hacmi hazırlanıyor…</p>
              </div>
            </div>
          ) : (
            paneStudyUids.map((uid, i) => (
              <StudyPane
                key={`${uid}-${i}`}
                paneIndex={i}
                role={comparison ? (i === 0 ? "Önceki" : "Son") : ""}
                study={studies.find((s) => s.uid === uid)}
                finding={active}
                otherFindings={otherFindings}
                comparison={Boolean(comparison)}
                pool={pool.current}
                tracker={tracker.current}
              />
            ))
          )}
        </main>
      </div>

      {expert ? (
        <footer className="flex h-6 shrink-0 items-center gap-4 border-t border-[var(--hairline)] bg-ink-900 px-3.5 text-[10px] text-chalk-700">
          <span className="readout">
            {coverage
              ? `${coverage.slices_attested}/${coverage.slices_total} kesit tasdikli`
              : "tasdik defteri okunuyor…"}
          </span>
          <span className="readout">
            tasdik eşiği {policy.minDwellMs} ms · ≥{policy.minScale} px/px · odaklı pencere
          </span>
          <span className="readout truncate">{locale?.name ?? ""}</span>
          <span className="min-w-0 flex-1" />
          <span className="readout truncate">
            tekerlek kesit · shift+tekerlek yakınlaştır · sağ tuş pencere · ok tuşları bulgu · H vurgular
          </span>
        </footer>
      ) : (
        <div />
      )}

      {lesionPane !== null && (() => {
        // One scene per examination. In a comparison both are offered and the
        // reader switches inside the scene, so it is never a guess which
        // examination the volume belongs to.
        const viewer = useViewer.getState();
        const options: VolumeOption[] = viewer.panes.flatMap((pane, i) => {
          const study = studies.find((s) => s.uid === pane.studyUid);
          if (!pane.meta || !study) return [];
          return [{
            paneIndex: i,
            studyPath: pane.studyPath,
            studyUid: pane.studyUid,
            seriesUid: pane.seriesUid,
            role: comparison ? (i === 0 ? "Önceki" : "Son") : "",
            dateLabel: longDate(study.date),
          }];
        });
        if (!options.length) return null;
        const initial = options.find((o) => o.paneIndex === lesionPane) ?? options[0]!;
        return (
          <LesionView
            options={options}
            initial={initial.paneIndex}
            findings={findings}
            activeId={activeId}
            onSelectFinding={(id) => void jumpToFinding(id)}
            onClose={() => setLesionPane(null)}
          />
        );
      })()}

      {reportOpen && (
        <ReportPanel
          session={session}
          findings={findings}
          locale={locale}
          reportSha={session.report_sha256 ?? ""}
          onClose={() => setReportOpen(false)}
        />
      )}

      {pending && session.work_dir && (
        <PageViewer
          pagePath={pending.page_path}
          purpose={pending.purpose}
          workDir={session.work_dir}
          sessionPath={sessionPath}
          requestedByAgent
          onAttested={() => {
            void api.displayShown(pending.id).catch(() => undefined);
            setPending(null);
            if (sessionPath) void useSession.getState().open(sessionPath);
          }}
          onDismiss={() => {
            void api.displayDeclined(pending.id, "okuyucu reddetti").catch(() => undefined);
            setPending(null);
          }}
        />
      )}
    </div>
  );
}
