/**
 * One examination, as a column: what it is, the image, and where you are in it.
 *
 * In a comparison the two columns are deliberately symmetrical and deliberately
 * labelled in words rather than by position -- "Önceki" and "Son" over the
 * dates, not a left and a right that the reader has to remember. The strip
 * under the heading answers the only question the comparison exists to answer,
 * for this examination: is the selected finding here, how big, or is it gone.
 *
 * The pane subscribes to its own slice of viewer state rather than receiving it
 * from the screen above. A wheel notch then re-renders two small columns
 * instead of the whole reading screen, which is most of the difference between
 * a scroll that keeps up and one that does not.
 */
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import type { SessionStudy } from "../api/types";
import type { AttestationTracker } from "../lib/attestation";
import {
  expectedTissue,
  format,
  lesionAt,
  longDate,
  type Finding,
  type TissueCheck,
} from "../lib/findings";
import type { SliceSourcePool } from "../lib/sliceCache";
import { segmentLesion, lesionMarker, profileFor } from "../lib/lesions";
import type { LesionSegmentation } from "../api/types";
import { useViewer } from "../state/viewer";
import { SliceCanvas, type CanvasMarker } from "./SliceCanvas";
import { SliceScrubber } from "./SliceScrubber";
import { AlertTriangle, Check, Minus, ShieldCheck } from "./icons";
import { ErrorBoundary } from "./ErrorBoundary";

interface Props {
  paneIndex: number;
  /** "Önceki" / "Son" in a comparison; empty for a single read. */
  role: string;
  study: SessionStudy | undefined;
  finding: Finding | null;
  otherFindings: Finding[];
  /** Two examinations side by side, rather than one read on its own. */
  comparison: boolean;
  pool: SliceSourcePool;
  tracker: AttestationTracker | null;
}

