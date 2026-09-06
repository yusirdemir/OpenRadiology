/**
 * The ledger, turned into something a family can read.
 *
 * A `Claim` in the session file is written for a radiologist: it carries the
 * technical sentence, the addresses it cites, the measurements with the hash of
 * the file they were read out of, and -- for a comparison -- one timeline entry
 * per examination. None of that is thrown away here. What this module does is
 * arrange it around the only question the reader actually came with: on this
 * scan, is the thing there, and is it better or worse than last time.
 *
 * Two rules are enforced, and both exist because getting them wrong frightens
 * people for no reason:
 *
 *  - a finding is only marked present on a study the ledger says it was seen
 *    on. `timeline[].status` is the authority, not the fact that a measurement
 *    happens to exist -- the residual 1.7 mm measured where an effusion used to
 *    be is evidence that it is gone, not evidence that it is there,
 *  - nothing is invented. Every sentence shown to the reader is a sentence the
 *    engine wrote; this file selects and orders, it never composes.
 */
import type { Claim, Measurement, Ref, Session, SessionStudy } from "../api/types";

export type Verdict =
  | "resolved"
  | "decreased"
  | "stable"
  | "increased"
  | "new"
  | "indeterminate"
  | "not_comparable"
  | "present";

/** How a finding stands on one particular examination. */
export type Presence = "present" | "absent" | "not_covered" | "unknown";

export interface FindingAtStudy {
  studyUid: string;
  presence: Presence;
  /** The engine's technical sentence for this examination. */
  text: string;
  /** The same thing in plain language. */
  explanation: string;
  /** Where to look. Present even when the finding is not: it is where it was. */
  ref: Ref | null;
  measurement: Measurement | null;
}

export interface Finding {
  id: string;
  verdict: Verdict;
  /** One plain sentence. This is the headline a non-specialist reads first. */
  headline: string;
  /** Why it matters, in plain language. */
  importance: string;
  uncertainty: string;
  withDoctor: string;
  /** The radiologist's sentence, kept for the expert view. */
  technical: string;
  /** The engine's own reason for the verdict, when it gave one. */
  reason: string;
  priority: Claim["priority"];
  confidence: Claim["confidence"];
  atStudy: Record<string, FindingAtStudy>;
  /** Ordered oldest first, for the change line. */
  series: FindingAtStudy[];
  /** Measured change, only when both endpoints are the same unit. */
  change: { from: number; to: number; unit: string; deltaPct: number | null } | null;
  claim: Claim;
}

export interface VerdictStyle {
  label: string;
  tone: "good" | "calm" | "warn" | "alert";
  /** Name of the icon that stands for this verdict; drawn, never typed. */
  icon: "check" | "down" | "equal" | "up" | "plus" | "question" | "minus" | "dot" | "alert";
}

export const VERDICTS: Record<Verdict, VerdictStyle> = {
  resolved: { label: "İyileşti", tone: "good", icon: "check" },
  decreased: { label: "Küçüldü", tone: "good", icon: "down" },
  stable: { label: "Değişmedi", tone: "calm", icon: "equal" },
  increased: { label: "Büyüdü", tone: "alert", icon: "up" },
  new: { label: "Yeni", tone: "warn", icon: "plus" },
  indeterminate: { label: "Belirsiz", tone: "warn", icon: "question" },
  not_comparable: { label: "Kıyaslanamadı", tone: "calm", icon: "minus" },
  present: { label: "Bulgu", tone: "warn", icon: "dot" },
};

export function verdictStyle(verdict: Verdict): VerdictStyle {
  return VERDICTS[verdict] ?? VERDICTS.present;
}

/**
 * A single read has no interval, so it has no verdict -- and dressing every
 * finding in the same alarm colour because of that would be a lie by palette.
 * One examination is graded by the priority the ledger already assigned: what
 * drives the next decision, what is worth following, and what was simply
 * noticed on the way past.
 */
