/**
 * The validator's findings, in the reader's language.
 *
 * `openrad check` returns the exact list of things stopping a report, which is
 * the most useful text in the application: it is the remaining work. It is
 * written in English inside the engine, and translating it there would fork a
 * message set the terminal and the MCP clients also depend on.
 *
 * So it is translated here, by pattern, and anything unrecognised falls through
 * verbatim. A finding shown in the wrong language is still a finding; a finding
 * quietly dropped because no rule matched would be a lie about what is blocking
 * the report.
 */
export interface Blocker {
  raw: string;
  text: string;
  /** Where in the interface this is fixed, when that is unambiguous. */
  where?: "session" | "series" | "region" | "claim" | "pages" | "comparison";
  translated: boolean;
}

type Rule = [RegExp, (m: RegExpMatchArray) => string, Blocker["where"]?];

const RULES: Rule[] = [
  // -- session metadata ---------------------------------------------------
  [/^Reading not complete$/, () => "Okuma tamamlanmadı olarak işaretli", "session"],
  [/^Record model\/reader$/, () => "Okuyucu (model veya kişi) kaydedilmemiş", "session"],
  [/^Disclose prior context\/leakage, even when none$/,
    () => "Okumadan önce bilinenler beyan edilmemiş (“hiçbir şey” de bir beyandır)", "session"],
  [/^Record actual coverage and sensitivity limitations$/,
    () => "Kapsam ve duyarlılık kısıtları yazılmamış", "session"],
  [/^Explain examination scope in plain language \(patient_context\)$/,
    () => "Tetkikin kapsamı hasta dilinde açıklanmamış", "session"],
  [/^Explain limitations in plain language \(patient_limitations\)$/,
    () => "Kısıtlar hasta dilinde açıklanmamış", "session"],
  [/^Invalid blind status$/, () => "Körlük durumu geçersiz", "session"],
  [/^Unsupported report language$/, () => "Rapor dili desteklenmiyor", "session"],
  [/^Claim IDs must be unique$/, () => "Bulgu kimlikleri benzersiz olmalı", "claim"],

  // -- comparison ---------------------------------------------------------
  [/^Comparison not completed$/, () => "Karşılaştırma sonuçlandırılmamış", "comparison"],
  [/^Comparison limitations\/method missing$/, () => "Karşılaştırma yöntemi ve kısıtları yazılmamış", "comparison"],
  [/^Plain-language comparison explanation missing$/,
    () => "Karşılaştırmanın hasta dilindeki açıklaması yok", "comparison"],

  // -- pages --------------------------------------------------------------
  [/^No rendered pages registered$/, () => "Hiç render edilmiş sayfa kaydedilmemiş", "pages"],
  [/^Unreviewed page: (.+)$/, (m) => `Görüntülenmemiş sayfa: ${fileName(m[1])}`, "pages"],
  [/^Page purpose missing$/, () => "Sayfanın amacı belirtilmemiş", "pages"],
  [/^Page needs valid study and series UID$/, () => "Sayfa geçerli çalışma ve seri kimliği taşımıyor", "pages"],
  [/^Missing\/changed input: (.+)$/, (m) => `Kaynak dosya eksik ya da değişmiş: ${m[1]}`, "pages"],
  [/^page marked reviewed without a view attestation: (.+?) \((.+)\)$/,
    (m) => `“Okundu” işaretli ama tasdiksiz sayfa: ${fileName(m[1])} — ${m[2]}`, "pages"],

  // -- series -------------------------------------------------------------
  [/^Series (\S+): disposition pending$/, (m) => `Seri ${m[1]}: okundu mu, dışlandı mı belirtilmemiş`, "series"],
  [/^Series (\S+): excluded\/unsupported series needs reason$/,
    (m) => `Seri ${m[1]}: dışlama gerekçesi yazılmamış`, "series"],
  [/^Series (\S+): read series geometry not checked$/,
    (m) => `Seri ${m[1]}: geometri doğrulaması yapılmamış`, "series"],
  [/^Series (\S+): read image series needs declared exhaustive passes$/,
    (m) => `Seri ${m[1]}: hangi render geçişlerinin tam yapıldığı bildirilmemiş`, "series"],
  [/^Series (\S+): read series has no pages$/, (m) => `Seri ${m[1]}: okundu deniyor ama hiç sayfası yok`, "series"],
  [/^Series (\S+) (\S+): (\d+) unread\/unrendered slices$/,
    (m) => `Seri ${m[1]} (${m[2]}): ${m[3]} kesit hiç render edilmemiş ya da okunmamış`, "series"],

  // -- regions and claims -------------------------------------------------
  [/^(.+): explanation missing$/, (m) => `${m[1]}: hekim ifadesi yazılmamış`, "region"],
  [/^(.+): patient-friendly explanation missing$/, (m) => `${m[1]}: hasta dilindeki açıklama yok`, "region"],
  [/^(.+): unresolved region$/, (m) => `${m[1]}: bölge hâlâ “bakılmadı”`, "region"],
  [/^(.+): source references missing$/, (m) => `${m[1]}: hiçbir kesit adresi gösterilmemiş`, "region"],
  [/^(.+): inspected image pages missing$/, (m) => `${m[1]}: incelenmiş sayfa gösterilmemiş`, "region"],
  [/^(.+): coordinates out of range$/, (m) => `${m[1]}: satır/sütun görüntünün dışında`, "region"],
  [/^(.+): unknown source SOP\/series\/study$/, (m) => `${m[1]}: adres bu çalışmada bulunamadı`, "region"],
  [/^(.+): unregistered page$/, (m) => `${m[1]}: kayıtlı olmayan bir sayfaya atıf`, "region"],
  [/^(.+): page belongs to another study$/, (m) => `${m[1]}: sayfa başka bir çalışmaya ait`, "region"],
  [/^(.+): wrong study reference$/, (m) => `${m[1]}: yanlış çalışmaya atıf`, "region"],
  [/^(.+): referenced series has not been read$/, (m) => `${m[1]}: atıf yapılan seri okunmuş olarak işaretli değil`, "region"],

  [/^(\S+): text missing$/, (m) => `${m[1]}: hekim ifadesi yazılmamış`, "claim"],
  [/^(\S+): confidence missing$/, (m) => `${m[1]}: güven düzeyi seçilmemiş`, "claim"],
  [/^(\S+): priority missing$/, (m) => `${m[1]}: öncelik seçilmemiş`, "claim"],
  [/^(\S+): patient explanation (\S+) missing$/,
    (m) => `${m[1]}: hasta açıklamasının “${patientField(m[2])}” alanı boş`, "claim"],
  [/^(\S+): measurement method missing$/, (m) => `${m[1]}: ölçüm yöntemi yazılmamış`, "claim"],
  [/^(\S+): invalid measurement unit$/, (m) => `${m[1]}: ölçüm birimi geçersiz`, "claim"],
  [/^(\S+): invalid measured value$/, (m) => `${m[1]}: ölçüm değeri geçersiz`, "claim"],
  [/^(\S+): measurement needs native row\/column coordinates$/,
    (m) => `${m[1]}: ölçüm satır/sütun adresi taşımıyor`, "claim"],
  [/^(\S+): measurement tool output missing$/, (m) => `${m[1]}: ölçümün kanıt dosyası yok`, "claim"],
  [/^(\S+): measurement output hash missing$/, (m) => `${m[1]}: kanıt dosyasının özeti yok`, "claim"],
  [/^(\S+): measurement output changed$/, (m) => `${m[1]}: kanıt dosyası kaydedildikten sonra değişmiş`, "claim"],
  [/^(\S+): comparison status missing$/, (m) => `${m[1]}: değişim verdikti seçilmemiş`, "comparison"],
  [/^(\S+): comparison rationale missing$/, (m) => `${m[1]}: değişim gerekçesi yazılmamış`, "comparison"],
  [/^(\S+): comparison endpoints reversed$/, (m) => `${m[1]}: karşılaştırma uçları ters sırada`, "comparison"],
  [/^(\S+): change claim needs explicit distinct comparison endpoints$/,
    (m) => `${m[1]}: değişim iddiası iki ayrı tetkik ucu ister`, "comparison"],
  [/^(\S+): change claim needs both endpoint studies' evidence$/,
    (m) => `${m[1]}: değişim iddiası her iki uçtan da kanıt ister`, "comparison"],
  [/^(\S+): timeline must cover every supplied study$/,
    (m) => `${m[1]}: zaman çizelgesi her tetkiki kapsamalı`, "comparison"],
  [/^(\S+): timepoint needs professional and plain-language descriptions$/,
    (m) => `${m[1]}: zaman noktası hem hekim hem hasta ifadesi ister`, "comparison"],
  [/^(\S+): timepoint source belongs to a different study$/,
    (m) => `${m[1]}: zaman noktasının adresi başka bir çalışmada`, "comparison"],
  [/^(\S+): invalid timepoint$/, (m) => `${m[1]}: zaman noktası geçersiz`, "comparison"],
];