export const StudyPane = memo(function StudyPane({
  paneIndex,
  role,
  study,
  finding,
  comparison,
  pool,
  tracker,
}: Props) {
  const pane = useViewer((s) => s.panes[paneIndex]);
  const active = useViewer((s) => s.activePane === paneIndex);
  const showHighlights = useViewer((s) => s.showHighlights);
  const showGrid = useViewer((s) => s.showGrid);
  const setIndex = useViewer((s) => s.setIndex);
  const setView = useViewer((s) => s.setView);
  const setWindow = useViewer((s) => s.setWindow);
  const setActivePane = useViewer((s) => s.setActivePane);
  const [failure, setFailure] = useState<string | null>(null);
  const [sample, setSample] = useState<{ medianHu: number; airFraction: number } | null>(null);
  const count = pane?.meta?.planes?.[pane.plane]?.count ?? 0;
  const here = study ? finding?.atStudy[study.uid] : undefined;

  /** Which slice of this series the selected finding actually lives on. */
  const target = useMemo(() => {
    if (!pane?.meta || !here?.ref) return null;
    const index = pane.meta.sop_uids.indexOf(here.ref.sop_uid);
    return index >= 0 ? index : null;
  }, [pane?.meta, here?.ref]);

  const lesion = useMemo(() => (here ? lesionAt(here) : null), [here]);

  /*
   * The finding, grown in the volume.
   *
   * Requested once the pane is sitting on the series the address belongs to,
   * because a seed only means anything in the series it was taken from. Until
   * it arrives the cited address is still marked, so nothing blinks.
   */
  const [segmentation, setSegmentation] = useState<LesionSegmentation | null>(null);
  useEffect(() => {
    setSegmentation(null);
    if (!pane?.meta || target === null || !lesion || here?.presence !== "present") return;
    let cancelled = false;
    void segmentLesion({
      studyPath: pane.studyPath,
      seriesUid: pane.seriesUid,
      index: target,
      row: lesion.row,
      col: lesion.col,
      profile: profileFor(finding?.technical ?? finding?.headline ?? ""),
    }).then((result) => {
      if (!cancelled) {
        setSegmentation(result);
        useViewer.getState().patchPane(paneIndex, { focus: result.focus_zyx });
      }
    }).catch((error) => { if (!cancelled) setFailure(String(error.message ?? error)); });
    return () => {
      cancelled = true;
    };
  }, [pane?.studyPath, pane?.seriesUid, pane?.meta, target, lesion, here?.presence, finding?.technical, finding?.headline, paneIndex]);

  // Does the tissue under the mark match what the finding says is there? The
  // answer comes from the samples on screen, not from anything the ledger
  // asserts about itself.
  const check = useMemo<TissueCheck | null>(() => {
    if (!sample || !finding || !here) return null;
    // Where something is recorded as gone, the address must still be tissue
    // where it could have been: bone or air under the mark means a misplaced address.
    const expectation = here.presence === "absent"
      ? { label: "yumuşak doku ya da akciğer", min: -1024, max: 150 }
      : here.presence === "present" ? expectedTissue(finding) : null;
    if (!expectation) return null;
    return {
      expectation,
      medianHu: sample.medianHu,
      airFraction: sample.airFraction,
      agrees: sample.medianHu >= expectation.min && sample.medianHu <= expectation.max,
    };
  }, [sample, finding, here?.presence]);

  const markers = useMemo<CanvasMarker[]>(() => {
    if (!pane?.meta || !here || !lesion) return [];
    if (segmentation && segmentation.series_uid === pane.seriesUid) {
      const marker = lesionMarker(segmentation, pane.index, pane.plane);
      if (marker) return [marker];
    }
    // Off the grown shape (or without one) the cited address is still marked on its own slice.
    if (pane.plane !== "ax" || pane.index !== target) return [];
    const label = here.measurement ? `${format(here.measurement.value)} ${here.measurement.unit} · kayıt` : undefined;
    // A resolved finding with a recorded caliper shows that caliper too, in the
    // resolved colour, so both columns are read the same way.
    if (here.presence === "absent" && !lesion.caliper) return [{ kind: "clean", row: lesion.row, col: lesion.col, caliper: null, label }];
    return [{ kind: "focus", row: lesion.row, col: lesion.col, caliper: lesion.caliper, label, ...(here.presence === "absent" ? { tone: "good" as const } : {}) }];
  }, [pane, here, lesion, segmentation, target]);

  const source = useMemo(
    () =>
      pane
        ? pool.for({
            study: pane.studyPath,
            series: pane.seriesUid,
            plane: pane.plane,
            mipMm: pane.mipMm,
            allowTilt: false,
          })
        : null,
    [pool, pane?.studyPath, pane?.seriesUid, pane?.plane, pane?.mipMm],
  );

  const onScrub = useCallback((value: number) => setIndex(paneIndex, value), [paneIndex, setIndex]);

  /**
   * How much of this series is cached, sampled twice a second.
   *
   * Deliberately not per frame and not per slice: this is furniture, and a
   * React render on every arriving slice would put the cost of drawing the
   * progress bar into the path it is reporting on.
   */
  const [coverage, setCoverage] = useState<Uint8Array | null>(null);
  useEffect(() => {
    if (!source || count <= 0) {
      setCoverage(null);
      return;
    }
    const buckets = 64;
    const sample = () =>
      setCoverage((previous) => {
        const next = source.coverage(count, buckets);
        if (previous && previous.length === next.length && previous.every((v, i) => v === next[i])) {
          return previous;
        }
        return next;
      });
    sample();
    const timer = window.setInterval(sample, 500);
    return () => window.clearInterval(timer);
  }, [source, count]);

  if (!pane) return null;

  return (
    <section
      className={`relative flex min-h-0 min-w-0 flex-col bg-ink-1000 ${
        active ? "ring-1 ring-inset ring-white/10" : ""
      }`}
      onPointerDownCapture={() => setActivePane(paneIndex)}
    >
      {/* ---- who this is: only when there is another column to tell apart ---- */}
      {comparison && (
        <header className="flex h-11 shrink-0 items-baseline gap-2.5 border-b border-[var(--hairline)] bg-ink-950 px-3.5">
          {role && <span className="eyebrow text-chalk-400">{role}</span>}
          <h2 className="truncate text-[14px] font-semibold tracking-tight text-chalk-100">
            {study ? longDate(study.date) : "—"}
          </h2>
          <span className="min-w-0 flex-1" />
          <span className="readout shrink-0 text-[10.5px] text-chalk-700">
            {pane.meta ? `${pane.meta.geometry.modality} · seri ${pane.meta.geometry.series_number}` : ""}
          </span>
        </header>
      )}

      {/* ---- the answer for this examination ---- */}
      {finding && here && (
        <PresenceStrip
          finding={finding}
          here={here}
          comparison={comparison}
          check={check}
          outline={segmentation}
        />
      )}

      {/* ---- the patient ---- */}
      <div className="relative min-h-0 flex-1">
        {pane.meta && source ? (
          <ErrorBoundary fallbackTitle="Kesit görüntüleyici yüklenemedi">
            <SliceCanvas
              source={source}
              index={pane.index}
              count={count}
              window={pane.window}
              view={pane.view}
              markers={markers}
              showMarkers={showHighlights}
              iop={pane.meta.geometry.iop}
              showGrid={showGrid}
              attest={tracker ?? undefined}
              studyUid={pane.studyUid}
              active={active}
              onView={(view) => setView(paneIndex, view)}
              onIndex={(index) => setIndex(paneIndex, index)}
              onWindow={(patch) => setWindow(paneIndex, patch)}
              onActivate={() => setActivePane(paneIndex)}
              onFailure={setFailure}
              onSample={setSample}
            />
          </ErrorBoundary>
        ) : (
          <PaneSkeleton error={pane.error} />
        )}
        {!comparison && pane.meta && (
          <span className="pointer-events-none absolute right-3 top-2 readout text-[10.5px] text-chalk-700">
            {`${pane.meta.geometry.modality} · seri ${pane.meta.geometry.series_number}`}
          </span>
        )}
        {failure && (
          <p className="absolute inset-x-4 bottom-16 rounded-[6px] border border-alert-400/30 bg-alert-900/80 px-3 py-2 text-[11px] text-alert-400">
            {failure}
          </p>
        )}
      </div>

      {/* ---- where you are, and how to travel ---- */}
      <SliceScrubber
        index={pane.index}
        count={count}
        coverage={coverage}
        marker={target}
        span={lesionSpan(segmentation, pane.plane)}
        active={active}
        onSeek={onScrub}
        onJumpToMarker={() => target !== null && onScrub(target)}
        isReady={(at) => Boolean(source?.has(at))}
      />

    </section>
  );
});