const PRIORITIES: Record<Claim["priority"], VerdictStyle> = {
  important: { label: "Önemli", tone: "warn", icon: "alert" },
  routine: { label: "Bulgu", tone: "calm", icon: "dot" },
  incidental: { label: "Rastlantısal", tone: "calm", icon: "dot" },
};

/** How a finding should be badged, given whether this is a comparison. */
export function findingBadge(finding: Finding, comparison: boolean): VerdictStyle {
  if (comparison) return verdictStyle(finding.verdict);
  return PRIORITIES[finding.priority] ?? PRIORITIES.routine;
}

const PRESENCE_BY_STATUS: Record<string, Presence> = {
  present: "present",
  not_seen: "absent",
  resolved: "absent",
  not_covered: "not_covered",
  indeterminate: "unknown",
};

/** Chronological order comes from DICOM, never from the order somebody clicked. */
export function orderedStudies(session: Session | null): SessionStudy[] {
  return [...(session?.studies ?? [])].sort((a, b) =>
    `${a.date}${a.time}`.localeCompare(`${b.date}${b.time}`),
  );
}

function measurementFor(claim: Claim, studyUid: string): Measurement | null {
  const all = (claim.measurements ?? []).filter((m) => m.ref.study_uid === studyUid);
  const dimensional = all.filter((m) => m.unit === "mm");
  const hits = dimensional.length ? dimensional : all;
  // A claim may carry several numbers for one study (a diameter and an extent).
  // The longest one is the one a report would quote.
  return hits.length ? (hits.reduce((a, b) => (b.value > a.value ? b : a)) as Measurement) : null;
}

function refFor(claim: Claim, studyUid: string, timelineRef: Ref | undefined): Ref | null {
  // The caliper's own reference is the slice the number was measured on; a
  // claim-level address is where the sentence points, which may be a slice
  // away. The measured one wins because the mark is drawn against the caliper.
  return (
    claim.measurements?.find((m) => m.ref.study_uid === studyUid && m.points?.length)?.ref ??
    timelineRef ??
    claim.refs.find((r) => r.study_uid === studyUid) ??
    claim.measurements?.find((m) => m.ref.study_uid === studyUid)?.ref ??
    null
  );
}

/**
 * The verdict, taken from the ledger rather than computed from the numbers.
 *
 * Two measurements 0.4 mm apart are not evidence of shrinkage; the engine knows
 * what its own measurement error is and has already decided. A single-study
 * read has no interval at all, so it gets `present` and no change line.
 */
function verdictOf(claim: Claim, comparison: boolean): Verdict {
  if (!comparison) return "present";
  const status = claim.comparison?.status ?? "";
  return (status in VERDICTS ? status : "not_comparable") as Verdict;
}