const PATIENT_FIELDS: Record<string, string> = {
  meaning: "ne anlama geliyor",
  importance: "neden önemli",
  uncertainty: "belirsizlik",
  discuss_with_doctor: "hekimle konuşulacaklar",
};

const patientField = (key: string | undefined): string => (key ? (PATIENT_FIELDS[key] ?? key) : "");
const fileName = (path: string | undefined): string => (path ? (path.split("/").pop() ?? path) : "");

export function translateBlocker(raw: string): Blocker {
  for (const [pattern, render, where] of RULES) {
    const match = raw.match(pattern);
    if (match) return { raw, text: render(match), where, translated: true };
  }
  return { raw, text: raw, translated: false };
}

export function translateBlockers(raws: string[]): Blocker[] {
  return raws.map(translateBlocker);
}

/** Group by where the reader goes to fix it, keeping the validator's order. */
export function groupBlockers(blockers: Blocker[]): { where: Blocker["where"]; items: Blocker[] }[] {
  const order: Blocker["where"][] = ["session", "series", "pages", "region", "claim", "comparison", undefined];
  const buckets = new Map<Blocker["where"], Blocker[]>();
  for (const blocker of blockers) {
    const list = buckets.get(blocker.where);
    if (list) list.push(blocker);
    else buckets.set(blocker.where, [blocker]);
  }
  return order.filter((where) => buckets.has(where)).map((where) => ({ where, items: buckets.get(where) ?? [] }));
}

export const GROUP_LABEL: Record<string, string> = {
  session: "Oturum bilgileri",
  series: "Seriler",
  pages: "Render edilmiş sayfalar",
  region: "Anatomik bölgeler",
  claim: "Bulgular",
  comparison: "Karşılaştırma",
  undefined: "Diğer",
};
