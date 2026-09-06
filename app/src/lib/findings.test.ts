import { describe, expect, it } from "vitest";
import type { Claim, Session } from "../api/types";
import {
  buildFindings,
  changeLabel,
  expectedTissue,
  findingBadge,
  lesionAt,
  monthsBetween,
  tally,
} from "./findings";

const A = "study.a";
const B = "study.b";

function reference(study: string, sop: string) {
  return { study_uid: study, series_uid: `${study}.series`, sop_uid: sop, row: 100, col: 120 };
}

function session(claims: Claim[], mode: Session["mode"] = "comparison"): Session {
  const studies = [
    { uid: A, date: "20260616", time: "170000" },
    { uid: B, date: "20260903", time: "210000" },
  ]
    .slice(0, mode === "comparison" ? 2 : 1)
    .map((s) => ({
      folder: s.uid,
      path: `/dicom/${s.uid}`,
      uid: s.uid,
      date: s.date,
      time: s.time,
      modalities: ["CT"],
      series: [],
      regions: {},
      source_files: [],
    }));
  return {
    schema: 2,
    created_utc: "",
    repo: "",
    work_dir: "",
    mode,
    reader: "",
    context_disclosure: "",
    blind_status: "not_claimed",
    limitations: [],
    patient_context: "",
    patient_limitations: "",
    reading_complete: true,
    toolchain: [],
    comparison: { status: "compared", reason: "", explanation: "" },
    studies,
    pages: [],
    claims,
  };
}

function claim(overrides: Partial<Claim>): Claim {
  return {
    id: "c1",
    text: "teknik cümle",
    confidence: "high",
    priority: "routine",
    refs: [],
    pages: [],
    patient: { meaning: "sade cümle", importance: "", uncertainty: "", discuss_with_doctor: "" },
    ...overrides,
  };
}

/**
 * The rule this whole redesign turns on: on the examination where a finding is
 * gone, it is gone -- even though the ledger carries a residual measurement
 * taken at the same address to prove it. Reading presence off the measurement
 * instead of off the timeline is what would put a red ring on a healed scan.
 */
describe("presence", () => {
  const resolved = claim({
    id: "effusion",
    comparison: { status: "resolved", reason: "", from_study_uid: A, to_study_uid: B },
    refs: [reference(A, "a1"), reference(B, "b1")],
    measurements: [
      { value: 20, unit: "mm", method: "d", evidence_file: "", sha256: "x", ref: reference(A, "a1") },
      { value: 1.7, unit: "mm", method: "d", evidence_file: "", sha256: "y", ref: reference(B, "b1") },
    ],
    timeline: [
      { study_uid: A, status: "present", text: "20 mm efüzyon", explanation: "", refs: [reference(A, "a1")] },
      { study_uid: B, status: "not_seen", text: "tamamen çekilmiş", explanation: "", refs: [reference(B, "b1")] },
    ],
  });

  it("marks a resolved finding absent on the later study despite its residual measurement", () => {
    const [finding] = buildFindings(session([resolved]));
    expect(finding?.atStudy[A]?.presence).toBe("present");
    expect(finding?.atStudy[B]?.presence).toBe("absent");
    // The number is still carried: it is the evidence the thing is gone.
    expect(finding?.atStudy[B]?.measurement?.value).toBe(1.7);
  });

  it("keeps an address for the study the finding is absent from", () => {
    const [finding] = buildFindings(session([resolved]));
    expect(finding?.atStudy[B]?.ref?.sop_uid).toBe("b1");
  });

  it("reports a study the timeline calls uncovered as uncovered, not as clean", () => {
    const partial = claim({
      timeline: [
        { study_uid: A, status: "present", text: "var", explanation: "" },
        { study_uid: B, status: "not_covered", text: "kapsam dışı", explanation: "" },
      ],
    });
    const [finding] = buildFindings(session([partial]));
    expect(finding?.atStudy[B]?.presence).toBe("not_covered");
  });
});