export function buildFindings(session: Session | null): Finding[] {
  if (!session) return [];
  const studies = orderedStudies(session);
  const comparison = session.mode === "comparison" && studies.length > 1;

  const findings = session.claims.map<Finding>((claim) => {
    const atStudy: Record<string, FindingAtStudy> = {};
    for (const study of studies) {
      const entry = claim.timeline?.find((t) => t.study_uid === study.uid);
      const measurement = measurementFor(claim, study.uid);
      const ref = refFor(claim, study.uid, entry?.refs?.[0]);
      // With no timeline the ledger is single-study: the claim is about the one
      // examination it cites, and silence elsewhere means "not covered here".
      const presence: Presence = entry
        ? (PRESENCE_BY_STATUS[entry.status] ?? "unknown")
        : ref
          ? "present"
          : "not_covered";
      atStudy[study.uid] = {
        studyUid: study.uid,
        presence,
        text: entry?.text ?? (studies.length === 1 ? claim.text : ""),
        explanation: entry?.explanation ?? "",
        ref,
        measurement,
      };
    }

    const series = studies.map((s) => atStudy[s.uid]).filter((x): x is FindingAtStudy => Boolean(x));
    const first = series[0];
    const last = series[series.length - 1];
    const change =
      comparison &&
      first?.measurement &&
      last?.measurement &&
      first !== last &&
      first.measurement.unit === last.measurement.unit
        ? {
            from: first.measurement.value,
            to: last.measurement.value,
            unit: first.measurement.unit,
            deltaPct:
              first.measurement.value > 0
                ? ((last.measurement.value - first.measurement.value) / first.measurement.value) * 100
                : null,
          }
        : null;

    return {
      id: claim.id,
      verdict: verdictOf(claim, comparison),
      headline: claim.patient?.meaning?.trim() || claim.text,
      importance: claim.patient?.importance?.trim() ?? "",
      uncertainty: claim.patient?.uncertainty?.trim() ?? "",
      withDoctor: claim.patient?.discuss_with_doctor?.trim() ?? "",
      technical: claim.text,
      reason: claim.comparison?.reason?.trim() ?? "",
      priority: claim.priority,
      confidence: claim.confidence,
      atStudy,
      series,
      change,
      claim,
    };
  });

  // Anything that got worse or is new comes first; then what needs a decision;
  // then the reassuring rest. Within a band the ledger's own order is kept.
  const rank: Record<Verdict, number> = {
    increased: 0,
    new: 1,
    indeterminate: 2,
    present: 3,
    not_comparable: 4,
    decreased: 5,
    resolved: 6,
    stable: 7,
  };
  const priorityRank = { important: 0, routine: 1, incidental: 2 } as const;
  return findings
    .map((f, i) => ({ f, i }))
    .sort(
      (a, b) =>
        rank[a.f.verdict] - rank[b.f.verdict] ||
        priorityRank[a.f.priority] - priorityRank[b.f.priority] ||
        a.i - b.i,
    )
    .map((entry) => entry.f);
}

/**
 * What the ledger's own sentence implies the tissue at its address should be.
 *
 * This exists so the viewer can check itself. A claim that says "calcified
 * granuloma" and points at a place reading minus eight hundred Hounsfield
 * units is not a display problem, it is a wrong address -- and a viewer that
 * draws a confident ring around it while saying nothing is worse than useless.
 * The bands are the ordinary radiological ones; anything the wording does not
 * pin down returns null and no claim is made either way.
 */
export interface TissueExpectation {
  label: string;
  /** Inclusive Hounsfield band the median of a small box should fall in. */
  min: number;
  max: number;
}

export function expectedTissue(finding: Finding): TissueExpectation | null {
  // The headline names the subject; the technical wording also mentions the
  // neighbours (a mass "next to a bulla"), which must not decide the check.
  return tissueOf(finding.headline) ?? tissueOf(`${finding.technical} ${finding.headline}`);
}

function tissueOf(source: string): TissueExpectation | null {
  const text = source.toLowerCase();
  if (/kalsifi|granülom|granulom|kireç|stapler|klips/.test(text)) {
    return { label: "kalsifik", min: 120, max: 3200 };
  }
  if (/bül|amfizem|hava kist|pnömotoraks/.test(text)) {
    return { label: "hava", min: -1024, max: -650 };
  }
  if (/efüzyon|plevral sıvı|basit kist|kortikal kist|su kesesi|sıvı dansite/.test(text)) {
    return { label: "sıvı", min: -30, max: 45 };
  }
  if (/kitle|solid nodül|lenf nod|güdük|stump|herni|jinekomasti|yumuşak doku/.test(text)) {
    return { label: "yumuşak doku", min: -30, max: 180 };
  }
  if (/damar|vasküler|vaskuler/.test(text)) {
    return { label: "damar", min: -20, max: 400 };
  }
  return null;
}

/** The verdict of that check, once the pixels have been looked at. */
export interface TissueCheck {
  expectation: TissueExpectation;
  medianHu: number;
  airFraction: number;
  agrees: boolean;
}

export interface Tally {
  better: number;
  same: number;
  worse: number;
  attention: number;
  total: number;
}

