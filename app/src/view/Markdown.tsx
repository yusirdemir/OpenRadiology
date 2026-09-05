/** Render the engine's Markdown as React elements. No HTML string ever exists. */
import { Fragment } from "react";
import { parseMarkdown, type Block, type Inline } from "../lib/markdown";

function Runs({ content }: { content: Inline[] }) {
  return (
    <>
      {content.map((run, i) => {
        if (run.code) return <code key={i}>{run.text}</code>;
        if (run.bold) return <strong key={i}>{run.text}</strong>;
        return <Fragment key={i}>{run.text}</Fragment>;
      })}
    </>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "heading": {
      const Tag = (["h1", "h2", "h3", "h3"] as const)[block.level - 1] ?? "h3";
      return (
        <Tag>
          <Runs content={block.content} />
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p>
          <Runs content={block.content} />
        </p>
      );
    case "list": {
      const items = block.items.map((item, i) => (
        <li key={i}>
          <Runs content={item} />
        </li>
      ));
      return block.ordered ? <ol>{items}</ol> : <ul>{items}</ul>;
    }
    case "quote":
      return (
        <blockquote>
          <Runs content={block.content} />
        </blockquote>
      );
    case "code":
      return (
        <pre className="readout overflow-x-auto rounded-[2px] border border-[var(--hairline)] bg-ink-950 p-2 text-[11px]">
          {block.text}
        </pre>
      );
    case "table":
      return (
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                {block.head.map((cell, i) => (
                  <th key={i}>
                    <Runs content={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, i) => (
                <tr key={i}>
                  {row.map((cell, j) => (
                    <td key={j}>
                      <Runs content={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "rule":
      return <hr className="my-4 border-t border-[var(--hairline)]" />;
    default:
      return null;
  }
}

export function Markdown({ source }: { source: string }) {
  const blocks = parseMarkdown(source);
  return (
    <div className="prose-report">
      {blocks.map((block, i) => (
        <BlockView key={i} block={block} />
      ))}
    </div>
  );
}