describe("verdicts", () => {
  it("takes the verdict from the ledger rather than from the numbers", () => {
    // 10.0 to 9.9 mm is not shrinkage, and the ledger already said so.
    const stable = claim({
      comparison: { status: "stable", reason: "" },
      measurements: [
        { value: 10, unit: "mm", method: "d", evidence_file: "", sha256: "x", ref: reference(A, "a1") },
        { value: 9.9, unit: "mm", method: "d", evidence_file: "", sha256: "y", ref: reference(B, "b1") },
      ],
    });
    const [finding] = buildFindings(session([stable]));
    expect(finding?.verdict).toBe("stable");
    expect(changeLabel(finding!)).toBe("10 → 9.9 mm");
  });

  it("gives a single read no verdict and no change line", () => {
    const single = claim({
      refs: [reference(A, "a1")],
      measurements: [
        { value: 55.3, unit: "mm", method: "d", evidence_file: "", sha256: "x", ref: reference(A, "a1") },
      ],
    });
    const [finding] = buildFindings(session([single], "single"));
    expect(finding?.verdict).toBe("present");
    expect(finding?.change).toBeNull();
    expect(changeLabel(finding!)).toBe("55.3 mm");
  });

  it("badges a single read by priority, so a benign finding is not dressed as an alarm", () => {
    const findings = buildFindings(
      session(
        [
          claim({ id: "mass", priority: "important", refs: [reference(A, "a1")] }),
          claim({ id: "cyst", priority: "incidental", refs: [reference(A, "a2")] }),
        ],
        "single",
      ),
    );
    const mass = findings.find((f) => f.id === "mass");
    const cyst = findings.find((f) => f.id === "cyst");
    expect(findingBadge(mass!, false).tone).toBe("warn");
    expect(findingBadge(cyst!, false).tone).toBe("calm");
  });

  it("falls back to not_comparable rather than inventing a verdict", () => {
    const [finding] = buildFindings(session([claim({ comparison: { status: "", reason: "" } })]));
    expect(finding?.verdict).toBe("not_comparable");
  });
});

describe("order and tally", () => {
  it("puts anything that got worse above the reassuring majority", () => {
    const findings = buildFindings(
      session([
        claim({ id: "stable", comparison: { status: "stable", reason: "" } }),
        claim({ id: "resolved", comparison: { status: "resolved", reason: "" } }),
        claim({ id: "grew", comparison: { status: "increased", reason: "" } }),
        claim({ id: "new", comparison: { status: "new", reason: "" } }),
      ]),
    );
    expect(findings.map((f) => f.id)).toEqual(["grew", "new", "resolved", "stable"]);
  });

  it("counts the four things a reader actually wants counted", () => {
    const counts = tally(
      buildFindings(
        session([
          claim({ id: "a", comparison: { status: "resolved", reason: "" } }),
          claim({ id: "b", comparison: { status: "stable", reason: "" } }),
          claim({ id: "c", comparison: { status: "stable", reason: "" } }),
          claim({ id: "d", comparison: { status: "increased", reason: "" } }),
          claim({ id: "e", comparison: { status: "indeterminate", reason: "" } }),
        ]),
      ),
    );
    expect(counts).toEqual({ better: 1, same: 2, worse: 1, attention: 1, total: 5 });
  });
});

describe("dates", () => {
  it("measures the interval between two examinations", () => {
    expect(monthsBetween("20260616", "20260903")?.toFixed(1)).toBe("2.6");
  });

  it("refuses to invent an interval from a malformed date", () => {
    expect(monthsBetween("", "20260903")).toBeNull();
  });
});

/**
 * The bug that made a fifty-millimetre mass look mis-marked: a caliper's `ref`
 * is its first endpoint, and a ring centred there sits half off the lesion.
 */