/** The one line at the top of the screen: did things get better or worse. */
export function tally(findings: Finding[]): Tally {
  const out: Tally = { better: 0, same: 0, worse: 0, attention: 0, total: findings.length };
  for (const finding of findings) {
    switch (finding.verdict) {
      case "resolved":
      case "decreased":
        out.better += 1;
        break;
      case "increased":
        out.worse += 1;
        break;
      case "new":
      case "indeterminate":
        out.attention += 1;
        break;
      default:
        out.same += 1;
    }
  }
  return out;
}

/**
 * Where a finding actually sits on its slice, and how big it is.
 *
 * This is the arithmetic a first version of the viewer got wrong, visibly and
 * embarrassingly. `ref.row/col` is not the centre of a lesion: for a calibrated
 * distance it is the *first caliper endpoint*, so a ring drawn around it sits
 * half off a fifty-millimetre mass. The centre is the midpoint of the two
 * points the engine recorded, and the diameter is the number it measured
 * between them.
 *
 * When the ledger recorded no points at all there is no outline to draw and
 * pretending otherwise is how a viewer lies: the address is shown as an
 * address, small and exact, with the size given as a number instead.
 */
export interface Lesion {
  row: number;
  col: number;
  /** Diameter in millimetres, 0 when the ledger did not establish one here. */
  sizeMm: number;
  /** The measured segment, when there is one, so the caliper itself is visible. */
  caliper: [{ row: number; col: number }, { row: number; col: number }] | null;
  /**
   * `extent`  the ledger fixed a centre and a size; the outline is meaningful.
   * `address` only a cited point is known; draw a target, never an outline.
   */
  kind: "extent" | "address";
}

export function lesionAt(at: FindingAtStudy): Lesion | null {
  const ref = at.ref;
  if (!ref || ref.row === undefined || ref.col === undefined) return null;
  const points = at.measurement?.points ?? [];
  const sizeMm = at.measurement?.unit === "mm" ? at.measurement.value : 0;

  const first = points[0];
  const second = points[1];
  if (first && second) {
    return {
      row: Math.round((first.row + second.row) / 2),
      col: Math.round((first.col + second.col) / 2),
      sizeMm,
      caliper: [first, second],
      kind: "extent",
    };
  }
  // A single seeded point sits inside the lesion the engine then grew, so it
  // is a centre; a bare reference with no points is only an address.
  if (first) {
    return { row: first.row, col: first.col, sizeMm, caliper: null, kind: "extent" };
  }
  return { row: ref.row, col: ref.col, sizeMm, caliper: null, kind: "address" };
}

/** `20.0 → 1.7 mm`, or a single number when there is nothing to compare with. */
export function changeLabel(finding: Finding): string {
  if (finding.change) {
    return `${format(finding.change.from)} → ${format(finding.change.to)} ${finding.change.unit}`;
  }
  const only = finding.series.find((s) => s.measurement)?.measurement;
  return only ? `${format(only.value)} ${only.unit}` : "";
}

export function format(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** DICOM StudyDate as `3 Eylül 2026`. The long form: a date is read once. */
const MONTHS_TR = [
  "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
];

export function longDate(raw: string): string {
  if (!/^\d{8}$/.test(raw)) return raw || "—";
  const month = MONTHS_TR[Number(raw.slice(4, 6)) - 1] ?? raw.slice(4, 6);
  return `${Number(raw.slice(6, 8))} ${month} ${raw.slice(0, 4)}`;
}

export function shortDate(raw: string): string {
  if (!/^\d{8}$/.test(raw)) return raw || "—";
  return `${raw.slice(6, 8)}.${raw.slice(4, 6)}.${raw.slice(0, 4)}`;
}

/** Whole months between two DICOM dates, for "iki tetkik arası 2,5 ay". */
export function monthsBetween(a: string, b: string): number | null {
  if (!/^\d{8}$/.test(a) || !/^\d{8}$/.test(b)) return null;
  const toDate = (raw: string) =>
    Date.UTC(Number(raw.slice(0, 4)), Number(raw.slice(4, 6)) - 1, Number(raw.slice(6, 8)));
  const days = (toDate(b) - toDate(a)) / 86_400_000;
  return days > 0 ? days / 30.44 : null;
}