/**
 * The slices the finding occupies, for the band under the scrubber.
 *
 * From the grown shape where there is one, and otherwise from the extent the
 * measurement implies -- the reader is owed the range either way, and the
 * range is the one thing that survives when the border does not.
 */
function lesionSpan(segmentation: LesionSegmentation | null, plane: "ax" | "cor" | "sag"): [number, number] | null {
  const slices = segmentation?.planes[plane];
  return slices?.length ? [slices[0]!.index, slices[slices.length - 1]!.index] : null;
}

/**
 * One sentence per examination, in the colour of its answer.
 *
 * This is the crux of the whole comparison: on the study where the finding was
 * seen it says how big, and on a study where the ledger says it is gone it says
 * so in green and the image carries no red ring at all.
 */
function PresenceStrip({
  finding,
  here,
  comparison,
  check,
  outline,
}: {
  finding: Finding;
  here: Finding["series"][number];
  comparison: boolean;
  check: TissueCheck | null;
  outline: LesionSegmentation | null;
}) {
  if (here.presence === "not_covered") {
    return (
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-[var(--hairline)] bg-ink-900 px-3.5 text-[11.5px] text-chalk-400">
        <Minus size={13} aria-hidden />
        <span>Bu bölge bu tetkikin kapsamı dışında.</span>
      </div>
    );
  }
  if (here.presence === "absent") {
    return (
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-good-400/25 bg-good-900/70 px-3.5 text-[11.5px] text-good-400">
        <Check size={14} strokeWidth={2.6} aria-hidden />
        <strong className="shrink-0 whitespace-nowrap font-semibold">Bu tetkikte yok.</strong>
        <span className="min-w-0 truncate text-good-400/80">{here.text || "Bulgu tamamen gerilemiş."}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5">
          <ResolutionBadge here={here} />
          {check && !check.agrees && <TissueBadge check={check} />}
        </span>
      </div>
    );
  }
  const size = here.measurement ? `${format(here.measurement.value)} ${here.measurement.unit}` : null;
  return (
    <div className="flex h-8 shrink-0 items-center gap-2 border-b border-focus-500/25 bg-[color-mix(in_srgb,var(--color-focus-500)_11%,transparent)] px-3.5 text-[11.5px]">
      <span aria-hidden className="h-1.5 w-1.5 shrink-0 rounded-full bg-focus-500" />
      {/* "Bu tetkikte var" only earns its place when there is another
          examination it could have been absent from. */}
      {comparison && <strong className="shrink-0 font-semibold text-chalk-100">Bu tetkikte var</strong>}
      {size && <span className="readout shrink-0 text-chalk-100">{size}</span>}
      <span className="truncate text-chalk-400">{here.text || finding.technical}</span>
      <span className="ml-auto flex shrink-0 items-center gap-1.5">
        <OutlineBadge outline={outline} />
        {check && <TissueBadge check={check} />}
      </span>
    </div>
  );
}

/**
 * A recorded number that is smaller than the grid can support. Two pixels on a
 * five-millimetre slice is not a depth; the reader deserves to know that before
 * the "20 → 1.7 mm" line is taken as a measurement.
 */
function ResolutionBadge({ here }: { here: Finding["series"][number] }) {
  const points = here.measurement?.points ?? [];
  const [a, b] = points;
  if (!a || !b) return null;
  const px = Math.hypot(a.row - b.row, a.col - b.col);
  if (px >= 3) return null;
  return (
    <span className="chip text-[10px] text-warn-400" title={`Kayıttaki ölçüm yalnızca ${px.toFixed(0)} piksel uzunluğunda; bu çözünürlükte bir derinlik değil, "sıfıra yakın" anlamına gelir.`}>
      {px.toFixed(0)} px · çözünürlük altı
    </span>
  );
}

