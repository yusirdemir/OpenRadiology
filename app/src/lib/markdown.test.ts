import { describe, expect, it } from "vitest";
import { parseInline, parseMarkdown } from "./markdown";

describe("inline", () => {
  it("splits bold and code runs", () => {
    expect(parseInline("bir **kitle** ve `S8` görüldü")).toEqual([
      { text: "bir " },
      { text: "kitle", bold: true },
      { text: " ve " },
      { text: "S8", code: true },
      { text: " görüldü" },
    ]);
  });

  it("leaves an unmatched marker as literal text", () => {
    expect(parseInline("2 ** 3")).toEqual([{ text: "2 ** 3" }]);
  });
});

describe("blocks", () => {
  it("reads headings, paragraphs and rules", () => {
    const blocks = parseMarkdown("# Başlık\n\nbir satır\ndevamı\n\n---\n");
    expect(blocks[0]).toEqual({ type: "heading", level: 1, content: [{ text: "Başlık" }] });
    expect(blocks[1]).toEqual({ type: "paragraph", content: [{ text: "bir satır devamı" }] });
    expect(blocks[2]).toEqual({ type: "rule" });
  });

  it("reads both list kinds without merging them", () => {
    const blocks = parseMarkdown("- a\n- b\n\n1. bir\n2. iki\n");
    expect(blocks[0]).toMatchObject({ type: "list", ordered: false });
    expect((blocks[0] as { items: unknown[] }).items).toHaveLength(2);
    expect(blocks[1]).toMatchObject({ type: "list", ordered: true });
  });

  it("reads a pipe table with its header", () => {
    const blocks = parseMarkdown("| Bölge | Durum |\n|---|---|\n| Sağ akciğer | **bulgu** |\n");
    const table = blocks[0] as { type: string; head: unknown[]; rows: unknown[][] };
    expect(table.type).toBe("table");
    expect(table.head).toHaveLength(2);
    expect(table.rows).toHaveLength(1);
    expect(table.rows[0]?.[1]).toEqual([{ text: "bulgu", bold: true }]);
  });

  it("keeps fenced code verbatim", () => {
    const blocks = parseMarkdown("```\nopenrad measure --roi 12,14,3\n```\n");
    expect(blocks[0]).toEqual({ type: "code", text: "openrad measure --roi 12,14,3" });
  });

  it("reads a block quote", () => {
    expect(parseMarkdown("> dikkat\n")[0]).toEqual({ type: "quote", content: [{ text: "dikkat" }] });
  });

  it("never produces raw HTML", () => {
    const blocks = parseMarkdown("<img src=x onerror=alert(1)>\n");
    expect(blocks[0]).toEqual({ type: "paragraph", content: [{ text: "<img src=x onerror=alert(1)>" }] });
  });
});
