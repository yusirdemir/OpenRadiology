import { describe, expect, it } from "vitest";
import { groupBlockers, translateBlocker, translateBlockers } from "./blockers";

describe("validator findings are made readable without being lost", () => {
  it("translates the session-level findings", () => {
    expect(translateBlocker("Record model/reader").text).toBe("Okuyucu (model veya kişi) kaydedilmemiş");
    expect(translateBlocker("Reading not complete").translated).toBe(true);
  });

  it("keeps the series number out of the pattern", () => {
    const blocker = translateBlocker("Series 9998: disposition pending");
    expect(blocker.text).toBe("Seri 9998: okundu mu, dışlandı mı belirtilmemiş");
    expect(blocker.where).toBe("series");
  });

  it("names the patient-explanation field that is empty", () => {
    expect(translateBlocker("C1: patient explanation discuss_with_doctor missing").text).toBe(
      "C1: hasta açıklamasının “hekimle konuşulacaklar” alanı boş",
    );
  });

  it("shortens a page path to its file name", () => {
    expect(translateBlocker("Unreviewed page: /a/b/c/S8_lung_axial_01.png").text).toBe(
      "Görüntülenmemiş sayfa: S8_lung_axial_01.png",
    );
  });

  it("carries the attestation refusal through", () => {
    const blocker = translateBlocker(
      "page marked reviewed without a view attestation: /w/x.png (2 of 3 source slices were never displayed)",
    );
    expect(blocker.text).toContain("tasdiksiz sayfa: x.png");
    expect(blocker.text).toContain("2 of 3");
  });

  it("passes an unknown finding through verbatim rather than dropping it", () => {
    const blocker = translateBlocker("Some future validator message");
    expect(blocker.text).toBe("Some future validator message");
    expect(blocker.translated).toBe(false);
  });

  it("groups by where the reader goes to fix it, keeping validator order", () => {
    const groups = groupBlockers(
      translateBlockers([
        "Series 3: disposition pending",
        "Record model/reader",
        "Series 4: disposition pending",
        "No rendered pages registered",
      ]),
    );
    expect(groups.map((g) => g.where)).toEqual(["session", "series", "pages"]);
    expect(groups[1]?.items).toHaveLength(2);
  });
});
