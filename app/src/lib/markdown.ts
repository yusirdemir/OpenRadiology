/**
 * A small Markdown reader for the engine's own output.
 *
 * The templates in templates/<lang>/report_template.md use a bounded set:
 * headings, bullet and ordered lists, block quotes, fenced code, pipe tables,
 * bold and inline code. Parsing exactly that into a typed tree -- rather than
 * pulling in a general Markdown library and injecting its HTML -- keeps the
 * report render free of a raw-HTML path, which is worth more than generality
 * in a document that carries patient findings.
 */
export type Inline = { text: string; bold?: boolean; code?: boolean; href?: string };

export type Block =
  | { type: "heading"; level: 1 | 2 | 3 | 4; content: Inline[] }
  | { type: "paragraph"; content: Inline[] }
  | { type: "list"; ordered: boolean; items: Inline[][] }
  | { type: "quote"; content: Inline[] }
  | { type: "code"; text: string }
  | { type: "table"; head: Inline[][]; rows: Inline[][][] }
  | { type: "rule" };

const HEADING = /^(#{1,4})\s+(.*)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^>\s?(.*)$/;
const RULE = /^(-{3,}|\*{3,}|_{3,})\s*$/;
const FENCE_MARK = "`".repeat(3);
const isFence = (line: string): boolean => line.trimStart().startsWith(FENCE_MARK);
const TABLE_ROW = /^\s*\|(.+)\|\s*$/;
const TABLE_DIVIDER = /^\s*\|[\s:|-]+\|\s*$/;

/**
 * Split a line into bold, code, link and plain runs. Unmatched markers stay
 * literal.
 *
 * Links in the engine's documents point at sibling files on disk -- the
 * professional report from the patient guide, a measurement's evidence file
 * from a claim. There is nothing useful for a browser to do with those, but
 * printing `[name](name)` at the reader was worse than either opening them or
 * saying nothing, so the text is kept and the target moves to the title.
 */
export function parseInline(raw: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]\n]+\]\([^)\s]*\))/g;
  let cursor = 0;
  for (let match = pattern.exec(raw); match !== null; match = pattern.exec(raw)) {
    if (match.index > cursor) out.push({ text: raw.slice(cursor, match.index) });
    const token = match[0];
    if (token.startsWith("**")) {
      out.push({ text: token.slice(2, -2), bold: true });
    } else if (token.startsWith("[")) {
      const split = token.indexOf("](");
      out.push({ text: token.slice(1, split), href: token.slice(split + 2, -1) });
    } else {
      out.push({ text: token.slice(1, -1), code: true });
    }
    cursor = match.index + token.length;
  }
  if (cursor < raw.length) out.push({ text: raw.slice(cursor) });
  return out.length ? out : [{ text: raw }];
}

function splitRow(line: string): Inline[][] {
  const inner = TABLE_ROW.exec(line)?.[1] ?? "";
  return inner.split("|").map((cell) => parseInline(cell.trim()));
}

export function parseMarkdown(source: string): Block[] {
  // The engine stamps a provenance comment at the top of every document. It is
  // there for whatever reads the file next, not for the person reading the
  // report, and printing it verbatim was the first thing on the page.
  const lines = source
    .replace(/\r\n/g, "\n")
    .replace(/<!--[\s\S]*?-->/g, "")
    .split("\n");
  const blocks: Block[] = [];
  let paragraph: string[] = [];

  const flushParagraph = () => {
    if (!paragraph.length) return;
    blocks.push({ type: "paragraph", content: parseInline(paragraph.join(" ").trim()) });
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";

    if (isFence(line)) {
      flushParagraph();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !isFence(lines[i] ?? "")) {
        body.push(lines[i] ?? "");
        i += 1;
      }
      blocks.push({ type: "code", text: body.join("\n") });
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      blocks.push({
        type: "heading",
        level: (heading[1]?.length ?? 1) as 1 | 2 | 3 | 4,
        content: parseInline(heading[2] ?? ""),
      });
      continue;
    }

    if (RULE.test(line)) {
      flushParagraph();
      blocks.push({ type: "rule" });
      continue;
    }

    if (TABLE_ROW.test(line) && TABLE_DIVIDER.test(lines[i + 1] ?? "")) {
      flushParagraph();
      const head = splitRow(line);
      const rows: Inline[][][] = [];
      i += 2;
      while (i < lines.length && TABLE_ROW.test(lines[i] ?? "")) {
        rows.push(splitRow(lines[i] ?? ""));
        i += 1;
      }
      i -= 1;
      blocks.push({ type: "table", head, rows });
      continue;
    }

    const quote = QUOTE.exec(line);
    if (quote) {
      flushParagraph();
      blocks.push({ type: "quote", content: parseInline(quote[1] ?? "") });
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet || ordered) {
      flushParagraph();
      const isOrdered = ordered !== null && bullet === null;
      const items: Inline[][] = [];
      while (i < lines.length) {
        const candidate = lines[i] ?? "";
        const asBullet = BULLET.exec(candidate);
        const asOrdered = ORDERED.exec(candidate);
        const match = isOrdered ? asOrdered : asBullet;
        if (!match || (isOrdered ? asBullet !== null : asOrdered !== null && asBullet === null)) break;
        items.push(parseInline(match[1] ?? ""));
        i += 1;
      }
      i -= 1;
      blocks.push({ type: "list", ordered: isOrdered, items });
      continue;
    }

    paragraph.push(line.trim());
  }
  flushParagraph();
  return blocks;
}