function OutlineBadge({ outline }: { outline: LesionSegmentation | null }) {
  if (!outline) return null;
  const reasons = outline.reasons.join(" · ");
  if (!outline.voxels) {
    return <span className="chip text-[10px] text-chalk-500" title={reasons || "Kapalı bir sınır bulunamadı; kayıttaki ölçüm gösteriliyor"}>Sınır belirlenemedi · kayıt</span>;
  }
  return <span className="chip text-[10px] text-amber-400" title={reasons || "Otomatik segmentasyon; kullanıcı incelemesi gerekir"}>
    {outline.status === "needs-review" ? "Otomatik sınır · sınırlı" : "Otomatik sınır"}
  </span>;
}

/**
 * The viewer marking its own homework.
 *
 * Green means the pixels under the mark read as the tissue the sentence
 * describes. Amber means they do not -- which is a statement about the saved
 * address, not about the patient, and is exactly the thing this application
 * exists to make visible rather than to smooth over.
 */
function TissueBadge({ check }: { check: TissueCheck }) {
  const hu = `${Math.round(check.medianHu)} HU`;
  return check.agrees ? (
    <span
      className="flex shrink-0 items-center gap-1 rounded-full border border-good-400/30 bg-good-900 px-1.5 py-[1px] text-[10px] font-medium text-good-400"
      title={`İşaretin altındaki doku ${hu}; "${check.expectation.label}" beklentisiyle uyuşuyor.`}
    >
      <ShieldCheck size={10} aria-hidden />
      doku uyuşuyor
    </span>
  ) : (
    <span
      className="flex shrink-0 items-center gap-1 rounded-full border border-warn-400/40 bg-warn-900 px-1.5 py-[1px] text-[10px] font-medium text-warn-400"
      title={`İşaretin altındaki doku ${hu} (%${Math.round(check.airFraction * 100)} hava). Bu cümle "${check.expectation.label}" bekliyor (${check.expectation.min}…${check.expectation.max} HU). Kayıttaki adres bu bulguya ait olmayabilir.`}
    >
      <AlertTriangle size={10} aria-hidden />
      adres {hu}
    </span>
  );
}

/**
 * The engine's refusals, said in the reader's language.
 *
 * These are not failures of the application: they are the engine declining to
 * present something as a volume when it is not one. Passing the English
 * sentence straight through told a family member nothing and read as a crash,
 * so it is translated and paired with the one thing that actually helps --
 * another reconstruction of the same acquisition, which the expert view picks.
 */
function explainPaneError(error: string): { text: string; hint: string | null; raw: string } {
  if (/irregularly spaced|not a regular volume|duplicate/i.test(error)) {
    return {
      text: "Bu rekonstrüksiyonun kesitleri eşit aralıklı değil, bu yüzden hacim olarak gösterilemiyor.",
      hint: "Uzman görünümünden aynı çekimin başka bir rekonstrüksiyonunu seçebilirsiniz.",
      raw: error,
    };
  }
  if (/gantry|tilt/i.test(error)) {
    return {
      text: "Bu seri eğik gantry ile çekilmiş; reformatlar güvenilir değil.",
      hint: "Aksiyel düzlemde inceleyin.",
      raw: error,
    };
  }
  if (/not found|no such/i.test(error)) {
    return {
      text: "Görüntü dosyaları bulunamadı. Arşiv klasörü taşınmış ya da bağlı değil olabilir.",
      hint: null,
      raw: error,
    };
  }
  return { text: error, hint: null, raw: error };
}

function PaneSkeleton({ error }: { error: string | null }) {
  if (error) {
    const explained = explainPaneError(error);
    return (
      <div className="grid h-full place-items-center p-8 text-center">
        <div className="max-w-[26rem]">
          <p className="text-[13px] leading-relaxed text-chalk-200">{explained.text}</p>
          {explained.hint && (
            <p className="mt-2 text-[12px] leading-relaxed text-chalk-500">{explained.hint}</p>
          )}
          <p className="readout mt-4 text-[10px] leading-relaxed text-chalk-700">{explained.raw}</p>
        </div>
      </div>
    );
  }
  return (
    <div className="grid h-full place-items-center">
      <div className="flex flex-col items-center gap-3">
        <span className="skeleton h-40 w-40 rounded-full" />
        <span className="text-[11.5px] text-chalk-600">Kesitler hazırlanıyor…</span>
      </div>
    </div>
  );
}