describe("where a finding actually is", () => {
  const withCaliper = claim({
    id: "effusion",
    refs: [reference(A, "a1")],
    measurements: [
      {
        value: 20,
        unit: "mm",
        method: "calibrated distance",
        evidence_file: "",
        sha256: "x",
        // The engine records the first caliper endpoint as the reference.
        ref: { ...reference(A, "a1"), row: 402, col: 180 },
        points: [
          { row: 402, col: 180 },
          { row: 426, col: 180 },
        ],
      },
    ],
    timeline: [{ study_uid: A, status: "present", text: "", explanation: "" }],
  });

  it("centres on the midpoint of the caliper, not on its first endpoint", () => {
    const [finding] = buildFindings(session([withCaliper], "single"));
    const spot = lesionAt(finding!.series[0]!);
    expect(spot).toMatchObject({ row: 414, col: 180, sizeMm: 20, kind: "extent" });
    // The reference alone would have put the mark twelve pixels too high.
    expect(finding!.series[0]!.ref?.row).toBe(402);
  });

  it("keeps the measured segment so the number can be checked against it", () => {
    const [finding] = buildFindings(session([withCaliper], "single"));
    expect(lesionAt(finding!.series[0]!)?.caliper).toEqual([
      { row: 402, col: 180 },
      { row: 426, col: 180 },
    ]);
  });

  it("treats a single seeded point as a centre", () => {
    const seeded = claim({
      refs: [reference(A, "a1")],
      measurements: [
        {
          value: 11.5,
          unit: "mm",
          method: "2D region growing",
          evidence_file: "",
          sha256: "x",
          ref: reference(A, "a1"),
          points: [{ row: 368, col: 190 }],
        },
      ],
      timeline: [{ study_uid: A, status: "present", text: "", explanation: "" }],
    });
    const [finding] = buildFindings(session([seeded], "single"));
    expect(lesionAt(finding!.series[0]!)).toMatchObject({ row: 368, col: 190, kind: "extent" });
  });

  /**
   * With no points the ledger fixed an address and never fixed an outline.
   * Drawing a lesion-sized ring around it would be a guess wearing the clothes
   * of a measurement, so the size is withheld and the mark becomes a target.
   */
  it("refuses to imply an outline it was never given", () => {
    const addressOnly = claim({
      refs: [reference(A, "a1")],
      measurements: [
        {
          value: 55.3,
          unit: "mm",
          method: "calibrated distance",
          evidence_file: "",
          sha256: "x",
          ref: reference(A, "a1"),
        },
      ],
      timeline: [{ study_uid: A, status: "present", text: "", explanation: "" }],
    });
    const [finding] = buildFindings(session([addressOnly], "single"));
    const spot = lesionAt(finding!.series[0]!);
    expect(spot).toMatchObject({ row: 100, col: 120, kind: "address", caliper: null });
    expect(spot?.sizeMm).toBe(55.3);
  });

  it("has nothing to draw when the ledger cited no coordinates", () => {
    const bare = claim({
      refs: [{ study_uid: A, series_uid: "s", sop_uid: "a1" }],
      timeline: [{ study_uid: A, status: "present", text: "", explanation: "" }],
    });
    const [finding] = buildFindings(session([bare], "single"));
    expect(lesionAt(finding!.series[0]!)).toBeNull();
  });
});

/**
 * The viewer checks its own marks against the pixels. These bands are what a
 * mismatch is measured against, so they are worth pinning down.
 */
describe("what the tissue under a mark should read", () => {
  const of = (text: string) => {
    const [finding] = buildFindings(session([claim({ text })], "single"));
    return expectedTissue(finding!);
  };

  it("expects a calcified granuloma to be dense", () => {
    expect(of("Sağ alt lobda 11 mm kalsifiye granülom")).toMatchObject({ label: "kalsifik", min: 120 });
  });

  it("expects pleural fluid to read near water", () => {
    expect(of("Sağ hemitoraksta serbest plevral efüzyon")).toMatchObject({ label: "sıvı" });
  });

  it("expects a bulla to read as air", () => {
    expect(of("Kitlenin altında ince duvarlı bül")).toMatchObject({ label: "hava", max: -650 });
  });

  it("claims nothing when the wording pins nothing down", () => {
    expect(of("Değerlendirme tanısal düzeydedir.")).toBeNull();
  });
});
